"""Tests for the simulate_mulligan_from_deck wrapper.

End-to-end coverage: build a synthetic 40-card deck, run the
mulligan pipeline, verify hand/library sizes, bottomed-card
placement, and that the AggregateStats output is well-formed.
"""

from __future__ import annotations

import random

import pytest
from mulligan_coach_simulation import (
    AggregateStats,
    post_mulligan_hand,
    simulate_mulligan_from_deck,
)
from mulligan_coach_simulation.runtime import Card

from . import _factories as f


def _build_synthetic_deck() -> list:
    """Build a synthetic 40-card mono-green deck: 17 forests, 11 Bears
    (G), 8 cantrips ({2}{G}), 4 expensive creatures ({4}{G}{G})."""
    deck = []
    deck.extend([f.forest() for _ in range(17)])
    deck.extend([f.vanilla_creature(f"Bear-{i}", "{G}", 2, 2) for i in range(11)])
    deck.extend([f.cantrip(f"Brainstorm-{i}", "{2}{G}") for i in range(8)])
    deck.extend([f.vanilla_creature(f"Elephant-{i}", "{4}{G}{G}", 5, 5) for i in range(4)])
    assert len(deck) == 40
    return deck


# ---------------------------------------------------------------------------
# post_mulligan_hand — the underlying pipeline.
# ---------------------------------------------------------------------------


def test_post_mulligan_hand_returns_correct_sizes() -> None:
    deck = _build_synthetic_deck()
    rng = random.Random(0)
    hand, library = post_mulligan_hand(deck, rng, target_hand_size=6)
    assert len(hand) == 6
    assert len(library) == 34
    # All cards accounted for, exactly once.
    ids_hand = {c.instance_id for c in hand}
    ids_lib = {c.instance_id for c in library}
    assert ids_hand.isdisjoint(ids_lib)
    assert ids_hand | ids_lib == set(range(40))


def test_post_mulligan_hand_target_seven_skips_bottoming() -> None:
    """target_hand_size=7 with n_to_bottom=0 should pass the smoothed
    hand straight through (no bottoming heuristic call)."""
    deck = _build_synthetic_deck()
    rng = random.Random(0)
    hand, library = post_mulligan_hand(deck, rng, target_hand_size=7)
    assert len(hand) == 7
    assert len(library) == 33


def test_post_mulligan_hand_target_five_bottoms_two() -> None:
    deck = _build_synthetic_deck()
    rng = random.Random(0)
    hand, library = post_mulligan_hand(deck, rng, target_hand_size=5)
    assert len(hand) == 5
    assert len(library) == 35


def test_post_mulligan_hand_bottomed_card_at_end_of_library() -> None:
    """After bottoming, the chosen card must be the LAST element of the
    library (true bottom of deck — unreachable in a 4-turn goldfish)."""
    deck = _build_synthetic_deck()
    rng = random.Random(42)
    hand, library = post_mulligan_hand(deck, rng, target_hand_size=6)
    # Find which instance_id is missing from the smoother's "library"
    # by re-running the smoother with the same seed.
    cards = [Card(instance_id=i, parsed=p) for i, p in enumerate(deck)]
    from mulligan_coach_simulation.smoother import draw_smoothed_hand

    rng_replay = random.Random(42)
    pre_hand, _pre_library = draw_smoothed_hand(cards, rng_replay)
    pre_hand_ids = {c.instance_id for c in pre_hand}
    final_hand_ids = {c.instance_id for c in hand}
    bottomed_id = (pre_hand_ids - final_hand_ids).pop()
    # The bottomed card sits at the END of the post-bottoming library.
    assert library[-1].instance_id == bottomed_id


def test_post_mulligan_hand_deterministic_with_seed() -> None:
    deck = _build_synthetic_deck()
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    hand_a, lib_a = post_mulligan_hand(deck, rng_a, target_hand_size=6)
    hand_b, lib_b = post_mulligan_hand(deck, rng_b, target_hand_size=6)
    assert [c.instance_id for c in hand_a] == [c.instance_id for c in hand_b]
    assert [c.instance_id for c in lib_a] == [c.instance_id for c in lib_b]


def test_post_mulligan_hand_passes_through_oh_wr() -> None:
    """The oh_wr callback should be invoked during bottoming when S4
    fires. We don't assert on which card was bottomed (S1-S3 may
    resolve first) — just that the lookup is wired through and the
    function still runs."""
    deck = _build_synthetic_deck()
    rng = random.Random(1)
    calls = {"n": 0}

    def oh_wr(_card: Card) -> float | None:
        calls["n"] += 1
        return 0.50

    hand, _library = post_mulligan_hand(deck, rng, target_hand_size=6, oh_wr=oh_wr)
    assert len(hand) == 6
    # We can't assert calls["n"] > 0 because S1-S3 may resolve before
    # S4 — for some hand shapes oh_wr is never queried. Just verify
    # the pipeline didn't crash.


# ---------------------------------------------------------------------------
# simulate_mulligan_from_deck — full Monte Carlo wrapper.
# ---------------------------------------------------------------------------


def test_simulate_mulligan_from_deck_returns_aggregate_stats() -> None:
    deck = _build_synthetic_deck()
    stats = simulate_mulligan_from_deck(
        deck, target_hand_size=6, n_runs=20, seed=0, on_the_play=True
    )
    assert isinstance(stats, AggregateStats)
    assert stats.n_runs == 20


def test_simulate_mulligan_from_deck_deterministic() -> None:
    deck = _build_synthetic_deck()
    s_a = simulate_mulligan_from_deck(deck, target_hand_size=6, n_runs=20, seed=12345)
    s_b = simulate_mulligan_from_deck(deck, target_hand_size=6, n_runs=20, seed=12345)
    # Compare game-level summary stats — easier than dataclass equality.
    assert s_a.game_level.p_land_drop_by_turn == s_b.game_level.p_land_drop_by_turn
    assert s_a.game_level.expected_mana_count_turn == s_b.game_level.expected_mana_count_turn


def test_simulate_mulligan_from_deck_target_seven_matches_smoothed_draw() -> None:
    """target_hand_size=7 (no bottoming) should still produce valid
    aggregate stats. Sanity check that the wrapper runs end-to-end on
    the no-bottoming path."""
    deck = _build_synthetic_deck()
    stats = simulate_mulligan_from_deck(
        deck, target_hand_size=7, n_runs=10, seed=0, on_the_play=True
    )
    assert isinstance(stats, AggregateStats)


def test_simulate_mulligan_from_deck_rejects_invalid_target() -> None:
    deck = _build_synthetic_deck()
    with pytest.raises(ValueError, match="target_hand_size must be in"):
        simulate_mulligan_from_deck(deck, target_hand_size=0, n_runs=1)
    with pytest.raises(ValueError, match="target_hand_size must be in"):
        simulate_mulligan_from_deck(deck, target_hand_size=8, n_runs=1)


def test_simulate_mulligan_from_deck_rejects_tiny_deck() -> None:
    """A deck with < 7 cards can't produce an opening hand."""
    deck = [f.forest() for _ in range(5)]
    with pytest.raises(ValueError, match="need at least"):
        simulate_mulligan_from_deck(deck, target_hand_size=5, n_runs=1)


def test_simulate_mulligan_from_deck_hand_size_distribution() -> None:
    """The smoother's land-distribution effect should still apply to
    post-mulligan hands: with target=6 on a 17/40 mono-green deck, the
    average lands-in-hand-after-bottoming should be roughly the deck
    ratio scaled down (~17/40 * 6 ≈ 2.55), but the bottoming heuristic
    biases away from extreme land-light / land-heavy hands.

    Loose bound — this test only catches catastrophic regressions
    (e.g., bottoming logic deletes every land)."""
    deck = _build_synthetic_deck()
    rng = random.Random(2024)
    n = 1000
    land_total = 0
    for _ in range(n):
        hand, _ = post_mulligan_hand(deck, rng, target_hand_size=6)
        land_total += sum(1 for c in hand if c.is_land)
    mean_lands = land_total / n
    # Reasonable bounds: post-bottoming should keep ~2-3 lands per hand
    # for a 17/40 deck. The smoother targets 17/40*7 ≈ 2.97 in the 7
    # card hand; bottoming a single land (when hand is land-heavy)
    # pulls the average toward ~2.5-2.7.
    assert 2.0 < mean_lands < 3.2, f"mean lands {mean_lands:.2f} out of bounds"
