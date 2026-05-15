# mulligan-coach-website

User-facing FastAPI + HTMX app for the Mulligan Coach recommendation
pipeline. Paste an Arena decklist, build (or randomise) an opening
hand, pick game context (on play / draw, mulligan number), and get a
Keep / Mulligan verdict with both win-probability arms and the key
diagnostic numbers behind the call.

This is the **shipped** website (in `packages/`, not `utilities/`).
The pipeline it drives is the project's full stack:
`cards` → `features` → `simulation` → `model`. See
[`CLAUDE.md`](CLAUDE.md) for layout and design notes.

## Highlights

* **Asymmetric sim budget.** The keep arm runs once at high
  precision (1000 sims); the mulligan arm averages over many
  independent fresh draws at lower per-hand precision (50 hands ×
  40 sims). Spending the mulligan budget on sample diversity rather
  than per-hand precision is much tighter — between-hand variance
  dominates within-hand sampling noise.
* **Mulligan-arm prefetch.** Pasting a deck fires a background
  compute of the mulligan arm with the form's default context, and
  the result is cached. The eventual *Keep or mulligan?* click
  usually only has to compute the keep arm (~1.5 s) instead of
  both arms in series.
* **Deeper-mulligan floor.** Each mulligan-arm sample's predicted
  P(win) is floored at the average prediction of the next-deeper
  mulligan level — modelling that a player wouldn't keep a hand
  worse than the average mull-to-(N-1) outcome. Almost free
  because the second-level prediction shares the same simulator
  output as the first.

## Running

```
uv sync                           # picks up the workspace member
uv run mulligan-coach-website     # http://127.0.0.1:8000
uv run uvicorn mulligan_coach_website.app:app --reload   # autoreload
```

A trained model directory is required to make recommendations. By
default the app looks for `models/tla_v2/` under the repo root;
override with `MULLIGAN_COACH_MODEL_DIR=/path/to/model_dir`.

Restrict which sets get loaded into the in-memory card store via
`MULLIGAN_COACH_WEBSITE_SETS=TLA,SOS` (omit to load every set with a
persisted `data/processed/parsed_cards/<SET>.json`).

## Tests

```
uv run pytest packages/website
```
