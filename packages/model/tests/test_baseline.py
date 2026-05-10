"""Tests for the saturated-cell logistic regression baseline."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from mulligan_coach_model import BaselineModel

# ---------------------------------------------------------------------------
# Synthetic training data helpers
# ---------------------------------------------------------------------------


def _synthetic_dataframe(
    *,
    cell_true_win_rates: dict[tuple[str, str, bool], float],
    opp_mull_effect: dict[int, float] | None = None,
    rows_per_cell: int = 1000,
    seed: int = 0,
) -> pd.DataFrame:
    """Build a synthetic training dataframe with known win rates.

    Each ``(wr_bucket, n_games_bucket, on_play)`` cell gets
    ``rows_per_cell`` rows, with ``won`` drawn Bernoulli at the
    specified rate. If ``opp_mull_effect`` is given, the win rate
    is shifted on the logit scale by the per-opp-mull value (so
    margin recovery can be tested independently).
    """
    rng = np.random.default_rng(seed)
    rows: list[dict[str, object]] = []
    opp_mull_values = list((opp_mull_effect or {0: 0.0}).keys())

    for (wr, ngames, on_play), base_p in cell_true_win_rates.items():
        for i in range(rows_per_cell):
            opp_mull = opp_mull_values[i % len(opp_mull_values)]
            shift = (opp_mull_effect or {}).get(opp_mull, 0.0)
            base_logit = math.log(base_p / (1 - base_p)) + shift
            p = 1.0 / (1.0 + math.exp(-base_logit))
            rows.append(
                {
                    "user_wr_bucket": wr,
                    "user_n_games_bucket": ngames,
                    "on_the_play": on_play,
                    "opp_mulligan_number": int(opp_mull),
                    "won": bool(rng.random() < p),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Fit + margin recovery
# ---------------------------------------------------------------------------


def test_fit_recovers_cell_margins_to_within_a_few_percent() -> None:
    """With 1000 rows/cell the L2-regularised baseline should recover
    each cell's empirical log-odds to within ~0.1 logit (≈ 2pp win-rate)."""
    cells = {
        ("<45%", "<10", True): 0.40,
        ("<45%", "<10", False): 0.38,
        ("55-60%", "500+", True): 0.62,
        ("55-60%", "500+", False): 0.58,
    }
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=1000)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    for key, true_p in cells.items():
        true_logit = math.log(true_p / (1 - true_p))
        # margin(... opp_mull=0) = cell_margin + opp_margins[0]. Sum.
        observed = model.margin(
            user_wr_bucket=key[0],
            user_n_games_bucket=key[1],
            on_the_play=key[2],
            opp_mulligan_number=0,
        )
        assert abs(observed - true_logit) < 0.2, (
            f"cell {key}: expected ~{true_logit:.3f}, got {observed:.3f}"
        )


def test_fit_recovers_opp_mulligan_additivity() -> None:
    """When opp_mull shifts the logit by a constant, the additive opp_mull
    coefficient should capture it (within the noise of 1000 rows/cell)."""
    cells = {("<45%", "<10", True): 0.50}
    opp_effect = {0: 0.0, 1: 0.3, 2: 0.6}
    df = _synthetic_dataframe(
        cell_true_win_rates=cells,
        opp_mull_effect=opp_effect,
        rows_per_cell=3000,  # more per-opp-mull rows for stable recovery
    )
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    # Compare DIFFERENCES so the absorbed cell intercept doesn't matter.
    delta_1 = model.opp_mulligan_margins[1] - model.opp_mulligan_margins[0]
    delta_2 = model.opp_mulligan_margins[2] - model.opp_mulligan_margins[0]
    assert abs(delta_1 - 0.3) < 0.1, f"opp_mull=1: got {delta_1:.3f}"
    assert abs(delta_2 - 0.6) < 0.1, f"opp_mull=2: got {delta_2:.3f}"


# ---------------------------------------------------------------------------
# Inference paths
# ---------------------------------------------------------------------------


def test_margin_with_unknown_user_buckets_uses_population_marginal() -> None:
    """Passing None for both user-bucket fields routes to the on_play
    marginal; the population marginal differs from any single cell."""
    cells = {
        ("<45%", "<10", True): 0.40,
        ("55-60%", "500+", True): 0.60,
    }
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=1000)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    unknown_margin = model.margin(
        user_wr_bucket=None,
        user_n_games_bucket=None,
        on_the_play=True,
        opp_mulligan_number=0,
    )
    # Population marginal should sit between the two cells (50/50 weight).
    low_cell = model.margin(
        user_wr_bucket="<45%",
        user_n_games_bucket="<10",
        on_the_play=True,
        opp_mulligan_number=0,
    )
    high_cell = model.margin(
        user_wr_bucket="55-60%",
        user_n_games_bucket="500+",
        on_the_play=True,
        opp_mulligan_number=0,
    )
    assert low_cell < unknown_margin < high_cell


def test_margin_with_unknown_opp_mulligan_uses_population_mean() -> None:
    cells = {("<45%", "<10", True): 0.50}
    opp_effect = {0: 0.0, 1: 0.4}
    df = _synthetic_dataframe(
        cell_true_win_rates=cells,
        opp_mull_effect=opp_effect,
        rows_per_cell=2000,
    )
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    expected_opp = model.population_mean_opp_mulligan_margin
    observed = model.margin(
        user_wr_bucket="<45%",
        user_n_games_bucket="<10",
        on_the_play=True,
        opp_mulligan_number=None,
    )
    cell_part = model.cell_margins[("<45%", "<10", True)]
    assert observed == pytest.approx(cell_part + expected_opp)


def test_margin_with_unknown_cell_falls_back_to_population_marginal() -> None:
    """A cell never seen at training time falls back to the on_play
    population marginal rather than blowing up."""
    cells = {("<45%", "<10", True): 0.45}
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    unknown = model.margin(
        user_wr_bucket=">=60%",  # never seen
        user_n_games_bucket="500+",
        on_the_play=True,
        opp_mulligan_number=0,
    )
    fallback = model.population_marginal_margins[True] + model.opp_mulligan_margins[0]
    assert unknown == pytest.approx(fallback)


def test_margin_with_unknown_opp_mulligan_value_falls_back_to_population_mean() -> None:
    cells = {("<45%", "<10", True): 0.50}
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    # opp_mull=7 is well outside the training range (only 0 was seen).
    observed = model.margin(
        user_wr_bucket="<45%",
        user_n_games_bucket="<10",
        on_the_play=True,
        opp_mulligan_number=7,
    )
    cell_part = model.cell_margins[("<45%", "<10", True)]
    expected_opp = model.population_mean_opp_mulligan_margin
    assert observed == pytest.approx(cell_part + expected_opp)


def test_margin_rejects_mixed_user_bucket_state() -> None:
    cells = {("<45%", "<10", True): 0.50}
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    with pytest.raises(ValueError, match="must both be set"):
        model.margin(
            user_wr_bucket="<45%",
            user_n_games_bucket=None,
            on_the_play=True,
            opp_mulligan_number=0,
        )
    with pytest.raises(ValueError, match="must both be set"):
        model.margin(
            user_wr_bucket=None,
            user_n_games_bucket="<10",
            on_the_play=True,
            opp_mulligan_number=0,
        )


# ---------------------------------------------------------------------------
# Monotonicity sanity check
# ---------------------------------------------------------------------------


def test_higher_wr_buckets_predict_higher_margin() -> None:
    """For the same n_games + on_play + opp_mull, a higher-WR bucket should
    yield a strictly higher margin (the bread-and-butter monotonicity check
    from the plan)."""
    cells = {
        ("<45%", "100-499", True): 0.42,
        ("45-50%", "100-499", True): 0.48,
        ("50-55%", "100-499", True): 0.52,
        ("55-60%", "100-499", True): 0.58,
    }
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=2000)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    margins = [
        model.margin(
            user_wr_bucket=wr,
            user_n_games_bucket="100-499",
            on_the_play=True,
            opp_mulligan_number=0,
        )
        for wr in ("<45%", "45-50%", "50-55%", "55-60%")
    ]
    assert margins == sorted(margins), f"margins not monotone: {margins}"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(tmp_path: Path) -> None:
    cells = {
        ("<45%", "<10", True): 0.40,
        ("55-60%", "500+", False): 0.62,
    }
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200)
    model = BaselineModel._fit_dataframe(df, l2_C=10.0)

    save_path = tmp_path / "baseline.json"
    model.save(save_path)
    loaded = BaselineModel.load(save_path)

    assert loaded.cell_margins == model.cell_margins
    assert loaded.opp_mulligan_margins == model.opp_mulligan_margins
    assert loaded.population_marginal_margins == model.population_marginal_margins
    assert loaded.population_mean_opp_mulligan_margin == pytest.approx(
        model.population_mean_opp_mulligan_margin
    )
    assert loaded.n_train_rows == model.n_train_rows

    # Compare a margin call to make sure inference is identical.
    for on_play in (True, False):
        for opp_mull in (None, 0):
            expected = model.margin(
                user_wr_bucket="<45%",
                user_n_games_bucket="<10",
                on_the_play=on_play,
                opp_mulligan_number=opp_mull,
            )
            actual = loaded.margin(
                user_wr_bucket="<45%",
                user_n_games_bucket="<10",
                on_the_play=on_play,
                opp_mulligan_number=opp_mull,
            )
            assert actual == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Fit-from-parquet (read path through .fit)
# ---------------------------------------------------------------------------


def test_fit_reads_parquet(tmp_path: Path) -> None:
    """The public .fit entry point reads parquet and concatenates shards.

    Verifies a real parquet round-trip (not just the in-memory _fit_dataframe).
    """
    cells = {("<45%", "<10", True): 0.40, ("55-60%", "500+", False): 0.60}
    df = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=500)
    p = tmp_path / "shard.parquet"
    df.to_parquet(p)

    model = BaselineModel.fit(p, l2_C=10.0)
    assert model.n_train_rows == len(df)
    # Sanity: monotone in WR bucket.
    a = model.margin(
        user_wr_bucket="<45%",
        user_n_games_bucket="<10",
        on_the_play=True,
        opp_mulligan_number=0,
    )
    b = model.margin(
        user_wr_bucket="55-60%",
        user_n_games_bucket="500+",
        on_the_play=False,
        opp_mulligan_number=0,
    )
    assert b > a


def test_fit_concatenates_multiple_shards(tmp_path: Path) -> None:
    cells = {("<45%", "<10", True): 0.50}
    df1 = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200, seed=1)
    df2 = _synthetic_dataframe(cell_true_win_rates=cells, rows_per_cell=200, seed=2)
    p1 = tmp_path / "shard1.parquet"
    p2 = tmp_path / "shard2.parquet"
    df1.to_parquet(p1)
    df2.to_parquet(p2)

    model = BaselineModel.fit([p1, p2], l2_C=10.0)
    assert model.n_train_rows == len(df1) + len(df2)


def test_fit_rejects_missing_columns() -> None:
    df = pd.DataFrame(
        {
            "user_wr_bucket": ["<45%"],
            "user_n_games_bucket": ["<10"],
            "on_the_play": [True],
            "won": [True],
            # opp_mulligan_number missing
        }
    )
    with pytest.raises(ValueError, match="opp_mulligan_number"):
        BaselineModel._fit_dataframe(df, l2_C=10.0)


def test_fit_rejects_empty_dataframe() -> None:
    df = pd.DataFrame(
        {
            col: []
            for col in (
                "user_wr_bucket",
                "user_n_games_bucket",
                "on_the_play",
                "opp_mulligan_number",
                "won",
            )
        }
    )
    with pytest.raises(ValueError, match="Empty"):
        BaselineModel._fit_dataframe(df, l2_C=10.0)


def test_fit_rejects_empty_path_list() -> None:
    with pytest.raises(ValueError, match="at least one parquet path"):
        BaselineModel.fit([], l2_C=10.0)
