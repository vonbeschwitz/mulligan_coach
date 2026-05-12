"""Tests for the Arena hand smoother."""

from __future__ import annotations

import random

import pytest
from mulligan_coach_simulation.runtime import Card
from mulligan_coach_simulation.smoother import draw_smoothed_hand

from ._factories import forest, vanilla_creature


def _build_deck(num_lands: int, deck_size: int = 40) -> list[Card]:
    """Build a synthetic deck with `num_lands` Forests and the rest
    vanilla creatures. Distinct instance_ids per copy so the hand
    smoother can't accidentally rely on object identity collapsing
    duplicates."""
    land_parsed = forest()
    spell_parsed = vanilla_creature("Spell", "{1}", 2, 2)
    cards: list[Card] = []
    for i in range(num_lands):
        cards.append(Card(instance_id=i, parsed=land_parsed))
    for i in range(deck_size - num_lands):
        cards.append(Card(instance_id=num_lands + i, parsed=spell_parsed))
    return cards


def test_smoother_returns_hand_and_remaining_library() -> None:
    deck = _build_deck(num_lands=17, deck_size=40)
    rng = random.Random(0)
    hand, library = draw_smoothed_hand(deck, rng)
    assert len(hand) == 7
    assert len(library) == 33
    # Every card from the deck appears exactly once across hand + library.
    ids_hand = {c.instance_id for c in hand}
    ids_lib = {c.instance_id for c in library}
    assert ids_hand.isdisjoint(ids_lib)
    assert ids_hand | ids_lib == {c.instance_id for c in deck}


def test_smoother_land_distribution_matches_arena_observations() -> None:
    """With a 17/40 deck and 3 candidates at temperature -0.015, the
    distribution should closely match the README's simulation result:
    ~79% 2-3 lands, near-zero rates of 0 or 6+ lands.

    Reference (from /home/basti/hand_smoother/README.md):
        2-3 lands: 79.2%
        0 lands:   0.003%
        6+ lands:  0.000%
    """
    deck = _build_deck(num_lands=17, deck_size=40)
    rng = random.Random(2024)
    n = 20_000
    land_counts = [0] * 8
    for _ in range(n):
        hand, _ = draw_smoothed_hand(deck, rng)
        lands = sum(1 for c in hand if c.is_land)
        land_counts[lands] += 1

    two_three = (land_counts[2] + land_counts[3]) / n
    zero_lands = land_counts[0] / n
    six_plus = (land_counts[6] + land_counts[7]) / n

    # Tolerances chosen for n=20k. The smoother's true means are
    # ~0.792 / ~0.00003 / ~0.0; we leave a healthy margin.
    assert 0.76 < two_three < 0.83, f"2-3 lands {two_three:.3f} out of band"
    assert zero_lands < 0.002, f"0 lands {zero_lands:.4f} too frequent"
    assert six_plus < 0.002, f"6+ lands {six_plus:.4f} too frequent"


def test_smoother_with_one_candidate_is_unsmoothed() -> None:
    """``num_candidates=1`` short-circuits the smoothing — every draw
    is whatever the shuffle gave. Distribution should approach the
    hypergeometric expectation: P(2 lands)=0.245, P(3 lands)=0.323,
    so P(2 or 3 lands) ≈ 0.568."""
    deck = _build_deck(num_lands=17, deck_size=40)
    rng = random.Random(123)
    n = 20_000
    twos_threes = 0
    for _ in range(n):
        hand, _ = draw_smoothed_hand(deck, rng, num_candidates=1)
        lands = sum(1 for c in hand if c.is_land)
        if lands in (2, 3):
            twos_threes += 1
    rate = twos_threes / n
    # Hypergeometric expectation ~0.568. SE at n=20k is ~0.0035 — wide tolerance.
    assert 0.54 < rate < 0.60, f"unsmoothed 2-3 rate {rate:.3f} out of band"


def test_smoother_rejects_invalid_temperature() -> None:
    deck = _build_deck(num_lands=17)
    rng = random.Random(0)
    with pytest.raises(ValueError, match="temperature must be negative"):
        draw_smoothed_hand(deck, rng, temperature=0.0)
    with pytest.raises(ValueError, match="temperature must be negative"):
        draw_smoothed_hand(deck, rng, temperature=0.5)


def test_smoother_rejects_invalid_candidate_count() -> None:
    deck = _build_deck(num_lands=17)
    rng = random.Random(0)
    with pytest.raises(ValueError, match="num_candidates must be >= 1"):
        draw_smoothed_hand(deck, rng, num_candidates=0)


def test_smoother_rejects_hand_larger_than_deck() -> None:
    deck = _build_deck(num_lands=2, deck_size=5)
    rng = random.Random(0)
    with pytest.raises(ValueError, match="hand_size 7 > deck size 5"):
        draw_smoothed_hand(deck, rng)


def test_smoother_is_deterministic_under_same_seed() -> None:
    deck = _build_deck(num_lands=17, deck_size=40)
    rng_a = random.Random(99)
    rng_b = random.Random(99)
    hand_a, lib_a = draw_smoothed_hand(deck, rng_a)
    hand_b, lib_b = draw_smoothed_hand(deck, rng_b)
    assert [c.instance_id for c in hand_a] == [c.instance_id for c in hand_b]
    assert [c.instance_id for c in lib_a] == [c.instance_id for c in lib_b]
