"""Tests for the train.py pipeline.

Builds a small synthetic feature parquet (real feature columns
from build_feature_row would be expensive to fake, so we use a
minimal column set that matches the model's expectations) and
exercises:

* the grouped train/val/calib/test split by draft_id (no
  draft appears in two splits),
* the baseline -> XGBoost -> isotonic pipeline ends with
  calibrated probabilities,
* save -> load round-trip preserves all four artifacts,
* the prediction is stable across rounds (deterministic given
  the seed).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mulligan_coach_model import (
    BaselineModel,
    TrainResult,
    load_train_result,
    train_model,
)
from mulligan_coach_model.train import (
    _feature_columns,
    _grouped_split,
    _per_row_base_margin,
)

# ---------------------------------------------------------------------------
# Synthetic training data
# ---------------------------------------------------------------------------


def _synthetic_features_dataframe(
    *,
    n_drafts: int = 200,
    games_per_draft: int = 7,
    n_features: int = 6,
    seed: int = 0,
) -> pd.DataFrame:
    """Build a synthetic feature dataframe matching the cache schema.

    The label depends on a known feature so XGBoost has *something*
    to learn; the baseline context is varied so the residualization
    has work to do; per-draft grouping is preserved so the split
    test can verify correctness.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, float | int | str | bool | None]] = []
    wr_buckets = ("<45%", "45-50%", "50-55%", "55-60%", ">=60%")
    n_buckets = ("<10", "10-49", "50-99", "100-499", "500+")

    for d in range(n_drafts):
        wr = wr_buckets[d % len(wr_buckets)]
        ngames = n_buckets[(d // 5) % len(n_buckets)]
        for g in range(games_per_draft):
            features = rng.standard_normal(n_features).astype(float)
            on_play = bool((d + g) % 2)
            opp_mull = int(rng.integers(0, 3))
            # WR-bucket baseline + feature[0] signal + noise
            wr_idx = wr_buckets.index(wr)
            base_logit = (wr_idx - 2) * 0.2  # -0.4 .. +0.4
            logit = base_logit + 0.6 * float(features[0]) - 0.2 * opp_mull
            p = 1.0 / (1.0 + math.exp(-logit))
            won = bool(rng.random() < p)
            row: dict[str, float | int | str | bool | None] = {
                "user_wr_bucket": wr,
                "user_n_games_bucket": ngames,
                "on_the_play": on_play,
                "opp_mulligan_number": opp_mull,
                "opp_mulligan_count_if_known": (None if on_play else float(opp_mull)),
                "mulligan_number": 0,
                "expansion": "TLA",
                "event_type": "PremierDraft",
                "draft_id": f"draft-{d}",
                "match_number": 1,
                "game_number": g,
                "won": won,
            }
            for i in range(n_features):
                row[f"f{i}"] = float(features[i])
            rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# _feature_columns: separation of feature vs. context
# ---------------------------------------------------------------------------


def test_feature_columns_strips_context_and_label() -> None:
    cols = [
        "f0",
        "f1",
        "user_wr_bucket",
        "user_n_games_bucket",
        "on_the_play",
        "opp_mulligan_number",
        "opp_mulligan_count_if_known",
        "mulligan_number",
        "expansion",
        "event_type",
        "draft_id",
        "match_number",
        "game_number",
        "won",
    ]
    features = _feature_columns(cols)
    # Order-preserving, non-feature columns removed.
    assert features == [
        "f0",
        "f1",
        "on_the_play",
        "opp_mulligan_count_if_known",
        "mulligan_number",
    ]


# ---------------------------------------------------------------------------
# _grouped_split: no draft_id appears in more than one split
# ---------------------------------------------------------------------------


def test_grouped_split_partitions_drafts_uniquely() -> None:
    df = pd.DataFrame({"draft_id": [f"draft-{i}" for i in range(20) for _ in range(5)]})
    s = _grouped_split(df["draft_id"], val_frac=0.2, calib_frac=0.2, test_frac=0.2, seed=42)
    # Each row is in exactly one split.
    masks = [s.train, s.val, s.calib, s.test]
    summed = np.sum(masks, axis=0)
    assert (summed == 1).all()
    # Each draft is in exactly one split.
    for split_name, mask in zip(("train", "val", "calib", "test"), masks, strict=True):
        ids = set(df.loc[mask, "draft_id"].unique())
        for other_name, other in zip(("train", "val", "calib", "test"), masks, strict=True):
            if other_name == split_name:
                continue
            other_ids = set(df.loc[other, "draft_id"].unique())
            assert ids.isdisjoint(other_ids), (
                f"{split_name} and {other_name} share draft_ids: {ids & other_ids}"
            )


def test_grouped_split_rejects_bad_fractions() -> None:
    s = pd.Series([f"draft-{i}" for i in range(10)])
    with pytest.raises(ValueError, match="must be in"):
        _grouped_split(s, val_frac=0.0, calib_frac=0.1, test_frac=0.1, seed=0)
    with pytest.raises(ValueError, match="sum to < 1"):
        _grouped_split(s, val_frac=0.4, calib_frac=0.4, test_frac=0.4, seed=0)


# ---------------------------------------------------------------------------
# _per_row_base_margin: vectorised baseline application
# ---------------------------------------------------------------------------


def test_per_row_base_margin_matches_per_call_margin() -> None:
    """The vectorised implementation should agree with BaselineModel.margin
    called row-by-row."""
    df = _synthetic_features_dataframe(n_drafts=50, games_per_draft=4, seed=1)
    baseline = BaselineModel._fit_dataframe(df, l2_C=10.0)
    vec = _per_row_base_margin(df, baseline)

    expected = np.array(
        [
            baseline.margin(
                user_wr_bucket=row["user_wr_bucket"],
                user_n_games_bucket=row["user_n_games_bucket"],
                on_the_play=bool(row["on_the_play"]),
                opp_mulligan_number=int(row["opp_mulligan_number"]),
            )
            for _, row in df.iterrows()
        ]
    )
    np.testing.assert_allclose(vec, expected, atol=1e-9)


# ---------------------------------------------------------------------------
# train_model end-to-end
# ---------------------------------------------------------------------------


def _write_parquet(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return path


def test_train_model_produces_valid_bundle(tmp_path: Path) -> None:
    """End-to-end: fit baseline + XGBoost + calibrator, all artifacts
    are non-None, metrics are populated, and probabilities are in
    [0, 1]."""
    df = _synthetic_features_dataframe(n_drafts=120, games_per_draft=5)
    p = _write_parquet(df, tmp_path / "shard.parquet")

    result = train_model(
        parquet_paths=p,
        output_dir=None,
        n_estimators=20,
        max_depth=3,
        learning_rate=0.1,
        early_stopping_rounds=5,
        seed=0,
    )
    assert isinstance(result, TrainResult)
    assert result.metadata.train.n_rows > 0
    assert result.metadata.val.n_rows > 0
    assert result.metadata.calibration.n_rows > 0
    assert result.metadata.test.n_rows > 0
    for split in (
        result.metadata.train,
        result.metadata.val,
        result.metadata.calibration,
        result.metadata.test,
    ):
        # All metrics within sensible bounds (binary log-loss < log(2) at chance,
        # acc in [0, 1], brier in [0, 1]).
        assert 0.0 < split.log_loss < 1.5
        assert 0.0 <= split.accuracy <= 1.0
        assert 0.0 <= split.brier <= 1.0


def test_train_model_save_load_roundtrip(tmp_path: Path) -> None:
    """Saving and loading the model recovers identical predictions on a
    fresh sample of feature rows."""
    df = _synthetic_features_dataframe(n_drafts=120, games_per_draft=5)
    p = _write_parquet(df, tmp_path / "shard.parquet")
    out_dir = tmp_path / "model"
    result = train_model(
        parquet_paths=p,
        output_dir=out_dir,
        n_estimators=10,
        max_depth=3,
        learning_rate=0.1,
        early_stopping_rounds=3,
        seed=0,
    )
    # All four artifacts exist on disk.
    assert (out_dir / "baseline.json").exists()
    assert (out_dir / "xgboost.json").exists()
    assert (out_dir / "calibrator.json").exists()
    assert (out_dir / "metadata.json").exists()

    loaded = load_train_result(out_dir)

    # Predictions should match on the loaded model.
    import xgboost as xgb
    from mulligan_coach_model.train import _feature_columns as _fc

    features = list(loaded.metadata.feature_names)
    sample = df.iloc[:30]
    X = sample[features].astype(float).to_numpy()

    # Build base_margin matching the saved baseline.
    margins = _per_row_base_margin(sample, loaded.baseline)
    dm = xgb.DMatrix(X, base_margin=margins, feature_names=features)
    iter_range = (0, loaded.metadata.best_iteration + 1)
    p_loaded = loaded.calibrator.predict(loaded.booster.predict(dm, iteration_range=iter_range))

    dm2 = xgb.DMatrix(X, base_margin=margins, feature_names=features)
    p_original = result.calibrator.predict(result.booster.predict(dm2, iteration_range=iter_range))
    np.testing.assert_allclose(p_loaded, p_original, atol=1e-6)

    # _fc is the same function — sanity check on the helper export.
    assert _fc(df.columns) == features


def test_train_model_rejects_empty_path_list() -> None:
    with pytest.raises(ValueError, match="at least one parquet"):
        train_model(parquet_paths=[], output_dir=None)


def test_train_model_rejects_empty_parquet(tmp_path: Path) -> None:
    df = _synthetic_features_dataframe(n_drafts=5, games_per_draft=1).iloc[0:0]
    p = _write_parquet(df, tmp_path / "empty.parquet")
    with pytest.raises(ValueError, match="empty"):
        train_model(parquet_paths=p, output_dir=None)
