# model — Claude instructions

## Purpose

XGBoost mulligan-recommendation model. Two parallel models share most of
the upstream pipeline:

* **Win model** — predicts P(win | this hand, context) from 17Lands
  game-data rows; labels are game outcomes.
* **Choice model** — predicts P(skilled player would keep | this hand,
  context) from 17Lands replay-data mulligan decisions, filtered to
  competent players; labels are keep/mull choices.

Both flow through `packages/features` and the simulator's per-row
castability aggregates. The choice pipeline reuses kept-hand
simulations from the win pipeline's cache so only mulled-away hands
need fresh sims.

Win-model layers (PRs 1-5):

```
src/mulligan_coach_model/
├── training_rows.py    # PR 1: DuckDB games view -> TrainingRow tuples
├── feature_matrix.py   # PR 2: simulate() + build_feature_row -> slim parquet cache
├── baseline.py         # PR 3: saturated-cell logistic regression -> base_margin
├── train.py            # PR 4: XGBoost fit + serialization (no post-hoc calibrator)
└── inference.py        # PR 5: predict(...) -> P(win) + recommend(...) keep-vs-mull
```

Choice-model layers (added later, mirror the win-model structure):

```
src/mulligan_coach_model/
├── choice_rows.py            # mulligan_decisions parquet -> ChoiceRow tuples (with player filter)
├── choice_feature_matrix.py  # cache-aware materialiser (reuses kept-hand sims)
├── choice_train.py           # XGBoost on was_kept label (no baseline)
└── choice_inference.py       # predict_keep_probability(...) -> P(keep)
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
* `materialize_feature_matrix(set_code, duckdb_path, output_dir,
  ..., n_workers=1, chunksize=32, chunk_rows=5000, resume=True)`
  opens the games view, builds the format stats once, streams rows
  to workers, and writes a **directory** of
  `chunk_NNNNNNNN.parquet` files. Each chunk is written atomically
  (`.chunk_*.tmp-<pid>` + rename), and a crashed run can resume by
  scanning surviving chunks for the
  `(draft_id, match_number, game_number)` skip-set. When
  `n_workers > 1`, per-row work fans out across a
  `multiprocessing.Pool` (spawn-based, Windows-safe). Real-world
  speedup at 8 workers is ~6-7x on million-row jobs; smaller jobs
  see less due to Pool spawn cost. Row order in the output is not
  preserved (`imap_unordered`); the model + baseline are
  order-invariant.
* `feature_parquet_paths(output_dir)` returns the sorted list of
  chunk paths — downstream callers
  (`BaselineModel.fit`, `train_model`) accept the resulting list
  directly.
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

## PR 4 — `train.py`

End-to-end training: load feature parquets, fit the baseline on
the training split only, compute per-row `base_margin`, fit
XGBoost with early stopping against the validation split,
evaluate on the calibration and test splits. Persist the three
artifacts (`baseline.json`, `xgboost.json`, `metadata.json`)
into a single output directory.

* `train_model(parquet_paths, output_dir, val_frac, calib_frac,
  test_frac, n_estimators, max_depth, learning_rate,
  early_stopping_rounds, baseline_l2_C, seed) -> TrainResult`.
* Grouped `draft_id` split (default 70/10/10/10) — no draft
  appears in multiple splits, preventing draft-level leakage
  (one strong drafter inflates win rate across 8-12 of their
  games).
* `_per_row_base_margin` vectorises the baseline margin lookup
  across the loaded dataframe for speed; correctness is asserted
  in tests vs. per-call `BaselineModel.margin`.
* `save_train_result` / `load_train_result` round-trip the
  bundle. Three human-inspectable JSON / XGBoost-native files,
  no pickled state.
* XGBoost objective is `binary:logistic`, metric `logloss`,
  `tree_method="hist"`. Default hyperparameters are the plan's
  starting point; tune via the validation log-loss.

### Why no post-hoc calibrator

An earlier version of the pipeline fit an isotonic regression on
the calibration split. We removed it after a head-to-head
benchmark (`scripts/compare_calibration_methods.py`):

* The booster's raw output is already well calibrated at this
  data scale (test ECE ~0.005, MCE ~0.01 with no post-hoc step).
  `binary:logistic` directly optimizes log-loss, and the
  `base_margin` trick removes player-skill / opp-mull variance
  before the booster sees it, so the residual the booster fits
  is already centered.
* Platt scaling on the same data fits ``A=0.99 / B=0.00`` —
  effectively the identity. Two parameters can't improve on a
  booster that's already where it should be.
* Isotonic adds a step-function quirk: the pool-adjacent-violators
  algorithm pins tail bins to ``y=0`` / ``y=1`` when the
  calibration split happens to be all-loss / all-win in that
  band. On `models/all3_v1` that saturated 64 of 106,905 test
  predictions and cost +0.0015 log-loss vs no calibration.

The four-way split is retained: the "calibration" split is now
just a second held-out eval point alongside `test`.

### Why fit the baseline on the training split only

The baseline is part of the model; it must never see eval rows
or test log-loss is overstated. The trade-off is slightly noisier
baseline coefficients (~70% of the rows instead of 100%) — at
~700k training rows the difference is negligible.

## PR 5 — `inference.py`

The single model-side interface that the website (PR 6) and
overlay (PR 7) consume.

* `ModelBundle.load(model_dir)` — load the three artifacts from a
  saved directory. `ModelBundle.from_train_result(result)` for
  the in-process path.
* `predict_win_probability(bundle, hand, deck, on_the_play,
  mulligan_number, opp_mulligan_number, event_type, set_code,
  shrunk, zscores, n_sims, seed)` — single-hand prediction.
  Returns a probability in `[0, 1]`. Runs the same pipeline
  training used: simulate -> build_feature_row -> baseline margin
  -> XGBoost.
* `recommend(bundle, ..., n_mulligan_samples=30)` -> `Recommendation`
  — compares `P(win | keep)` vs `P(win | mulligan-to-(N+1))`.
  The mulligan arm is a Monte Carlo over fresh 7-card draws from
  the shuffled deck; the verdict is "keep" iff the keep arm wins
  (ties resolve to keep).

### Baseline cancellation at inference

User-skill buckets aren't passed to inference — we don't query
17Lands at runtime. The baseline's
`margin(None, None, on_play, opp_mull)` path uses the precomputed
population marginal for the on-play cell. Both arms of the
recommend comparison see the SAME baseline (same context), so
the baseline term cancels and the verdict reflects only the
XGBoost delta.

### NaN handling for missing features

`_predict_proba` defaults missing feature columns to `np.nan` so a
ParsedCard set whose feature builder doesn't emit some
column (e.g. an older parser run) doesn't crash an inference
call against a newer model — XGBoost uses NaN as missing and
routes it according to the trained tree splits.

## End-to-end verification

After all five PRs merge, the full pipeline runs as:

1. `materialize_feature_matrix(set_code="TLA", output_dir=..., ...)` — build the
   slim feature parquet for TLA Premier Draft.
2. `train_model(parquet_paths=[...], output_dir="models/v1")` —
   fit baseline + XGBoost; save the three artifacts.
3. `bundle = ModelBundle.load("models/v1")` — load for inference.
4. `recommend(bundle, hand=..., deck=..., ...)` — get a keep/mull
   verdict on a hand.

The `tests/test_inference.py::test_recommend_produces_valid_recommendation`
integration test exercises 1-4 end-to-end on synthetic data; it's
the canonical proof that the pipeline composes correctly.

## Choice (keep/mull) model

The choice pipeline answers a different question than the win model:
"what would a *competent player* decide to do with this hand?" rather
than "what is the win probability if we keep?"

### Why both

The two probabilities are independent signals:

* **P(win)** — useful for raw expected-value reasoning. Calibrated on
  game outcomes across all players (after baseline residualisation).
* **P(keep)** — useful as a sanity check or ensemble component, and as
  the primary signal when callers care about decision similarity rather
  than absolute outcome quality. Trained on the *decisions* of
  skilled-enough players, so it captures human intuition about
  keepability that the win model would have to derive indirectly.

Both share the same upstream `simulate -> build_feature_row` chain, so
running them together costs one sim per hand (call
`predict_keep_probability_from_feature_row` on the already-built row).

### Data source

Labels come from `scripts/mulligan_decisions/build_dataset.py`, which
extracts every candidate opening hand (kept AND mulliganed) from the
17Lands replay-data CSVs into per-set parquets at
`data/processed/seventeenlands/mulligan_decisions/{SET}.{EVENT}.parquet`.
Unlike `game_data` (which only records the final kept hand), `replay_data`
captures the actual mulligan choices the player made.

### Player-skill filter (`choice_rows.should_keep_player`)

We drop a decision row when **both** of:

* The player has played at least `min_n_games_to_judge` (default 50)
  games in the format (so we have a meaningful sample), AND
* Their lifetime win rate is strictly below `min_win_rate` (default
  0.50).

When either signal is unknown, we keep the row — can't judge them as
bad without both. This is a "remove the known-bad" filter rather than
a "keep only the known-good" filter, so it preserves most of the data
while pruning the tail that's most likely to mull badly. Both
thresholds are CLI-configurable on
`packages/model/scripts/materialize_choice_features.py`.

### Kept-hand simulation reuse (`choice_feature_matrix.KeptHandCache`)

Every `was_kept=True` decision row's underlying `(hand, deck,
mulligan_number)` is *also* the hand the player actually played, which
the win-model pipeline has already simulated and turned into a
200-column feature row at
`data/processed/model_training/{SET}/{EVENT}/`. The choice
materialiser loads that cache once per format (~1 GB DataFrame for a
TLA-sized format), looks up each kept-hand decision by `(draft_id,
match_number, game_number)`, and reuses the cached features
bit-identically — only swapping the win-model's `won` label for the
choice model's `was_kept` label and updating the context columns.

Mulled-away hands (`was_kept=False`) and any cache misses fall back
through the same `simulate -> build_feature_row` chain the
fresh-compute path uses. In TLA's dataset this is a ~10x compute
reduction: about 63k mulled hands to simulate vs 626k total decision
rows.

Bit-identical reuse only works because:

* The simulator's seed is deterministic in `(draft_id, match_number,
  game_number)` — same row in the win-model cache and the choice
  materialiser produces the same aggregate.
* `build_feature_row` is pure given its inputs and the simulator
  aggregate. The kept-hand row's `mulligan_number` in the cache
  equals `num_mulligans_in_game`, which by construction equals the
  ChoiceRow's `mulligan_number` for `was_kept=True` rows.

### Why no baseline residualization

The win model strips player-skill + opp-mulligan variance via a
saturated-cell logistic baseline because game outcome is heavily
confounded by skill (a brilliant keep loses if the player misplays).
For the choice model the label is the player's *decision*, not the
outcome. After filtering to competent players upstream, the remaining
decisions are a population of competent choices and the natural
"baseline" is dominated by `mulligan_number`, which XGBoost picks up
directly as a feature. Layering a separate baseline would be
redundant and would require a context column the choice model
shouldn't need at inference time.

### Choice-model output schema

Same 200 features as the win-model cache, plus:

* `was_kept` — the label.
* `num_mulligans_in_game` — context for audit (not a feature; would
  be label-leaking at inference because we don't know the final
  mulligan count when the decision is being made).
* `user_n_games_raw`, `user_wr_raw` — raw 17Lands buckets for
  filter-audit. Not features.
* `opp_mulligan_count_if_known`, `opp_mulligan_number`,
  `mulligan_number`, `expansion`, `event_type`, `draft_id`,
  `build_index`, `match_number`, `game_number` — same meaning as the
  win-model cache.

### Choice-model run order

1. `scripts/mulligan_decisions/build_dataset.py --sets TLA TMT ECL SOS`
   — pulls 17Lands replay CSVs into `mulligan_decisions/` parquets.
2. `packages/model/scripts/materialize_choice_features.py --sets TLA
   TMT ECL SOS --n-workers 8` — builds
   `data/processed/choice_training/{SET}/{EVENT}/chunk_*.parquet`
   atomically + resumably, with kept-hand sim reuse.
3. `packages/model/scripts/train_choice_model.py --sets TLA TMT ECL
   SOS --output-dir models/choice_v1` — trains XGBoost on the
   combined chunks; writes `xgboost.json` + `metadata.json`.

### Choice-model artifacts

Two files in `models/<name>/`:

* `xgboost.json` — booster in its native JSON format.
* `metadata.json` — feature names + per-split metrics + best
  iteration + seed.

No `baseline.json` because the choice model doesn't use one. Load
via `ChoiceModelBundle.load(model_dir)`.

## Pipeline version lineage (`versioning.py`)

`versioning.py` makes the simulator → feature-cache → model lineage a
first-class artifact so a resumed materialisation can't silently stitch
two simulator semantics into one shard, and a training run can't quietly
train on a cache older than the live code (the choice_v7 incident).

### Version identity

`pipeline_versions()` returns
`{"simulation": SIMULATION_SEMANTICS_VERSION, "features":
FEATURES_SEMANTICS_VERSION}` — the two ints from the simulation and
features packages. See each package's CLAUDE.md for the same-PR bump
rules. Both are `1` today (version 1 == "current semantics").

### `_meta.json` shard sidecar

Every feature-cache shard directory
(`data/processed/{model,choice}_training/<SET>/<EVENT>/`) carries a
`_meta.json` (underscore keeps it out of the `chunk_*.parquet` glob) with
the flat shape:

```json
{"pipeline_versions": {"simulation": 1, "features": 1},
 "set_code": "TLA", "event_type": "PremierDraft",
 "n_sims_per_row": 200, "created_at": "…ISO…",
 "unverified_legacy": false}
```

Unknown keys are tolerated on read (forward compat). Both materialisers
call `stamp_or_check_shard_meta` before any expensive work:

* **Fresh dir / `overwrite=True`** — write the sidecar (overwrite also
  deletes the old one first).
* **Resume with a matching sidecar** — proceed, leaving it untouched.
* **Resume with a mismatching sidecar** — raise `ShardVersionError`
  naming exactly what differs (pipeline versions, `n_sims_per_row`,
  set/event) and advising `--overwrite`. Mixing per-row sim budgets mixes
  feature noise levels, so `n_sims_per_row` is a hard check too.
* **Resume with chunks but NO sidecar (legacy shard)** — WARN, then stamp
  the current versions with `unverified_legacy=true`. This is the grace
  path for every shard built before stamping existed; it is *not*
  verification, just a record that we couldn't verify.

### Training checks + `--allow-version-mismatch`

`train_model` / `train_choice_model` derive shard dirs as
`{p.parent for p in parquet_paths}`, read each `_meta.json`, and:

* record a per-shard `shard_lineage` in `metadata.json` (a legacy dir
  with no sidecar is recorded as `pipeline_versions: null`);
* raise `ShardVersionError` if any shard's versions differ from the live
  `pipeline_versions()` (this also catches shards disagreeing with each
  other), **unless** `allow_version_mismatch=True`.

The kwarg is surfaced as `--allow-version-mismatch` on
`train_multi_set.py`, `train_choice_model.py`, and `retrain_all.py`
(which forwards it to both training steps). Default refuses the mix;
passing it records `version_mismatch_allowed: true` in `metadata.json`.

`metadata.json` gains four keys (all optional — old models load fine
without them): `pipeline_versions`, `shard_lineage`,
`version_mismatch_allowed`, `split_method`.

### Load-time warning (warn, never fail)

`ModelBundle.load` / `ChoiceModelBundle.load` compare the model's
recorded `pipeline_versions` to the live ones. On mismatch or absence
they log one WARNING and set `bundle.version_warning: str | None` — they
never raise (frozen-EXE users can legitimately run skewed versions;
surfacing it in the UI is a later roadmap step).

### Materialisation-invariant split (`draftid_hash_v1`)

`_grouped_split` in both `train.py` (4-way) and `choice_train.py` (3-way)
assigns each `draft_id` to a split via
`draftid_hash_unit(seed, draft_id)` =
`sha256(f"{seed}:{draft_id}")[:8] / 2**64`, using cumulative-fraction
bands (val → [calib →] test → train). Because the assignment depends only
on `(seed, draft_id)`, re-materialising a cache no longer reshuffles the
split. Split sizes are now binomial around the target fractions rather
than exact — fine at our draft counts.

**Comparability caveat:** models trained with the OLD permutation split
are NOT split-comparable with hash-split models (a draft can move between
train and test). Each model's own held-out metrics remain the only honest
cross-model comparison; `metadata.json`'s `split_method` records which
scheme a model used (`draftid_hash_v1`; absent on old models).

## scripts/

Standalone analysis scripts that run against a trained model
directory plus the source data. Not part of the package's public
import surface — invoke via the workspace's `python.exe`
(`.venv/Scripts/python.exe packages/model/scripts/<name>.py`).

### `materialize_feature_matrix.py`

Thin CLI over
`mulligan_coach_model.feature_matrix.materialize_feature_matrix`.
Materialises the win-model feature parquet for one or more sets,
writing chunked output under
`data/processed/model_training/<SET>/<EVENT>/`. Use
`--overwrite` after a simulator-semantics change to wipe the
existing cache (the chunk-level resume otherwise treats existing
chunks as authoritative).

Typical use:

```
.venv/Scripts/python.exe packages/model/scripts/materialize_feature_matrix.py \
  --sets TLA TMT ECL --n-workers 8 --overwrite
```

### `retrain_all.py`

Chain script that runs all four steps end-to-end:
materialise win cache -> train win model -> materialise choice
cache -> train choice model. Designed for unattended overnight
runs: each sub-step logs to a timestamped directory under
`logs/retrain_all_<timestamp>/`. `--skip-existing` skips whole
steps whose output directory already exists (so a mid-chain
crash can be picked up). `--overwrite-caches` forces re-
materialisation (use after a simulator-semantics change).

Typical use:

```
.venv/Scripts/python.exe packages/model/scripts/retrain_all.py \
  --win-sets TLA TMT ECL \
  --choice-sets TLA TMT \
  --win-output-dir models/all3_v2 \
  --choice-output-dir models/choice_v3 \
  --overwrite-caches \
  --n-workers 8
```

Wall-clock on TLA + TMT + ECL with 8 workers: ~28 hours of
materialisation + ~1 hour of training. Plan accordingly.

### `validate_bottoming.py`

Empirical validation of the bottoming heuristic in
`mulligan_coach_simulation.bottoming`. For each of N sampled real
TLA hands:

1. Asks the heuristic which of the 7 cards to bottom.
2. Brute-forces all 7 candidates: for each, simulates the resulting
   6-card hand + 33-card library through the model and predicts
   P(win) at `mulligan_number=1`.
3. Reports the heuristic pick's rank (1=optimal) and P(win) gap
   from the best candidate.

The model is fed a 6-card hand at `mulligan_number=1`, slightly
out-of-distribution from training (which used 7-card pre-bottom
hands), so absolute P(win) values are noisier than usual but
*rankings* among candidates are meaningful. Log written to
`<model_dir>/bottoming_validation.log`.

### `mulligan_analysis_per_deck.py`

Per-deck mulligan benchmark — replaces the unconditional `0.4295`
flat baseline that `mulligan_analysis.log` originally used. For
each of N sampled kept-7 test rows:

1. Predicts `P(win | keep)` on the actual recorded hand.
2. Estimates `P(win | mull this deck to 6)` by averaging predictions
   over `N_MULLIGAN_SAMPLES` smoother-aware 7-card draws from the
   deck (with `mulligan_number=1`).
3. Flags rows where `p_keep < p_mull_per_deck` and compares to the
   unconditional `p_keep < 0.4295` flagging.

Key insight: the per-deck mull WR has std ~0.06 across decks (p10
0.37, p90 0.54) — meaningful real variance the flat baseline was
hiding. The per-deck rule is materially more conservative because
weak decks have mull-to-6 WRs *below* 0.4295, so the unconditional
rule over-flags them as "should have mulled" when actually mulling
wouldn't help.

The bottoming heuristic is NOT used by this script because the
model treats the hand as 7-card pre-bottom (matches training
distribution); only the smoother is needed. Log written to
`<model_dir>/mulligan_analysis_per_deck.log`.

### A note on feature bias on mulligan rows

The 17Lands London-mulligan convention: every recorded
`opening_hand_*` sums to 7 cards (the pre-bottom draw), regardless
of `mulligan_number`. Hand-level features and the simulator
playability stats are computed on those 7 cards, even when the
player actually played with fewer. The model treats `mulligan_number`
as context and has learned to deflate predictions accordingly.

Empirical calibration on test rows (from a quick ad-hoc check):

| mulligan_number | n | mean predicted | actual WR | gap |
|---|---|---|---|---|
| 0 | 45,245 | 0.578 | 0.580 | -0.003 |
| 1 | 5,146 | 0.430 | 0.430 | +0.001 |
| 2 | 230 | 0.283 | 0.239 | +0.044 |

Mulligan-to-6 is well-calibrated globally and within deciles of
predicted P(win). Mulligan-to-5 (n=230) over-predicts by 4.4pp —
could be sampling noise (~1.6σ) or genuine miscalibration on a
low-data subset.

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
* **No post-hoc calibration** — the booster's raw output is the
  final prediction. The booster trained with `binary:logistic` +
  `base_margin` is already well calibrated at our data scale;
  isotonic added saturation at the tails without improving
  log-loss, and Platt fit the identity. See the `train.py`
  section above.

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
