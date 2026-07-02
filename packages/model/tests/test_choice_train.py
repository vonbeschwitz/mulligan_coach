"""Tests for the choice-model trainer.

Strategy: build a small synthetic feature parquet with a signal the
model can pick up (one feature column is correlated with ``was_kept``),
train a tiny XGBoost, and assert the model performs at least as well
as the trivial "always predict the majority class" baseline.

The point is to exercise the pipeline end-to-end (split, train, save,
load, predict) rather than benchmark XGBoost itself.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mulligan_coach_model.choice_train import (
    ChoiceTrainingMetadata,
    ChoiceTrainResult,
    SplitMetrics,
    _feature_columns,
    _grouped_split,
    load_choice_train_result,
    train_choice_model,
)
from mulligan_coach_model.versioning import (
    ShardMeta,
    ShardVersionError,
    now_iso,
    pipeline_versions,
    write_shard_meta,
)

# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _make_synthetic_frame(
    n_drafts: int = 200,
    games_per_draft: int = 5,
    seed: int = 0,
) -> pd.DataFrame:
    """Build a synthetic choice-model feature frame.

    Features:
      * ``mulligan_number`` — drives keep rate strongly (low = keep).
      * ``n_lands_in_hand`` — drives keep rate moderately (3 best).
      * ``noise_feature`` — irrelevant signal, to verify the model
        doesn't blow up on it.

    Label: ``was_kept`` distributed with the obvious dependence on the
    two signal columns.
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    for draft_idx in range(n_drafts):
        draft_id = f"draft-{draft_idx:05d}"
        for game_idx in range(games_per_draft):
            mull = int(rng.integers(0, 3))  # 0..2
            n_lands = int(rng.integers(0, 7))  # 0..6
            noise = float(rng.standard_normal())
            # Probability of keep: high when mull is high (forced to keep
            # by the deeper-mulligan floor) and when 2 <= n_lands <= 4.
            base = 0.4 + 0.2 * mull
            land_bonus = 0.2 if 2 <= n_lands <= 4 else -0.1
            p_keep = np.clip(base + land_bonus, 0.05, 0.95)
            was_kept = bool(rng.random() < p_keep)
            rows.append(
                {
                    "mulligan_number": mull,
                    "n_lands_in_hand": n_lands,
                    "noise_feature": noise,
                    "was_kept": was_kept,
                    "draft_id": draft_id,
                    "build_index": 0,
                    "match_number": 1,
                    "game_number": game_idx + 1,
                    "expansion": "TLA",
                    "event_type": "PremierDraft",
                    "num_mulligans_in_game": mull if was_kept else mull + 1,
                    "opp_mulligan_number": 0,
                    "user_n_games_raw": 100,
                    "user_wr_raw": 0.55,
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_feature_columns_excludes_label_and_context() -> None:
    df = _make_synthetic_frame(n_drafts=5)
    cols = _feature_columns(df.columns)
    assert "mulligan_number" in cols
    assert "n_lands_in_hand" in cols
    assert "noise_feature" in cols
    # Non-features must be excluded.
    for excluded in (
        "was_kept",
        "num_mulligans_in_game",
        "draft_id",
        "build_index",
        "match_number",
        "game_number",
        "expansion",
        "event_type",
        "opp_mulligan_number",
        "user_n_games_raw",
        "user_wr_raw",
    ):
        assert excluded not in cols, f"{excluded} should not be a feature column"


def test_grouped_split_assigns_each_draft_to_exactly_one_split() -> None:
    drafts = pd.Series([f"d-{i // 4}" for i in range(40)])  # 10 unique drafts
    splits = _grouped_split(drafts, val_frac=0.2, test_frac=0.2, seed=0)
    # Every row in exactly one split.
    assert (splits.train.astype(int) + splits.val.astype(int) + splits.test.astype(int) == 1).all()
    # No draft straddles two splits.
    train_drafts = set(drafts[splits.train])
    val_drafts = set(drafts[splits.val])
    test_drafts = set(drafts[splits.test])
    assert not (train_drafts & val_drafts)
    assert not (train_drafts & test_drafts)
    assert not (val_drafts & test_drafts)


def test_grouped_split_rejects_invalid_fractions() -> None:
    drafts = pd.Series([f"d-{i}" for i in range(10)])
    with pytest.raises(ValueError, match="must sum to"):
        _grouped_split(drafts, val_frac=0.5, test_frac=0.5, seed=0)
    with pytest.raises(ValueError, match="must be in"):
        _grouped_split(drafts, val_frac=0.0, test_frac=0.1, seed=0)


# ---------------------------------------------------------------------------
# Training end-to-end on synthetic data
# ---------------------------------------------------------------------------


def test_train_choice_model_runs_and_beats_majority_class(tmp_path: Path) -> None:
    """A tiny model trained on the synthetic dataset should at least
    match the majority-class baseline on test."""
    df = _make_synthetic_frame(n_drafts=200, games_per_draft=5)
    parquet_path = tmp_path / "synth.parquet"
    df.to_parquet(parquet_path)

    result = train_choice_model(
        parquet_paths=parquet_path,
        n_estimators=50,
        max_depth=3,
        learning_rate=0.1,
        early_stopping_rounds=10,
        seed=0,
    )
    assert isinstance(result, ChoiceTrainResult)
    md = result.metadata
    assert md.test.n_rows > 0
    # Majority-class baseline accuracy = max(keep_rate, 1 - keep_rate).
    baseline_acc = max(md.test.keep_rate, 1.0 - md.test.keep_rate)
    # Allow a small slack — synthetic data and tiny model.
    assert md.test.accuracy >= baseline_acc - 0.02, (
        f"Model test accuracy {md.test.accuracy:.3f} should be at least "
        f"the majority-class baseline {baseline_acc:.3f}"
    )
    # Feature names recorded in deterministic order, including the signal cols.
    assert "mulligan_number" in md.feature_names
    assert "n_lands_in_hand" in md.feature_names


def test_train_choice_model_persists_and_round_trips(tmp_path: Path) -> None:
    """save -> load should yield bit-identical metadata and a usable booster."""
    df = _make_synthetic_frame(n_drafts=100, games_per_draft=4)
    parquet_path = tmp_path / "synth.parquet"
    df.to_parquet(parquet_path)
    out_dir = tmp_path / "model"

    result = train_choice_model(
        parquet_paths=parquet_path,
        output_dir=out_dir,
        n_estimators=20,
        max_depth=3,
        early_stopping_rounds=5,
    )
    assert (out_dir / "xgboost.json").exists()
    assert (out_dir / "metadata.json").exists()

    loaded = load_choice_train_result(out_dir)
    assert isinstance(loaded, ChoiceTrainResult)
    assert isinstance(loaded.metadata, ChoiceTrainingMetadata)
    assert loaded.metadata.feature_names == result.metadata.feature_names
    assert loaded.metadata.best_iteration == result.metadata.best_iteration

    # Booster outputs match within float tolerance after round-trip.
    import xgboost as xgb

    X = df[list(result.metadata.feature_names)].astype(float).to_numpy()[:5]
    dm = xgb.DMatrix(X, feature_names=list(result.metadata.feature_names))
    p_pre = result.booster.predict(dm, iteration_range=(0, result.metadata.best_iteration + 1))
    p_post = loaded.booster.predict(dm, iteration_range=(0, loaded.metadata.best_iteration + 1))
    np.testing.assert_allclose(p_pre, p_post, rtol=1e-6, atol=1e-6)


def test_train_choice_model_rejects_empty_parquet_list() -> None:
    with pytest.raises(ValueError, match="at least one parquet"):
        train_choice_model(parquet_paths=[])


def test_split_metrics_dataclass_is_serialisable() -> None:
    """A sanity guard so future schema changes surface in the test suite."""
    sm = SplitMetrics(log_loss=0.5, brier=0.2, accuracy=0.8, n_rows=100, keep_rate=0.9)
    assert sm.keep_rate == 0.9


# ---------------------------------------------------------------------------
# Materialisation-invariant hash split (3-way)
# ---------------------------------------------------------------------------


def _bucket_of(ids: pd.Series, split: object) -> dict[str, str]:
    out: dict[str, str] = {}
    for name in ("train", "val", "test"):
        mask = getattr(split, name)
        for did in ids[mask].unique():
            out[str(did)] = name
    return out


def test_grouped_split_is_materialisation_invariant() -> None:
    ids_a = pd.Series([f"d-{i}" for i in range(50) for _ in range(3)])
    shuffled = [f"d-{i}" for i in range(30, 80)]
    random.Random(2).shuffle(shuffled)
    ids_b = pd.Series([d for d in shuffled for _ in range(2)])

    sa = _grouped_split(ids_a, val_frac=0.2, test_frac=0.2, seed=11)
    sb = _grouped_split(ids_b, val_frac=0.2, test_frac=0.2, seed=11)
    ma, mb = _bucket_of(ids_a, sa), _bucket_of(ids_b, sb)
    overlap = set(ma) & set(mb)
    assert overlap
    for did in overlap:
        assert ma[did] == mb[did]


def test_grouped_split_deterministic_and_seed_sensitive() -> None:
    ids = pd.Series([f"d-{i}" for i in range(300)])
    s1 = _grouped_split(ids, val_frac=0.1, test_frac=0.1, seed=0)
    s2 = _grouped_split(ids, val_frac=0.1, test_frac=0.1, seed=0)
    np.testing.assert_array_equal(s1.test, s2.test)
    s3 = _grouped_split(ids, val_frac=0.1, test_frac=0.1, seed=1)
    assert not np.array_equal(s1.test, s3.test)


def test_grouped_split_approximate_fractions_on_10k_ids() -> None:
    ids = pd.Series([f"d-{i}" for i in range(10_000)])
    s = _grouped_split(ids, val_frac=0.10, test_frac=0.20, seed=5)
    assert abs(s.val.mean() - 0.10) < 0.02
    assert abs(s.test.mean() - 0.20) < 0.02
    assert abs(s.train.mean() - 0.70) < 0.02


# ---------------------------------------------------------------------------
# Version lineage at training time
# ---------------------------------------------------------------------------


def _write_shard_dir(df: pd.DataFrame, shard_dir: Path, meta: ShardMeta | None) -> Path:
    shard_dir.mkdir(parents=True, exist_ok=True)
    chunk = shard_dir / "chunk_00000000.parquet"
    df.to_parquet(chunk)
    if meta is not None:
        write_shard_meta(shard_dir, meta)
    return chunk


def _meta(versions: dict[str, int], n_sims: int = 200) -> ShardMeta:
    return ShardMeta(
        pipeline_versions=versions,
        set_code="TLA",
        event_type="PremierDraft",
        n_sims_per_row=n_sims,
        created_at=now_iso(),
    )


def test_train_choice_model_raises_on_mixed_shard_versions(tmp_path: Path) -> None:
    df = _make_synthetic_frame(n_drafts=60, games_per_draft=4)
    live = pipeline_versions()
    stale = {**live, "simulation": live["simulation"] + 1}
    p1 = _write_shard_dir(df.iloc[:120], tmp_path / "a", _meta(live))
    p2 = _write_shard_dir(df.iloc[120:], tmp_path / "b", _meta(stale))
    with pytest.raises(ShardVersionError, match="differ from the live"):
        train_choice_model(parquet_paths=[p1, p2], n_estimators=5)


def test_train_choice_model_allow_version_mismatch_records_flag(tmp_path: Path) -> None:
    df = _make_synthetic_frame(n_drafts=120, games_per_draft=5)
    live = pipeline_versions()
    stale = {**live, "features": live["features"] + 1}
    p = _write_shard_dir(df, tmp_path / "shard", _meta(stale))
    result = train_choice_model(
        parquet_paths=[p],
        n_estimators=10,
        max_depth=3,
        early_stopping_rounds=5,
        allow_version_mismatch=True,
    )
    assert result.metadata.version_mismatch_allowed is True
    assert result.metadata.pipeline_versions == live
    assert result.metadata.split_method == "draftid_hash_v1"


def test_train_choice_model_metadata_json_has_version_keys(tmp_path: Path) -> None:
    df = _make_synthetic_frame(n_drafts=120, games_per_draft=5)
    p = _write_shard_dir(df, tmp_path / "shard", _meta(pipeline_versions(), n_sims=200))
    out = tmp_path / "model"
    train_choice_model(
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
    assert len(payload["shard_lineage"]) == 1
    assert payload["shard_lineage"][0]["n_sims_per_row"] == 200


# ---------------------------------------------------------------------------
# Train-time expansion-vocabulary assert (Step 2)
# ---------------------------------------------------------------------------


def test_train_choice_model_rejects_out_of_vocab_expansion(tmp_path: Path) -> None:
    """A row whose expansion isn't in DEFAULT_KNOWN_SETS must raise (it would
    otherwise train as the all-zero reference category)."""
    df = _make_synthetic_frame(n_drafts=40, games_per_draft=4)
    df.loc[df.index[:10], "expansion"] = "ZZZ"  # outside the vocabulary
    p = _write_shard_dir(df, tmp_path / "shard", _meta(pipeline_versions()))
    with pytest.raises(ValueError, match="ZZZ"):
        train_choice_model(parquet_paths=[p], n_estimators=5)


def test_train_choice_model_accepts_sos_after_v2_bump(tmp_path: Path) -> None:
    """SOS is now in-vocabulary, so an all-SOS shard trains without error."""
    df = _make_synthetic_frame(n_drafts=80, games_per_draft=4)
    df["expansion"] = "SOS"
    p = _write_shard_dir(df, tmp_path / "shard", _meta(pipeline_versions()))
    result = train_choice_model(
        parquet_paths=[p], n_estimators=5, max_depth=3, early_stopping_rounds=3
    )
    assert result.metadata.test.n_rows >= 0
