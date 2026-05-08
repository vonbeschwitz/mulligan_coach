# card-viewer — Claude instructions

## Purpose

A local FastAPI + HTMX web utility for visually verifying the encodings
produced by `packages/cards/`. Shows the actual Scryfall card image side
by side with the structured `ParsedCard` JSON, with name search and
structured filtering on encoding fields.

This is a **developer tool**, not part of the shipped Mulligan Coach
recommendation pipeline. It's a workspace member purely so it shares the
project's `.venv` and can `from mulligan_coach_cards import …` directly.

## Layout

```
src/card_viewer/
├── __init__.py
├── app.py                  # FastAPI app, lifespan, routes, main() entrypoint
├── data.py                 # CardStore: parse-once cache + image_url helper
├── filters.py              # Filter Pydantic model, apply_filter, chip helpers
├── templates/
│   ├── base.html
│   ├── index.html          # Sidebar form + #results pane
│   ├── _results.html       # HTMX partial: filtered card grid
│   └── card_detail.html    # Image + chips + reasons + JSON
└── static/style.css        # ~30 lines, no framework
tests/test_app.py           # TestClient smoke tests; auto-skip if data missing
```

## How it works

1. On startup, the FastAPI `lifespan` builds a `CardStore` by reading
   the Scryfall snapshot (`load_all_cards()`, `filter_cards(set_code=...)`)
   and pairing each card with the best available `ParsedCard`:
   * If `data/processed/parsed_cards/<SET>.json` (written by
     `mulligan-coach-cards run-detector`) has the card by `oracle_id`,
     that entry is used verbatim. Critical for `llm_encoded` and
     `needs_human` cards — re-parsing would lose the hand-encoded
     fields.
   * Otherwise the deterministic parser (`parse_card`) runs on the raw
     dict. This keeps the viewer useful on a clean checkout where
     `run-detector` hasn't been invoked yet.
   Each `CardEntry` pairs the `ParsedCard` with its source Scryfall dict
   so templates can read `image_uris` for hotlinking the CDN URL.
2. `GET /` renders the index page; the form has
   `hx-trigger="... load"` so HTMX immediately fetches `/search` with
   no filters and populates the grid.
3. `GET /search` runs `apply_filter(store, Filter)` and returns the
   `_results.html` partial. List-valued filters (sets, colors,
   role_flags, mode_kinds) come through as repeated query params.
4. `GET /card/{set}/{n}` renders the detail page; 404 if not loaded.
5. Tile chips: parser status, set, types, colors, plus one chip per
   active role flag (`active_role_flags`), one per distinct mode kind
   (`active_mode_kinds`), and a `mana ability` chip when applicable —
   driven by helpers in `filters.py` so the predicate map stays the
   single source of truth.

## Running

```
uv sync                          # picks up the workspace member
uv run card-viewer               # http://127.0.0.1:8000
uv run uvicorn card_viewer.app:app --reload   # autoreload during template hacking
```

Override sets via `CARD_VIEWER_SETS=TLA` in the environment before
launching.

## Tests

```
uv run pytest utilities/card_viewer
```

Smoke tests use FastAPI's `TestClient`. The fixture skips the whole
module if `data/raw/scryfall/oracle_cards.*.json` isn't present, so
fresh checkouts without downloaded data don't fail CI.

## Extending

* **New filter**: add a field to `Filter`, an entry to `ROLE_FLAG_KEYS`
  + `ROLE_FLAG_PREDICATES` if it's a role flag, the corresponding
  branch in `apply_filter`, and a checkbox/group in `index.html`. The
  helper functions (`active_role_flags`, `display_role_flag`) pick up
  new entries automatically.
* **New chip**: add a `chip-<name>` rule to `static/style.css` and the
  chip span in `_results.html` and `card_detail.html`. Keep both
  templates in sync — there's no shared partial today.
* **Symbol images for mana costs**: deferred. Raw `{1}{G}` is fine for
  v1; if you add symbol rendering, do it in a Jinja2 filter so both
  templates pick it up.

## Dependencies

`fastapi`, `uvicorn[standard]`, `jinja2`, `pydantic`, plus the
workspace `mulligan-coach-cards`. No JavaScript framework — HTMX from
the unpkg CDN.

## Out of scope

* Not exposed beyond `127.0.0.1`. This is a local dev tool; never bind
  to `0.0.0.0` or run it behind a public reverse proxy without adding
  auth.
* No write endpoints. Everything is GET.
* No card-image proxy. Browsers hotlink Scryfall's CDN directly; if
  Scryfall ever blocks hotlinking we'll need to add a small image
  proxy route.
