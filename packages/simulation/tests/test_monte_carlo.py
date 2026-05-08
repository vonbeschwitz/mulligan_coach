"""Tests for the Monte Carlo wrapper and the aggregator."""

from __future__ import annotations

import json

import pytest
from mulligan_coach_simulation import (
    AggregateStats,
    DeckEncodingError,
    simulate,
)

from . import _factories as f

# ----------------------------------------------------------------------
# Reproducibility & validation
# ----------------------------------------------------------------------


def test_simulate_is_deterministic_under_seed() -> None:
    """Running with the same seed twice produces byte-identical stats."""
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    a = simulate(parsed_hand, parsed_library, n_runs=20, seed=42)
    b = simulate(parsed_hand, parsed_library, n_runs=20, seed=42)
    assert a.model_dump() == b.model_dump()


def test_simulate_rejects_unencoded_card() -> None:
    deck_hand = [f.forest()] + [f.needs_llm("Mystery")] + [f.forest()] * 5
    library = [f.forest()] * 33
    with pytest.raises(DeckEncodingError):
        simulate(deck_hand, library, n_runs=1, seed=0)


def test_simulate_aggregate_stats_round_trip_through_json() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    stats = simulate(parsed_hand, parsed_library, n_runs=10, seed=1)

    data = stats.model_dump_json()
    revived = AggregateStats.model_validate_json(data)
    assert revived.model_dump() == stats.model_dump()


# ----------------------------------------------------------------------
# Sanity properties
# ----------------------------------------------------------------------


def test_one_drop_in_mono_color_deck_castable_almost_always() -> None:
    """A 17-Forest, 23-{G}-1-drop deck: a 1-drop in opening hand should
    nearly always be castable turn 1 (since at least one Forest is
    almost certainly in hand too)."""
    library = [f.forest()] * 17 + [f.vanilla_creature("Elf", "{G}", 1, 1)] * 23
    # Put one 1-drop in hand, rest random.
    parsed_hand = (
        [f.forest()] * 3
        + [f.vanilla_creature("Elf", "{G}", 1, 1)]
        + [f.vanilla_creature("Elf", "{G}", 1, 1)] * 3
    )
    stats = simulate(parsed_hand, library, n_runs=300, seed=99)

    elf = stats.by_card_name["Elf"]
    # With 3 Forests in opening hand, the elf is castable turn 1
    # whenever there's a Forest with it (always, since we hardcoded 3).
    assert elf.p_castable_by_turn[0] >= 0.95


def test_zero_land_deck_gives_no_castable() -> None:
    """A deck with zero lands: nothing should ever be castable in 4
    turns, so the first-castable distribution is concentrated in '5+'.
    """
    library = [f.vanilla_creature("Elf", "{G}", 1, 1)] * 33
    parsed_hand = [f.vanilla_creature("Elf", "{G}", 1, 1)] * 7
    stats = simulate(parsed_hand, library, n_runs=20, seed=0)

    elf = stats.by_card_name["Elf"]
    # All instances first-castable-turn = 5 → the 5+ bucket is 1.0,
    # the others 0.0.
    assert elf.first_castable_turn_distribution[4] == pytest.approx(1.0)
    assert all(v == 0.0 for v in elf.first_castable_turn_distribution[:4])
    # And p_castable_by_turn is 0 for all four turns.
    assert all(v == 0.0 for v in elf.p_castable_by_turn)


def test_aggregate_n_copies_in_deck_matches_input() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 13 + [f.llanowar_elves()] * 5 + [f.cultivate()] * 5
    stats = simulate(parsed_hand, parsed_library, n_runs=10, seed=0)

    assert stats.by_card_name["Forest"].n_copies_in_deck == 17
    assert stats.by_card_name["Llanowar Elves"].n_copies_in_deck == 8
    assert stats.by_card_name["Cultivate"].n_copies_in_deck == 5


def test_aggregate_p_in_hand_increases_with_turn() -> None:
    """A card that's NOT in opening hand should have monotonically
    non-decreasing P(in hand by T) across turns 1..4 — once drawn,
    always still 'has been in hand'."""
    parsed_hand = [f.forest()] * 7  # no Llanowar in opening hand
    parsed_library = [f.forest()] * 13 + [f.llanowar_elves()] * 20
    stats = simulate(parsed_hand, parsed_library, n_runs=200, seed=0)

    elf = stats.by_card_name["Llanowar Elves"]
    for t in range(3):
        assert elf.p_in_hand_by_turn[t] <= elf.p_in_hand_by_turn[t + 1] + 1e-9


def test_aggregate_distribution_sums_to_one() -> None:
    """The first-castable-turn distribution covers buckets 1, 2, 3, 4,
    5+ — every instance must land in exactly one, so the sum is 1.0."""
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = (
        [f.forest()] * 13 + [f.cultivate()] * 5 + [f.vanilla_creature("Bomb", "{4}{G}", 5, 5)] * 15
    )
    stats = simulate(parsed_hand, parsed_library, n_runs=50, seed=2)

    for cs in stats.by_card_name.values():
        if cs.n_copies_in_deck == 0:
            continue
        assert sum(cs.first_castable_turn_distribution) == pytest.approx(1.0, abs=1e-6)


def test_aggregate_lands_never_castable() -> None:
    """Lands have no hand-mode in our sense, so first_castable_turn is
    always 5."""
    parsed_hand = [f.forest()] * 7
    parsed_library = [f.forest()] * 33
    stats = simulate(parsed_hand, parsed_library, n_runs=10, seed=0)
    forest = stats.by_card_name["Forest"]
    assert forest.first_castable_turn_distribution[4] == pytest.approx(1.0)


# ----------------------------------------------------------------------
# JSON serialisation
# ----------------------------------------------------------------------


def test_aggregate_serialises_to_json() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    stats = simulate(parsed_hand, parsed_library, n_runs=5, seed=0)
    blob = stats.model_dump_json()
    # Round-trip through the json module too — guards against any
    # custom-encoder shenanigans we might add later.
    assert json.loads(blob)["n_runs"] == 5
