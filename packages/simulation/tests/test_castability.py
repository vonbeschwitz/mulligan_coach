"""Tests for ``is_castable`` (per-mode castability of a single card) and
``castability_snapshot`` (the turn-start option-value snapshot).

The snapshot tests are the main payoff of this step: they verify that
the simulator picks the right witness land drop and that per-mode
reporting captures cycling alongside cast.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import (
    Cost,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    Mode,
    parse_mana_cost,
)
from mulligan_coach_simulation.castability import (
    castability_snapshot,
    is_castable,
)
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _state(hand: list[Card], lands: list[Card] | None = None) -> GameState:
    state = GameState.initial(hand=hand, library=[], on_the_play=True, rng=random.Random(0))
    if lands:
        state.battlefield_lands.extend(lands)
    return state


def _creature_with_cycling(name: str, cast_cost: str, cycle_cost: str) -> object:
    """Build a creature with both a cast Mode and a cycle Mode.

    Tests want to assert that per-mode castability correctly reports
    one mode payable when the other isn't.
    """
    cast_mode = Mode(
        kind="cast", cost=Cost(mana=parse_mana_cost(cast_cost)), effects=[EntersBattlefieldEffect()]
    )
    cycle_mode = Mode(
        kind="cycle",
        cost=Cost(mana=parse_mana_cost(cycle_cost), discard_self=True),
        effects=[DrawCardsEffect(n=1)],
    )
    parsed = f.vanilla_creature(name, cast_cost, 2, 2)
    parsed.modes = [cast_mode, cycle_mode]
    return parsed


# ----------------------------------------------------------------------
# is_castable
# ----------------------------------------------------------------------


def test_is_castable_one_drop_with_one_land() -> None:
    forest = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=2, parsed=f.llanowar_elves())
    state = _state(hand=[elf], lands=[forest])
    result = is_castable(elf, state)
    assert result.castable is True
    assert result.modes_castable == [0]
    assert result.witness_payment is not None


def test_is_castable_no_lands_no_castable() -> None:
    elf = Card(instance_id=1, parsed=f.llanowar_elves())
    state = _state(hand=[elf])
    assert is_castable(elf, state).castable is False


def test_is_castable_per_mode_cycle_only_when_cast_too_expensive() -> None:
    parsed = _creature_with_cycling("Decatur Bear", "{4}{G}", "{2}")
    bear = Card(instance_id=1, parsed=parsed)  # type: ignore[arg-type]
    forest1 = Card(instance_id=2, parsed=f.forest())
    forest2 = Card(instance_id=3, parsed=f.forest())
    state = _state(hand=[bear], lands=[forest1, forest2])
    result = is_castable(bear, state)
    assert result.castable is True
    # Only cycle (mode 1) is castable; cast (mode 0) needs 5 mana.
    assert result.modes_castable == [1]


def test_is_castable_both_modes_when_lands_suffice() -> None:
    parsed = _creature_with_cycling("Decatur Bear", "{1}{G}", "{2}")
    bear = Card(instance_id=1, parsed=parsed)  # type: ignore[arg-type]
    state = _state(
        hand=[bear],
        lands=[Card(instance_id=i, parsed=f.forest()) for i in range(2, 4)],
    )
    result = is_castable(bear, state)
    assert sorted(result.modes_castable) == [0, 1]


# ----------------------------------------------------------------------
# castability_snapshot
# ----------------------------------------------------------------------


def test_snapshot_witnesses_the_in_hand_land() -> None:
    """No lands on battlefield + Forest in hand + Llanowar in hand:
    Llanowar is castable iff we play the Forest. Snapshot witness should
    name the Forest's instance_id."""
    forest = Card(instance_id=10, parsed=f.forest())
    elf = Card(instance_id=20, parsed=f.llanowar_elves())
    state = _state(hand=[forest, elf])

    records, aggregate = castability_snapshot(state)
    elf_records = [r for r in records if r.card_instance_id == elf.instance_id]
    assert len(elf_records) == 1
    rec = elf_records[0]
    assert rec.castable is True
    assert rec.witness_land_choice == forest.instance_id
    assert aggregate[elf.instance_id] is True


def test_snapshot_no_land_witness_when_already_castable() -> None:
    """Llanowar in hand + Forest already on battlefield: castable with
    no land drop. Witness should be None."""
    forest = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=2, parsed=f.llanowar_elves())
    state = _state(hand=[elf], lands=[forest])

    records, _ = castability_snapshot(state)
    rec = next(r for r in records if r.card_instance_id == elf.instance_id)
    assert rec.castable is True
    assert rec.witness_land_choice is None


def test_snapshot_uncastable_when_no_land_helps() -> None:
    """Cultivate ({2}{G}) on turn 1 with one Forest in hand: total
    achievable mana is 1, so it's uncastable regardless of land choice."""
    forest = Card(instance_id=1, parsed=f.forest())
    cultivate = Card(instance_id=2, parsed=f.cultivate())
    state = _state(hand=[forest, cultivate])

    records, aggregate = castability_snapshot(state)
    cultivate_recs = [r for r in records if r.card_instance_id == cultivate.instance_id]
    assert all(not r.castable for r in cultivate_recs)
    assert aggregate[cultivate.instance_id] is False


def test_snapshot_skips_lands_in_records() -> None:
    """Lands in hand should not appear in CastabilityRecords — they have
    no hand-castable modes."""
    forest = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=2, parsed=f.llanowar_elves())
    state = _state(hand=[forest, elf])

    records, aggregate = castability_snapshot(state)
    assert all(r.card_instance_id != forest.instance_id for r in records)
    assert forest.instance_id not in aggregate


def test_snapshot_emits_one_row_per_castable_mode() -> None:
    parsed = _creature_with_cycling("Decatur Bear", "{1}{G}", "{2}")
    bear = Card(instance_id=1, parsed=parsed)  # type: ignore[arg-type]
    forest1 = Card(instance_id=2, parsed=f.forest())
    forest2 = Card(instance_id=3, parsed=f.forest())
    state = _state(hand=[bear, forest1, forest2])

    records, _ = castability_snapshot(state)
    bear_records = [r for r in records if r.card_instance_id == bear.instance_id]
    # one record for cast (mode 0), one for cycle (mode 1)
    assert len(bear_records) == 2
    kinds = sorted(r.mode_kind for r in bear_records)
    assert kinds == ["cast", "cycle"]


def test_snapshot_does_not_mutate_state() -> None:
    """The snapshot routine snapshots/restores around each candidate.
    On return, the live state should be byte-identical to before the
    call (modulo Python identity)."""
    forest = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=2, parsed=f.llanowar_elves())
    state = _state(hand=[forest, elf])

    pre_hand_ids = [c.instance_id for c in state.hand]
    pre_lands = [c.instance_id for c in state.battlefield_lands]
    pre_tapped = set(state.tapped)
    pre_land_drop_used = state.land_drop_used

    castability_snapshot(state)

    assert [c.instance_id for c in state.hand] == pre_hand_ids
    assert [c.instance_id for c in state.battlefield_lands] == pre_lands
    assert state.tapped == pre_tapped
    assert state.land_drop_used == pre_land_drop_used
