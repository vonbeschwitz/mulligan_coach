"""Tests for the 196-feature XGBoost feature builder.

The strategy:

* Each sub-builder (deck / hand / simulation / context) gets focused
  unit tests with hand-crafted inputs and known expected outputs.
* :func:`build_feature_row` gets a shape/integration test that
  exercises the full path through a simulated game.

Real simulation is exercised — the simulator is fast enough that a
50-game run inside a test is well under a second.
"""

from __future__ import annotations

import math

import _factories as f  # type: ignore[import-not-found]
import pytest
from mulligan_coach_cards import ParsedCard
from mulligan_coach_features import (
    DEFAULT_KNOWN_EVENT_TYPES,
    DEFAULT_KNOWN_SETS,
    CardZScores,
    ShrunkWinRates,
    build_context_features,
    build_deck_features,
    build_feature_row,
    build_hand_features,
    build_simulation_features,
)
from mulligan_coach_simulation import GameLevelStats, simulate
from mulligan_coach_simulation.stats import AggregateStats, CardStats

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _empty_shrunk() -> dict[str, ShrunkWinRates]:
    """Empty stats — exercises the "no 17Lands data" path."""
    return {}


def _empty_zscores() -> dict[str, CardZScores]:
    return {}


def _empty_aggregate(n_runs: int = 1, on_the_play: bool = True) -> AggregateStats:
    """Aggregate with no per-card data — used for tests that don't
    need a real simulation."""
    return AggregateStats(
        n_runs=n_runs,
        seed=None,
        on_the_play=on_the_play,
    )


# ---------------------------------------------------------------------------
# Context features (3 / 7 columns)
# ---------------------------------------------------------------------------


def test_context_on_the_play_flag() -> None:
    out = build_context_features(on_the_play=True, event_type="PremierDraft", set_code="TLA")
    assert out["on_the_play"] == 1.0
    out = build_context_features(on_the_play=False, event_type="PremierDraft", set_code="TLA")
    assert out["on_the_play"] == 0.0


def test_context_one_hot_event_and_set() -> None:
    out = build_context_features(on_the_play=True, event_type="Sealed", set_code="TLA")
    assert out["event_type_Sealed"] == 1.0
    assert out["event_type_PremierDraft"] == 0.0
    assert out["set_code_TLA"] == 1.0
    assert out["set_code_TMT"] == 0.0


def test_context_unknown_event_and_set_zero_columns() -> None:
    """Unknown vocab values produce all-zero columns; no error.

    Critical for inference time when a new set ships before retraining.
    """
    out = build_context_features(on_the_play=True, event_type="UnknownEvent", set_code="NEW")
    assert all(out[f"event_type_{et}"] == 0.0 for et in DEFAULT_KNOWN_EVENT_TYPES)
    assert all(out[f"set_code_{sc}"] == 0.0 for sc in DEFAULT_KNOWN_SETS)


def test_context_column_count() -> None:
    """Default vocab → 9 columns: 1 on_the_play + 3 event + 5 set (v2)."""
    out = build_context_features(on_the_play=True, event_type="PremierDraft", set_code="TMT")
    assert len(out) == 1 + len(DEFAULT_KNOWN_EVENT_TYPES) + len(DEFAULT_KNOWN_SETS)


def test_context_emits_six_set_one_hots() -> None:
    """The current vocabulary emits exactly six ``set_code_*`` columns
    (v2 bump added SOS + MSH; v4 bump added HOB)."""
    out = build_context_features(on_the_play=True, event_type="PremierDraft", set_code="TLA")
    set_cols = sorted(k for k in out if k.startswith("set_code_"))
    assert set_cols == sorted(f"set_code_{s}" for s in DEFAULT_KNOWN_SETS)
    assert len(set_cols) == 6


def test_context_newer_sets_one_hot_correctly() -> None:
    """SOS / MSH (added in the 1->2 bump) and HOB (3->4 bump) each light
    up their own column and zero every other set column — not the
    all-zero reference category unknown sets get."""
    for set_code in ("SOS", "MSH", "HOB"):
        out = build_context_features(on_the_play=True, event_type="PremierDraft", set_code=set_code)
        assert out[f"set_code_{set_code}"] == 1.0
        others = [f"set_code_{s}" for s in DEFAULT_KNOWN_SETS if s != set_code]
        assert all(out[o] == 0.0 for o in others)


def test_context_unknown_set_all_zero() -> None:
    """A set outside the vocabulary still maps to all-zero one-hots
    (the reference-category fallback for genuinely unknown sets)."""
    out = build_context_features(on_the_play=True, event_type="PremierDraft", set_code="ZZZ")
    assert all(out[f"set_code_{s}"] == 0.0 for s in DEFAULT_KNOWN_SETS)


# ---------------------------------------------------------------------------
# Deck-level features (16)
# ---------------------------------------------------------------------------


def _simple_deck() -> list[ParsedCard]:
    """A 40-card mono-green deck: 17 lands + 23 spells (mostly creatures
    + 2 burn + 2 cantrips). The pct features have hand-computable values.
    """
    deck: list[ParsedCard] = []
    deck.extend(f.forest() for _ in range(17))
    deck.extend(f.vanilla_creature(f"Bear{i}", "{1}{G}") for i in range(10))  # MV 2 (10)
    deck.extend(f.vanilla_creature(f"Knight{i}", "{2}{G}") for i in range(5))  # MV 3 (5)
    deck.extend(f.vanilla_creature(f"Wyvern{i}", "{3}{G}") for i in range(2))  # MV 4 (2)
    deck.extend(f.vanilla_creature(f"Dragon{i}", "{4}{G}{G}") for i in range(2))  # MV 6 (2)
    deck.extend(f.burn_spell(f"Bolt{i}", "{R}") for i in range(2))  # MV 1, removal (2)
    deck.extend(f.cantrip(f"Opt{i}", "{U}") for i in range(2))  # MV 1, draw (2)
    assert len(deck) == 40
    return deck


def test_deck_features_count_is_16() -> None:
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    assert len(out) == 16


def test_deck_pct_lands_uses_full_deck() -> None:
    """``pct_lands_in_deck`` is over total deck size (not nonland count)."""
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    assert out["pct_lands_in_deck"] == pytest.approx(17 / 40)


def test_deck_pct_creatures_and_removal() -> None:
    """Both denominators are nonland count (23)."""
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    # 19 creatures, 2 burn (removal), 23 nonland.
    assert out["pct_creatures_in_deck"] == pytest.approx(19 / 23)
    assert out["pct_removal_in_deck"] == pytest.approx(2 / 23)


def test_deck_curve_buckets_sum_to_one() -> None:
    """The four spell-MV buckets partition the nonland deck."""
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    total = (
        out["pct_spells_mv_le_2_in_deck"]
        + out["pct_spells_mv_eq_3_in_deck"]
        + out["pct_spells_mv_4_5_in_deck"]
        + out["pct_spells_mv_ge_6_in_deck"]
    )
    assert total == pytest.approx(1.0)


def test_deck_creature_curve_individual_buckets() -> None:
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    # MV 2: 10 / 23. MV 3: 5 / 23. MV 4-5: 2 / 23. MV ≥6: 2 / 23.
    assert out["pct_creatures_mv_le_2_in_deck"] == pytest.approx(10 / 23)
    assert out["pct_creatures_mv_eq_3_in_deck"] == pytest.approx(5 / 23)
    assert out["pct_creatures_mv_4_5_in_deck"] == pytest.approx(2 / 23)
    assert out["pct_creatures_mv_ge_6_in_deck"] == pytest.approx(2 / 23)


def test_deck_color_counts() -> None:
    """Mono-green creature core + 2 burn (R) + 2 cantrips (U) →
    G is required by 19, U by 2, R by 2. n_main = colors > 3 = 1.
    n_total = colors > 0 = 3."""
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    assert out["n_main_colors_in_deck"] == 1.0
    assert out["n_total_colors_in_deck"] == 3.0


def test_deck_avg_wr_zero_when_no_stats() -> None:
    """Without any shrunk data, the avg WR features are 0.0 (safe default)."""
    out = build_deck_features(_simple_deck(), shrunk=_empty_shrunk())
    assert out["avg_oh_wr_of_spells"] == 0.0
    assert out["avg_gd_wr_of_spells"] == 0.0
    assert out["avg_gih_wr_of_spells"] == 0.0


def test_deck_avg_wr_uses_name_join() -> None:
    """When two cards' names match shrunk entries, the avg WR is over
    those values. The join is by name (arena_id-independent), so a card
    with no ratings entry is skipped even if it has an arena_id."""
    deck = [
        f.forest(),
        f.vanilla_creature("A", "{G}", arena_id=1),
        f.vanilla_creature("B", "{1}{G}", arena_id=2),
        # No ratings entry for "C" → never joins; skipped in avg.
        f.vanilla_creature("C", "{2}{G}"),
    ]
    shrunk = {
        "A": f.make_shrunk("A", oh=0.60, gd=0.55, gih=0.58),
        "B": f.make_shrunk("B", oh=0.50, gd=0.45, gih=0.48),
    }
    out = build_deck_features(deck, shrunk=shrunk)
    assert out["avg_oh_wr_of_spells"] == pytest.approx(0.55)
    assert out["avg_gd_wr_of_spells"] == pytest.approx(0.50)
    assert out["avg_gih_wr_of_spells"] == pytest.approx(0.53)


# ---------------------------------------------------------------------------
# Hand-level features (72)
# ---------------------------------------------------------------------------


def test_hand_features_count_is_72() -> None:
    """13 basic + 42 role-by-MV + 11 performance + 4 hand-quality + 2 color = 72."""
    hand = [f.forest(), f.vanilla_creature("Bear", "{1}{G}")]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    assert len(out) == 72


def test_hand_basic_counts() -> None:
    hand = [
        f.forest(),
        f.forest(),
        f.nonbasic_dual("Dual", "R", "G"),
        f.vanilla_creature("Cub", "{G}"),  # MV 1
        f.vanilla_creature("Bear", "{1}{G}"),  # MV 2
        f.vanilla_creature("Knight", "{2}{G}"),  # MV 3
        f.ramp_sorcery(),  # MV 3 ramp (Cultivate)
    ]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=1,
    )
    assert out["mulligan_number"] == 1.0
    assert out["n_lands_in_hand"] == 3.0
    assert out["n_nonbasic_lands_in_hand"] == 1.0
    assert out["n_spells_in_hand"] == 4.0
    assert out["n_ramp_spells_in_hand"] == 1.0  # Cultivate
    assert out["n_creatures_mv_le_2_in_hand"] == 2.0  # Cub, Bear
    assert out["n_creatures_mv_3_in_hand"] == 1.0  # Knight
    assert out["n_creatures_mv_4_in_hand"] == 0.0
    assert out["n_creatures_mv_ge_6_in_hand"] == 0.0


def test_hand_multi_modal_and_mv6_split() -> None:
    """Two MV≥6 spells: one cycler, one no-alt-mode. They split correctly."""
    hand = [
        f.cycler("Heavy Cycler", "{5}{G}", "{1}"),  # CMC 6, has alt mode
        f.vanilla_creature("Big Beast", "{4}{G}{G}"),  # CMC 6, no alt mode
    ]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    assert out["n_multi_modal_spells_in_hand"] == 1.0
    assert out["n_spells_mv_ge_6_with_alt_mode_in_hand"] == 1.0
    assert out["n_spells_mv_ge_6_no_alt_mode_in_hand"] == 1.0


def test_hand_role_by_mv_grid_count() -> None:
    """42 role-by-MV features: 14 roles x 3 buckets. Verify by key count.

    The "creatures_mv_3_in_hand" basic-section feature uses the same
    ``_mv_3_`` substring as a role-by-MV key, so we restrict to role
    names known to be in the role-by-MV grid (none of which is "creatures").
    """
    role_names = [
        "removal_destroy_or_exile",
        "burn",
        "bounce",
        "top_library",
        "removal_aura",
        "punch_fight",
        "combat_trick",
        "pump_aura",
        "equipment",
        "vehicle",
        "saga",
        "class_card",
        "planeswalker",
        "card_draw_or_manipulation",
    ]
    hand: list[ParsedCard] = []
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    role_keys = [
        k
        for k in out
        if k.endswith("_in_hand") and any(k.startswith(f"n_{role}_") for role in role_names)
    ]
    assert len(role_keys) == len(role_names) * 3 == 42


def test_hand_role_by_mv_burn_buckets() -> None:
    """Three burn spells, one in each MV bucket → exactly one count per bucket."""
    hand = [
        f.burn_spell("Bolt", "{R}"),  # MV 1 → mv_0_2
        f.burn_spell("Three", "{1}{R}"),  # MV 2 → mv_0_2
        f.burn_spell("Sniper", "{2}{R}"),  # MV 3 → mv_3
        f.burn_spell("Inferno", "{3}{R}{R}"),  # MV 5 → mv_4_5
    ]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    assert out["n_burn_mv_0_2_in_hand"] == 2.0
    assert out["n_burn_mv_3_in_hand"] == 1.0
    assert out["n_burn_mv_4_5_in_hand"] == 1.0


def test_hand_z_buckets_partition() -> None:
    """Each card with a non-None z-score lands in exactly one bucket.

    Hand: 4 spells, z-scores spread across the four bins. Sum of
    bucket counts should equal 4.
    """
    hand = [
        f.vanilla_creature("A", "{G}", arena_id=1),  # z = 2.0 → top
        f.vanilla_creature("B", "{G}", arena_id=2),  # z = 0.6 → mid_high
        f.vanilla_creature("C", "{G}", arena_id=3),  # z = -0.3 → mid_low
        f.vanilla_creature("D", "{G}", arena_id=4),  # z = -1.0 → bottom
    ]
    zscores = {
        "A": f.make_zscores("A", z_oh=2.0),
        "B": f.make_zscores("B", z_oh=0.6),
        "C": f.make_zscores("C", z_oh=-0.3),
        "D": f.make_zscores("D", z_oh=-1.0),
    }
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=zscores,
        mulligan_number=0,
    )
    assert out["n_spells_oh_z_gt_1_3"] == 1.0
    assert out["n_spells_oh_z_0_4_to_1_3"] == 1.0
    assert out["n_spells_oh_z_neg_0_7_to_0_4"] == 1.0
    assert out["n_spells_oh_z_lt_neg_0_7"] == 1.0
    total = (
        out["n_spells_oh_z_gt_1_3"]
        + out["n_spells_oh_z_0_4_to_1_3"]
        + out["n_spells_oh_z_neg_0_7_to_0_4"]
        + out["n_spells_oh_z_lt_neg_0_7"]
    )
    assert total == 4.0


def test_hand_z_bucket_boundaries() -> None:
    """The boundary conventions: top is strict (>1.3), mid bins are
    closed on the upper side (≤1.3, <0.4 is wrong: should be ≥0.4 in
    mid_high). Verify a card exactly at z=1.3 falls in mid_high, and
    a card at z=0.4 falls in mid_high too."""
    hand = [
        f.vanilla_creature("A", "{G}", arena_id=10),  # z = 1.3 → mid_high
        f.vanilla_creature("B", "{G}", arena_id=11),  # z = 0.4 → mid_high
        f.vanilla_creature("C", "{G}", arena_id=12),  # z = -0.7 → mid_low (≥ -0.7)
    ]
    zscores = {
        "A": f.make_zscores("A", z_oh=1.3),
        "B": f.make_zscores("B", z_oh=0.4),
        "C": f.make_zscores("C", z_oh=-0.7),
    }
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=zscores,
        mulligan_number=0,
    )
    assert out["n_spells_oh_z_gt_1_3"] == 0.0
    assert out["n_spells_oh_z_0_4_to_1_3"] == 2.0
    assert out["n_spells_oh_z_neg_0_7_to_0_4"] == 1.0


def test_hand_z_bucket_skips_none() -> None:
    """Card without z-score (no ratings entry) drops out of all
    buckets — doesn't contribute to any count."""
    hand = [
        f.vanilla_creature("A", "{G}"),  # no ratings entry
        f.vanilla_creature("B", "{G}", arena_id=1),  # no zscores entry
    ]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores={},
        mulligan_number=0,
    )
    assert out["n_spells_oh_z_gt_1_3"] == 0.0
    assert out["n_spells_oh_z_lt_neg_0_7"] == 0.0


def test_hand_wr_summaries_max_sum_avg_earliness() -> None:
    """Verifies max/sum/avg WR summaries and earliness score."""
    hand = [
        f.vanilla_creature("A", "{G}", arena_id=1),
        f.vanilla_creature("B", "{1}{G}", arena_id=2),
    ]
    shrunk = {
        "A": f.make_shrunk("A", oh=0.60, gd=0.50, gih=0.55, weight=0.8),
        "B": f.make_shrunk("B", oh=0.50, gd=0.55, gih=0.52, weight=0.6),
    }
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=shrunk,
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    assert out["max_oh_wr_of_hand_spells"] == pytest.approx(0.60)
    assert out["max_gd_wr_of_hand_spells"] == pytest.approx(0.55)
    assert out["max_gih_wr_of_hand_spells"] == pytest.approx(0.55)
    assert out["avg_gih_wr_of_hand_spells"] == pytest.approx((0.55 + 0.52) / 2)
    assert out["sum_gih_wr_of_hand_spells"] == pytest.approx(0.55 + 0.52)
    # earliness: card A's OH-GD = 0.10, card B's = -0.05. Avg = 0.025.
    assert out["avg_earliness_score_of_hand"] == pytest.approx(0.025)
    assert out["avg_shrinkage_weight_of_hand_spells"] == pytest.approx(0.7)


def test_hand_color_features() -> None:
    """Double-pip and distinct-color counts behave as expected."""
    hand = [
        f.vanilla_creature("A", "{W}{W}"),  # double white pip
        f.vanilla_creature("B", "{1}{W}{U}"),  # 2 distinct colors
        f.vanilla_creature("C", "{2}{R}"),  # single R pip
    ]
    out = build_hand_features(
        hand,
        deck=[],
        shrunk=_empty_shrunk(),
        zscores=_empty_zscores(),
        mulligan_number=0,
    )
    assert out["n_double_or_triple_pip_cards_in_hand"] == 1.0
    # Distinct colors in hand: W, U, R = 3.
    assert out["n_distinct_colors_required_by_hand"] == 3.0


# ---------------------------------------------------------------------------
# Simulation features (105)
# ---------------------------------------------------------------------------


def test_simulation_features_count_is_105() -> None:
    """7 mana-availability + 96 castability + 2 additional = 105."""
    hand = [f.forest(), f.vanilla_creature("Bear", "{1}{G}")]
    out = build_simulation_features(
        hand,
        deck=[],
        aggregate_stats=_empty_aggregate(),
        zscores=_empty_zscores(),
    )
    assert len(out) == 105


def test_simulation_mana_availability_from_game_level() -> None:
    """The 7 mana-availability features mirror ``game_level`` positions."""
    aggregate = AggregateStats(
        n_runs=10,
        seed=None,
        on_the_play=True,
        game_level=GameLevelStats(
            p_land_drop_by_turn=[0.9, 0.8, 0.7, 0.6],
            expected_mana_count_turn=[2.0, 2.8, 3.6],
        ),
    )
    out = build_simulation_features(
        hand=[],
        deck=[],
        aggregate_stats=aggregate,
        zscores=_empty_zscores(),
    )
    assert out["p_land_drop_by_turn_2"] == pytest.approx(0.9)
    assert out["p_land_drop_by_turn_3"] == pytest.approx(0.8)
    assert out["p_land_drop_by_turn_4"] == pytest.approx(0.7)
    assert out["p_land_drop_by_turn_5"] == pytest.approx(0.6)
    assert out["expected_mana_count_turn_2"] == pytest.approx(2.0)
    assert out["expected_mana_count_turn_3"] == pytest.approx(2.8)
    assert out["expected_mana_count_turn_4"] == pytest.approx(3.6)


def test_simulation_p_any_with_two_independent_cards() -> None:
    """Two distinct deck names each with per-game-marginal P(castable
    in T2 snapshot) = 0.5 → P(any) = 1 - 0.5*0.5 = 0.75 across deck.

    Builds an aggregate with hand-crafted CardStats so we can verify
    the 1 - prod(1-p) formula on a clean two-name case. The feature
    iterates over the DECK (not the hand) and reads
    ``p_castable_in_snapshot_by_turn`` — the per-game marginal that
    includes drawn cards as well as opening-hand cards.
    """
    bear_a = f.vanilla_creature("Bear A", "{1}{G}")
    bear_b = f.vanilla_creature("Bear B", "{1}{G}")
    deck = [bear_a, bear_b]
    aggregate = AggregateStats(
        n_runs=100,
        seed=None,
        on_the_play=True,
        by_card_name={
            "Bear A": CardStats(
                name="Bear A",
                oracle_id=bear_a.oracle_id,
                n_copies_in_deck=1,
                p_castable_in_snapshot_by_turn=[0.0, 0.5, 0.7, 0.9],
            ),
            "Bear B": CardStats(
                name="Bear B",
                oracle_id=bear_b.oracle_id,
                n_copies_in_deck=1,
                p_castable_in_snapshot_by_turn=[0.0, 0.5, 0.7, 0.9],
            ),
        },
    )
    out = build_simulation_features(
        hand=[], deck=deck, aggregate_stats=aggregate, zscores=_empty_zscores()
    )
    # P(any creature castable T2) = 1 - 0.5*0.5 = 0.75
    assert out["p_any_creature_t2"] == pytest.approx(0.75)
    # avg count (sum of per-name marginals) = 0.5 + 0.5 = 1.0
    assert out["avg_count_creature_t2"] == pytest.approx(1.0)


def test_simulation_features_count_drawn_creatures_not_in_hand() -> None:
    """Regression: ``p_any_creature_t2`` must NOT be zero when the
    opening hand has no creatures but the deck does — the simulator's
    per-snapshot aggregate already counts drawn instances, and the
    feature builder must read that deck-wide marginal rather than
    the hand-only one. Prior to this fix this returned 0.0 and the
    website surfaced a misleading "0% chance of casting a creature
    on T2"."""
    forest_hand = [f.forest()] * 7  # zero creatures in hand
    bear = f.vanilla_creature("Bear", "{1}{G}")
    deck = forest_hand + [bear] * 5  # 5 deck creatures, none in hand
    aggregate = AggregateStats(
        n_runs=100,
        seed=None,
        on_the_play=True,
        by_card_name={
            "Bear": CardStats(
                name="Bear",
                oracle_id=bear.oracle_id,
                n_copies_in_deck=5,
                # 30% per-game chance ≥1 Bear was castable in the T2
                # snapshot (drawn on T1 / T2 from the library).
                p_castable_in_snapshot_by_turn=[0.0, 0.3, 0.5, 0.7],
            ),
        },
    )
    out = build_simulation_features(
        hand=forest_hand,
        deck=deck,
        aggregate_stats=aggregate,
        zscores=_empty_zscores(),
    )
    # Zero creatures in hand, but the deck-wide marginal is 0.3.
    assert out["p_any_creature_t2"] == pytest.approx(0.3)
    # Same for T3 / T4: features track the simulator marginal.
    assert out["p_any_creature_mv_0_2_t3"] == pytest.approx(0.5)


def test_simulation_high_oh_split_filters_correctly() -> None:
    """high_oh restricts to z-OH > 0.5. Build a deck where only one
    name qualifies and confirm the high_oh feature collapses to it."""
    bomb = f.vanilla_creature("Bomb", "{1}{G}", arena_id=1)  # z = 1.5
    filler = f.vanilla_creature("Filler", "{1}{G}", arena_id=2)  # z = 0.2
    deck = [bomb, filler]
    aggregate = AggregateStats(
        n_runs=100,
        seed=None,
        on_the_play=True,
        by_card_name={
            "Bomb": CardStats(
                name="Bomb",
                oracle_id=bomb.oracle_id,
                n_copies_in_deck=1,
                p_castable_in_snapshot_by_turn=[0.0, 0.5, 0.7, 0.9],
            ),
            "Filler": CardStats(
                name="Filler",
                oracle_id=filler.oracle_id,
                n_copies_in_deck=1,
                p_castable_in_snapshot_by_turn=[0.0, 0.5, 0.7, 0.9],
            ),
        },
    )
    zscores = {
        "Bomb": f.make_zscores("Bomb", z_oh=1.5),
        "Filler": f.make_zscores("Filler", z_oh=0.2),
    }
    out = build_simulation_features(hand=[], deck=deck, aggregate_stats=aggregate, zscores=zscores)
    # high_oh: only the bomb counts. avg_count = 0.5, p_any = 0.5.
    assert out["avg_count_creature_high_oh_t2"] == pytest.approx(0.5)
    assert out["p_any_creature_high_oh_t2"] == pytest.approx(0.5)
    # all: both count. avg_count = 1.0, p_any = 0.75.
    assert out["avg_count_creature_t2"] == pytest.approx(1.0)
    assert out["p_any_creature_t2"] == pytest.approx(0.75)


def test_simulation_t1_emits_only_p_any() -> None:
    """T1 features are p_any only (no avg_count). Verify by key
    inspection."""
    hand = [f.vanilla_creature("Bear", "{G}")]
    out = build_simulation_features(
        hand, deck=[], aggregate_stats=_empty_aggregate(), zscores=_empty_zscores()
    )
    t1_keys = [k for k in out if k.endswith("_t1")]
    # 3 broad x 2 (all + high_oh) x 1 metric = 6
    assert len(t1_keys) == 6
    assert all(k.startswith("p_any_") for k in t1_keys)


def test_simulation_additional_castability_uses_turn_4_per_card() -> None:
    """The two avg_pct_*_by_turn_4 features are the mean of per-card
    p_castable_by_turn[3] across deck / hand spells."""
    bear = f.vanilla_creature("Bear", "{1}{G}")
    knight = f.vanilla_creature("Knight", "{2}{W}")
    deck = [f.forest(), f.forest(), bear, knight]
    hand = [bear]
    aggregate = AggregateStats(
        n_runs=100,
        seed=None,
        on_the_play=True,
        by_card_name={
            "Bear": CardStats(
                name="Bear",
                oracle_id=bear.oracle_id,
                n_copies_in_deck=1,
                p_castable_by_turn=[0.0, 0.5, 0.8, 0.9],
            ),
            "Knight": CardStats(
                name="Knight",
                oracle_id=knight.oracle_id,
                n_copies_in_deck=1,
                p_castable_by_turn=[0.0, 0.0, 0.6, 0.7],
            ),
        },
    )
    out = build_simulation_features(
        hand, deck=deck, aggregate_stats=aggregate, zscores=_empty_zscores()
    )
    # Deck has 2 spells (Bear at 0.9, Knight at 0.7). Avg = 0.8.
    assert out["avg_pct_deck_spells_with_colored_mana_by_turn_4"] == pytest.approx(0.8)
    # Hand has 1 spell (Bear at 0.9).
    assert out["avg_pct_hand_spells_with_colored_mana_by_turn_4"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# Top-level row builder
# ---------------------------------------------------------------------------


def test_build_feature_row_total_column_count() -> None:
    """Sanity: the assembled row has the expected number of columns.

    Context: 1 + 3 + 6 = 10 columns (the set one-hot grew from 3 to 5 in
    the FEATURES_SEMANTICS_VERSION 1 -> 2 bump — SOS + MSH — and to 6 in
    the 3 -> 4 bump — HOB).
    Deck: 16. Hand: 72. Simulation: 105.
    Total: 10 + 16 + 72 + 105 = 203 columns.
    """
    bear = f.vanilla_creature("Bear", "{1}{G}", arena_id=42)
    hand = [f.forest(), f.forest(), bear, bear, f.forest(), f.forest(), f.forest()]
    deck = (
        list(hand)
        + [f.forest()] * 10
        + [f.vanilla_creature(f"Bear{i}", "{1}{G}") for i in range(23)]
    )
    aggregate = simulate(hand, deck[len(hand) :], n_runs=10, seed=0)
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk={"Bear": f.make_shrunk("Bear")},
        zscores={"Bear": f.make_zscores("Bear")},
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code="TLA",
    )
    assert len(row) == 203


def test_build_feature_row_name_join_populates_stats_without_arena_id() -> None:
    """The whole point of Step 5: cards with ``arena_id=None`` still get
    populated per-card WR / z-score features when the name-keyed stats
    table has their rows. Under the old arena_id join every stats
    feature here would have fallen to zero.
    """
    bear = f.vanilla_creature("Bear", "{1}{G}")
    wolf = f.vanilla_creature("Wolf", "{2}{G}")
    # Both have no arena_id — the v2 join would have zeroed every stats
    # feature; the name join must still populate them.
    assert bear.arena_id is None and wolf.arena_id is None
    hand = [f.forest(), f.forest(), f.forest(), bear, bear, wolf, wolf]
    deck = list(hand) + [f.forest()] * 11 + [bear] * 12 + [wolf] * 10
    assert len(deck) == 40
    aggregate = simulate(hand, deck[len(hand) :], n_runs=20, seed=0)
    shrunk = {
        "Bear": f.make_shrunk("Bear", oh=0.60, gd=0.55, gih=0.58),
        "Wolf": f.make_shrunk("Wolf", oh=0.50, gd=0.48, gih=0.49),
    }
    zscores = {
        "Bear": f.make_zscores("Bear", z_oh=1.5),  # > 1.3 bucket
        "Wolf": f.make_zscores("Wolf", z_oh=0.6),  # 0.4..1.3 bucket
    }
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk=shrunk,
        zscores=zscores,
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code="TLA",
    )
    # Deck-level avg WR features are nonzero (the name join succeeded
    # despite arena_id=None on every card).
    assert row["avg_oh_wr_of_spells"] > 0.0
    assert row["avg_gih_wr_of_spells"] > 0.0
    # Hand z-bucket counts populate: 2 Bear copies at z=1.5, 2 Wolf at z=0.6.
    assert row["n_spells_oh_z_gt_1_3"] == 2.0
    assert row["n_spells_oh_z_0_4_to_1_3"] == 2.0


def test_build_feature_row_no_key_collisions() -> None:
    """Builders don't write overlapping keys.

    Tested by verifying the sum of sub-builders' key counts equals the
    full row's key count (set-union behaviour, no shadowing).
    """
    bear = f.vanilla_creature("Bear", "{1}{G}")
    hand = [f.forest(), bear]
    deck = (
        list(hand)
        + [f.forest()] * 15
        + [f.vanilla_creature(f"Bear{i}", "{1}{G}") for i in range(23)]
    )
    aggregate = simulate(hand, deck[len(hand) :], n_runs=5, seed=0)
    context = build_context_features(on_the_play=True, event_type="PremierDraft", set_code="TLA")
    deck_f = build_deck_features(deck, shrunk={})
    hand_f = build_hand_features(hand, deck=deck, shrunk={}, zscores={}, mulligan_number=0)
    sim_f = build_simulation_features(hand, deck=deck, aggregate_stats=aggregate, zscores={})
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk={},
        zscores={},
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code="TLA",
    )
    sum_keys = len(context) + len(deck_f) + len(hand_f) + len(sim_f)
    assert sum_keys == len(row), "Some keys collided — builders write overlapping names"


def test_build_feature_row_values_are_all_floats() -> None:
    """XGBoost wants a uniform numeric input. Every feature value must
    be a real float (not a Decimal, not a bool, not None)."""
    bear = f.vanilla_creature("Bear", "{1}{G}")
    hand = [bear]
    deck = (
        list(hand) + [f.forest()] * 16 + [f.vanilla_creature(f"B{i}", "{1}{G}") for i in range(23)]
    )
    aggregate = simulate(hand, deck[len(hand) :], n_runs=5, seed=0)
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk={},
        zscores={},
        on_the_play=False,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code="TLA",
    )
    for key, value in row.items():
        assert isinstance(value, float), f"{key} is {type(value).__name__}, not float"
        assert math.isfinite(value), f"{key} = {value} is not finite"


def test_build_feature_row_custom_vocab_drops_unknown() -> None:
    """Passing a custom set_code vocab that excludes the current set
    yields a row with no set_code one-hot=1 (graceful degradation)."""
    bear = f.vanilla_creature("Bear", "{1}{G}")
    hand = [bear]
    deck = (
        list(hand) + [f.forest()] * 16 + [f.vanilla_creature(f"B{i}", "{1}{G}") for i in range(23)]
    )
    aggregate = simulate(hand, deck[len(hand) :], n_runs=2, seed=0)
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk={},
        zscores={},
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code="NEW_SET",
        known_sets=("TMT", "ECL", "TLA"),  # NEW_SET not in vocab
    )
    assert row["set_code_TMT"] == 0.0
    assert row["set_code_ECL"] == 0.0
    assert row["set_code_TLA"] == 0.0
