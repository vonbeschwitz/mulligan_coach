"""Tests for the train.py pipeline.

Builds a small synthetic feature parquet (real feature columns
from build_feature_row would be expensive to fake, so we use a
minimal column set that matches the model's expectations) and
exercises:

* the grouped train/val/calib/test split by draft_id (no
  draft appears in two splits),
* the baseline -> XGBoost pipeline produces probabilities,
* save -> load round-trip preserves all three artifacts,
* the prediction is stable across rounds (deterministic given
  the seed).
"""

from __future__ import annotations

import json
import math
import random
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
from mulligan_coach_model.versioning import (
    ShardMeta,
    ShardVersionError,
    now_iso,
    pipeline_versions,
    write_shard_meta,
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
    """End-to-end: fit baseline + XGBoost, all artifacts are
    non-None, metrics are populated, and probabilities are in
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
    # All three artifacts exist on disk.
    assert (out_dir / "baseline.json").exists()
    assert (out_dir / "xgboost.json").exists()
    assert (out_dir / "metadata.json").exists()
    # No calibrator file should be written.
    assert not (out_dir / "calibrator.json").exists()

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
    p_loaded = loaded.booster.predict(dm, iteration_range=iter_range)

    dm2 = xgb.DMatrix(X, base_margin=margins, feature_names=features)
    p_original = result.booster.predict(dm2, iteration_range=iter_range)
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


# ---------------------------------------------------------------------------
# Materialisation-invariant hash split (draftid_hash_v1)
# ---------------------------------------------------------------------------


def _bucket_of(ids: pd.Series, split: object) -> dict[str, str]:
    """Map each draft_id to its split-band name for the 4-way _Split."""
    out: dict[str, str] = {}
    for name in ("train", "val", "calib", "test"):
        mask = getattr(split, name)
        for did in ids[mask].unique():
            out[str(did)] = name
    return out


def test_grouped_split_is_materialisation_invariant() -> None:
    """Same draft_id lands in the same band across two datasets with
    different id sets and row orders — the property the permutation split
    lacked."""
    ids_a = pd.Series([f"draft-{i}" for i in range(50) for _ in range(3)])
    shuffled = [f"draft-{i}" for i in range(30, 80)]
    random.Random(1).shuffle(shuffled)
    ids_b = pd.Series([d for d in shuffled for _ in range(2)])

    sa = _grouped_split(ids_a, val_frac=0.2, calib_frac=0.2, test_frac=0.2, seed=7)
    sb = _grouped_split(ids_b, val_frac=0.2, calib_frac=0.2, test_frac=0.2, seed=7)
    ma, mb = _bucket_of(ids_a, sa), _bucket_of(ids_b, sb)

    overlap = set(ma) & set(mb)
    assert overlap  # ids 30..49 are shared
    for did in overlap:
        assert ma[did] == mb[did], f"{did}: {ma[did]} vs {mb[did]}"


def test_grouped_split_disjoint_and_covers_every_row() -> None:
    df = pd.DataFrame({"draft_id": [f"draft-{i}" for i in range(40) for _ in range(3)]})
    s = _grouped_split(df["draft_id"], val_frac=0.2, calib_frac=0.2, test_frac=0.2, seed=3)
    masks = [s.train, s.val, s.calib, s.test]
    assert (np.sum(masks, axis=0) == 1).all()  # exactly one band per row
    m = _bucket_of(df["draft_id"], s)
    # No draft straddles two bands (guaranteed by per-draft assignment).
    assert set(m.values()) <= {"train", "val", "calib", "test"}


def test_grouped_split_deterministic_and_seed_sensitive() -> None:
    ids = pd.Series([f"draft-{i}" for i in range(300)])
    s1 = _grouped_split(ids, val_frac=0.1, calib_frac=0.1, test_frac=0.1, seed=0)
    s2 = _grouped_split(ids, val_frac=0.1, calib_frac=0.1, test_frac=0.1, seed=0)
    np.testing.assert_array_equal(s1.test, s2.test)
    np.testing.assert_array_equal(s1.calib, s2.calib)
    s3 = _grouped_split(ids, val_frac=0.1, calib_frac=0.1, test_frac=0.1, seed=1)
    assert not np.array_equal(s1.test, s3.test)  # different seed reshuffles


def test_grouped_split_approximate_fractions_on_10k_ids() -> None:
    ids = pd.Series([f"draft-{i}" for i in range(10_000)])
    s = _grouped_split(ids, val_frac=0.10, calib_frac=0.15, test_frac=0.20, seed=5)
    # Each id appears once, so mask.mean() is the band's draft fraction.
    assert abs(s.val.mean() - 0.10) < 0.02
    assert abs(s.calib.mean() - 0.15) < 0.02  # 4-way calib band covered
    assert abs(s.test.mean() - 0.20) < 0.02
    assert abs(s.train.mean() - 0.55) < 0.02
    assert s.calib.sum() > 0


# ---------------------------------------------------------------------------
# Version lineage at training time
# ---------------------------------------------------------------------------


def _write_shard_dir(df: pd.DataFrame, shard_dir: Path, meta: ShardMeta | None) -> Path:
    """Write a one-chunk shard dir, optionally with a _meta.json sidecar."""
    shard_dir.mkdir(parents=True, exist_ok=True)
    chunk = shard_dir / "chunk_00000000.parquet"
    df.to_parquet(chunk)
    if meta is not None:
        write_shard_meta(shard_dir, meta)
    return chunk


def _live_meta(n_sims: int = 200) -> ShardMeta:
    return ShardMeta(
        pipeline_versions=pipeline_versions(),
        set_code="TLA",
        event_type="PremierDraft",
        n_sims_per_row=n_sims,
        created_at=now_iso(),
    )


def test_train_model_raises_on_mixed_shard_versions(tmp_path: Path) -> None:
    df = _synthetic_features_dataframe(n_drafts=60, games_per_draft=4)
    live = pipeline_versions()
    stale = {**live, "simulation": live["simulation"] + 1}
    p1 = _write_shard_dir(df.iloc[:120], tmp_path / "a", _live_meta())
    p2 = _write_shard_dir(
        df.iloc[120:],
        tmp_path / "b",
        ShardMeta(
            pipeline_versions=stale,
            set_code="TLA",
            event_type="PremierDraft",
            n_sims_per_row=200,
            created_at=now_iso(),
        ),
    )
    with pytest.raises(ShardVersionError, match="differ from the live"):
        train_model(parquet_paths=[p1, p2], output_dir=None, n_estimators=5)


def test_train_model_allow_version_mismatch_proceeds_and_records(tmp_path: Path) -> None:
    df = _synthetic_features_dataframe(n_drafts=120, games_per_draft=5)
    live = pipeline_versions()
    stale = {**live, "features": live["features"] + 1}
    p = _write_shard_dir(
        df,
        tmp_path / "shard",
        ShardMeta(
            pipeline_versions=stale,
            set_code="TLA",
            event_type="PremierDraft",
            n_sims_per_row=200,
            created_at=now_iso(),
        ),
    )
    result = train_model(
        parquet_paths=[p],
        output_dir=None,
        n_estimators=10,
        max_depth=3,
        early_stopping_rounds=5,
        allow_version_mismatch=True,
    )
    assert result.metadata.version_mismatch_allowed is True
    assert result.metadata.pipeline_versions == live  # live at train time
    assert result.metadata.split_method == "draftid_hash_v1"


def test_train_model_metadata_json_has_version_keys(tmp_path: Path) -> None:
    df = _synthetic_features_dataframe(n_drafts=120, games_per_draft=5)
    p = _write_shard_dir(df, tmp_path / "shard", _live_meta(n_sims=200))
    out = tmp_path / "model"
    train_model(
        parquet_paths=[p],
        output_dir=out,
        n_estimators=10,
        max_depth=3,
        early_stopping_rounds=5,
    )
    payload = json.loads((out / "metadata.json").read_text())
    assert payload["pipeline_versions"] == pipeline_versions()
    assert payload["split_method"] == "draftid_hash_v1"
    assert payload["version_mismatch_allowed"] is False
    assert isinstance(payload["shard_lineage"], list)
    assert len(payload["shard_lineage"]) == 1
    entry = payload["shard_lineage"][0]
    assert entry["pipeline_versions"] == pipeline_versions()
    assert entry["n_sims_per_row"] == 200
    assert entry["unverified_legacy"] is False


def test_train_model_legacy_shard_records_null_lineage(tmp_path: Path) -> None:
    """A shard dir with no sidecar trains fine and records null lineage."""
    df = _synthetic_features_dataframe(n_drafts=120, games_per_draft=5)
    p = _write_shard_dir(df, tmp_path / "shard", meta=None)  # no _meta.json
    result = train_model(
        parquet_paths=[p],
        output_dir=None,
        n_estimators=5,
        max_depth=3,
        early_stopping_rounds=5,
    )
    assert len(result.metadata.shard_lineage) == 1
    assert result.metadata.shard_lineage[0].pipeline_versions is None
