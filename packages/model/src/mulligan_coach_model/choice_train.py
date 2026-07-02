"""XGBoost training entry point for the choice (keep/mull) model.

End-to-end pipeline:

1. Load one or more choice-model feature parquets (output of
   :func:`choice_feature_matrix.materialize_choice_feature_matrix`).
2. Grouped train / val / test split by ``draft_id`` — same rationale
   as the win model: one strong drafter has multiple games, so a
   row-level split would leak the player's tendencies across folds.
3. Fit XGBoost with early stopping on the validation split.
4. Evaluate on the test split: log-loss, Brier, accuracy.
5. Optionally persist ``xgboost.json`` + ``metadata.json`` to
   ``output_dir``.

Why no baseline residualization
--------------------------------

The win model uses a saturated-cell logistic baseline to strip
player-skill + opp-mulligan variance before XGBoost sees the row.
That made sense because game outcome is heavily confounded by player
skill — even a brilliant keep loses if the player misplays.

For the choice model the label is the player's *decision*, not the
outcome. After the player-skill filter (we drop known-below-average
players upstream in :mod:`choice_rows`), the remaining decisions
are a population of competent choices. The natural baseline — "what
fraction of decisions at this mulligan level are keeps" — is
heavily dominated by mulligan_number, which is already a feature
column XGBoost will pick up directly. Layering a separate baseline
would be redundant and introduce a context column the choice model
shouldn't depend on (player skill at *inference* time isn't queryable).

Why a 3-way split (no calibration set)
---------------------------------------

The win model carries a calibration split as a second held-out eval
point even though no post-hoc calibrator runs there. For the choice
model we keep things simple: train / val (for early stopping) / test
(for the final metric). If a future iteration wants to fit a Platt
or isotonic calibrator on top, we'll add it as a fourth split then.

Materialisation-invariant split
-------------------------------

Like the win model, ``_grouped_split`` assigns each ``draft_id`` via a
``sha256(seed:draft_id)`` hash (``SPLIT_METHOD`` = ``draftid_hash_v1``)
rather than a numpy permutation over the observed draft_ids, so
re-materialising a cache no longer reshuffles the split.

**Comparability note:** models trained with the old permutation split are
NOT split-comparable with hash-split models; each model's own held-out
metrics remain the only honest cross-model comparison.
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
from mulligan_coach_features import DEFAULT_KNOWN_SETS

from .versioning import (
    SPLIT_METHOD,
    ShardLineageEntry,
    check_training_lineage,
    draftid_hash_unit,
    gather_shard_lineage,
    pipeline_versions,
)

log = logging.getLogger(__name__)


# Columns in the choice-model parquet that are NOT features.
# Kept in sync with the cache schema in :mod:`choice_feature_matrix`.
_NON_FEATURE_COLUMNS = frozenset(
    {
        # Label.
        "was_kept",
        # Direct or near-direct leaks of the label. num_mulligans_in_game
        # equals mulligan_number on kept rows, so it would let the model
        # cheat at training and break at inference (we don't know the
        # final mulligan count when the decision is being made).
        "num_mulligans_in_game",
        # Identity / split context — not modelling signal.
        "expansion",
        "event_type",
        "draft_id",
        "build_index",
        "match_number",
        "game_number",
        # Per-row opponent context that the win model used for the
        # baseline. The conditional feature opp_mulligan_count_if_known
        # is the XGBoost-visible version; the raw column stays for audit.
        "opp_mulligan_number",
        # Per-player skill values — audit only. The choice model is
        # supposed to predict *good* players' decisions; the filter on
        # the dataset handles that, the model shouldn't condition on
        # the skill at inference (which we don't have).
        "user_n_games_raw",
        "user_wr_raw",
    }
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitMetrics:
    """Per-split predicted-vs-observed metrics."""

    log_loss: float
    brier: float
    accuracy: float
    n_rows: int
    keep_rate: float
    """Fraction of rows where ``was_kept=True``. The base rate to beat —
    a model that always predicts "keep" gets this accuracy."""


@dataclass(frozen=True)
class ChoiceTrainingMetadata:
    """Audit / provenance fields for a fitted choice model.

    The version-lineage fields default to "unknown / not recorded" so
    models trained before Step 1 still load
    (:func:`load_choice_train_result` tolerates their absence).
    """

    feature_names: tuple[str, ...]
    train: SplitMetrics
    val: SplitMetrics
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
    """How the train/val/test split was drawn (``draftid_hash_v1``);
    ``None`` for old permutation-split models."""


@dataclass(frozen=True)
class ChoiceTrainResult:
    """Bundle returned by :func:`train_choice_model`."""

    booster: xgb.Booster
    metadata: ChoiceTrainingMetadata


# ---------------------------------------------------------------------------
# Split + metrics helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Split:
    train: np.ndarray
    val: np.ndarray
    test: np.ndarray


def _grouped_split(
    draft_ids: pd.Series,
    *,
    val_frac: float,
    test_frac: float,
    seed: int,
) -> _Split:
    """Assign each unique ``draft_id`` to exactly one of three splits.

    Three splits — no calibration set — see module docstring. Uses the same
    materialisation-invariant hash assignment as the win model
    (``SPLIT_METHOD`` = ``draftid_hash_v1``): each draft's unit-interval
    position comes from
    :func:`mulligan_coach_model.versioning.draftid_hash_unit`, which depends
    only on ``(seed, draft_id)``. Bands are cumulative and ordered
    val -> test -> train::

        u < val_frac              -> val
        u < val_frac + test_frac  -> test
        else                      -> train

    Split sizes are binomial around the target fractions rather than exact.
    """
    for name, val in (("val", val_frac), ("test", test_frac)):
        if not 0.0 < val < 1.0:
            raise ValueError(f"{name}_frac must be in (0, 1); got {val!r}")
    total_eval = val_frac + test_frac
    if total_eval >= 1.0:
        raise ValueError(f"val_frac + test_frac must sum to < 1; got {total_eval}")

    val_cut = val_frac
    test_cut = val_frac + test_frac

    assignment: dict[object, str] = {}
    for did in draft_ids.unique():
        u = draftid_hash_unit(seed, did)
        if u < val_cut:
            assignment[did] = "val"
        elif u < test_cut:
            assignment[did] = "test"
        else:
            assignment[did] = "train"

    bucket = draft_ids.map(assignment)
    split = _Split(
        train=(bucket == "train").to_numpy(),
        val=(bucket == "val").to_numpy(),
        test=(bucket == "test").to_numpy(),
    )
    if not split.train.any():
        raise ValueError(
            "Hash split assigned no rows to training (too few drafts for these "
            "fractions). Add more data or reduce val/test fractions."
        )
    return split


def _feature_columns(df_columns: Iterable[str]) -> list[str]:
    """Return columns from the parquet that are XGBoost-visible features.

    Order is stable on the input iteration so the same parquet schema
    always produces the same ``feature_names`` list.
    """
    return [col for col in df_columns if col not in _NON_FEATURE_COLUMNS]


def _assert_expansions_in_vocabulary(expansions: pd.Series) -> None:
    """Refuse to train if any row's ``expansion`` is outside the feature
    builder's one-hot vocabulary (:data:`DEFAULT_KNOWN_SETS`).

    An unrepresented set has no ``set_code_<S>`` column, so it trains as
    the all-zero *reference category* — silently colliding with every
    other unknown set (the SOS/MSH bug this roadmap step kills). This is a
    *coverage* check, deliberately distinct from the Step 1 version check
    (which catches semantics *drift*): both must hold. There is no bypass
    flag — a silently-unrepresentable set is exactly the failure mode we
    want to make loud.
    """
    known = set(DEFAULT_KNOWN_SETS)
    unknown = expansions[~expansions.isin(known)]
    if unknown.empty:
        return
    # value_counts() sorts by frequency so the biggest offenders lead.
    detail = ", ".join(f"{exp!r} ({n} rows)" for exp, n in unknown.value_counts().items())
    raise ValueError(
        f"Training rows contain expansion(s) outside the feature one-hot "
        f"vocabulary DEFAULT_KNOWN_SETS={tuple(DEFAULT_KNOWN_SETS)}: {detail}. "
        f"An unrepresented set trains as the all-zero reference category, "
        f"silently colliding with other unknown sets. Fix by: (a) appending "
        f"the set to DEFAULT_KNOWN_SETS in packages/features (bumping "
        f"FEATURES_SEMANTICS_VERSION in the same PR), then (b) re-materialising "
        f"the cache, or patching existing shards' set_code_* one-hots with "
        f"packages/model/scripts/patch_set_onehots.py."
    )


def _compute_metrics(y_true: np.ndarray, p_pred: np.ndarray) -> SplitMetrics:
    """Log-loss / Brier / accuracy / keep-rate on one split."""
    eps = 1e-7
    p = np.clip(p_pred, eps, 1.0 - eps)
    log_loss = float(-(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)).mean())
    brier = float(((p - y_true) ** 2).mean())
    accuracy = float(((p >= 0.5).astype(int) == y_true).mean())
    keep_rate = float(y_true.mean())
    return SplitMetrics(
        log_loss=log_loss,
        brier=brier,
        accuracy=accuracy,
        n_rows=len(y_true),
        keep_rate=keep_rate,
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
        "keep_rate": m.keep_rate,
    }


def _save_metadata(metadata: ChoiceTrainingMetadata, path: Path) -> None:
    payload = {
        "feature_names": list(metadata.feature_names),
        "train": _metrics_to_dict(metadata.train),
        "val": _metrics_to_dict(metadata.val),
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
# Top-level entry point
# ---------------------------------------------------------------------------


def train_choice_model(
    *,
    parquet_paths: Sequence[Path] | Path,
    output_dir: Path | None = None,
    val_frac: float = 0.10,
    test_frac: float = 0.10,
    n_estimators: int = 500,
    max_depth: int = 6,
    learning_rate: float = 0.05,
    early_stopping_rounds: int = 20,
    seed: int = 0,
    allow_version_mismatch: bool = False,
) -> ChoiceTrainResult:
    """End-to-end XGBoost training on choice (keep/mull) labels.

    Parameters
    ----------
    parquet_paths:
        One or more choice-model feature parquets. Multiple shards are
        concatenated, so a multi-format model can be fit in a single call.
    output_dir:
        When provided, ``xgboost.json`` + ``metadata.json`` are written
        here. Created if missing. ``None`` skips persistence (useful in
        tests that only inspect the in-memory result).
    val_frac, test_frac:
        Draft-grouped fractions for the two held-out splits. Training
        gets the remainder (``1 - val - test``). Default 80/10/10. Each
        must be in ``(0, 1)`` and they must sum to ``< 1``.
    n_estimators, max_depth, learning_rate, early_stopping_rounds:
        XGBoost hyperparameters. Starting defaults match the win model;
        tune via validation log-loss on real data.
    seed:
        Controls the draft assignment and the XGBoost RNG.
    allow_version_mismatch:
        When ``False`` (default), raise :class:`ShardVersionError` if any
        training shard was built under pipeline versions differing from the
        live code (the choice_v7 incident — mixed simulator versions). Pass
        ``True`` to train on the mix anyway; recorded in ``metadata.json``
        as ``version_mismatch_allowed``. Legacy shards with no version
        sidecar only warn.
    """
    paths = [parquet_paths] if isinstance(parquet_paths, Path) else list(parquet_paths)
    if not paths:
        raise ValueError("Need at least one parquet path to fit.")

    # Version lineage: read each shard's _meta.json and refuse to mix
    # mismatched simulator/feature semantics unless explicitly allowed.
    shard_lineage = gather_shard_lineage(paths)
    check_training_lineage(shard_lineage, allow_version_mismatch=allow_version_mismatch)

    log.info("Loading %d choice-feature parquet shard(s)", len(paths))
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    if len(df) == 0:
        raise ValueError("Loaded an empty feature dataframe; nothing to train on.")
    log.info(
        "Loaded %d total rows (keep_rate=%.3f)",
        len(df),
        float(df["was_kept"].astype(int).mean()),
    )

    # Coverage check (Step 2): every row's set must be representable in the
    # one-hot vocabulary, or it silently trains as the reference category.
    _assert_expansions_in_vocabulary(df["expansion"])

    feature_names = _feature_columns(df.columns)
    if not feature_names:
        raise ValueError(
            "No feature columns found after stripping non-feature columns; "
            "the parquet schema may have changed."
        )

    splits = _grouped_split(
        df["draft_id"],
        val_frac=val_frac,
        test_frac=test_frac,
        seed=seed,
    )
    log.info(
        "Split rows: train=%d val=%d test=%d",
        int(splits.train.sum()),
        int(splits.val.sum()),
        int(splits.test.sum()),
    )

    X = df[feature_names].astype(float).to_numpy()
    y = df["was_kept"].astype(int).to_numpy()

    dtrain = xgb.DMatrix(X[splits.train], label=y[splits.train], feature_names=feature_names)
    dval = xgb.DMatrix(X[splits.val], label=y[splits.val], feature_names=feature_names)
    dtest = xgb.DMatrix(X[splits.test], label=y[splits.test], feature_names=feature_names)

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
    iter_range = (0, best_iteration + 1)
    p_train = booster.predict(dtrain, iteration_range=iter_range)
    p_val = booster.predict(dval, iteration_range=iter_range)
    p_test = booster.predict(dtest, iteration_range=iter_range)

    metadata = ChoiceTrainingMetadata(
        feature_names=tuple(feature_names),
        train=_compute_metrics(y[splits.train], p_train),
        val=_compute_metrics(y[splits.val], p_val),
        test=_compute_metrics(y[splits.test], p_test),
        best_iteration=best_iteration,
        seed=seed,
        pipeline_versions=pipeline_versions(),
        shard_lineage=tuple(shard_lineage),
        version_mismatch_allowed=allow_version_mismatch,
        split_method=SPLIT_METHOD,
    )
    log.info(
        "Done. test_log_loss=%.4f test_brier=%.4f test_accuracy=%.4f keep_rate=%.3f",
        metadata.test.log_loss,
        metadata.test.brier,
        metadata.test.accuracy,
        metadata.test.keep_rate,
    )

    result = ChoiceTrainResult(booster=booster, metadata=metadata)
    if output_dir is not None:
        save_choice_train_result(result, output_dir)
    return result


def save_choice_train_result(result: ChoiceTrainResult, output_dir: Path) -> None:
    """Write a :class:`ChoiceTrainResult` to disk under ``output_dir``.

    Two files (no baseline.json — the choice model doesn't use one):

    * ``xgboost.json`` — booster in its native JSON format.
    * ``metadata.json`` — feature names + per-split metrics.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    result.booster.save_model(str(output_dir / "xgboost.json"))
    _save_metadata(result.metadata, output_dir / "metadata.json")
    log.info("Saved choice-model artifacts to %s", output_dir)


def load_choice_train_result(model_dir: Path) -> ChoiceTrainResult:
    """Inverse of :func:`save_choice_train_result`."""
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
            keep_rate=float(d["keep_rate"]),
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
    metadata = ChoiceTrainingMetadata(
        feature_names=tuple(payload["feature_names"]),
        train=_mk_split(payload["train"]),
        val=_mk_split(payload["val"]),
        test=_mk_split(payload["test"]),
        best_iteration=int(payload["best_iteration"]),
        seed=int(payload["seed"]),
        pipeline_versions=pipeline_versions_loaded,
        shard_lineage=shard_lineage,
        version_mismatch_allowed=bool(payload.get("version_mismatch_allowed", False)),
        split_method=payload.get("split_method"),
    )
    return ChoiceTrainResult(booster=booster, metadata=metadata)
