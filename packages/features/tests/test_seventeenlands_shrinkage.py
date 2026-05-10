"""Tests for sample-size shrinkage of 17Lands per-card win rates.

We construct ``SeventeenLandsStats`` instances directly — the shrinkage
function takes the typed model, not raw parquets, so no I/O is needed.
"""

from __future__ import annotations

from typing import Any

import pytest
from mulligan_coach_cards import SeventeenLandsStats
from mulligan_coach_features import (
    DEFAULT_K_BASE,
    FormatPriors,
    PlayRateBins,
    ShrunkWinRates,
    compute_format_priors,
    shrink_stats,
)


def _stats(**overrides: Any) -> SeventeenLandsStats:
    """Build a ``SeventeenLandsStats`` with sensible defaults.

    Defaults represent a "normal" mid-roll card: present in stats with
    moderate sample size and average win rates. Tests override only the
    fields they care about.
    """
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
        "pick_count": 500,
        "avg_pick": 7.0,
        "game_count": 600,
        "pool_count": 1000,
        "play_rate": 0.6,
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


def _format_with_spread(n: int = 50) -> list[SeventeenLandsStats]:
    """A small synthetic format where WR scales linearly with play_rate.

    play_rate spans [0.04, 0.96] (n evenly-spaced points), GIH WR =
    0.40 + 0.30 * play_rate (so high-play-rate cards win more, exactly
    the relationship the conditional-mean prior is designed to capture).
    OH and GD WR follow the same shape with small offsets so the per-WR
    means are distinguishable.
    """
    out: list[SeventeenLandsStats] = []
    for i in range(n):
        # Spread play_rate across (0, 1) without hitting the edges.
        pr = (i + 0.5) / n
        gih = 0.40 + 0.30 * pr
        out.append(
            _stats(
                name=f"Card {i}",
                mtga_id=1000 + i,
                pick_count=2000,
                play_rate=pr,
                opening_hand_win_rate=gih - 0.005,
                drawn_win_rate=gih + 0.005,
                ever_drawn_win_rate=gih,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Core shrinkage behaviour
# ---------------------------------------------------------------------------


def test_high_pick_count_gives_almost_no_shrinkage() -> None:
    """A card picked 10,000 times should land almost exactly on its raw WR."""
    others = _format_with_spread(n=30)
    target = _stats(
        name="Popular",
        mtga_id=99999,
        pick_count=10_000,
        play_rate=0.6,
        ever_drawn_win_rate=0.60,
    )
    shrunk = shrink_stats([*others, target])
    out = shrunk[99999]
    expected_weight = 10_000 / (10_000 + DEFAULT_K_BASE)
    assert out.weight == pytest.approx(expected_weight, abs=1e-9)
    assert out.weight > 0.95
    # Within 0.005 of the raw value — the small gap is the prior's pull.
    assert out.shrunk_ever_drawn_win_rate is not None
    assert abs(out.shrunk_ever_drawn_win_rate - 0.60) < 0.005


def test_low_pick_count_normal_play_rate_pulls_to_conditional_mean() -> None:
    """The 'rare card' case — small sample, average play_rate. Should be pulled
    most of the way to the matching decile mean, not stay at its raw value.

    We compute priors from the surrounding format only (not including the
    target) so the expected conditional mean is unambiguous.
    """
    others = _format_with_spread(n=30)
    # play_rate=0.5 in the synthetic format → conditional mean ≈ 0.55.
    target = _stats(
        name="Just Released",
        mtga_id=99999,
        pick_count=20,
        play_rate=0.5,
        ever_drawn_win_rate=0.30,
    )
    priors = compute_format_priors(others)
    shrunk = shrink_stats([target], priors=priors)
    out = shrunk[99999]
    expected_prior = priors.conditional_mean_gih_wr.lookup(0.5)
    assert expected_prior is not None
    assert out.weight < 0.05
    assert out.shrunk_ever_drawn_win_rate is not None
    # Shrunk value should be much closer to the prior than to the raw 0.30.
    assert abs(out.shrunk_ever_drawn_win_rate - expected_prior) < abs(
        out.shrunk_ever_drawn_win_rate - 0.30
    )
    # And concretely: it lands in the 0.50-0.58 band the synthetic prior covers.
    assert 0.50 < out.shrunk_ever_drawn_win_rate < 0.58


def test_high_pick_count_low_play_rate_keeps_raw_low_wr() -> None:
    """The headline 'bad card' case: heavily-picked card that gets sideboarded
    out and has a mediocre raw WR. Conditional prior is itself low (because
    other low-play-rate cards are also bad), so the shrunk value should stay
    near the raw WR, NOT jump to the overall set mean.
    """
    others = _format_with_spread(n=30)
    target = _stats(
        name="Bad Sideboard",
        mtga_id=99999,
        pick_count=2000,
        play_rate=0.05,
        ever_drawn_win_rate=0.40,
    )
    shrunk = shrink_stats([*others, target])
    out = shrunk[99999]

    # Overall mean of the synthetic format ≈ 0.55 (midpoint of 0.40-0.70).
    overall_mean = 0.55
    assert out.shrunk_ever_drawn_win_rate is not None
    # The shrunk value must not be pulled near the overall mean.
    assert abs(out.shrunk_ever_drawn_win_rate - 0.40) < abs(
        out.shrunk_ever_drawn_win_rate - overall_mean
    )
    # And concretely: the shrunk value stays in the low range.
    assert out.shrunk_ever_drawn_win_rate < 0.46


def test_no_stats_card_uses_play_rate_conditional_mean() -> None:
    """raw WR is None but play_rate is known → impute via the conditional mean."""
    others = _format_with_spread(n=30)
    target = _stats(
        name="No Stats Yet",
        mtga_id=99999,
        pick_count=80,
        play_rate=0.30,
        opening_hand_win_rate=None,
        drawn_win_rate=None,
        ever_drawn_win_rate=None,
    )
    shrunk = shrink_stats([*others, target])
    out = shrunk[99999]
    # Conditional mean for play_rate≈0.30 in the synthetic format: ~0.49.
    assert out.shrunk_ever_drawn_win_rate is not None
    assert 0.46 < out.shrunk_ever_drawn_win_rate < 0.52


def test_no_stats_no_play_rate_falls_back_to_overall_mean() -> None:
    """Neither raw WR nor play_rate available → use the format-wide mean."""
    others = _format_with_spread(n=30)
    target = _stats(
        name="Total Mystery",
        mtga_id=99999,
        pick_count=10,
        play_rate=None,
        opening_hand_win_rate=None,
        drawn_win_rate=None,
        ever_drawn_win_rate=None,
    )
    priors = compute_format_priors([*others, target])
    shrunk = shrink_stats([*others, target], priors=priors)
    out = shrunk[99999]
    assert priors.overall_mean_gih_wr is not None
    assert out.shrunk_ever_drawn_win_rate == pytest.approx(priors.overall_mean_gih_wr)


def test_pick_count_zero_returns_prior() -> None:
    """pick_count=0 → weight=0 → shrunk value is exactly the prior."""
    others = _format_with_spread(n=30)
    target = _stats(
        name="Never Picked",
        mtga_id=99999,
        pick_count=0,
        play_rate=0.5,
        ever_drawn_win_rate=0.30,
    )
    priors = compute_format_priors(others)
    shrunk = shrink_stats([target], priors=priors)
    out = shrunk[99999]
    expected_prior = priors.conditional_mean_gih_wr.lookup(0.5)
    assert expected_prior is not None
    assert out.weight == 0.0
    # With weight=0 the raw WR (0.30) is fully ignored; shrunk == prior exactly.
    assert out.shrunk_ever_drawn_win_rate == pytest.approx(expected_prior)


# ---------------------------------------------------------------------------
# Decile binning machinery
# ---------------------------------------------------------------------------


def test_play_rate_bins_lookup_recovers_synthetic_relationship() -> None:
    """With WR = play_rate, decile bins should reproduce the relationship.

    100 evenly-spread cards → each decile averages roughly its center
    play_rate, so lookup(p) ≈ p within bin granularity.
    """
    cards = [
        _stats(
            name=f"Card {i}",
            mtga_id=i,
            play_rate=(i + 0.5) / 100,
            ever_drawn_win_rate=(i + 0.5) / 100,
        )
        for i in range(100)
    ]
    priors = compute_format_priors(cards)
    bins = priors.conditional_mean_gih_wr
    # 10 means, 9 inner edges.
    assert len(bins.means) == 10
    assert len(bins.edges) == 9
    # Bin centers should track the input. Tolerance ~0.05 = bin half-width.
    for query in (0.05, 0.25, 0.50, 0.75, 0.95):
        result = bins.lookup(query)
        assert result is not None
        assert abs(result - query) < 0.06


def test_play_rate_bins_below_lowest_edge_returns_first_bin() -> None:
    cards = [
        _stats(
            name=f"Card {i}",
            mtga_id=i,
            play_rate=0.1 + i * 0.05,
            ever_drawn_win_rate=0.40 + i * 0.02,
        )
        for i in range(20)
    ]
    priors = compute_format_priors(cards)
    bins = priors.conditional_mean_gih_wr
    # Query well below any input play_rate → first-bin mean.
    result = bins.lookup(0.0)
    assert result == pytest.approx(bins.means[0])


def test_play_rate_bins_above_highest_edge_returns_last_bin() -> None:
    cards = [
        _stats(
            name=f"Card {i}",
            mtga_id=i,
            play_rate=0.1 + i * 0.05,
            ever_drawn_win_rate=0.40 + i * 0.02,
        )
        for i in range(20)
    ]
    priors = compute_format_priors(cards)
    bins = priors.conditional_mean_gih_wr
    result = bins.lookup(1.0)
    assert result == pytest.approx(bins.means[-1])


def test_empty_play_rate_bins_returns_none() -> None:
    """Constructed directly — represents the 'no usable data for this WR' case."""
    bins = PlayRateBins(edges=(), means=())
    assert bins.lookup(0.5) is None


# ---------------------------------------------------------------------------
# Format priors: aggregation + validation
# ---------------------------------------------------------------------------


def test_overall_mean_skips_none_values() -> None:
    cards = [
        _stats(name="A", mtga_id=1, opening_hand_win_rate=0.50),
        _stats(name="B", mtga_id=2, opening_hand_win_rate=0.55),
        _stats(name="C", mtga_id=3, opening_hand_win_rate=None),
        _stats(name="D", mtga_id=4, opening_hand_win_rate=0.60),
    ]
    priors = compute_format_priors(cards)
    assert priors.overall_mean_oh_wr == pytest.approx(0.55)


def test_compute_format_priors_rejects_mixed_format() -> None:
    cards = [
        _stats(name="A", mtga_id=1, expansion="TLA"),
        _stats(name="B", mtga_id=2, expansion="ECL"),
    ]
    with pytest.raises(ValueError, match="expansion"):
        compute_format_priors(cards)


def test_compute_format_priors_rejects_mixed_event_type() -> None:
    cards = [
        _stats(name="A", mtga_id=1, event_type="PremierDraft"),
        _stats(name="B", mtga_id=2, event_type="Sealed"),
    ]
    with pytest.raises(ValueError, match="event_type"):
        compute_format_priors(cards)


def test_shrink_stats_rejects_mixed_format_against_priors() -> None:
    """Shrink with priors from one format and a card from another → error."""
    cards = _format_with_spread(n=20)
    priors = compute_format_priors(cards)
    bad = _stats(name="Wrong Set", mtga_id=99, expansion="OTHER")
    with pytest.raises(ValueError, match="doesn't match priors"):
        shrink_stats([bad], priors=priors)


def test_compute_format_priors_rejects_empty_input() -> None:
    with pytest.raises(ValueError, match="no stats rows"):
        compute_format_priors([])


def test_negative_k_base_rejected() -> None:
    cards = _format_with_spread(n=10)
    with pytest.raises(ValueError, match="k_base"):
        shrink_stats(cards, k_base=-1.0)


def test_all_none_field_warns_and_falls_back() -> None:
    """If every card has WR=None for a field, the prior is None and shrunk
    values for that field equal the (also None) raw value. Should warn."""
    cards = [
        _stats(
            name=f"Card {i}",
            mtga_id=i,
            pick_count=100,
            play_rate=0.4,
            opening_hand_win_rate=None,
            drawn_win_rate=0.55,
            ever_drawn_win_rate=0.55,
        )
        for i in range(10)
    ]
    with pytest.warns(UserWarning, match="opening_hand_win_rate"):
        priors = compute_format_priors(cards)
    assert priors.overall_mean_oh_wr is None
    assert priors.conditional_mean_oh_wr.means == ()

    # Cards with raw_OH=None and no prior → shrunk_OH=None (no information).
    shrunk = shrink_stats(cards, priors=priors)
    for sw in shrunk.values():
        assert sw.shrunk_opening_hand_win_rate is None
        # Other fields still got real shrinkage.
        assert sw.shrunk_ever_drawn_win_rate is not None


def test_shrink_stats_returns_dict_keyed_by_mtga_id() -> None:
    cards = _format_with_spread(n=12)
    shrunk = shrink_stats(cards)
    assert set(shrunk.keys()) == {c.mtga_id for c in cards}
    for k, v in shrunk.items():
        assert isinstance(v, ShrunkWinRates)
        assert v.mtga_id == k


def test_priors_dataclass_holds_expected_metadata() -> None:
    """FormatPriors carries the (set, event_type) and contributing-row count."""
    cards = _format_with_spread(n=15)
    priors = compute_format_priors(cards)
    assert isinstance(priors, FormatPriors)
    assert priors.expansion == "TST"
    assert priors.event_type == "PremierDraft"
    assert priors.n_cards == 15
