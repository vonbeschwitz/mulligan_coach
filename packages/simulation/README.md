# simulation

Monte Carlo goldfish simulator for the first four turns of an MTG
Limited game. Given a hand and library of `ParsedCard` instances (from
the `cards` package), it simulates many shuffles, follows a fixed
land-drop and main-phase policy, and reports — for each card in the
deck — the probability of being castable by turn N.

This package is the second stage of the recommendation pipeline:

```
data-download  →  cards  →  simulation  →  model
```

The full design lives in `CLAUDE.md` next to this file.

## Tests

```
uv run pytest packages/simulation
```
