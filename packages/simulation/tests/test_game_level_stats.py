"""Tests for the game-level aggregator (``p_land_drop_by_turn`` and
``expected_mana_count_turn``).

Two layers of coverage:

* Unit tests with hand-built :class:`GameTrace` rows — exercise the
  aggregator's arithmetic in isolation.
* An integration test that runs a real :func:`simulate` on a known
  deck and checks the high-level numbers land in the expected
  ballpark.
"""

from __future__ import annotations

import random

import pytest
from mulligan_coach_simulation import (
    GameLevelStats,
    GameTrace,
    TurnSnapshot,
    aggregate_game_level,
    simulate,
)

from . import _factories as f


def _trace(*lands_in_play: int, mana_sources: tuple[int, ...] | None = None) -> GameTrace:
    """Build a minimal GameTrace with one snapshot per turn 1..5.

    ``lands_in_play`` is positional, one value per turn (1..5).
    ``mana_sources`` defaults to ``lands_in_play`` for turns 1..3 (the
    turns the mana count is computed over — indices T-2 for T in 2,3,4
    → snapshots at turns 2,3,4 in this fixture).
    """
    assert len(lands_in_play) == 5
    if mana_sources is None:
        mana_sources = lands_in_play[:5]
    snaps = [
        TurnSnapshot(
            turn=t,
            lands_in_play_after_drop=lands_in_play[t - 1],
            mana_sources_at_start_of_main=mana_sources[t - 1],
        )
        for t in range(1, 6)
    ]
    return GameTrace(seed=0, on_the_play=True, turns=snaps)


# ---------------------------------------------------------------------------
# Unit tests for aggregate_game_level
# ---------------------------------------------------------------------------


def test_empty_games_returns_default_stats() -> None:
    """No traces → all-zero defaults rather than a div-by-zero crash."""
    out = aggregate_game_level([])
    assert isinstance(out, GameLevelStats)
    assert out.p_land_drop_by_turn == [0.0, 0.0, 0.0, 0.0]
    assert out.expected_mana_count_turn == [0.0, 0.0, 0.0]


def test_one_perfect_trace_all_land_drops_hit() -> None:
    """A trace with N lands in play on each turn N → every land-drop
    probability is 1.0."""
    games = [_trace(1, 2, 3, 4, 5)]
    out = aggregate_game_level(games)
    assert out.p_land_drop_by_turn == [1.0, 1.0, 1.0, 1.0]


def test_one_trace_stuck_on_two_lands() -> None:
    """Two lands at turns 2-5: turn 2 hits, turns 3/4/5 miss."""
    games = [_trace(1, 2, 2, 2, 2)]
    out = aggregate_game_level(games)
    assert out.p_land_drop_by_turn == [1.0, 0.0, 0.0, 0.0]


def test_two_games_average() -> None:
    """Two games, one perfect and one stuck at 2 lands → averages
    [1.0, 0.5, 0.5, 0.5] across turns 2..5."""
    games = [_trace(1, 2, 3, 4, 5), _trace(1, 2, 2, 2, 2)]
    out = aggregate_game_level(games)
    assert out.p_land_drop_by_turn == [1.0, 0.5, 0.5, 0.5]


def test_expected_mana_count_averages() -> None:
    """Mana sources [_, 3, 5, 7, _] in one game (only turns 2-4 read).
    Expected averages: [3.0, 5.0, 7.0]."""
    games = [_trace(1, 2, 3, 4, 5, mana_sources=(1, 3, 5, 7, 0))]
    out = aggregate_game_level(games)
    assert out.expected_mana_count_turn == [3.0, 5.0, 7.0]


def test_expected_mana_count_averages_across_games() -> None:
    """Two games: [_, 3, 5, 7, _] and [_, 4, 4, 4, _].
    Means: [(3+4)/2, (5+4)/2, (7+4)/2] = [3.5, 4.5, 5.5]."""
    games = [
        _trace(0, 0, 0, 0, 0, mana_sources=(1, 3, 5, 7, 0)),
        _trace(0, 0, 0, 0, 0, mana_sources=(1, 4, 4, 4, 0)),
    ]
    out = aggregate_game_level(games)
    assert out.expected_mana_count_turn == [3.5, 4.5, 5.5]


def test_missing_snapshot_does_not_count_for_that_turn() -> None:
    """A game with no turn-5 snapshot doesn't reduce the OTHER turns'
    sample size — it just doesn't contribute to turn 5. Useful as a
    defensive path; in normal use the engine produces all 5 snapshots."""
    snaps = [
        TurnSnapshot(turn=t, lands_in_play_after_drop=t, mana_sources_at_start_of_main=t)
        for t in (1, 2, 3, 4)
    ]
    incomplete = GameTrace(seed=0, on_the_play=True, turns=snaps)
    full = _trace(1, 2, 3, 4, 5)
    out = aggregate_game_level([incomplete, full])
    # T2/T3/T4 hits in both games → 2/2.
    assert out.p_land_drop_by_turn[0] == 1.0
    assert out.p_land_drop_by_turn[1] == 1.0
    assert out.p_land_drop_by_turn[2] == 1.0
    # T5 only hit in 'full' (1/1).
    assert out.p_land_drop_by_turn[3] == 1.0


def test_p_land_drop_indices_match_documented_order() -> None:
    """Sanity guard against silently re-ordering the output positions:
    index 0 is turn 2, index 3 is turn 5."""
    games = [_trace(1, 2, 0, 0, 5)]
    out = aggregate_game_level(games)
    # T2 hit (2 >= 2), T3 miss (0 < 3), T4 miss (0 < 4), T5 hit (5 >= 5).
    assert out.p_land_drop_by_turn == [1.0, 0.0, 0.0, 1.0]


# ---------------------------------------------------------------------------
# Integration: real Monte Carlo, sanity bounds
# ---------------------------------------------------------------------------


def test_simulate_returns_game_level_stats_on_aggregate() -> None:
    """The public ``simulate`` exposes ``game_level`` on the returned
    aggregate. With an all-Forest deck and a 7-land hand on the play,
    every game makes 4 land drops by turn 4 → P >= 0.99 for turns 2-4."""
    parsed_hand = [f.forest()] * 7
    parsed_library = [f.forest()] * 33

    agg = simulate(
        parsed_hand,
        parsed_library,
        on_the_play=True,
        n_runs=50,
        seed=42,
    )
    assert isinstance(agg.game_level, GameLevelStats)
    # 7 lands in hand and only lands in the library → can't fail to
    # make any land drop in the first 4 turns.
    assert agg.game_level.p_land_drop_by_turn[0] == pytest.approx(1.0, abs=1e-9)
    assert agg.game_level.p_land_drop_by_turn[1] == pytest.approx(1.0, abs=1e-9)
    assert agg.game_level.p_land_drop_by_turn[2] == pytest.approx(1.0, abs=1e-9)
    # Turn 5 also hits (a Forest is drawn on turn 5 → always playable).
    assert agg.game_level.p_land_drop_by_turn[3] == pytest.approx(1.0, abs=1e-9)
    # Mana counts should equal land drops on turn N (no other mana
    # sources in this deck).
    assert agg.game_level.expected_mana_count_turn[0] == pytest.approx(2.0, abs=1e-9)
    assert agg.game_level.expected_mana_count_turn[1] == pytest.approx(3.0, abs=1e-9)
    assert agg.game_level.expected_mana_count_turn[2] == pytest.approx(4.0, abs=1e-9)


def test_simulate_mana_dorks_inflate_mana_count() -> None:
    """A hand of 2 Forests + a Llanowar Elves + 4 vanilla creatures
    should reach 3 mana on turn 3 (2 lands + dork untapped by turn 3),
    higher than the same hand without the dork."""
    parsed_hand = (
        [f.forest()] * 2 + [f.llanowar_elves()] + [f.vanilla_creature("Bear", "{G}", 2, 2)] * 4
    )
    parsed_library = [f.forest()] * 33

    agg = simulate(
        parsed_hand,
        parsed_library,
        on_the_play=True,
        n_runs=30,
        seed=11,
    )
    # By turn 3: 3 lands in play + Llanowar Elves (cast turn 2, untapped
    # at start of turn 3) → 4 mana sources. The exact value depends on
    # whether the dork gets cast on turn 2 (it should, per the spell
    # policy S1 — accelerate mana).
    assert agg.game_level.expected_mana_count_turn[1] >= 3.5


def test_simulate_one_land_hand_has_partial_land_drops() -> None:
    """A 1-land hand on the play: turn 1 land drop is automatic
    (1 land in hand), turn 2 requires drawing a second land, and so on.

    Library is 17/33 lands so P(any single draw is a land) ≈ 0.515.
    Sanity bounds rather than exact equality — the engine's policy
    plus shuffle ordering shift the numbers slightly.
    """
    parsed_hand = [f.forest()] + [f.vanilla_creature("Bear", "{G}", 2, 2)] * 6
    parsed_library = [f.forest()] * 17 + [f.vanilla_creature("Bear", "{G}", 2, 2)] * 16

    rng = random.Random(7)
    agg = simulate(
        parsed_hand,
        parsed_library,
        on_the_play=True,
        n_runs=200,
        seed=rng.randrange(1 << 30),
    )
    # T2 needs a second land via the turn-2 draw → roughly 17/33.
    assert 0.30 < agg.game_level.p_land_drop_by_turn[0] < 0.75
    # T5 needs five lands; opening 1 plus four draws of 17/33-ish.
    # Well below 1.0, well above 0.
    assert 0.05 < agg.game_level.p_land_drop_by_turn[3] < 0.85
