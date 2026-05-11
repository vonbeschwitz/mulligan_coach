"""XGBoost training entry point with baseline residualization + calibration.

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
6. Predict on the calibration split (with base_margin) and fit an
   isotonic regression on the
   ``(predicted_prob, observed_won)`` pairs.
7. Evaluate on the test split: log-loss, Brier, accuracy.
8. Optionally serialise the three model files
   (``baseline.json``, ``xgboost.json``, ``calibrator.json``) plus
   ``metadata.json`` into a single output directory.

The composition (baseline -> XGBoost -> isotonic) preserves
probability calibration: the baseline does the logit-scale
residualization, XGBoost learns a zero-mean residual on top, and
the isotonic step trims any systematic miscalibration that
remains.
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
from sklearn.isotonic import IsotonicRegression

from .baseline import BaselineModel

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

    Saved alongside the booster + calibrator + baseline so PR 5's
    :class:`ModelBundle.load` can validate that the feature column
    order matches at inference time (mismatched order silently
    corrupts predictions).
    """

    feature_names: tuple[str, ...]
    train: SplitMetrics
    val: SplitMetrics
    calibration: SplitMetrics
    test: SplitMetrics
    best_iteration: int
    seed: int


@dataclass(frozen=True)
class TrainResult:
    """Bundle returned by :func:`train_model`.

    Holds the three independently-saveable artifacts plus the
    training metadata. PR 5's inference path consumes these
    objects directly when called in-process.
    """

    booster: xgb.Booster
    baseline: BaselineModel
    calibrator: IsotonicRegression
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

    Random group assignment via a numpy-RNG shuffle. The proportions
    are draft-level, so row-level proportions track to within
    sampling noise.

    Validates that the four fractions are in ``[0, 1)`` and sum to
    less than 1 (the remainder is the training split).
    """
    for name, val in (("val", val_frac), ("calib", calib_frac), ("test", test_frac)):
        if not 0.0 < val < 1.0:
            raise ValueError(f"{name}_frac must be in (0, 1); got {val!r}")
    total_eval = val_frac + calib_frac + test_frac
    if total_eval >= 1.0:
        raise ValueError(f"val_frac + calib_frac + test_frac must sum to < 1; got {total_eval}")

    unique = draft_ids.unique()
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(unique)
    n = len(shuffled)
    n_val = round(n * val_frac)
    n_calib = round(n * calib_frac)
    n_test = round(n * test_frac)
    n_train = n - n_val - n_calib - n_test
    if n_train <= 0:
        raise ValueError("Splits leave no rows for training; reduce val/calib/test fractions.")

    train_ids = set(shuffled[:n_train].tolist())
    val_ids = set(shuffled[n_train : n_train + n_val].tolist())
    calib_ids = set(shuffled[n_train + n_val : n_train + n_val + n_calib].tolist())
    test_ids = set(shuffled[n_train + n_val + n_calib :].tolist())

    return _Split(
        train=draft_ids.isin(train_ids).to_numpy(),
        val=draft_ids.isin(val_ids).to_numpy(),
        calib=draft_ids.isin(calib_ids).to_numpy(),
        test=draft_ids.isin(test_ids).to_numpy(),
    )


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
# Calibrator persistence
# ---------------------------------------------------------------------------


def _save_calibrator(calibrator: IsotonicRegression, path: Path) -> None:
    """Serialise an ``IsotonicRegression`` to JSON via its knots.

    The fitted state we care about is ``X_thresholds_`` and
    ``y_thresholds_`` (the monotone-step function knots);
    ``out_of_bounds`` controls extrapolation. We don't shell out to
    ``pickle`` so the file is human-inspectable.
    """
    payload = {
        "X_thresholds": calibrator.X_thresholds_.tolist(),
        "y_thresholds": calibrator.y_thresholds_.tolist(),
        "out_of_bounds": calibrator.out_of_bounds,
        "increasing": calibrator.increasing,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def _load_calibrator(path: Path) -> IsotonicRegression:
    payload = json.loads(path.read_text())
    iso = IsotonicRegression(
        out_of_bounds=payload.get("out_of_bounds", "clip"),
        increasing=payload.get("increasing", True),
    )
    # Reconstruct the fitted attributes directly. sklearn's
    # `IsotonicRegression.predict` only needs `X_thresholds_` /
    # `y_thresholds_` / `f_` to run.
    x = np.asarray(payload["X_thresholds"], dtype=float)
    y = np.asarray(payload["y_thresholds"], dtype=float)
    iso.X_thresholds_ = x
    iso.y_thresholds_ = y
    # `f_` is the underlying interp1d the predict path calls; build
    # it from the knots so .predict works without a re-fit.
    from scipy.interpolate import interp1d

    iso.f_ = interp1d(
        x,
        y,
        kind="linear",
        bounds_error=False,
        fill_value=(y[0], y[-1]),
    )
    iso.X_min_ = float(x[0])
    iso.X_max_ = float(x[-1])
    return iso


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
) -> TrainResult:
    """End-to-end training: baseline -> XGBoost -> calibration.

    Parameters
    ----------
    parquet_paths:
        One or more feature-cache parquet shards (output of
        :func:`materialize_feature_matrix`). Multiple shards are
        concatenated so a unified multi-format model can be fit
        in one call.
    output_dir:
        When provided, the four files
        (``baseline.json``, ``xgboost.json``, ``calibrator.json``,
        ``metadata.json``) are written here. The directory is
        created if missing. ``None`` skips persistence — useful
        for tests that only inspect the in-memory artifacts.
    val_frac, calib_frac, test_frac:
        Draft-grouped fractions for the three eval splits. Training
        gets the remainder (``1 - val - calib - test``). Default
        70/10/10/10. Must each be in ``(0, 1)`` and sum to ``< 1``.
    n_estimators, max_depth, learning_rate, early_stopping_rounds:
        XGBoost hyperparameters. Starting defaults are from the plan;
        tune via the validation log-loss.
    baseline_l2_C:
        L2 inverse-regularisation strength for the baseline. Higher
        = less shrinkage.
    seed:
        Controls the train/val/calib/test draft assignment and the
        XGBoost RNG. Same seed -> same split.

    Returns
    -------
    TrainResult
        In-memory bundle of the fitted objects + metadata.
    """
    paths = [parquet_paths] if isinstance(parquet_paths, Path) else list(parquet_paths)
    if not paths:
        raise ValueError("Need at least one parquet path to fit.")
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
    # ensures the early-stopping point is honoured.
    iter_range = (0, best_iteration + 1)

    p_train = booster.predict(dtrain, iteration_range=iter_range)
    p_val = booster.predict(dval, iteration_range=iter_range)
    p_calib = booster.predict(dcalib, iteration_range=iter_range)
    p_test_uncalibrated = booster.predict(dtest, iteration_range=iter_range)

    # Fit isotonic regression on the calibration split. clip handles
    # the rare deploy-time prediction outside [min_calib, max_calib]
    # by pinning to the closest knot rather than extrapolating.
    calibrator = IsotonicRegression(out_of_bounds="clip", increasing=True)
    calibrator.fit(p_calib, y[splits.calib].astype(float))

    # Apply calibration to every split for metrics.
    p_train_cal = calibrator.predict(p_train)
    p_val_cal = calibrator.predict(p_val)
    p_calib_cal = calibrator.predict(p_calib)
    p_test_cal = calibrator.predict(p_test_uncalibrated)

    metadata = TrainingMetadata(
        feature_names=tuple(feature_names),
        train=_compute_metrics(y[splits.train], p_train_cal),
        val=_compute_metrics(y[splits.val], p_val_cal),
        calibration=_compute_metrics(y[splits.calib], p_calib_cal),
        test=_compute_metrics(y[splits.test], p_test_cal),
        best_iteration=best_iteration,
        seed=seed,
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
        calibrator=calibrator,
        metadata=metadata,
    )

    if output_dir is not None:
        save_train_result(result, output_dir)

    return result


def save_train_result(result: TrainResult, output_dir: Path) -> None:
    """Write a :class:`TrainResult` to disk under ``output_dir``.

    Four files:

    * ``baseline.json`` — fitted :class:`BaselineModel` coefficients.
    * ``xgboost.json`` — XGBoost booster (its native JSON format).
    * ``calibrator.json`` — isotonic-regression knots.
    * ``metadata.json`` — feature names + per-split metrics.

    Inference in PR 5 reads the same four files via
    :meth:`ModelBundle.load`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result.baseline.save(output_dir / "baseline.json")
    result.booster.save_model(str(output_dir / "xgboost.json"))
    _save_calibrator(result.calibrator, output_dir / "calibrator.json")
    _save_metadata(result.metadata, output_dir / "metadata.json")
    log.info("Saved model artifacts to %s", output_dir)


def load_train_result(model_dir: Path) -> TrainResult:
    """Inverse of :func:`save_train_result`.

    Used by PR 5's :meth:`ModelBundle.load`; exposed here so tests
    can round-trip a fitted model through disk.
    """
    baseline = BaselineModel.load(model_dir / "baseline.json")
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    calibrator = _load_calibrator(model_dir / "calibrator.json")
    metadata_path = model_dir / "metadata.json"
    payload = json.loads(metadata_path.read_text())

    def _mk_split(d: dict[str, float | int]) -> SplitMetrics:
        return SplitMetrics(
            log_loss=float(d["log_loss"]),
            brier=float(d["brier"]),
            accuracy=float(d["accuracy"]),
            n_rows=int(d["n_rows"]),
        )

    metadata = TrainingMetadata(
        feature_names=tuple(payload["feature_names"]),
        train=_mk_split(payload["train"]),
        val=_mk_split(payload["val"]),
        calibration=_mk_split(payload["calibration"]),
        test=_mk_split(payload["test"]),
        best_iteration=int(payload["best_iteration"]),
        seed=int(payload["seed"]),
    )
    return TrainResult(
        booster=booster,
        baseline=baseline,
        calibrator=calibrator,
        metadata=metadata,
    )
