# mulligan-coach-website

Local FastAPI + HTMX testing/validation app for the Mulligan Coach
recommendation pipeline. Paste an Arena decklist, build (or randomise)
an opening hand, pick game context (on play / draw, mulligan number),
and get the model's verdict plus the key diagnostic numbers behind
the call.

The pipeline it drives is the project's full stack:
`cards` → `features` → `simulation` → `model` (via the shared
`recommend` service). See [`CLAUDE.md`](CLAUDE.md) for layout and
design notes.

## Highlights

* **Production verdict = choice model.** *Keep or mulligan?* calls the
  shared `recommend` service's `recommend_choice`, which maps
  P(a skilled player would keep this hand) onto the same five-band
  verdict the overlay shows.
* **Playability diagnostics.** The result panel shows the simulator's
  mana-base / curve / per-card castability numbers, so you can see
  *why* a hand is rated the way it is.
* The legacy win-model path (asymmetric sim budget, mulligan-arm
  prefetch, deeper-mulligan floor) is retained in `packages/recommend`
  and documented in [`CLAUDE.md`](CLAUDE.md)'s legacy section; it is
  no longer what the site displays.

## Running

```
uv sync                           # picks up the workspace member
uv run mulligan-coach-website     # http://127.0.0.1:8000
uv run uvicorn mulligan_coach_website.app:app --reload   # autoreload
```

A trained choice-model directory is required to make recommendations.
By default the app looks for `models/choice_prod/` under the repo
root; override with `MULLIGAN_COACH_CHOICE_MODEL_DIR=/path/to/model_dir`.
(The legacy win model defaults to `models/all3_v2/`, overridable via
`MULLIGAN_COACH_MODEL_DIR`.)

Restrict which sets get loaded into the in-memory card store via
`MULLIGAN_COACH_WEBSITE_SETS=TLA,SOS` (omit to load every set with a
persisted `data/processed/parsed_cards/<SET>.json`).

## Tests

```
uv run pytest packages/website
```
