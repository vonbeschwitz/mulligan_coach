# model — Claude instructions

## Purpose

XGBoost mulligan-recommendation model. Consumes the upstream
feature row built by `packages/features` and the simulator's
per-row castability aggregates, predicts P(win | this hand,
context), and compares keep vs. mulligan to produce the
recommendation.

Five logical layers, landing as five sequential PRs:

```
src/mulligan_coach_model/
├── training_rows.py    # PR 1 (this PR): DuckDB games view -> TrainingRow tuples
├── feature_matrix.py   # PR 2: simulate() + build_feature_row -> slim parquet cache
├── baseline.py         # PR 3: saturated-cell logistic regression -> base_margin
├── train.py            # PR 4: XGBoost fit + isotonic calibration + serialization
└── inference.py        # PR 5: predict(...) -> P(win) + recommend(...) keep-vs-mull
```

## Why residualize on context

The 17Lands game data carries strong context signals (player skill
bucket, opponent mulligan count) that are predictive of game
outcome but mostly orthogonal to the mulligan decision itself. We
strip that variance out via XGBoost's ``base_margin`` mechanism: a
logistic-regression baseline predicts a per-row logit offset; the
gradient-boosted ensemble learns the *delta* on top of it. The
output remains a calibrated probability, and the keep-vs-mull
comparison is invariant to the per-player skill term (it cancels in
the comparison).

## PR 1 — `training_rows.py`

Pure data-prep. Reads the unified `games` view (set up by
`packages/data-download/.../seventeenlands/duckdb_views.py`) and
emits one :class:`TrainingRow` per game with:

* `hand` — a tuple of 7 `ParsedCard` instances reconstructed from
  the `opening_hand_<NAME>` columns. The 17Lands London-mulligan
  convention is that this is the *pre-bottom* draw; the actual
  cards the player bottomed are not recorded. Downstream feature
  building treats this as the hand and passes `mulligan_number` as
  a separate context feature.
* `deck` — a tuple of 40 `ParsedCard` instances reconstructed from
  the `deck_<NAME>` columns. Includes the hand cards.
* Context: `on_the_play`, `mulligan_number`, `opp_mulligan_number`,
  `expansion`, `event_type`, `draft_id`, `game_number`, `won`.
* Coarsened user-skill buckets: `user_wr_bucket` (5 bins +
  "unknown") and `user_n_games_bucket` (5 bins + "unknown"). Five
  bins is the size the saturated-cell baseline can support with
  ~1M training rows without going hollow.

### Card-name reconstruction

The 17Lands columns use card names directly. We match via a
per-set lookup built from
:func:`mulligan_coach_cards.load_parsed_cards(set_code)`, augmented
with:

* **DFC front-face fallback.** `ParsedCard.name` for a DFC is the
  joint `"Front // Back"` form; 17Lands columns use the front-face
  name only. `build_name_lookup` indexes both keys.
* **Synthesised basic lands.** Basics live in Scryfall's main bulk,
  not the per-set parsed-cards JSON, so we synthesise them on the
  fly with the same shape used by
  `packages/features/tests/_factories.py:basic`. The simulator only
  needs `RoleFeatures(is_land=True)` plus the single `ManaAbility`
  for these to work.

Names that resolve to neither cause the row to be skipped (logged
via :class:`TrainingRowStats.unknown_card_names`).

### Row-quality filters

A row is dropped when:

* The SQL filter (`expansion = ? AND event_type = ?`) eliminates
  it. Wrong set / event type is the dominant case.
* Required context columns are NULL (`won`, `draft_id`, etc.).
* `num_mulligans` is outside `[0, 6]` (data quirks: we observed 2
  rows with `num_mulligans=7` across ~1.1M games).
* `hand_size != 7` or `deck_size != 40` (data corruption).
* Any non-zero deck or opening_hand card name is absent from the
  lookup (truly unknown card — likely a 17Lands column from a
  different set that bled in via `union_by_name=True`).

Drop counts and per-name unknown-card frequencies are accumulated
on the caller's :class:`TrainingRowStats` instance for audit.

### Why a streaming iterator

The wide `games` view has ~1700 deck / opening_hand columns. A
plain SELECT into pandas materialises tens of GB of NULL-padded
cells. The iterator uses ``cursor.fetchmany(batch_size=1000)`` so
memory stays bounded; the trade-off is one Python-level scan of
the row's wide column tuple per game, which is fast enough for
~1M rows per format.

## PR 2 — `feature_matrix.py`

Per-row simulation + feature builder + parquet writer.

* `build_row(tr, format_stats, n_sims_per_row)` — runs
  `simulate(hand, library=deck-hand, on_play, n_runs, seed)` then
  `build_feature_row(...)`, then layers on the cache schema's
  extras (label, context columns, the conditional
  `opp_mulligan_count_if_known` feature). Returns a flat dict
  ready for parquet.
* `iter_feature_rows(training_rows, format_stats, n_sims_per_row,
  on_error)` — streams `build_row` over an iterable of
  TrainingRows. Per-row simulator / feature-builder errors are
  classified into `MaterializationStats.rows_failed_simulation`
  vs `rows_failed_feature_build` and logged; the iterator
  doesn't abort the shard. `on_error="raise"` is available for
  tests.
* `materialize_feature_matrix(set_code, duckdb_path, output_path,
  ...)` — opens the games view, builds the format stats once,
  streams rows in batches, and writes a single parquet shard
  atomically via `os.replace` from a `.tmp-<pid>` neighbour.
  Refuses to overwrite an existing shard unless `overwrite=True`.

### Slim cache schema

The plan calls for "features + label + context only — no raw
`AggregateStats` retained." Concretely:

* 200 columns from `build_feature_row`.
* `opp_mulligan_count_if_known` — `float | None`. NULL when the
  player was on the play; the opponent's mulligan count when on
  the draw. This is the *feature*; the baseline reads the always-
  populated `opp_mulligan_number` instead.
* Baseline / split context: `user_wr_bucket`,
  `user_n_games_bucket`, `opp_mulligan_number`,
  `mulligan_number`, `expansion`, `event_type`, `draft_id`,
  `game_number`, `won`.

`on_the_play` is intentionally not duplicated — it's already in
the feature row.

### Per-row seed

Reproducibility comes from a stable
`sha256(draft_id || \\x00 || game_number)[:4]` digest into the
simulator's seed. Same row -> bit-identical aggregate -> bit-
identical feature row. Mid-format simulator changes will break
this hash equivalence; that's intentional, because the cached
shard would be stale anyway and should be re-materialised.

## PR 3 — `baseline.py`

Saturated-cell logistic regression for residualising the
training label on player skill + opp_mulligan. Used as XGBoost's
``base_margin`` so the gradient-boosted predictor only learns
the *delta* on top of the per-row baseline.

* `BaselineModel.fit(parquet_paths, l2_C=10.0)` reads one or more
  feature parquet shards, builds the saturated-cell + opp_mull
  feature matrix via one-hot encoding (`pd.get_dummies`), and
  fits `sklearn.LogisticRegression(C=l2_C, solver="lbfgs",
  fit_intercept=True)`. The fitted intercept is folded into each
  cell margin so inference is a clean `cell + opp` sum.
* `BaselineModel.margin(user_wr_bucket, user_n_games_bucket,
  on_the_play, opp_mulligan_number)` is the inference entry point.
  Handles three info-set cases:
    * Training time: both user buckets and opp_mull known.
    * Deploy on the draw: user buckets None, opp_mull known.
    * Deploy on the play: both unknown -> falls back to the
      precomputed population marginals (per-on_play cell marginal
      + population-mean opp_mull margin).
  Mixed-state (one user bucket known, the other not) raises —
  that's not a case the codebase generates.
* `BaselineModel.save` / `.load` use a small JSON document
  (~50 cells x a few floats) so a human can eyeball the fitted
  coefficients.

### Why saturated cells

The plan calls for `β_cell[wr x n_games x on_play]` rather than
`β_wr + β_n_games + β_on_play`. The wr-by-n_games interaction is
real: low-n-games / high-wr players regress toward the mean *more*
than low-n-games / low-wr players, and in opposite directions.
An additive decomposition would force the n_games effect to be a
constant shift independent of WR. Saturated cells (one coefficient
per (wr, n_games, on_play) triple) plus L2 shrinkage is the
right answer for ~50–70 cells × ~1M training rows.

### Why XGBoost cancellation

The recommendation compares `P(win | keep)` vs `P(win | mull-to-N)`.
Both use the same `(user_wr, user_n_games, on_play, opp_mull)`
context, so the baseline margin cancels in the comparison. The
recommendation only reflects the XGBoost-learned delta — exactly
what we want for a hand-specific decision.

## What's deferred to later PRs

* **XGBoost + calibration.** PR 4.
* **Inference + recommendation.** PR 5.

## Tests

```
uv run pytest packages/model
```

Tests build an in-memory DuckDB ``games`` view from column-oriented
dicts (no parquet I/O, no parsed_cards-JSON dependency) and exercise
the iterator against hand-built rows. Bucketing helpers are
parametrised directly.

## Decisions locked in

* **Approach B with `base_margin` residualization** (logistic
  baseline on context buckets; XGBoost predicts the delta).
* **Saturated cell baseline** for `(user_wr_bucket x
  user_n_games_bucket x on_the_play)` rather than additive main
  effects — captures the interaction between win rate and number
  of games (low-N high-WR players should shrink more than low-N
  low-WR players; opposite directions of shrinkage can't be one
  constant additive coefficient).
* **`opp_mulligan_count_if_known` as a conditional feature** —
  missing on the play, value on the draw, using XGBoost's native
  missing-value handling. Conceptually two roles: appears in the
  baseline for residualization (both info sets) AND as a feature
  only on the draw.
* **Single unified multi-format model** — `set_code` is a feature
  in the row; all formats train together.
* **Slim feature parquet cache** — features + label + context only.
* **Grouped train/val/calibration/test split by `draft_id`** —
  prevents same-draft leakage in CV.
* **Isotonic calibration on a separate held-out split** — for
  displaying probabilities the user can trust.

## Known limitations

* The hand surfaced from 17Lands is the **pre-bottom 7-card draw**,
  not the post-bottom 7-N hand the player actually started the
  game with. The model treats this as the hand and `mulligan_number`
  as context — implicitly learning "with 2 mulligans, only 5 of
  these 7 were actually playable." This is the best we can do
  with the 17Lands data shape; 17Lands doesn't record which
  cards were bottomed.
* MTGJSON's arena_id lag affects all three current sets (TLA, ECL,
  TMT have ``arena_id=None`` on every ParsedCard). The downstream
  feature builder's `avg_*_wr_of_*` / Z-bucket-count features fall
  to 0 as a result. The model still trains (per-card features are
  a small fraction of the 200) but per-card signal is weak until
  MTGJSON catches up.
