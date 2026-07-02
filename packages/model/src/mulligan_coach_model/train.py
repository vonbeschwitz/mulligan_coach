"""XGBoost training entry point with baseline residualization.

End-to-end pipeline (one function — :func:`train_model` — calls the
others):

1. Load one or more feature-cache parquet shards (PR 2 output).
2. Grouped train / val / calibration / test split by ``draft_id``
   so games from one draft never appear in multiple splits — that
   would leak draft-level structure (a strong drafter inflates
   the WR across all 8-12 games from one draft).
3. Fit :class:`BaselineModel` (PR 3) on the training split only.
4. Compute per-row ``base_margin`` from the baseline for every split.
5. Train XGBoost on the training split with ``base_margin`` and
   early stopping against the validation split.
6. Evaluate on the calibration and test splits: log-loss, Brier,
   accuracy. (No post-hoc calibrator is fit — see below.)
7. Optionally serialise the two model files
   (``baseline.json``, ``xgboost.json``) plus ``metadata.json``
   into a single output directory.

No post-hoc calibration step
----------------------------

An earlier version of the pipeline fit an isotonic regression on a
held-out calibration split. We removed it after a head-to-head
comparison (see ``scripts/compare_calibration_methods.py``):

* The booster trained with ``base_margin`` + ``binary:logistic`` is
  already well calibrated (test ECE ~0.005, MCE ~0.01 with no
  post-hoc step).
* Platt scaling on the same data fits A=0.99 / B=0.00 — effectively
  the identity transform — confirming the booster needs no shift.
* Isotonic adds a step-function quirk: the pool-adjacent-violators
  algorithm pins tail bins to ``y=0`` / ``y=1`` when the calibration
  split is sparse and unlucky in that band. That saturation costs
  log-loss (a single misclassified saturated row contributes ~16
  nats clipped at the floor).

The four-way split shape is retained: the "calibration" split is
now simply a second held-out eval point alongside ``test``.

Materialisation-invariant split
-------------------------------

``_grouped_split`` assigns each ``draft_id`` to a split via a
``sha256(seed:draft_id)`` hash (see
:func:`mulligan_coach_model.versioning.draftid_hash_unit`) rather than a
numpy permutation over the *observed* set of draft_ids. The old
permutation index depended on how many unique draft_ids were present and
the order they first appeared, so re-materialising a cache reshuffled the
train/val/test assignment and made cross-run held-out comparisons leak.
The hash assignment depends only on ``(seed, draft_id)``.

**Comparability note:** models trained with the old permutation split are
NOT split-comparable with hash-split models — a draft that was in "test"
under the old scheme may be in "train" under the new one. Each model's own
held-out metrics remain the only honest cross-model comparison; do not
compare a permutation-split model's test log-loss against a hash-split
model's directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from .baseline import BaselineModel
from .versioning import (
    SPLIT_METHOD,
    ShardLineageEntry,
    check_training_lineage,
    draftid_hash_unit,
    gather_shard_lineage,
    pipeline_versions,
)

log = logging.getLogger(__name__)


# Columns the feature parquet carries that are NOT XGBoost features.
# Everything else in the parquet is a feature. Kept in sync with
# :mod:`feature_matrix` cache schema.
_NON_FEATURE_COLUMNS = frozenset(
    {
        "user_wr_bucket",
        "user_n_games_bucket",
        # opp_mulligan_number is for the baseline; the conditional
        # feature `opp_mulligan_count_if_known` is the XGBoost-visible
        # version with the on-play / on-draw missingness.
        "opp_mulligan_number",
        "expansion",
        "event_type",
        "draft_id",
        "match_number",
        "game_number",
        "won",
    }
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitMetrics:
    """Predicted-vs-observed metrics on one of the four splits."""

    log_loss: float
    brier: float
    accuracy: float
    n_rows: int


@dataclass(frozen=True)
class TrainingMetadata:
    """Audit / provenance fields for a fitted model.

    Saved alongside the booster + baseline so :meth:`ModelBundle.load`
    can validate that the feature column order matches at inference
    time (mismatched order silently corrupts predictions).

    The version-lineage fields (``pipeline_versions``, ``shard_lineage``,
    ``version_mismatch_allowed``, ``split_method``) default to
    "unknown / not recorded" so models trained before Step 1 still load
    (:func:`load_train_result` tolerates their absence).
    """

    feature_names: tuple[str, ...]
    train: SplitMetrics
    val: SplitMetrics
    calibration: SplitMetrics
    test: SplitMetrics
    best_iteration: int
    seed: int
    pipeline_versions: dict[str, int] | None = None
    """Live simulator/feature versions at train time. ``None`` for models
    trained before version stamping."""
    shard_lineage: tuple[ShardLineageEntry, ...] = ()
    """Per-shard-directory provenance of the training caches."""
    version_mismatch_allowed: bool = False
    """Whether the run was forced past a version-mismatch check."""
    split_method: str | None = None
    """How the train/val/calib/test split was drawn (``draftid_hash_v1``);
    ``None`` for old permutation-split models."""


@dataclass(frozen=True)
class TrainResult:
    """Bundle returned by :func:`train_model`.

    Holds the two independently-saveable artifacts plus the
    training metadata. The inference path consumes these
    objects directly when called in-process.
    """

    booster: xgb.Booster
    baseline: BaselineModel
    metadata: TrainingMetadata


# ---------------------------------------------------------------------------
# Split + helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Split:
    """Boolean masks indexing the loaded feature dataframe."""

    train: np.ndarray
    val: np.ndarray
    calib: np.ndarray
    test: np.ndarray


def _grouped_split(
    draft_ids: pd.Series,
    *,
    val_frac: float,
    calib_frac: float,
    test_frac: float,
    seed: int,
) -> _Split:
    """Assign each unique ``draft_id`` to exactly one of four splits.

    Materialisation-invariant hash assignment (``SPLIT_METHOD`` =
    ``draftid_hash_v1``): each draft's unit-interval position comes from
    :func:`mulligan_coach_model.versioning.draftid_hash_unit`, which depends
    only on ``(seed, draft_id)``. A draft therefore always lands in the same
    band regardless of the dataset's composition, row order, or whether the
    cache was re-materialised — unlike the old permutation index, whose
    assignment depended on the count and first-appearance order of the
    observed draft_ids.

    Bands are cumulative and ordered val -> calib -> test -> train::

        u < val_frac                       -> val
        u < val_frac + calib_frac          -> calib
        u < val_frac + calib_frac + test_frac -> test
        else                               -> train

    Split sizes are now binomial around the target fractions rather than
    exact; at our draft counts (thousands) the deviation is fractions of a
    percent. Validates that the four fractions are in ``(0, 1)`` and sum to
    less than 1 (the remainder is the training split).
    """
    for name, val in (("val", val_frac), ("calib", calib_frac), ("test", test_frac)):
        if not 0.0 < val < 1.0:
            raise ValueError(f"{name}_frac must be in (0, 1); got {val!r}")
    total_eval = val_frac + calib_frac + test_frac
    if total_eval >= 1.0:
        raise ValueError(f"val_frac + calib_frac + test_frac must sum to < 1; got {total_eval}")

    val_cut = val_frac
    calib_cut = val_frac + calib_frac
    test_cut = val_frac + calib_frac + test_frac

    # One hash per unique draft_id, then map every row through the same
    # assignment (cheap: the unique set is far smaller than the row count).
    assignment: dict[object, str] = {}
    for did in draft_ids.unique():
        u = draftid_hash_unit(seed, did)
        if u < val_cut:
            assignment[did] = "val"
        elif u < calib_cut:
            assignment[did] = "calib"
        elif u < test_cut:
            assignment[did] = "test"
        else:
            assignment[did] = "train"

    bucket = draft_ids.map(assignment)
    split = _Split(
        train=(bucket == "train").to_numpy(),
        val=(bucket == "val").to_numpy(),
        calib=(bucket == "calib").to_numpy(),
        test=(bucket == "test").to_numpy(),
    )
    if not split.train.any():
        raise ValueError(
            "Hash split assigned no rows to training (too few drafts for these "
            "fractions). Add more data or reduce val/calib/test fractions."
        )
    return split


def _feature_columns(df_columns: Iterable[str]) -> list[str]:
    """Return columns from the parquet that are XGBoost-visible features.

    Order is stable on the input iteration order so the same parquet
    schema always produces the same ``feature_names`` list.
    """
    return [col for col in df_columns if col not in _NON_FEATURE_COLUMNS]


def _per_row_base_margin(
    df: pd.DataFrame,
    baseline: BaselineModel,
) -> np.ndarray:
    """Compute the logit-scale baseline margin for every row.

    Vectorised by building a per-cell lookup once and indexing with
    ``df[__cell__]`` plus the opp-mull column. ~1M rows finishes in
    well under a second.
    """
    # Build cell margin lookup; rows whose (wr, n_games, on_play)
    # cell isn't in the baseline fall through to the on-play
    # population marginal via the .map default.
    cell_labels = (
        df["user_wr_bucket"].astype(str)
        + "|"
        + df["user_n_games_bucket"].astype(str)
        + "|"
        + df["on_the_play"].astype(int).astype(str)
    )
    cell_margin_map: dict[str, float] = {}
    for (wr, ng, on_play), val in baseline.cell_margins.items():
        cell_margin_map[f"{wr}|{ng}|{int(bool(on_play))}"] = val

    cell_margins = cell_labels.map(cell_margin_map)
    # Fall back to the on_play population marginal for unseen cells.
    on_play_mask = df["on_the_play"].astype(bool)
    on_play_marg = baseline.population_marginal_margins.get(True, 0.0)
    off_play_marg = baseline.population_marginal_margins.get(False, 0.0)
    cell_margins = cell_margins.where(
        cell_margins.notna(),
        np.where(on_play_mask, on_play_marg, off_play_marg),
    )

    opp_margins = df["opp_mulligan_number"].astype(int).map(baseline.opp_mulligan_margins)
    opp_margins = opp_margins.fillna(baseline.population_mean_opp_mulligan_margin)

    margins = (cell_margins.astype(float) + opp_margins.astype(float)).to_numpy()
    return np.asarray(margins, dtype=float)


def _compute_metrics(
    y_true: np.ndarray,
    p_pred: np.ndarray,
) -> SplitMetrics:
    """Log-loss / Brier / accuracy on one split.

    ``p_pred`` is the post-calibration probability (in [0, 1]). We
    clip away from {0, 1} to keep log-loss finite even when the
    model gets very confident.
    """
    eps = 1e-7
    p = np.clip(p_pred, eps, 1.0 - eps)
    log_loss = float(-(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)).mean())
    brier = float(((p - y_true) ** 2).mean())
    accuracy = float(((p >= 0.5).astype(int) == y_true).mean())
    return SplitMetrics(
        log_loss=log_loss,
        brier=brier,
        accuracy=accuracy,
        n_rows=len(y_true),
    )


# ---------------------------------------------------------------------------
# Metadata persistence
# ---------------------------------------------------------------------------


def _metrics_to_dict(m: SplitMetrics) -> dict[str, float | int]:
    return {
        "log_loss": m.log_loss,
        "brier": m.brier,
        "accuracy": m.accuracy,
        "n_rows": m.n_rows,
    }


def _save_metadata(metadata: TrainingMetadata, path: Path) -> None:
    payload = {
        "feature_names": list(metadata.feature_names),
        "train": _metrics_to_dict(metadata.train),
        "val": _metrics_to_dict(metadata.val),
        "calibration": _metrics_to_dict(metadata.calibration),
        "test": _metrics_to_dict(metadata.test),
        "best_iteration": metadata.best_iteration,
        "seed": metadata.seed,
        # Version lineage (Step 1). See mulligan_coach_model.versioning.
        "pipeline_versions": metadata.pipeline_versions,
        "shard_lineage": [e.to_json_dict() for e in metadata.shard_lineage],
        "version_mismatch_allowed": metadata.version_mismatch_allowed,
        "split_method": metadata.split_method,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


# ---------------------------------------------------------------------------
# train_model — top-level entry point
# ---------------------------------------------------------------------------


def train_model(
    *,
    parquet_paths: Sequence[Path] | Path,
    output_dir: Path | None = None,
    val_frac: float = 0.10,
    calib_frac: float = 0.10,
    test_frac: float = 0.10,
    n_estimators: int = 500,
    max_depth: int = 6,
    learning_rate: float = 0.05,
    early_stopping_rounds: int = 20,
    baseline_l2_C: float = 10.0,
    seed: int = 0,
    allow_version_mismatch: bool = False,
) -> TrainResult:
    """End-to-end training: baseline -> XGBoost.

    Parameters
    ----------
    parquet_paths:
        One or more feature-cache parquet shards (output of
        :func:`materialize_feature_matrix`). Multiple shards are
        concatenated so a unified multi-format model can be fit
        in one call.
    output_dir:
        When provided, the three files (``baseline.json``,
        ``xgboost.json``, ``metadata.json``) are written here.
        The directory is created if missing. ``None`` skips
        persistence — useful for tests that only inspect the
        in-memory artifacts.
    val_frac, calib_frac, test_frac:
        Draft-grouped fractions for the three eval splits. Training
        gets the remainder (``1 - val - calib - test``). Default
        70/10/10/10. Must each be in ``(0, 1)`` and sum to ``< 1``.
        The "calibration" split is retained as a second held-out
        eval point even though no calibrator is fit on it.
    n_estimators, max_depth, learning_rate, early_stopping_rounds:
        XGBoost hyperparameters. Starting defaults are from the plan;
        tune via the validation log-loss.
    baseline_l2_C:
        L2 inverse-regularisation strength for the baseline. Higher
        = less shrinkage.
    seed:
        Controls the train/val/calib/test draft assignment and the
        XGBoost RNG. Same seed -> same split.
    allow_version_mismatch:
        When ``False`` (default), a :class:`ShardVersionError` is raised if
        any training shard was built under pipeline versions that differ
        from the live simulator + feature code (the choice_v7 incident:
        training on a cache older than the code). Pass ``True`` to train on
        the mix anyway — the choice is recorded in ``metadata.json`` as
        ``version_mismatch_allowed``. Legacy shards with no version sidecar
        only warn, regardless of this flag.

    Returns
    -------
    TrainResult
        In-memory bundle of the fitted objects + metadata.
    """
    paths = [parquet_paths] if isinstance(parquet_paths, Path) else list(parquet_paths)
    if not paths:
        raise ValueError("Need at least one parquet path to fit.")

    # Version lineage: read each shard's _meta.json and refuse to mix
    # mismatched simulator/feature semantics unless explicitly allowed.
    shard_lineage = gather_shard_lineage(paths)
    check_training_lineage(shard_lineage, allow_version_mismatch=allow_version_mismatch)

    log.info("Loading %d feature parquet shard(s)", len(paths))
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    if len(df) == 0:
        raise ValueError("Loaded an empty feature dataframe; nothing to train on.")
    log.info("Loaded %d total rows", len(df))

    feature_names = _feature_columns(df.columns)
    if not feature_names:
        raise ValueError(
            "No feature columns found after stripping non-feature columns; "
            "the parquet schema may have changed."
        )

    splits = _grouped_split(
        df["draft_id"],
        val_frac=val_frac,
        calib_frac=calib_frac,
        test_frac=test_frac,
        seed=seed,
    )
    log.info(
        "Split rows: train=%d val=%d calib=%d test=%d",
        int(splits.train.sum()),
        int(splits.val.sum()),
        int(splits.calib.sum()),
        int(splits.test.sum()),
    )

    # Fit baseline on TRAIN ONLY so it never sees eval rows.
    baseline = BaselineModel._fit_dataframe(df.loc[splits.train], l2_C=baseline_l2_C)

    # Per-row base_margin for every split.
    base_margin = _per_row_base_margin(df, baseline)

    X = df[feature_names].astype(float).to_numpy()
    y = df["won"].astype(int).to_numpy()

    dtrain = xgb.DMatrix(
        X[splits.train],
        label=y[splits.train],
        base_margin=base_margin[splits.train],
        feature_names=feature_names,
    )
    dval = xgb.DMatrix(
        X[splits.val],
        label=y[splits.val],
        base_margin=base_margin[splits.val],
        feature_names=feature_names,
    )
    dcalib = xgb.DMatrix(
        X[splits.calib],
        label=y[splits.calib],
        base_margin=base_margin[splits.calib],
        feature_names=feature_names,
    )
    dtest = xgb.DMatrix(
        X[splits.test],
        label=y[splits.test],
        base_margin=base_margin[splits.test],
        feature_names=feature_names,
    )

    params = {
        "objective": "binary:logistic",
        "eval_metric": "logloss",
        "tree_method": "hist",
        "max_depth": max_depth,
        "eta": learning_rate,
        "seed": seed,
    }
    log.info("Training XGBoost: %s", params)
    booster = xgb.train(
        params,
        dtrain,
        num_boost_round=n_estimators,
        evals=[(dtrain, "train"), (dval, "val")],
        early_stopping_rounds=early_stopping_rounds,
        verbose_eval=False,
    )
    best_iteration = int(booster.best_iteration)

    # Predict on each split using the best iteration. iteration_range=(0, best+1)
    # ensures the early-stopping point is honoured. The booster output
    # is the final prediction — no post-hoc calibration step.
    iter_range = (0, best_iteration + 1)
    p_train = booster.predict(dtrain, iteration_range=iter_range)
    p_val = booster.predict(dval, iteration_range=iter_range)
    p_calib = booster.predict(dcalib, iteration_range=iter_range)
    p_test = booster.predict(dtest, iteration_range=iter_range)

    metadata = TrainingMetadata(
        feature_names=tuple(feature_names),
        train=_compute_metrics(y[splits.train], p_train),
        val=_compute_metrics(y[splits.val], p_val),
        calibration=_compute_metrics(y[splits.calib], p_calib),
        test=_compute_metrics(y[splits.test], p_test),
        best_iteration=best_iteration,
        seed=seed,
        pipeline_versions=pipeline_versions(),
        shard_lineage=tuple(shard_lineage),
        version_mismatch_allowed=allow_version_mismatch,
        split_method=SPLIT_METHOD,
    )
    log.info(
        "Done. test_log_loss=%.4f test_brier=%.4f test_accuracy=%.4f",
        metadata.test.log_loss,
        metadata.test.brier,
        metadata.test.accuracy,
    )

    result = TrainResult(
        booster=booster,
        baseline=baseline,
        metadata=metadata,
    )

    if output_dir is not None:
        save_train_result(result, output_dir)

    return result


def save_train_result(result: TrainResult, output_dir: Path) -> None:
    """Write a :class:`TrainResult` to disk under ``output_dir``.

    Three files:

    * ``baseline.json`` — fitted :class:`BaselineModel` coefficients.
    * ``xgboost.json`` — XGBoost booster (its native JSON format).
    * ``metadata.json`` — feature names + per-split metrics.

    Inference reads the same three files via :meth:`ModelBundle.load`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result.baseline.save(output_dir / "baseline.json")
    result.booster.save_model(str(output_dir / "xgboost.json"))
    _save_metadata(result.metadata, output_dir / "metadata.json")
    log.info("Saved model artifacts to %s", output_dir)


def load_train_result(model_dir: Path) -> TrainResult:
    """Inverse of :func:`save_train_result`.

    Used by :meth:`ModelBundle.load`; exposed here so tests can
    round-trip a fitted model through disk.
    """
    baseline = BaselineModel.load(model_dir / "baseline.json")
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    metadata_path = model_dir / "metadata.json"
    payload = json.loads(metadata_path.read_text())

    def _mk_split(d: dict[str, float | int]) -> SplitMetrics:
        return SplitMetrics(
            log_loss=float(d["log_loss"]),
            brier=float(d["brier"]),
            accuracy=float(d["accuracy"]),
            n_rows=int(d["n_rows"]),
        )

    # Version-lineage keys are absent on pre-Step-1 models; tolerate that.
    raw_versions = payload.get("pipeline_versions")
    pipeline_versions_loaded: dict[str, int] | None = (
        {str(k): int(v) for k, v in raw_versions.items()}
        if isinstance(raw_versions, dict)
        else None
    )
    shard_lineage = tuple(
        ShardLineageEntry.from_json_dict(e) for e in payload.get("shard_lineage", [])
    )
    metadata = TrainingMetadata(
        feature_names=tuple(payload["feature_names"]),
        train=_mk_split(payload["train"]),
        val=_mk_split(payload["val"]),
        calibration=_mk_split(payload["calibration"]),
        test=_mk_split(payload["test"]),
        best_iteration=int(payload["best_iteration"]),
        seed=int(payload["seed"]),
        pipeline_versions=pipeline_versions_loaded,
        shard_lineage=shard_lineage,
        version_mismatch_allowed=bool(payload.get("version_mismatch_allowed", False)),
        split_method=payload.get("split_method"),
    )
    return TrainResult(
        booster=booster,
        baseline=baseline,
        metadata=metadata,
    )
