# simulation-viewer — Claude instructions

## Purpose

A local FastAPI + HTMX web utility for inspecting **one** Monte Carlo
simulation trace turn-by-turn. Sibling to `utilities/card_viewer`:
that one validates the cards-package step (encoding cards from
Scryfall data), this one validates the simulation-package step
(running a goldfish from a hand + library).

This is a **developer tool**, not part of the shipped Mulligan Coach
recommendation pipeline. It's a workspace member purely so it shares
the project's `.venv` and can `from mulligan_coach_simulation import …`
directly.

## Layout

```
src/simulation_viewer/
├── __init__.py
├── app.py                 # FastAPI routes, lifespan, main() entry point
├── data.py                # CardStore: load parsed_cards from disk + synthetic basics
├── decklist.py            # MTGA decklist parser → ParseResult + ParseIssue
├── hand.py                # Hand-state helpers (random / add / remove / resolve)
├── templates/
│   ├── base.html
│   ├── index.html         # Main page hosting the form
│   ├── _validation.html   # HTMX partial: status panel + autocomplete <datalist>
│   ├── _hand.html         # HTMX partial: hand chips + add/random/remove
│   └── _trace.html        # HTMX partial: rendered GameTrace
└── static/style.css
tests/test_app.py          # TestClient smoke tests
```

## How it works

The form is a single `<form id="decksim">` posted in pieces via HTMX:

* `decklist` textarea triggers `POST /validate` on input change
  (debounced 300 ms). Response: `_validation.html`, swapped into
  `#validation`. The `<datalist id="deck-cards">` for the manual-hand
  autocomplete lives inside that partial, so re-pasting refreshes it.
* `POST /hand` is one endpoint with an `action` field
  (`random` / `add` / `remove`). Each button sets the appropriate
  `hx-vals` and includes the whole form via `hx-include="#decksim"`.
  Response: `_hand.html`, swapped into `#hand`.
* `POST /simulate` validates the hand size, calls `iter_traces(...,
  n_runs=1, verbose=True)`, takes the single yielded `GameTrace`, and
  renders it via `_trace.html` into `#trace` (outside the form, so the
  swap doesn't blow away form state).

User-facing errors (deck has bad lines, hand wrong size, hand id no
longer matches deck, simulator raised) render inline in the affected
pane rather than returning 4xx — the alternative would just paint a
broken HTMX swap.

### Hand state

There is **no server-side session.** Every HTMX request includes the
entire form, which contains zero or more `<input type="hidden"
name="hand_ids">` slots. Each id is `"<SET>:<COLLECTOR>"` (or
`"BASIC:<basic name>"` for synthesised basics). Two copies of the same
card are two separate `hand_ids` inputs with the same value — order
in the form is the rendered order.

`hand.resolve_hand` plucks ParsedCard copies out of the expanded deck
keyed by id, so the library passed to `iter_traces` is exactly the
deck minus the hand. Out-of-deck ids surface as a chip flagged
`missing` plus an inline error.

### Basic lands

`data/processed/parsed_cards/<SET>.json` does **not** include basic
lands (the parser doesn't emit them). MTGA decklists reference ~17 of
them per Limited deck, so `data.synthetic_basics()` builds five
hand-encoded `ParsedCard`s using the same construction the simulation
tests use (`packages/simulation/tests/_factories.py:_basic`). They're
indexed in `by_name` only — `(set, collector)` lookups fall through
to the name index.

## Running

```
uv sync                                              # picks up the workspace member
uv run simulation-viewer                             # http://127.0.0.1:8000
uv run uvicorn simulation_viewer.app:app --reload    # autoreload during template hacking
```

Override loaded sets via `SIMULATION_VIEWER_SETS=TLA` (etc.) before
launching.

## Tests

```
uv run pytest utilities/simulation_viewer
```

Tests use a "40 Mountain" smoke deck because the synthetic basics are
always available — tests run on a clean checkout where no sets have
been parsed yet.

## Limitations

* **No fetch-target events.** The simulator's `resolve_fetch` (in
  `packages/simulation/src/mulligan_coach_simulation/effects.py`)
  picks up a basic / specific land and puts it in the requested zone,
  but doesn't currently emit a separate `ActionEvent` for the choice.
  The viewer shows whatever's in `trace.actions`. If we want explicit
  fetch-decision visibility later, add a `FetchEvent` to the
  discriminated union in `packages/simulation/.../trace.py`.
* **No mulligan UI.** v1 only supports 7-card opening hands. London
  mulligan-to-N would need a hand-size selector and would feed the
  simulator the post-mulligan hand.
* **No batch mode.** This utility runs one trace per click. Use
  `mulligan_coach_simulation.simulate(...)` directly (or the
  upcoming `model` package) for aggregate stats.

## Out of scope

* Not exposed beyond `127.0.0.1`. Local dev tool only; never bind to
  `0.0.0.0`.
* No write endpoints. Everything is a `POST` for HTMX, but nothing
  mutates the persistent store.
* No card images. The card_viewer pulls Scryfall hot-link URLs; this
  utility deliberately doesn't (`load_all_cards` of the 170 MiB
  oracle dump is dead weight here — `load_parsed_cards` is enough).
