"""FastAPI app for the simulation viewer.

Routes:

* ``GET /``          — the single-page form (empty state).
* ``POST /validate`` — parse the pasted decklist; render a status panel
  plus a ``<datalist>`` of unique card names for the autocomplete.
* ``POST /hand``     — mutate the hand (action ∈ {random, add, remove})
  and re-render the chip strip.
* ``POST /simulate`` — run one ``iter_traces`` call (n_runs=1) and
  render the resulting :class:`GameTrace` turn-by-turn.
* ``GET /healthz``   — trivial liveness check.

Every HTMX request POSTs the entire form via ``hx-include="#decksim"``,
so the server is purely stateless apart from the read-only
:class:`CardStore` it builds at startup.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from mulligan_coach_simulation import GameTrace, iter_traces
from mulligan_coach_simulation.trace import ActionEvent, InstanceOutcome

from .data import CardStore
from .decklist import ParseResult, parse_mtga_decklist, unique_card_names
from .hand import (
    HAND_SIZE,
    HandEntry,
    add_card_by_name,
    random_hand,
    remove_at,
    resolve_hand,
)

# Resolve templates / static via importlib.resources so the package
# works whether installed editable or zipped — the hatch build config
# force-includes both directories into the wheel.
_TEMPLATES_DIR = str(files("simulation_viewer") / "templates")
_STATIC_DIR = str(files("simulation_viewer") / "static")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the card store once at startup.

    Loading three sets of ParsedCards from disk is well under a second;
    we don't try to be clever about caching.
    """
    app.state.store = CardStore.build()
    yield


app = FastAPI(title="Mulligan Coach — simulation viewer", lifespan=lifespan)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the empty form. Hand and trace panes start empty."""
    store: CardStore = request.app.state.store
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "loaded_sets": store.loaded_sets,
            "n_cards": len(store.entries),
            "hand_size": HAND_SIZE,
        },
    )


# ---------------------------------------------------------------------------
# Validate
# ---------------------------------------------------------------------------


@app.post("/validate", response_class=HTMLResponse)
def validate(
    request: Request,
    decklist: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Parse the pasted decklist; render the status panel + datalist.

    Always returns 200 — the panel itself shows ✅ or the per-line
    issue list. The datalist is populated even on partial success, so
    the autocomplete keeps working for the resolved cards while the
    user fixes the broken lines.
    """
    store: CardStore = request.app.state.store
    result = parse_mtga_decklist(decklist, store)
    return templates.TemplateResponse(
        request,
        "_validation.html",
        {
            "result": result,
            "datalist_names": unique_card_names(result),
        },
    )


# ---------------------------------------------------------------------------
# Hand mutations (single endpoint, action-discriminated)
# ---------------------------------------------------------------------------


@app.post("/hand", response_class=HTMLResponse)
def hand(
    request: Request,
    action: Annotated[str, Form()] = "",
    decklist: Annotated[str, Form()] = "",
    hand_ids: Annotated[list[str] | None, Form()] = None,
    add_name: Annotated[str, Form()] = "",
    index: Annotated[int | None, Form()] = None,
) -> HTMLResponse:
    """One endpoint for the three hand mutations.

    Splitting this into three routes would multiply boilerplate without
    making the contract any clearer — every action takes the same form
    state and returns the same partial. The client sends ``action`` via
    HTMX's ``hx-vals``.
    """
    store: CardStore = request.app.state.store
    result = parse_mtga_decklist(decklist, store)
    current_hand: list[str] = hand_ids or []
    error: str | None = None

    if action == "random":
        try:
            current_hand = random_hand(result)
        except ValueError as e:
            error = str(e)
    elif action == "add":
        current_hand, add_error = add_card_by_name(result, current_hand, add_name)
        error = add_error
    elif action == "remove":
        if index is not None:
            current_hand = remove_at(current_hand, index)
    elif action:
        error = f"Unknown hand action {action!r}."

    return _render_hand(request, result, current_hand, error)


# ---------------------------------------------------------------------------
# Simulate
# ---------------------------------------------------------------------------


@app.post("/simulate", response_class=HTMLResponse)
def simulate(
    request: Request,
    decklist: Annotated[str, Form()] = "",
    hand_ids: Annotated[list[str] | None, Form()] = None,
    on_play: Annotated[bool, Form()] = False,
    seed: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Run a single goldfish and render the trace.

    Inline-renders user errors (deck invalid, hand wrong size, hand id
    no longer matches the deck) instead of returning 4xx — the trace
    pane is the user's "where did the answer go?" surface and a 4xx
    would just paint a broken-state HTMX swap.
    """
    store: CardStore = request.app.state.store
    parsed = parse_mtga_decklist(decklist, store)
    current_hand: list[str] = hand_ids or []

    error: str | None = None
    if not parsed.is_valid:
        error = (
            f"Decklist has {len(parsed.unknown_lines)} issue(s); fix the "
            "validation panel above before running a simulation."
        )
    elif len(current_hand) != HAND_SIZE:
        error = (
            f"Hand has {len(current_hand)} card(s); needs exactly "
            f"{HAND_SIZE}. Use Random hand or add cards manually."
        )

    if error is not None:
        return templates.TemplateResponse(
            request,
            "_trace.html",
            {"error": error, "trace": None, "actions_by_turn": {}, "name_by_id": {}},
        )

    entries, library, hand_errors = resolve_hand(parsed, current_hand)
    if hand_errors:
        return templates.TemplateResponse(
            request,
            "_trace.html",
            {
                "error": " ".join(hand_errors),
                "trace": None,
                "actions_by_turn": {},
                "name_by_id": {},
            },
        )

    hand_cards = [e.card for e in entries if e.card is not None]
    parsed_seed = _parse_seed(seed)

    try:
        trace = next(
            iter_traces(
                hand_cards,
                library,
                on_the_play=on_play,
                n_runs=1,
                seed=parsed_seed,
                verbose=True,
            )
        )
    except Exception as e:  # surface any simulator error to the trace pane
        return templates.TemplateResponse(
            request,
            "_trace.html",
            {
                "error": f"Simulation error: {type(e).__name__}: {e}",
                "trace": None,
                "actions_by_turn": {},
                "name_by_id": {},
            },
        )

    return templates.TemplateResponse(
        request,
        "_trace.html",
        {
            "error": None,
            "trace": trace,
            "actions_by_turn": _group_actions_by_turn(trace.actions),
            "name_by_id": _instances_by_id_to_name(trace.instances),
            "first_castable_summary": _first_castable_summary(trace.instances),
        },
    )


# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """Trivial liveness check; doubles as a quick "is the store loaded?"."""
    store: CardStore = request.app.state.store
    return {
        "ok": True,
        "n_cards": len(store.entries),
        "loaded_sets": store.loaded_sets,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_hand(
    request: Request,
    result: ParseResult,
    hand_ids: list[str],
    error: str | None,
) -> HTMLResponse:
    """Resolve current hand ids and render the chip-strip partial.

    Resolution may emit its own errors (e.g., "slot 3 has no matching
    copy left") — we surface the first one we find so the user sees
    a concrete reason rather than a silent broken state.
    """
    entries, _library, resolve_errors = resolve_hand(result, hand_ids)
    return templates.TemplateResponse(
        request,
        "_hand.html",
        {
            "result": result,
            "entries": entries,
            "hand_size": HAND_SIZE,
            "error": error or (resolve_errors[0] if resolve_errors else None),
        },
    )


def _parse_seed(raw: str) -> int | None:
    """Empty string → ``None``; non-numeric input ignored (None)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _group_actions_by_turn(
    actions: list[ActionEvent],
) -> dict[int, list[ActionEvent]]:
    """Partition the verbose action stream into per-turn buckets."""
    out: dict[int, list[ActionEvent]] = defaultdict(list)
    for ev in actions:
        out[ev.turn].append(ev)
    return out


def _instances_by_id_to_name(instances: list[InstanceOutcome]) -> dict[int, str]:
    """``instance_id → display name`` so templates can dereference
    ``LandDropEvent.chosen_instance_id`` etc."""
    return {inst.instance_id: inst.name for inst in instances}


def _first_castable_summary(
    instances: list[InstanceOutcome],
) -> list[tuple[str, list[str]]]:
    """Per-name first-castable buckets, sorted by name.

    Mirrors the simulator's ``_summarise_first_castable`` (in
    ``packages/simulation/src/mulligan_coach_simulation/trace.py``)
    but returns structured data instead of pre-rendered strings —
    the template formats it. ``5+`` means "never in the 4-turn window".
    """
    by_name: dict[str, list[str]] = defaultdict(list)
    for inst in instances:
        label = str(inst.first_castable_turn) if inst.first_castable_turn < 5 else "5+"
        by_name[inst.name].append(label)
    return sorted((name, sorted(labels)) for name, labels in by_name.items())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """``uv run simulation-viewer`` entry point.

    Binds 127.0.0.1 only — this is a local dev tool, never exposed to
    the network. For autoreload during template hacking use
    ``uv run uvicorn simulation_viewer.app:app --reload`` directly.
    """
    uvicorn.run("simulation_viewer.app:app", host="127.0.0.1", port=8000, reload=False)


# Re-export so test code can call helpers directly without import gymnastics.
__all__ = [
    "GameTrace",
    "HandEntry",
    "app",
    "main",
]
