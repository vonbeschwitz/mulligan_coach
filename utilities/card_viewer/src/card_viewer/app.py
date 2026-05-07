"""FastAPI app for the card viewer.

Routes:

* ``GET /``                                — index page (sidebar + empty results pane)
* ``GET /search``                          — HTMX partial that renders the filtered grid
* ``GET /card/{set_code}/{collector_no}``  — detail page (image + JSON encoding)
* ``GET /healthz``                         — trivial liveness check

The card store is built once at startup inside the ``lifespan`` context
manager and stashed on ``app.state.store``. Parsing the ~740 cards across
TMT/ECL/TLA takes well under a second; we don't try to be clever about
caching to disk.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated

import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .data import CardStore, image_url
from .filters import (
    MODE_KINDS,
    PRIMARY_TYPES,
    ROLE_FLAG_KEYS,
    Filter,
    active_mode_kinds,
    active_role_flags,
    apply_filter,
    display_role_flag,
)

# Default sets for the v1 viewer. Override at the shell with
# ``CARD_VIEWER_SETS=TLA``. The current Premier-Draft sets are TMT/ECL/TLA
# per the project memory; this default tracks the shipped formats.
DEFAULT_SETS = "TMT,ECL,TLA"


def _sets_from_env() -> list[str]:
    raw = os.environ.get("CARD_VIEWER_SETS", DEFAULT_SETS)
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


# Resolve the templates / static dirs via importlib.resources so this works
# regardless of whether the package is installed editable (uv sync's default)
# or zipped into a wheel later. The hatchling build config force-includes
# both directories into the wheel.
_TEMPLATES_DIR = str(files("card_viewer") / "templates")
_STATIC_DIR = str(files("card_viewer") / "static")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Build the card store at startup.

    FastAPI calls this once before the first request and once at shutdown.
    Building the store is synchronous and CPU-bound (regex parsing); the
    server has nothing else to do during boot, so a plain blocking call
    is fine.
    """
    app.state.store = CardStore.build(_sets_from_env())
    yield


app = FastAPI(title="Mulligan Coach — card encoding viewer", lifespan=lifespan)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Render the index page (sidebar form + empty #results pane).

    The form has ``hx-trigger="... load"`` so HTMX immediately fetches
    ``/search`` with no filters and populates the grid. That gives us a
    "show everything" landing view without duplicating render code.
    """
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "role_flag_keys": ROLE_FLAG_KEYS,
            "primary_types": PRIMARY_TYPES,
            "mode_kinds": MODE_KINDS,
            "loaded_sets": _sets_from_env(),
            "n_cards": len(request.app.state.store.entries),
        },
    )


@app.get("/search", response_class=HTMLResponse)
def search(request: Request, f: Annotated[Filter, Query()]) -> HTMLResponse:
    """HTMX partial: filtered card grid.

    FastAPI's ``Annotated[Filter, Query()]`` syntax (introduced in 0.115)
    builds the ``Filter`` from the query string. List-valued fields
    (``sets``, ``colors``, ``role_flags``, ``mode_kinds``) come through
    as repeated query params
    (``?role_flags=is_equipment&role_flags=is_vehicle``), which is
    exactly what HTMX naturally sends from a checkbox group.
    """
    entries, total = apply_filter(request.app.state.store, f)
    return templates.TemplateResponse(
        request,
        "_results.html",
        {
            "entries": entries,
            "total": total,
            "shown": len(entries),
            # Template helpers. Passed as callables so Jinja2 can invoke
            # them inline without us having to pre-compute everything.
            "image_url": image_url,
            "active_role_flags": active_role_flags,
            "active_mode_kinds": active_mode_kinds,
            "display_role_flag": display_role_flag,
        },
    )


@app.get("/card/{set_code}/{collector_number}", response_class=HTMLResponse)
def card_detail(request: Request, set_code: str, collector_number: str) -> HTMLResponse:
    """Detail page for one card.

    404 if the requested card isn't in the loaded sets — this happens when
    the URL refers to a set that isn't in ``CARD_VIEWER_SETS``.
    """
    entry = request.app.state.store.get(set_code, collector_number)
    if entry is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Card {set_code.upper()}/{collector_number} not in loaded sets. "
                "Set CARD_VIEWER_SETS to include this set, or check the URL."
            ),
        )
    return templates.TemplateResponse(
        request,
        "card_detail.html",
        {
            "e": entry,
            "image_url": image_url,
            "active_role_flags": active_role_flags,
            "active_mode_kinds": active_mode_kinds,
            "display_role_flag": display_role_flag,
        },
    )


@app.get("/healthz")
def healthz(request: Request) -> dict[str, object]:
    """Trivial liveness check; also handy for confirming the store loaded."""
    return {"ok": True, "n_cards": len(request.app.state.store.entries)}


def main() -> None:
    """Entry point for the ``card-viewer`` console script.

    Binds 127.0.0.1 only (this is a local dev tool, never exposed to the
    network). For autoreload during template hacking use
    ``uv run uvicorn card_viewer.app:app --reload`` directly.
    """
    uvicorn.run("card_viewer.app:app", host="127.0.0.1", port=8000, reload=False)
