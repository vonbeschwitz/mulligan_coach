"""FastAPI app for the user-facing Mulligan Coach website.

Routes:

* ``GET  /``                — single-page form (deck + hand + context).
* ``POST /validate``        — parse the pasted decklist; render the
  status panel + autocomplete ``<datalist>``.
* ``POST /hand``            — mutate the hand (action ∈
  {random, add, remove, clear}) and re-render the card grid.
* ``POST /recommend``       — run the keep / mulligan recommendation
  and render the verdict panel.
* ``GET  /card-image/{name}`` — 302 to Scryfall's normal-size PNG URL.
* ``GET  /healthz``         — JSON liveness check + load status.

Every HTMX request POSTs the whole form via ``hx-include="#builder"``,
so the server is purely stateless apart from the read-only
:class:`CardStore` and :class:`RecommendationService` it builds at
startup.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

import jinja2
import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.datastructures import URL, URLPath

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
from .recommendation import (
    DEFAULT_N_MULLIGAN_SAMPLES,
    DEFAULT_N_SIMS_KEEP,
    DEFAULT_N_SIMS_PER_MULLIGAN,
    RecommendationService,
    load_service,
)
from .scryfall import ScryfallImages

log = logging.getLogger(__name__)

# Resolve templates / static via importlib.resources so the package
# works whether installed editable (uv sync default) or zipped into a
# wheel — the hatch build config force-includes both directories.
_TEMPLATES_DIR = str(files("mulligan_coach_website") / "templates")
_STATIC_DIR = str(files("mulligan_coach_website") / "static")

# Default deck size for a Limited deck. Sealed pools may build 41s
# (Yorion-style), but the trained model expects exactly 40. We accept
# 40 at the recommend boundary and surface a clear error otherwise.
_REQUIRED_DECK_SIZE = 40


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the card store, recommendation service, and Scryfall client.

    Loading a few hundred ParsedCards from disk is well under a second
    each; the model bundle adds another second or so. The
    recommendation service is best-effort — if the model directory
    isn't there, the app boots in "sim-disabled" mode and surfaces a
    banner on the index page.
    """
    app.state.store = CardStore.build()
    app.state.service = load_service(app.state.store.loaded_sets)
    app.state.scryfall = ScryfallImages.build()
    log.info(
        "website ready: %d cards, %d sets, model_loaded=%s",
        len(app.state.store.entries),
        len(app.state.store.loaded_sets),
        app.state.service.status.model_loaded,
    )
    try:
        yield
    finally:
        # Drain the background recommendation executor before closing
        # the Scryfall client so in-flight workers don't keep the
        # process alive past lifespan teardown.
        app.state.service.shutdown()
        await app.state.scryfall.aclose()


app = FastAPI(title="Mulligan Coach", lifespan=lifespan)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# Starlette's default Jinja ``url_for`` global calls ``request.url_for``,
# which resolves against ``scope["router"]``. With the unnamed sub-app
# mounts in dev_site, that lookup descends into whichever sub-app's
# mount appears first and returns the wrong route when names collide.
# Override the global to always query THIS app's router. We have to
# prepend ``scope["root_path"]`` ourselves because ``request.base_url``
# uses the outermost app's root.
@jinja2.pass_context
def _app_url_for(context: Any, name: str, /, **path_params: Any) -> URL:
    request: Request = context["request"]
    url_path = request.app.url_path_for(name, **path_params)
    root_path = request.scope.get("root_path", "")
    prefixed = URLPath(
        root_path + str(url_path),
        protocol=url_path.protocol,
        host=url_path.host,
    )
    return prefixed.make_absolute_url(base_url=request.base_url)


templates.env.globals["url_for"] = _app_url_for


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the empty form."""
    store: CardStore = request.app.state.store
    service: RecommendationService = request.app.state.service
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "loaded_sets": store.loaded_sets,
            "n_cards": len(store.entries),
            "hand_size": HAND_SIZE,
            "required_deck_size": _REQUIRED_DECK_SIZE,
            "service_status": service.status,
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

    Side effect: when the deck parses cleanly to the right size, fire
    a background prefetch of the mulligan arm. The eventual
    ``/recommend`` click usually hits the warm cache, so the user
    only waits for the keep arm (~1.5 s) instead of both arms in
    series (~5 s).

    Always returns 200 — the panel itself shows the result-and-issues
    split, and the ``<datalist>`` is populated even on partial success
    so the autocomplete keeps working for the resolved cards while
    the user fixes the broken lines.
    """
    store: CardStore = request.app.state.store
    service: RecommendationService = request.app.state.service
    result = parse_mtga_decklist(decklist, store)

    # Fire-and-forget the mulligan arm with the form's default context.
    # If the user changes context (on/draw, mulligan number, sim depth)
    # before clicking, the cache will miss and /recommend computes
    # fresh — correct but slower. Worst case is wasted background CPU.
    if result.is_valid and result.total_cards == _REQUIRED_DECK_SIZE and service.status.ready:
        deck_cards = [entry.card for entry in result.cards for _ in range(entry.count)]
        service.prefetch_mulligan(deck=deck_cards)

    return templates.TemplateResponse(
        request,
        "_validation.html",
        {
            "result": result,
            "datalist_names": unique_card_names(result),
            "required_deck_size": _REQUIRED_DECK_SIZE,
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
    """One endpoint for the four hand mutations.

    Splitting this into four routes would multiply boilerplate
    without making the contract any clearer — every action takes
    the same form state and returns the same partial. The client
    sends ``action`` via HTMX's ``hx-vals``.
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
    elif action == "clear":
        current_hand = []
    elif action:
        error = f"Unknown hand action {action!r}."

    return _render_hand(request, result, current_hand, error)


# ---------------------------------------------------------------------------
# Recommend
# ---------------------------------------------------------------------------


@app.post("/recommend", response_class=HTMLResponse)
def recommend_route(
    request: Request,
    decklist: Annotated[str, Form()] = "",
    hand_ids: Annotated[list[str] | None, Form()] = None,
    on_the_play: Annotated[bool, Form()] = True,
    mulligan_number: Annotated[int, Form()] = 0,
    # Empty string from the form's "unknown" <option> needs explicit
    # coercion — FastAPI's `int | None` Form converter raises a 422
    # on `""`. We accept it as a string and convert manually below.
    opp_mulligan_number: Annotated[str, Form()] = "",
    # Asymmetric sim budget: keep arm = one hand at high precision;
    # mulligan arm = many independent samples x few sims each
    # (between-hand variance dominates within-hand sampling noise).
    n_sims_keep: Annotated[int, Form()] = DEFAULT_N_SIMS_KEEP,
    n_sims_per_mulligan: Annotated[int, Form()] = DEFAULT_N_SIMS_PER_MULLIGAN,
    n_mulligan_samples: Annotated[int, Form()] = DEFAULT_N_MULLIGAN_SAMPLES,
) -> HTMLResponse:
    """Run keep vs. mulligan and render the recommendation.

    User-facing errors (model not loaded, deck wrong size, hand
    wrong size, hand id no longer matches deck) render inline in
    the recommendation pane rather than returning 4xx — a 4xx
    would just paint a broken HTMX swap.
    """
    store: CardStore = request.app.state.store
    service: RecommendationService = request.app.state.service
    parsed = parse_mtga_decklist(decklist, store)
    current_hand: list[str] = hand_ids or []

    # Input gates: model + deck + hand. We check the model first so a
    # user without a trained model gets the most actionable message.
    if not service.status.ready:
        return _render_recommendation_error(
            request,
            service.status.error or "Model is not loaded; cannot make a recommendation.",
        )
    if not parsed.is_valid:
        return _render_recommendation_error(
            request,
            f"Decklist has {len(parsed.unknown_lines)} issue(s); fix the validation panel above.",
        )
    if parsed.total_cards != _REQUIRED_DECK_SIZE:
        return _render_recommendation_error(
            request,
            f"Deck has {parsed.total_cards} cards; the trained model expects "
            f"exactly {_REQUIRED_DECK_SIZE}.",
        )
    if len(current_hand) != HAND_SIZE:
        return _render_recommendation_error(
            request,
            f"Hand has {len(current_hand)} card(s); needs exactly {HAND_SIZE}. "
            "Use Random hand or add cards manually.",
        )

    entries, library, hand_errors = resolve_hand(parsed, current_hand)
    if hand_errors:
        return _render_recommendation_error(request, " ".join(hand_errors))

    hand_cards = [e.card for e in entries if e.card is not None]
    deck_cards = hand_cards + library  # full 40-card deck for the model.

    # Form sends "" for the "unknown" option; coerce to None. Anything
    # numeric goes through int(). On the play we don't care about the
    # opponent's mulligan count (the feature is masked out anyway).
    opp_mull: int | None
    if on_the_play or not opp_mulligan_number.strip():
        opp_mull = None
    else:
        try:
            opp_mull = int(opp_mulligan_number)
        except ValueError:
            opp_mull = None

    try:
        recommendation = service.recommend_asymmetric(
            hand=hand_cards,
            deck=deck_cards,
            on_the_play=on_the_play,
            mulligan_number=mulligan_number,
            opp_mulligan_number=opp_mull,
            n_sims_keep=n_sims_keep,
            n_sims_per_mulligan=n_sims_per_mulligan,
            n_mulligan_samples=n_mulligan_samples,
        )
    except (ValueError, RuntimeError) as exc:
        return _render_recommendation_error(request, str(exc))
    except Exception as exc:  # surface unexpected failures inline rather than 500
        log.exception("recommendation failed")
        return _render_recommendation_error(
            request,
            f"Recommendation failed: {type(exc).__name__}: {exc}",
        )

    # The keep arm's predicted set is the deck's primary set; pass it
    # to the template so the user knows which format the model was
    # asked about (useful when the user's deck is, e.g., Sealed with
    # cards from two sets).
    primary_set = RecommendationService.primary_set_of(deck_cards)
    set_stats_present = primary_set in service.stats_by_set if primary_set else False
    return templates.TemplateResponse(
        request,
        "_recommendation.html",
        {
            "recommendation": recommendation,
            "error": None,
            "primary_set": primary_set,
            "set_stats_present": set_stats_present,
            "on_the_play": on_the_play,
            "mulligan_number": mulligan_number,
        },
    )


# ---------------------------------------------------------------------------
# Card image proxy
# ---------------------------------------------------------------------------


@app.get("/card-image/{name:path}")
async def card_image(request: Request, name: str) -> RedirectResponse:
    """Resolve a card name to its Scryfall image URL and 302.

    Using ``{name:path}`` so card names with slashes (DFC joint names
    like ``"Wear // Tear"``) survive the route match. The actual fetch
    is via ``ScryfallImages.url_for`` which caches per-process.
    """
    scryfall: ScryfallImages = request.app.state.scryfall
    url = await scryfall.url_for(name)
    # 307 (not 302) so HTTP semantics preserve the request method —
    # browsers treat the data: URL placeholder identically either way,
    # and 307 is the modern default.
    return RedirectResponse(url, status_code=307)


# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------


@app.get("/healthz")
def healthz(request: Request) -> dict[str, Any]:
    """Trivial liveness check; doubles as a "is everything loaded?" probe."""
    store: CardStore = request.app.state.store
    service: RecommendationService = request.app.state.service
    return {
        "ok": True,
        "n_cards": len(store.entries),
        "loaded_sets": store.loaded_sets,
        "model_loaded": service.status.model_loaded,
        "formats_with_stats": service.status.formats_with_stats,
        "formats_missing_stats": service.status.formats_missing_stats,
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
    """Resolve current hand ids and render the hand grid partial.

    Resolution may emit its own errors ("slot 3 has no matching copy
    left") — we surface the first one so the user sees a concrete
    reason rather than a silent broken state.
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


def _render_recommendation_error(request: Request, error: str) -> HTMLResponse:
    """Render the recommendation panel with an inline error message.

    Used for every pre-flight validation failure (model not loaded,
    deck/hand wrong size, etc.) so the user sees a coherent panel
    rather than an HTMX-swap blank space.
    """
    return templates.TemplateResponse(
        request,
        "_recommendation.html",
        {
            "recommendation": None,
            "error": error,
            "primary_set": None,
            "set_stats_present": False,
            "on_the_play": True,
            "mulligan_number": 0,
        },
    )


# Re-export so test code can import the HandEntry type cleanly.
__all__ = ["HandEntry", "app", "main"]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """``uv run mulligan-coach-website`` entry point.

    Binds 127.0.0.1 only. This is a local dev tool today; if it ever
    runs behind a public reverse proxy, audit the routes first
    (everything is read-only today but ``/card-image`` does an
    outbound fetch). For autoreload during template hacking use
    ``uv run uvicorn mulligan_coach_website.app:app --reload``
    directly.
    """
    uvicorn.run("mulligan_coach_website.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
