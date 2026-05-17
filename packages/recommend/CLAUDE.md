# recommend — Claude instructions

## Purpose

Shared keep/mulligan recommendation service used by the website
(`packages/website`) and the overlay (`packages/overlay`). Lifted
out of `packages/website` so neither downstream package has to
take a FastAPI dep just to call the recommender.

Pure Python — no Qt, no FastAPI, no Jinja2. Composes the four
upstream packages (cards, features, simulation, model) into one
entry point.

## Layout

```
src/mulligan_coach_recommend/
├── __init__.py        # Re-exports public surface
└── service.py         # Everything: FormatStats, RecommendationService,
                       # cache, deeper-mulligan floor, _predict_levels_for_hand,
                       # load_service
```

Kept as a single ~900-line `service.py` rather than splitting into
sub-modules. The four logical pieces (format stats, asymmetric
recommend + cache, mulligan-arm floor, model loading) live next to
each other and reading them in order tells one coherent story. If
the file grows past ~1500 lines or a piece develops its own tests,
split then.

## Design rationale

See `packages/website/CLAUDE.md` § "Recommendation pipeline" — the
full asymmetric / cache / bias / floor reasoning lives there, since
the website is where the design was first proven. This package owns
the implementation; the website's CLAUDE.md owns the why.

## Tests

This package has no test suite of its own. Coverage comes from two
places:

* `packages/website/tests/test_app.py` exercises the service via
  the FastAPI route layer.
* `packages/model/tests/test_inference.py` exercises the pieces of
  the inference path that the service composes.

Tests against the service in isolation can land here as new
behaviour is added (e.g. when the recommend layer grows logic
that doesn't show up cleanly through the website's route tests).

## Privately re-exported helpers

`_deck_signature`, `_MulliganCacheKey`, and `_stable_seed` are
underscore-prefixed (internal) but re-exported from `__init__.py`
because two analysis scripts under `packages/model/scripts/`
(`replay_mulligan_benchmark.py`, `website_mulligan_benchmark.py`)
need to build the same cache key the service uses internally, so
they can stress the cache or simulate the asymmetric path
off-line. Treat the names as semi-public: don't rename without
also updating those scripts.
