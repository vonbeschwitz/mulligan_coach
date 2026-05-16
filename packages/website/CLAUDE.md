# website — Claude instructions

## Purpose

User-facing FastAPI + HTMX app that ties the four upstream packages
(`cards`, `features`, `simulation`, `model`) into a single page:
paste a deck, build a hand, get a Keep / Mulligan recommendation.

This is the **shipped** website — distinct from the `utilities/`
entries (`card-viewer`, `simulation-viewer`, `dev-site`) which are
internal dev tools. The website's job is to be the primary
validation surface for the recommendation pipeline before the
overlay lands; once the overlay exists, the website remains as a
testing / verification UI.

## Layout

```
src/mulligan_coach_website/
├── __init__.py             # Re-exports app + main
├── app.py                  # FastAPI routes, lifespan, main() entry point
├── data.py                 # CardStore: load parsed_cards + synth basics
├── decklist.py             # MTGA decklist parser -> ParseResult + ParseIssue
├── hand.py                 # Hand-state helpers (random / add / remove / resolve)
├── recommendation.py       # ModelBundle + asymmetric recommend + mulligan
│                           # prefetch cache + deeper-mulligan floor heuristic
├── scryfall.py             # Async Scryfall image-URL cache for hand thumbnails
├── templates/
│   ├── base.html
│   ├── index.html          # Single-page form + the inline UX helpers
│   ├── _validation.html    # Deck status + datalist for autocomplete
│   ├── _hand.html          # 7-slot hand grid with card images
│   └── _recommendation.html  # Keep/Mulligan verdict + win-prob arms + floor
└── static/style.css        # Dark theme adapted from the prior FIN site
tests/
├── test_app.py             # TestClient smoke tests for each route
├── test_decklist.py        # Pure-function parser coverage
├── test_hand.py            # Pure-function hand-state helpers
└── _factories.py           # Hand-built ParsedCard fixtures
```

The skeleton mirrors `utilities/simulation_viewer` (FastAPI + HTMX
+ ParsedCard store + decklist parser) but the website is in
`packages/`, not `utilities/` — it's part of the shipped product.

## How it works

### The form

The page is a single `<form id="builder">` posted in pieces via
HTMX, identical pattern to `simulation_viewer`:

* `decklist` textarea triggers `POST /validate` on input change
  (debounced 300 ms). Response: `_validation.html`, swapped into
  `#validation`. The `<datalist id="deck-cards">` populated by the
  validation partial drives the hand-builder autocomplete.
* `POST /hand` is one endpoint with an `action` field
  (`random` / `add` / `remove` / `clear`). Each button sets
  `hx-vals` and includes the whole form via `hx-include="#builder"`.
  Response: `_hand.html`, swapped into `#hand`.
* `POST /recommend` validates the hand size and required context,
  calls `recommend_asymmetric(...)`, and renders the result via
  `_recommendation.html` into `#recommendation` (outside the form
  so the swap doesn't blow away form state).
* `GET /card-image/{name:path}` returns a 307 to Scryfall's
  hot-link URL for that card's normal-size image. The Scryfall TOS
  permits hot-linking so we don't have to host images ourselves.

### Hand state

No server-side session — every HTMX request POSTs the whole form
including a `hand_ids` list of `"<SET>:<COLLECTOR>"` strings (or
`"BASIC:<name>"` for synthesised basic lands). `hand.resolve_hand`
plucks ParsedCard copies out of the expanded deck keyed by id. This
keeps the server stateless and identical request/response across
worker processes (relevant if we ever scale beyond one process).

### Inline JS helpers (in `templates/index.html`)

A small `<script>` at the bottom of the index template adds two
micro-features that don't fit naturally in HTMX alone:

1. **Auto-add on autocomplete pick.** Watches the *Add card* input;
   when its current value matches one of the deck-cards datalist
   options exactly, programmatically clicks the *Add* button. This
   catches both "clicked a suggestion" and "typed the full name".
   The Add button has `hx-on::after-request="…input.value=''"` so
   the input clears after the request, ready for the next pick.
2. **Clear stale recommendation on click.** The *Keep or mulligan?*
   button carries `hx-on::before-request="clearRecommendation()"`,
   which swaps the recommendation pane to a "Computing…" message
   the instant the user clicks. The old verdict is gone immediately;
   the new one swaps in when it's ready.

### Basic lands

Same trick as `simulation_viewer`: the persistent
`data/processed/parsed_cards/<SET>.json` doesn't include basics,
so we synthesise the five English basics at startup. MTGA decklists
routinely include ~17 basics; without this every opening hand with
a Plains would fail to resolve.

## Recommendation pipeline

The pipeline in `recommendation.py` is the heart of the package.
Four pieces compose:

### 0. Arena BO1 hand smoother

Every place the website draws a fresh opening hand —
`hand.random_hand` for the "Random hand" button, and
`_compute_mulligan_arm` for each mulligan-arm sample — uses
`mulligan_coach_simulation.draw_smoothed_hand` instead of uniform
`rng.sample`. The 17Lands training data was drawn through Arena's
BO1 smoother (softmax over three shuffles, weighted toward
deck-matching land ratios), so uniform sampling at inference time
would feed the model out-of-distribution land-flooded and
land-screwed hands and bias the mulligan arm downward. Skip the
smoother and the should-mull rate roughly halves — see
`models/all3_v1/website_mulligan_benchmark.log` for the empirical
gap.

### 1. Asymmetric sim budget

The keep arm evaluates **one specific hand**; per-hand `n_sims` is
the only precision dial.
The mulligan arm averages predicted P(win) over `n_mulligan_samples`
independent freshly-drawn hands; *between-hand* variance dominates
the *within-hand* sampling noise — so a fixed total sim budget is
better spent on many samples × few sims-per-sample than the reverse.

Defaults:

```
keep arm     : 1   hand  × 1000 sims = 1000 sims
mulligan arm : 50  hands × 40   sims = 2000 sims
```

(See `DEFAULT_N_SIMS_KEEP` / `DEFAULT_N_SIMS_PER_MULLIGAN` /
`DEFAULT_N_MULLIGAN_SAMPLES` in `recommendation.py`.)

### 2. Mulligan-arm prefetch cache

The mulligan arm only depends on `(deck, context, sim settings)` —
not the kept hand. So `POST /validate`, after a clean parse, fires
`service.prefetch_mulligan(deck=...)` to start computing the
mulligan arm with the form's default context in a background
thread. A process-wide LRU cache holds the `Future`; the eventual
`POST /recommend` click usually finds a ready result and the user
only waits for the keep arm (~1.5 s) instead of both arms in
series (~5 s).

Cache details:

* Key: `(deck_signature, on_play, mulligan_number_to,
  opp_mull, n_sims_per_mulligan, n_mulligan_samples)`. The deck
  signature is the sorted tuple of `set:collector_number` per
  copy — different printings of the same name don't collide.
* Value: a `Future[MulliganArmResult]`. `recommend_asymmetric` reads
  this and the result includes the floor diagnostics for the
  template.
* Seeding: every key gets a **deterministic 32-bit seed** via
  `sha256(repr(key))[:4]`. That makes a cached value reproducible
  across repeat requests with the same settings — without it, every
  call would sample fresh hands and the cache would silently lie
  about matching previous output.
* LRU: 32 entries. Plenty for a single-user dev session.

The executor is a 2-worker `ThreadPoolExecutor` — one slot for the
keep arm, one for the mulligan prefetch / arm. Threading suffices
because the simulator releases the GIL through numpy and XGBoost
predict is C-side; a process pool would parallelise more cleanly
but adds pickle / spawn overhead that isn't worth it at this scale.

### 3. Deeper-mulligan floor heuristic

A drawn mulligan-arm sample whose P(win) is well below the average
isn't an outcome a player would actually keep — they'd just
mulligan one more level. So:

1. For each drawn 7-card sample we predict P(win) at **two**
   mulligan levels: the target one (e.g. mull-to-6) and the next
   level deeper (mull-to-5).
2. The deeper level's average is the **floor**.
3. Each sample's target-level P(win) is clamped: if it scored
   below the floor, replace it with the floor value.
4. The mulligan arm reports `mean(clamped values)`.

The shared-simulate trick keeps this almost free: simulator output
and baseline margin don't depend on `mulligan_number` (it's a
context one-hot the model reads from the feature row), so the two
predictions share one `simulate(...)` call and only cost the extra
~5 ms of XGBoost predict per sample, not another ~50 ms of
simulator. See `_predict_levels_for_hand`.

The floor is skipped at the boundary `mulligan_number_to == 6` —
the model can't evaluate mull-to-(7-cards-from-1) cleanly, and
there's no deeper mulligan to fall back to.

`_recommendation.html` surfaces the floor diagnostics: the floor
value, how many samples were clamped, and what the unfloored mean
would have been if any clamping happened.

### Model loading

The trained ModelBundle and per-format shrunk WR / z-score dicts
are built once at app startup inside the FastAPI lifespan handler
and stashed on `app.state.service`. Loading is conditional: if
`MULLIGAN_COACH_MODEL_DIR` doesn't exist, the app boots in
"sim-only" mode — every `/recommend` call returns a clear error
message rather than crashing. This makes the website usable in
dev environments without a fully-trained model.

Format support: the current model is `all3_v1` — trained on
Premier Draft data across TLA + ECL + TMT (1.07M rows). All three
sets are now in-distribution; the `set_code_{TMT,ECL,TLA}` one-hot
fires for the corresponding deck and the per-set learned offsets
apply. SOS isn't trained yet — recommendations for an SOS deck
will still run, but the set one-hot will be all-zero (XGBoost
treats the missing one-hot as "no in-distribution set") so the
call falls back to the format-agnostic part of the model.

## Routes

| Method | Path                       | Returns                                      |
|--------|----------------------------|----------------------------------------------|
| GET    | `/`                        | Full index page (deck + hand + context).     |
| POST   | `/validate`                | Validation panel + `<datalist>` partial. Side effect: prefetches the mulligan arm. |
| POST   | `/hand`                    | Hand grid partial (after a mutation).        |
| POST   | `/recommend`               | Recommendation panel partial.                |
| GET    | `/card-image/{name:path}`  | 307 redirect to Scryfall normal-size PNG.    |
| GET    | `/healthz`                 | JSON: `{ok, n_cards, loaded_sets, model_loaded, formats_with_stats, formats_missing_stats}`. |

## Running

```
uv sync                                                # installs the workspace member
uv run mulligan-coach-website                          # http://127.0.0.1:8000
uv run uvicorn mulligan_coach_website.app:app --reload # autoreload during template hacking
```

Override loaded sets via `MULLIGAN_COACH_WEBSITE_SETS=TLA,SOS`.
Override the model directory via `MULLIGAN_COACH_MODEL_DIR=...`
(default: `models/all3_v1/` under the repo root).

## Tests

```
uv run pytest packages/website
```

TestClient smoke tests cover each route. The model bundle isn't
loaded in tests (would slow them down enormously); the
`/recommend` test exercises the "model not loaded" error path
instead. Deeper end-to-end model coverage lives in
`packages/model/tests/test_inference.py`.

## A note on importing model-package privates

`recommendation.py` imports two underscore-prefixed names from
the model package — `_library_from_deck` (from
`feature_matrix`) and `_predict_proba` (from `inference`). They're
not part of the model package's documented public surface but are
stable helpers we deliberately reuse to inline a *shared-simulate*
multi-level predict path (`_predict_levels_for_hand`). If those
names ever move or change shape, this package must follow the
model package — or we promote them to the public API and the
import line above becomes a clean re-export.

## Out of scope

* **No authentication.** Local dev tool today; the shipped overlay
  calls the model directly without going through the website. If
  the website is ever exposed to the network, add a deploy
  reverse-proxy with auth — don't bake it in here.
* **No deck building.** Decks come in as an MTGA copy-paste; we
  don't help the user choose cards. Deck building belongs in the
  Arena client.
* **No bottoming UI.** Mulligan-to-N still draws 7 cards (London);
  the model is trained on the 17Lands pre-bottom convention and we
  mirror that here. A future feature could suggest which card to
  bottom but requires a separate prediction call per candidate.
* **No persistence.** Hands and decks live in form state. There is
  no "save my deck" feature; refreshes blow away the page.
