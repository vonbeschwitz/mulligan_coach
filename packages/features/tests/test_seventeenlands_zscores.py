"""Tests for per-card z-scores of shrunk 17Lands win rates.

Inputs are constructed directly as ``ShrunkWinRates`` to keep the tests
focused on the z-score logic itself, decoupled from the shrinkage
pipeline. A small end-to-end test exercises the full
shrink → z-score chain to catch wiring errors.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from mulligan_coach_cards import SeventeenLandsStats
from mulligan_coach_features import (
    CardZScores,
    FormatWRDistribution,
    ShrunkWinRates,
    compute_format_wr_distribution,
    fold_card_name,
    shrink_stats,
    zscore_stats,
)


def _shrunk(
    mtga_id: int,
    name: str | None = None,
    oh: float | None = 0.55,
    gd: float | None = 0.55,
    gih: float | None = 0.55,
    weight: float = 0.8,
) -> ShrunkWinRates:
    """Construct a ShrunkWinRates with default fields filled in.

    ``name`` defaults to a unique ``"Card <mtga_id>"`` so callers that
    build several rows don't accidentally collide on the folded-name
    join key that :func:`zscore_stats` now uses.
    """
    return ShrunkWinRates(
        mtga_id=mtga_id,
        name=name if name is not None else f"Card {mtga_id}",
        shrunk_opening_hand_win_rate=oh,
        shrunk_drawn_win_rate=gd,
        shrunk_ever_drawn_win_rate=gih,
        weight=weight,
    )


def _stats(**overrides: Any) -> SeventeenLandsStats:
    """SeventeenLandsStats for the end-to-end test (mirrors the
    shrinkage-test fixture so the chain looks identical)."""
    base: dict[str, Any] = {
        "name": "Test Card",
        "mtga_id": 1,
        "expansion": "TST",
        "event_type": "PremierDraft",
        "color": "G",
        "rarity": "common",
        "types": ["Creature - Beast"],
        "layout": "standard",
        "seen_count": 1000,
        "avg_seen": 5.0,
        "pick_count": 2000,
        "avg_pick": 7.0,
        "game_count": 600,
        "pool_count": 1000,
        "play_rate": 0.5,
        "win_rate": 0.55,
        "opening_hand_game_count": 100,
        "opening_hand_win_rate": 0.55,
        "drawn_game_count": 200,
        "drawn_win_rate": 0.55,
        "ever_drawn_game_count": 300,
        "ever_drawn_win_rate": 0.55,
        "never_drawn_game_count": 200,
        "never_drawn_win_rate": 0.50,
        "drawn_improvement_win_rate": 0.05,
    }
    base.update(overrides)
    return SeventeenLandsStats.model_validate(base)


# ---------------------------------------------------------------------------
# compute_format_wr_distribution
# ---------------------------------------------------------------------------


def test_compute_distribution_simple_values() -> None:
    """Hand-checked mean and std: shrunk OH WRs of [0.50, 0.55, 0.60]
    give mean=0.55 and population std ≈ 0.0408."""
    rows = [
        _shrunk(mtga_id=1, oh=0.50),
        _shrunk(mtga_id=2, oh=0.55),
        _shrunk(mtga_id=3, oh=0.60),
    ]
    d = compute_format_wr_distribution(rows)
    assert d.n_cards == 3
    assert d.mean_oh_wr == pytest.approx(0.55, abs=1e-9)
    # Population std: sqrt(((0.05)^2 + 0 + (0.05)^2) / 3) ≈ 0.04082...
    assert d.std_oh_wr is not None
    assert d.std_oh_wr == pytest.approx(0.040824829, abs=1e-7)


def test_compute_distribution_uses_population_std_not_sample() -> None:
    """ddof=0, not ddof=1 — we describe the format as the whole pool,
    not as a sample. Two values 0.50 and 0.60: population std = 0.05,
    sample std would be ~0.0707."""
    rows = [_shrunk(mtga_id=1, oh=0.50), _shrunk(mtga_id=2, oh=0.60)]
    d = compute_format_wr_distribution(rows)
    assert d.std_oh_wr == pytest.approx(0.05, abs=1e-9)


def test_compute_distribution_skips_none_values() -> None:
    """Cards whose shrunk OH is None don't contribute to the mean/std
    (matches the shrinkage module's handling of every-card-None fields)."""
    rows = [
        _shrunk(mtga_id=1, oh=0.50),
        _shrunk(mtga_id=2, oh=None),
        _shrunk(mtga_id=3, oh=0.60),
    ]
    d = compute_format_wr_distribution(rows)
    # n_cards still counts all rows — n_cards is "format size", not
    # "how many had this field populated".
    assert d.n_cards == 3
    assert d.mean_oh_wr == pytest.approx(0.55, abs=1e-9)
    assert d.std_oh_wr == pytest.approx(0.05, abs=1e-9)


def test_compute_distribution_all_none_field_returns_none() -> None:
    """If every card has shrunk_X=None for some field, that field's
    mean and std are both None — z-scoring will then yield None per card."""
    rows = [
        _shrunk(mtga_id=1, oh=0.50, gih=None),
        _shrunk(mtga_id=2, oh=0.60, gih=None),
    ]
    d = compute_format_wr_distribution(rows)
    assert d.mean_oh_wr is not None
    assert d.mean_gih_wr is None
    assert d.std_gih_wr is None


def test_compute_distribution_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="no shrunk WR rows"):
        compute_format_wr_distribution([])


# ---------------------------------------------------------------------------
# zscore_stats
# ---------------------------------------------------------------------------


def test_zscore_basic_three_card_distribution() -> None:
    """For OH WRs [0.50, 0.55, 0.60]: mean=0.55, std≈0.04082.
    Z-scores are then ≈ [-1.2247, 0, +1.2247]."""
    rows = [
        _shrunk(mtga_id=1, name="Low", oh=0.50),
        _shrunk(mtga_id=2, name="Mid", oh=0.55),
        _shrunk(mtga_id=3, name="High", oh=0.60),
    ]
    out = zscore_stats(rows)
    assert out["Low"].z_opening_hand_win_rate == pytest.approx(-math.sqrt(1.5), abs=1e-7)
    assert out["Mid"].z_opening_hand_win_rate == pytest.approx(0.0, abs=1e-9)
    assert out["High"].z_opening_hand_win_rate == pytest.approx(math.sqrt(1.5), abs=1e-7)


def test_zscore_none_when_card_value_is_none() -> None:
    """A card whose shrunk_OH=None still gets a CardZScores entry but
    with z_opening_hand_win_rate=None. Its drawn/gih fields are scored
    independently — None on OH only zeroes out OH's z-score."""
    rows = [
        # Vary every WR field so std > 0 across the format. Otherwise
        # std_X=0 would make z_X=None regardless of the per-card value,
        # masking the OH-specific None-handling we're testing.
        _shrunk(mtga_id=1, name="A", oh=0.50, gd=0.51, gih=0.52),
        _shrunk(mtga_id=2, name="B", oh=None, gd=0.55, gih=0.56),
        _shrunk(mtga_id=3, name="C", oh=0.60, gd=0.59, gih=0.58),
    ]
    out = zscore_stats(rows)
    assert out["A"].z_opening_hand_win_rate is not None
    assert out["B"].z_opening_hand_win_rate is None
    # The card with OH=None still has an entry — None doesn't drop the row.
    assert out["B"].mtga_id == 2
    # gd / gih are independently scored; the OH=None doesn't propagate.
    assert out["B"].z_drawn_win_rate is not None
    assert out["B"].z_ever_drawn_win_rate is not None


def test_zscore_none_when_format_has_no_signal() -> None:
    """If the entire format has shrunk_GIH=None, every card's
    z_ever_drawn_win_rate is None (no reference distribution)."""
    rows = [
        _shrunk(mtga_id=1, name="A", oh=0.50, gih=None),
        _shrunk(mtga_id=2, name="B", oh=0.60, gih=None),
    ]
    out = zscore_stats(rows)
    assert all(out[name].z_ever_drawn_win_rate is None for name in ("A", "B"))
    # OH still works.
    assert out["A"].z_opening_hand_win_rate is not None


def test_zscore_zero_std_returns_none() -> None:
    """Degenerate case: every card has the exact same shrunk WR.
    std=0 → z-score is None (no spread to normalise against; would
    otherwise blow up to NaN)."""
    rows = [_shrunk(mtga_id=i, oh=0.55) for i in (1, 2, 3)]
    out = zscore_stats(rows)
    for i in (1, 2, 3):
        assert out[f"Card {i}"].z_opening_hand_win_rate is None


def test_zscore_accepts_precomputed_distribution() -> None:
    """Passing a precomputed FormatWRDistribution bypasses the inline
    computation. Useful for z-scoring a subset of the format against
    the full-format reference."""
    full = [_shrunk(mtga_id=i, oh=0.40 + 0.02 * i) for i in range(10)]
    distribution = compute_format_wr_distribution(full)
    # Z-score one specific card against that reference.
    out = zscore_stats([_shrunk(mtga_id=999, name="Target", oh=0.60)], distribution=distribution)
    expected_z = (0.60 - distribution.mean_oh_wr) / distribution.std_oh_wr  # type: ignore[operator]
    assert out["Target"].z_opening_hand_win_rate == pytest.approx(expected_z, abs=1e-9)


def test_zscore_distribution_round_trip_mean_and_std() -> None:
    """Sanity invariant: the z-scores of the SAME rows used to build
    the distribution should themselves have mean ≈ 0 and std ≈ 1
    (when no None values are involved)."""
    rows = [_shrunk(mtga_id=i, oh=0.40 + 0.01 * i) for i in range(30)]
    out = zscore_stats(rows)
    zs = [out[fold_card_name(r.name)].z_opening_hand_win_rate for r in rows]
    assert all(z is not None for z in zs)
    arr = [z for z in zs if z is not None]  # narrow for type-checker
    mean = sum(arr) / len(arr)
    var = sum((z - mean) ** 2 for z in arr) / len(arr)
    assert abs(mean) < 1e-9
    assert math.sqrt(var) == pytest.approx(1.0, abs=1e-9)


def test_zscore_keys_match_folded_name() -> None:
    """zscore_stats returns dict keyed by folded card name, matching
    shrink_stats — the arena_id-independent join key."""
    rows = [_shrunk(mtga_id=42, name="Alpha", oh=0.55), _shrunk(mtga_id=7, name="Beta", oh=0.60)]
    out = zscore_stats(rows)
    assert set(out.keys()) == {"Alpha", "Beta"}
    assert isinstance(out["Alpha"], CardZScores)


# ---------------------------------------------------------------------------
# End-to-end: shrink → z-score
# ---------------------------------------------------------------------------


def test_end_to_end_shrink_then_zscore() -> None:
    """A small synthetic format goes through compute_format_priors →
    shrink_stats → compute_format_wr_distribution → zscore_stats and
    the resulting z-scores are finite numbers for cards with raw WRs.
    """
    rows = []
    for i in range(20):
        pr = (i + 0.5) / 20
        gih = 0.40 + 0.30 * pr
        rows.append(
            _stats(
                name=f"Card {i}",
                mtga_id=100 + i,
                pick_count=2000,
                play_rate=pr,
                opening_hand_win_rate=gih - 0.005,
                drawn_win_rate=gih + 0.005,
                ever_drawn_win_rate=gih,
            )
        )
    shrunk = shrink_stats(rows)
    distribution = compute_format_wr_distribution(shrunk.values())
    zscores = zscore_stats(shrunk.values(), distribution=distribution)

    # Every card has finite OH z-score (shrunk OH is populated for all).
    for i in range(20):
        z = zscores[f"Card {i}"].z_opening_hand_win_rate
        assert z is not None
        assert math.isfinite(z)

    # The lowest-WR card has the lowest z-score; the highest-WR card has
    # the highest. Tests both ordering and that z-scoring preserves the
    # underlying WR's monotonicity.
    lows = zscores["Card 0"].z_opening_hand_win_rate
    highs = zscores["Card 19"].z_opening_hand_win_rate
    assert lows is not None and highs is not None
    assert lows < 0 < highs


def test_returned_type_is_format_wr_distribution() -> None:
    """Constructor + class identity check — saves a moment of confusion
    when someone fish for ``FormatPriors`` and gets z-distribution back."""
    rows = [_shrunk(mtga_id=1), _shrunk(mtga_id=2)]
    d = compute_format_wr_distribution(rows)
    assert isinstance(d, FormatWRDistribution)
