# recommend — Claude instructions

## Purpose

Shared keep/mulligan recommendation service used by the website
(`packages/website`) and the overlay (`packages/overlay`). Lifted
out of `packages/website` so neither downstream package has to
take a FastAPI dep just to call the recommender.

Pure Python — no Qt, no FastAPI, no Jinja2. Composes the four
upstream packages (cards, features, simulation, model) into the
recommendation entry points.

## Production vs legacy entry point

**`RecommendationService.recommend_choice` is the production verdict
path.** Both the website (`app.py`) and the overlay (`coordinator.py`)
call it. It runs the choice model (`P(a skilled player keeps this
hand)`) over the simulator features and returns a `ChoiceRecommendation`.

`recommend_asymmetric` (→ `AsymmetricRecommendation`) is the **legacy
win-model path**: it runs the win model twice (keep vs. simulated
mulligan-to-N-1) with the asymmetric sim budget, +4 pp mulligan bias,
prefetch cache, and deeper-mulligan floor. It's kept because the win
model is still useful as an ensemble / sanity signal and several
analysis scripts drive it, but it is **not** what any shipped surface
displays. When you read "asymmetric / cache / bias / floor" below,
treat it as legacy machinery, not the current verdict.

## Layout

```
src/mulligan_coach_recommend/
├── __init__.py        # Re-exports public surface
└── service.py         # Everything: FormatStats, RecommendationService,
                       # cache, deeper-mulligan floor, _predict_levels_for_hand,
                       # load_service
```

Kept as a single ~900-line `service.py` rather than splitting into
sub-modules. The logical pieces (format stats, the production
`recommend_choice` path, the legacy asymmetric recommend + cache,
mulligan-arm floor, model loading) live next to each other and
reading them in order tells one coherent story. If the file grows
past ~1500 lines or a piece develops its own tests, split then.

## Design rationale

See `packages/website/CLAUDE.md` § "Recommendation pipeline" — the
full asymmetric / cache / bias / floor reasoning lives there, since
the website is where that (now-legacy) design was first proven. This
package owns the implementation; the website's CLAUDE.md owns the why.
Note the production surfaces now call `recommend_choice`; the
asymmetric write-up documents the legacy win-model path.

## Tests

`tests/test_recommend_reload.py` covers the `reload_*` swap + status
logic (the seam the auto-update manifest fetcher calls after writing a
fresh parquet / model bundle), using a stub bundle so no real parquet
or trained model is needed. Additional coverage comes from two more
places:

* `packages/website/tests/test_app.py` exercises the service via
  the FastAPI route layer.
* `packages/model/tests/test_inference.py` exercises the pieces of
  the inference path that the service composes.

More service-in-isolation tests are planned (verdict-threshold
boundaries, seed determinism, `opp_mulligan` NaN convention, deck-size
bounds — roadmap Step 4) and should land here as `test_service.py`.

## Privately re-exported helpers

`_deck_signature`, `_MulliganCacheKey`, and `_stable_seed` are
underscore-prefixed (internal) but re-exported from `__init__.py`
because two analysis scripts under `packages/model/scripts/`
(`replay_mulligan_benchmark.py`, `website_mulligan_benchmark.py`)
need to build the same cache key the service uses internally, so
they can stress the cache or simulate the asymmetric path
off-line. Treat the names as semi-public: don't rename without
also updating those scripts.
