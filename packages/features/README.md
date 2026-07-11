# features

Derived per-card and hand-level features for the Mulligan Coach
project. Consumes `ParsedCard` and `SeventeenLandsStats` from the
`cards` package; produces feature vectors for the XGBoost models.

The first inhabitant is `seventeenlands_shrinkage` — sample-size-aware
shrinkage of per-card OH/GD/GIH win rates toward a play-rate-conditional
prior. See `CLAUDE.md` for design notes.

## Quick start

```
uv sync
uv run pytest packages/features
uv run python packages/features/scripts/inspect_shrinkage.py TLA
```
