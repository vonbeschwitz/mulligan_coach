"""Tests for verbose-mode action logging and pretty-print."""

from __future__ import annotations

import random

from mulligan_coach_cards import ParsedCard
from mulligan_coach_simulation import (
    DrawEvent,
    GameTrace,
    LandDropEvent,
    SpellCastEvent,
    iter_traces,
    pretty_print,
    simulate,
)
from mulligan_coach_simulation.engine import simulate_one_game
from mulligan_coach_simulation.runtime import Card

from . import _factories as f


def _hand_lib(
    parsed_hand: list[ParsedCard], parsed_library: list[ParsedCard]
) -> tuple[list[Card], list[Card]]:
    n = len(parsed_hand)
    hand = [Card(instance_id=i, parsed=p) for i, p in enumerate(parsed_hand)]
    library = [Card(instance_id=n + i, parsed=p) for i, p in enumerate(parsed_library)]
    return hand, library


def test_verbose_default_is_off() -> None:
    """Without ``verbose=True`` the engine emits no actions."""
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    hand, library = _hand_lib(parsed_hand, parsed_library)
    trace = simulate_one_game(hand, library, on_the_play=True, rng=random.Random(0), seed=0)
    assert trace.actions == []


def test_verbose_records_drawn_and_land_drops() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    hand, library = _hand_lib(parsed_hand, parsed_library)
    trace = simulate_one_game(
        hand, library, on_the_play=False, rng=random.Random(0), seed=0, verbose=True
    )
    assert any(isinstance(ev, DrawEvent) for ev in trace.actions)
    land_drops = [ev for ev in trace.actions if isinstance(ev, LandDropEvent)]
    # Four turns, all with a Forest in hand → four land drops.
    assert len(land_drops) == 4
    for ld in land_drops:
        assert ld.chosen_name == "Forest"


def test_verbose_records_spell_cast_priority() -> None:
    parsed_hand = [f.forest()] * 3 + [f.llanowar_elves()] * 2
    parsed_library = [f.forest()] * 33
    hand, library = _hand_lib(parsed_hand, parsed_library)
    trace = simulate_one_game(
        hand, library, on_the_play=True, rng=random.Random(1), seed=1, verbose=True
    )
    casts = [ev for ev in trace.actions if isinstance(ev, SpellCastEvent)]
    assert any(c.card_name == "Llanowar Elves" and c.priority == "S1b" for c in casts)


def test_pretty_print_runs_without_error() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    hand, library = _hand_lib(parsed_hand, parsed_library)
    trace = simulate_one_game(
        hand, library, on_the_play=True, rng=random.Random(0), seed=0, verbose=True
    )
    text = pretty_print(trace)
    # Some structural anchors. Don't snapshot the full output — it's
    # too brittle and the contents change as we tune the policies.
    assert "=== Game" in text
    assert "Turn 1" in text and "Turn 4" in text
    assert "land drop:" in text
    assert "Llanowar Elves" in text


def test_iter_traces_yields_one_per_run() -> None:
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    traces = list(iter_traces(parsed_hand, parsed_library, n_runs=5, seed=0, verbose=True))
    assert len(traces) == 5
    for t in traces:
        assert isinstance(t, GameTrace)
        # Verbose=True populates actions.
        assert t.actions


def test_simulate_with_verbose_still_aggregates() -> None:
    """Verbose flag on the public ``simulate`` is just plumbing —
    aggregate stats should match the non-verbose run."""
    parsed_hand = [f.forest()] * 4 + [f.llanowar_elves()] * 3
    parsed_library = [f.forest()] * 33
    a = simulate(parsed_hand, parsed_library, n_runs=10, seed=42, verbose=False)
    b = simulate(parsed_hand, parsed_library, n_runs=10, seed=42, verbose=True)
    assert a.model_dump() == b.model_dump()
