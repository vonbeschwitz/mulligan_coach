"""Tests for the choice-model inference path.

Mirrors test_choice_train: train a tiny model on a synthetic frame,
then exercise the :class:`ChoiceModelBundle` load + predict path.

The full end-to-end :func:`predict_keep_probability` needs real
format_stats (shrunk + zscores) plus a working simulator over real
ParsedCards. We skip those tests unless TLA-set artifacts are
available locally, matching the pattern in test_choice_feature_matrix.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xgboost as xgb
from mulligan_coach_model.choice_inference import (
    ChoiceModelBundle,
    predict_keep_probability_from_feature_row,
)
from mulligan_coach_model.choice_train import train_choice_model


def _has_real_tla_stats() -> bool:
    try:
        from mulligan_coach_cards import load_parsed_cards, load_premier_draft_stats

        load_parsed_cards("TLA")
        load_premier_draft_stats("TLA")
    except Exception:
        return False
    return True


_HAS_REAL_TLA_STATS = _has_real_tla_stats()


def _train_tiny_model(tmp_path: Path) -> Path:
    """Train a tiny model on a synthetic frame and return the saved model dir."""
    rng = np.random.default_rng(0)
    rows: list[dict[str, object]] = []
    for draft_idx in range(80):
        draft_id = f"draft-{draft_idx:05d}"
        for game_idx in range(5):
            mull = int(rng.integers(0, 3))
            n_lands = int(rng.integers(0, 7))
            p_keep = float(
                np.clip(0.4 + 0.2 * mull + (0.2 if 2 <= n_lands <= 4 else -0.1), 0.05, 0.95)
            )
            was_kept = bool(rng.random() < p_keep)
            rows.append(
                {
                    "mulligan_number": mull,
                    "n_lands_in_hand": n_lands,
                    "noise_feature": float(rng.standard_normal()),
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
    df = pd.DataFrame(rows)
    parquet_path = tmp_path / "synth.parquet"
    df.to_parquet(parquet_path)
    out_dir = tmp_path / "model"
    train_choice_model(
        parquet_paths=parquet_path,
        output_dir=out_dir,
        n_estimators=20,
        max_depth=3,
        early_stopping_rounds=5,
    )
    return out_dir


# ---------------------------------------------------------------------------
# ChoiceModelBundle.load
# ---------------------------------------------------------------------------


def test_choice_model_bundle_load_round_trips(tmp_path: Path) -> None:
    model_dir = _train_tiny_model(tmp_path)
    bundle = ChoiceModelBundle.load(model_dir)
    assert isinstance(bundle, ChoiceModelBundle)
    assert "mulligan_number" in bundle.feature_names
    assert bundle.best_iteration >= 0


def test_choice_model_bundle_from_train_result(tmp_path: Path) -> None:
    """Bypassing disk: train_result -> bundle should also work."""
    from mulligan_coach_model.choice_train import load_choice_train_result

    model_dir = _train_tiny_model(tmp_path)
    result = load_choice_train_result(model_dir)
    bundle = ChoiceModelBundle.from_train_result(result)
    assert bundle.feature_names == result.metadata.feature_names
    assert bundle.best_iteration == result.metadata.best_iteration


# ---------------------------------------------------------------------------
# predict_keep_probability_from_feature_row
# ---------------------------------------------------------------------------


def test_predict_from_feature_row_returns_probability(tmp_path: Path) -> None:
    """Feed a feature dict directly; should return a value in [0, 1]."""
    model_dir = _train_tiny_model(tmp_path)
    bundle = ChoiceModelBundle.load(model_dir)
    row = {
        "mulligan_number": 0.0,
        "n_lands_in_hand": 3.0,
        "noise_feature": 0.0,
    }
    p = predict_keep_probability_from_feature_row(bundle, row)
    assert 0.0 <= p <= 1.0


def test_predict_from_feature_row_handles_missing_feature(tmp_path: Path) -> None:
    """A feature the bundle expects but the row doesn't carry -> NaN ->
    XGBoost handles it as missing. Must not raise."""
    model_dir = _train_tiny_model(tmp_path)
    bundle = ChoiceModelBundle.load(model_dir)
    p = predict_keep_probability_from_feature_row(bundle, {"mulligan_number": 0.0})
    assert 0.0 <= p <= 1.0


def test_predict_responsive_to_signal_features(tmp_path: Path) -> None:
    """Higher mulligan_number should monotonically increase predicted
    keep probability (since the synthetic generator made that the
    dominant signal)."""
    model_dir = _train_tiny_model(tmp_path)
    bundle = ChoiceModelBundle.load(model_dir)
    p_mull0 = predict_keep_probability_from_feature_row(
        bundle, {"mulligan_number": 0.0, "n_lands_in_hand": 3.0, "noise_feature": 0.0}
    )
    p_mull2 = predict_keep_probability_from_feature_row(
        bundle, {"mulligan_number": 2.0, "n_lands_in_hand": 3.0, "noise_feature": 0.0}
    )
    # With the synthetic generator, mull=2 should bias more toward keep.
    assert p_mull2 >= p_mull0, (
        f"Expected p_keep at mull=2 ({p_mull2:.3f}) >= mull=0 ({p_mull0:.3f})"
    )


# ---------------------------------------------------------------------------
# End-to-end (real simulator)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_REAL_TLA_STATS,
    reason="Needs real TLA cards/stats for the simulator + feature builder.",
)
def test_predict_keep_probability_end_to_end(tmp_path: Path) -> None:
    """Full pipeline: train a tiny model whose feature set matches the
    real build_feature_row output for TLA, then predict_keep_probability
    on a hand."""
    # The tiny model trained on a synthetic frame has a different feature
    # set than the real build_feature_row; we just need to verify the
    # call composes without exception and returns a probability.
    from mulligan_coach_cards import load_parsed_cards, load_premier_draft_stats
    from mulligan_coach_features import (
        compute_format_priors,
        compute_format_wr_distribution,
        shrink_stats,
        zscore_stats,
    )
    from mulligan_coach_model.choice_inference import predict_keep_probability
    from mulligan_coach_model.training_rows import _BASIC_LANDS, _make_basic_land

    model_dir = _train_tiny_model(tmp_path)
    bundle = ChoiceModelBundle.load(model_dir)

    # Build format stats.
    stats_lookup = load_premier_draft_stats("TLA")
    all_stats = list(stats_lookup.by_arena_id.values())
    priors = compute_format_priors(all_stats)
    shrunk = shrink_stats(all_stats, priors=priors)
    distribution = compute_format_wr_distribution(shrunk.values())
    zscores = zscore_stats(shrunk.values(), distribution=distribution)

    # Build hand + deck from basics + the first TLA non-basic card.
    parsed = list(load_parsed_cards("TLA"))
    a_card = parsed[0]
    basics = {n: _make_basic_land(n, c, s) for n, c, s in _BASIC_LANDS}
    hand = [basics["Forest"]] * 4 + [a_card] * 3
    deck = [basics["Forest"]] * 17 + [a_card] * 23

    p = predict_keep_probability(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=True,
        mulligan_number=0,
        opp_mulligan_number=None,
        event_type="PremierDraft",
        set_code="TLA",
        shrunk=shrunk,
        zscores=zscores,
        n_sims=20,
        seed=42,
    )
    assert 0.0 <= p <= 1.0


# Keep xgb import live so a refactor surfaces a missing dep here.
assert xgb is not None
