"""Tests for the per-game engine.

These exercise the four-turn loop with deterministic seeds and a
small synthetic deck. The Monte Carlo wrapper has its own tests in
:mod:`test_monte_carlo`.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import ParsedCard
from mulligan_coach_simulation.engine import simulate_one_game
from mulligan_coach_simulation.runtime import Card

from . import _factories as f


def _hand_and_library_from_parsed(
    parsed_hand: list[ParsedCard], parsed_library: list[ParsedCard]
) -> tuple[list[Card], list[Card]]:
    n_hand = len(parsed_hand)
    hand = [Card(instance_id=i, parsed=p) for i, p in enumerate(parsed_hand)]
    library = [Card(instance_id=n_hand + i, parsed=p) for i, p in enumerate(parsed_library)]
    return hand, library


def test_simulate_one_game_smoke() -> None:
    """A 7-card opening hand of 4 Forests + 3 Llanowar elves: every
    Llanowar should be castable turn 1."""
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33  # rest of the "deck"

    hand, library = _hand_and_library_from_parsed(parsed_hand, parsed_library)
    rng = random.Random(42)
    trace = simulate_one_game(hand, library, on_the_play=True, rng=rng, seed=42)

    # First turn: all three Llanowar should be castable.
    elf_outcomes = [o for o in trace.instances if o.name == "Llanowar Elves"]
    assert len(elf_outcomes) == 3
    for o in elf_outcomes:
        assert o.first_castable_turn == 1
        assert o.first_in_hand_turn == 0  # opening hand


def test_simulate_one_game_uncastable_when_no_lands() -> None:
    """A hand of just creatures (no lands), all in hand at start.
    First-castable-turn should be 5 (never)."""
    parsed_hand = [f.llanowar_elves()] * 3
    parsed_library = [f.vanilla_creature("Bear", "{1}{G}", 2, 2)] * 30

    hand, library = _hand_and_library_from_parsed(parsed_hand, parsed_library)
    trace = simulate_one_game(hand, library, on_the_play=True, rng=random.Random(0), seed=0)

    # Without lands in the deck or hand, no Llanowar can ever be cast.
    for o in trace.instances:
        if o.name == "Llanowar Elves" and o.first_in_hand_turn != 5:
            assert o.first_castable_turn == 5


def test_simulate_one_game_records_drawn_cards() -> None:
    """Cards drawn each turn should appear in the per-turn snapshot."""
    parsed_hand = [f.forest()] * 7
    parsed_library = [f.llanowar_elves()] * 33

    hand, library = _hand_and_library_from_parsed(parsed_hand, parsed_library)
    trace = simulate_one_game(hand, library, on_the_play=False, rng=random.Random(0), seed=0)

    # On the draw → turn 1 includes a draw step.
    turn1 = next(t for t in trace.turns if t.turn == 1)
    assert len(turn1.cards_drawn) == 1
    # Subsequent turns also draw.
    for t in trace.turns[1:]:
        assert len(t.cards_drawn) == 1


def test_simulate_one_game_on_the_play_skips_turn1_draw() -> None:
    parsed_hand = [f.forest()] * 7
    parsed_library = [f.llanowar_elves()] * 33

    hand, library = _hand_and_library_from_parsed(parsed_hand, parsed_library)
    trace = simulate_one_game(hand, library, on_the_play=True, rng=random.Random(0), seed=0)

    turn1 = next(t for t in trace.turns if t.turn == 1)
    assert turn1.cards_drawn == []


def test_simulate_one_game_cultivate_unlocks_late_drops() -> None:
    """Hand: 3 Forests + 1 Cultivate + 1 four-drop creature. Library:
    rest are Forests so Cultivate's basic-fetch always succeeds.
    Cultivate ramps to 4 mana on turn 4 → the four-drop becomes castable.
    """
    parsed_hand = [f.forest()] * 3 + [f.cultivate()] + [f.vanilla_creature("Bomb", "{4}{G}", 5, 5)]
    parsed_library = [f.forest()] * 35

    hand, library = _hand_and_library_from_parsed(parsed_hand, parsed_library)
    trace = simulate_one_game(hand, library, on_the_play=True, rng=random.Random(7), seed=7)

    bomb = next(o for o in trace.instances if o.name == "Bomb")
    # Without Cultivate, on the play, with 3 lands in hand: turn 4 we'd
    # have 4 mana and the bomb costs 5 — uncastable. With Cultivate
    # casting on turn 3 (3 lands) putting one tapped basic + one to
    # hand, by turn 4 we'd have 5 lands and the bomb is castable.
    assert bomb.first_castable_turn == 4
