# dev-site — Claude instructions

## Purpose

A FastAPI umbrella that mounts every other dev utility under prefix
URLs so they can run on a single port. There is intentionally no
business logic here — the umbrella's only jobs are:

1. Render a landing page with one tile per mounted utility.
2. Mount each utility under a stable prefix.
3. Serve as the single `uv run dev-site` entry point that replaces
   running each utility on its own port.

This is a **developer tool**, not part of the shipped Mulligan Coach
recommendation pipeline. It's a workspace member purely so it shares
the project's `.venv` and can import the other utilities' FastAPI
apps directly.

## Layout

```
src/dev_site/
├── __init__.py
├── app.py             # FastAPI umbrella, mounts, lifespan, main()
├── templates/
│   ├── base.html
│   └── index.html     # Landing tiles
└── static/style.css
tests/test_app.py      # TestClient smoke tests
```

## How it works

`app.py` imports each utility's `app` object directly:

```python
from card_viewer.app import app as card_viewer_app
from simulation_viewer.app import app as simulation_viewer_app
```

It mounts them under unnamed prefixes:

```python
app.mount("/card-viewer", card_viewer_app)
app.mount("/simulation-viewer", simulation_viewer_app)
```

**Don't add `name=` to those mounts.** Starlette's `Mount.url_path_for`
only descends into a sub-app's routes when the lookup name starts with
`"<mount_name>:"`, which means the sub-apps' templates calling
`request.url_for("validate")` raise `NoMatchFound` when their parent
mount is named. Unnamed mounts allow transparent name lookup AND still
prefix the resulting URL with the mount path. Verified empirically on
Starlette 1.0.0 / FastAPI 0.136.x.

The landing page resolves mount URLs from the `MOUNTS` dict at the top
of `app.py` (single source of truth: also used for the actual mount
calls), passed to the template as context.

The umbrella's `lifespan` explicitly enters each mounted utility's
`lifespan_context`. Starlette's automatic lifespan-forwarding for
mounted apps has historically been fragile across versions; nesting
the children here makes the dependency order explicit and works
regardless of the host Starlette version.

## Adding a new mounted utility

1. Create the new utility under `utilities/<name>/` (mirror the
   `card_viewer` / `simulation_viewer` layout).
2. Make sure its templates use `request.url_for(...)` for every
   internal link — hardcoded `/path` references break under a mount
   prefix. Each route function name becomes its `url_for` key
   automatically.
3. In `dev_site/app.py`:
   * Add the import: `from <name>.app import app as <name>_app`.
   * Wrap the new app's lifespan inside the existing `lifespan`
     context manager (one extra `async with`).
   * Add a `MOUNTS` entry with the prefix and a human label.
   * Add `app.mount(MOUNTS["<name>"]["prefix"], <name>_app)` —
     **no `name=` argument**, see above for why.
4. Add a tile to `templates/index.html` reading from
   `mounts.<name>.prefix` / `mounts.<name>.label`.
5. Add a smoke test to `tests/test_mounts.py` that hits the new
   utility's healthz under its prefix.

## Running

```
uv sync                                           # picks up dev-site
uv run dev-site                                   # http://127.0.0.1:8000
uv run uvicorn dev_site.app:app --reload          # autoreload
```

## Tests

```
uv run pytest utilities/dev_site
```

The smoke tests rely on `TestClient` and the Scryfall snapshot fixture
inherited from card-viewer (auto-skip on a fresh checkout). They don't
re-test what the mounted utilities already test — only the wiring
(landing page, mount prefixes, lifespan composition).

## Out of scope

* Not exposed beyond `127.0.0.1`. This is a local dev tool; never bind
  to `0.0.0.0` or run it behind a public reverse proxy without adding
  auth.
* No write endpoints. The umbrella itself has only `GET /` and
  `GET /healthz`; mounted utilities define their own routes.
* No shared chrome (cross-utility nav bar, theme switcher). Each
  mounted utility renders its own pages exactly as it did standalone.
