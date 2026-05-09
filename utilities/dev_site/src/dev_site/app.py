"""FastAPI umbrella that mounts every dev utility under one URL.

Each utility (`card_viewer`, `simulation_viewer`) keeps its own standalone
console-script entry (``uv run card-viewer`` etc.) for isolated debugging.
This umbrella simply imports their ``app`` objects and mounts them under
prefixes so all of them can be browsed at the same time:

* ``http://127.0.0.1:8000/``                   landing page (links to both)
* ``http://127.0.0.1:8000/card-viewer/``       full card-viewer
* ``http://127.0.0.1:8000/simulation-viewer/`` full simulation-viewer

Each mounted utility (and this umbrella) installs a per-app ``url_for``
Jinja global that resolves names against ``request.app`` rather than
the outermost router; see the ``_app_url_for`` block below for why.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any

import jinja2
import uvicorn
from card_viewer.app import app as card_viewer_app
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from simulation_viewer.app import app as simulation_viewer_app
from starlette.datastructures import URL, URLPath

# Resolve the templates / static dirs via importlib.resources so this works
# regardless of whether the package is installed editable (uv sync's default)
# or zipped into a wheel later. The hatchling build config force-includes
# both directories into the wheel.
_TEMPLATES_DIR = str(files("dev_site") / "templates")
_STATIC_DIR = str(files("dev_site") / "static")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Explicitly enter every mounted utility's lifespan.

    Starlette's automatic lifespan-forwarding for mounted ASGI apps has
    historically been fragile across versions; nesting the children's
    ``lifespan_context`` managers here makes the dependency order explicit
    and works regardless of the host Starlette version.
    """
    async with (
        card_viewer_app.router.lifespan_context(card_viewer_app),
        simulation_viewer_app.router.lifespan_context(simulation_viewer_app),
    ):
        yield


app = FastAPI(title="Mulligan Coach — dev utilities", lifespan=lifespan)
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


# Starlette's default Jinja ``url_for`` global calls ``request.url_for``,
# which resolves against ``scope["router"]``. With the unnamed sub-app
# mounts below, that lookup descends into whichever sub-app's mount
# appears first and returns the wrong route when names collide
# (commonly: ``index``, ``static``, ``healthz``). Override the global to
# always query THIS app's router, which keeps the umbrella's own brand /
# static links pointing at its own routes.
#
# Same shape as the override in card_viewer/simulation_viewer; the
# umbrella isn't mounted under anything so ``scope["root_path"]`` is
# always empty, but we keep the prepend logic identical so the helper
# works if dev_site itself is ever mounted somewhere.
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

# Mount prefixes for the dev utilities. Single source of truth — used both
# to register the mounts below and to render the landing page's tile links.
# Add a new entry here AND add an `app.mount(...)` line below when adding
# a new utility (see CLAUDE.md).
MOUNTS = {
    "card_viewer": {"prefix": "/card-viewer", "label": "Card viewer"},
    "simulation_viewer": {
        "prefix": "/simulation-viewer",
        "label": "Simulation viewer",
    },
}


# IMPORTANT: register the umbrella's OWN routes (`/`, `/healthz`, `/static`)
# BEFORE the unnamed sub-app mounts. The router walks routes in
# registration order during ``url_path_for`` lookups, and the unnamed
# mounts descend transparently into their sub-apps. If a mount were
# registered first, the umbrella's ``url_for("index")`` (resolving the
# brand link) would short-circuit on the first sub-app's `index` route
# and return e.g. ``/card-viewer/`` instead of ``/``. Same trap for
# ``static`` (the umbrella's static mount is named, so it's safe by
# itself, but keeping it grouped with the other umbrella-level routes
# documents the ordering requirement).
@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Landing page with one tile per mounted utility."""
    return templates.TemplateResponse(request, "index.html", {"mounts": MOUNTS})


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Trivial liveness check for the umbrella itself.

    Each mounted utility has its own /healthz under its prefix
    (``/card-viewer/healthz``, ``/simulation-viewer/healthz``).
    """
    return {"status": "ok"}


# Static assets for the umbrella's own landing page. Each mounted utility
# brings its own /static handler under its respective prefix
# (/card-viewer/static, /simulation-viewer/static).
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# Mount the utilities WITHOUT a `name=` argument. Starlette's named-mount
# logic (Mount.url_path_for) only descends into a sub-app's routes when
# the lookup name starts with "<mount_name>:" — which means a sub-app's
# `request.url_for("validate")` raises NoMatchFound when its parent mount
# is named. Unnamed mounts allow transparent name lookup AND still prefix
# the resulting URL with the mount path. Verified empirically on
# Starlette 1.0.0 / FastAPI 0.136.x.
app.mount(MOUNTS["card_viewer"]["prefix"], card_viewer_app)
app.mount(MOUNTS["simulation_viewer"]["prefix"], simulation_viewer_app)


def main() -> None:
    """Entry point for the ``dev-site`` console script.

    Binds 127.0.0.1 only (this is a local dev tool, never exposed to the
    network). For autoreload during template hacking use
    ``uv run uvicorn dev_site.app:app --reload`` directly.
    """
    uvicorn.run("dev_site.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
