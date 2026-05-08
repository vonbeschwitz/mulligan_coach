"""Tests for the GameState primitives.

We hand-drive a 3-turn sequence: draw, play_land, tap, untap_all,
begin/end_turn, snapshot/restore. The point is to verify the
bookkeeping (lists, sets, counters) — no policies are involved.
"""

from __future__ import annotations

import random

import pytest
from mulligan_coach_cards import ParsedCard
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _make_card(parsed: ParsedCard, instance_id: int) -> Card:
    return Card(instance_id=instance_id, parsed=parsed)


def _new_state(hand: list[Card], library: list[Card], on_play: bool = True) -> GameState:
    return GameState.initial(hand=hand, library=library, on_the_play=on_play, rng=random.Random(0))


def test_card_runtime_flags() -> None:
    forest = _make_card(f.forest(), 1)
    elf = _make_card(f.llanowar_elves(), 2)
    creature = _make_card(f.vanilla_creature("Bear", "{1}{G}", 2, 2), 3)
    sorcery = _make_card(f.cantrip("Brainstorm", "{U}"), 4)

    assert forest.is_land and not forest.is_creature and forest.has_mana_ability
    assert not elf.is_land and elf.is_creature and elf.has_mana_ability
    assert not creature.is_land and creature.is_creature and not creature.has_mana_ability
    assert not sorcery.is_land and not sorcery.is_creature and not sorcery.has_mana_ability


def test_draw_moves_top_of_library() -> None:
    deck = [_make_card(f.forest(), i) for i in range(5)]
    state = _new_state(hand=[], library=deck.copy())
    drawn = state.draw(2)
    assert [c.instance_id for c in drawn] == [0, 1]
    assert [c.instance_id for c in state.hand] == [0, 1]
    assert [c.instance_id for c in state.library] == [2, 3, 4]


def test_draw_caps_at_library_size() -> None:
    deck = [_make_card(f.forest(), i) for i in range(2)]
    state = _new_state(hand=[], library=deck.copy())
    drawn = state.draw(5)
    assert len(drawn) == 2
    assert state.library == []


def test_play_land_moves_to_battlefield_and_marks_used() -> None:
    forest = _make_card(f.forest(), 1)
    state = _new_state(hand=[forest], library=[])

    assert not state.land_drop_used
    state.play_land(forest)
    assert forest.instance_id in [c.instance_id for c in state.battlefield_lands]
    assert forest not in state.hand
    assert state.land_drop_used
    assert not state.is_tapped(forest)  # basic land enters untapped


def test_play_land_etb_tapped_for_always_predicate() -> None:
    dual = _make_card(f.etb_tapped_dual("Boros Guildgate", "R", "W"), 7)
    state = _new_state(hand=[dual], library=[])
    state.play_land(dual)
    assert state.is_tapped(dual)


def test_second_land_drop_raises() -> None:
    forest1 = _make_card(f.forest(), 1)
    forest2 = _make_card(f.forest(), 2)
    state = _new_state(hand=[forest1, forest2], library=[])
    state.play_land(forest1)
    with pytest.raises(RuntimeError):
        state.play_land(forest2)


def test_play_land_rejects_non_land() -> None:
    elf = _make_card(f.llanowar_elves(), 1)
    state = _new_state(hand=[elf], library=[])
    with pytest.raises(ValueError):
        state.play_land(elf)


def test_play_land_rejects_card_not_in_hand() -> None:
    forest = _make_card(f.forest(), 1)
    state = _new_state(hand=[], library=[])
    with pytest.raises(ValueError):
        state.play_land(forest)


def test_begin_turn_untaps_clears_sickness_resets_land_drop() -> None:
    forest = _make_card(f.forest(), 1)
    elf = _make_card(f.llanowar_elves(), 2)
    state = _new_state(hand=[forest], library=[])
    state.battlefield_mana_perms.append(elf)
    state.summoning_sick.add(elf.instance_id)
    state.tap(elf)
    state.tap(forest)  # not on the battlefield, but tap is just a set add
    state.land_drop_used = True
    pre_turn = state.turn

    state.begin_turn()

    assert state.turn == pre_turn + 1
    assert not state.tapped
    assert not state.summoning_sick
    assert not state.land_drop_used


def test_snapshot_and_restore_round_trip() -> None:
    forest = _make_card(f.forest(), 1)
    elf = _make_card(f.llanowar_elves(), 2)
    state = _new_state(hand=[forest, elf], library=[_make_card(f.forest(), 3)])

    snap = state.snapshot()

    # Mutate the state in many ways.
    state.draw(1)
    state.play_land(forest)
    state.tap(elf)
    state.summoning_sick.add(elf.instance_id)
    state.battlefield_other.append(elf)
    state.turn = 7

    state.restore(snap)

    assert [c.instance_id for c in state.hand] == [1, 2]
    assert state.library and state.library[0].instance_id == 3
    assert state.battlefield_lands == []
    assert state.battlefield_other == []
    assert state.tapped == set()
    assert state.summoning_sick == set()
    assert state.turn == 0
    assert state.land_drop_used is False


def test_restore_then_mutate_does_not_corrupt_snapshot() -> None:
    """A snapshot must remain valid even if the live state is mutated
    after a restore; otherwise the castability snapshot routine — which
    snapshots once, restores once per land-candidate — would silently
    produce wrong answers."""
    forest = _make_card(f.forest(), 1)
    state = _new_state(hand=[forest], library=[])

    snap = state.snapshot()
    state.play_land(forest)
    state.restore(snap)
    state.play_land(forest)  # OK because restore reverted land_drop_used

    # Now mutate after restore.
    state.battlefield_lands.append(_make_card(f.forest(), 99))
    # snap should still describe the original state.
    assert len(snap.battlefield_lands) == 0
    assert 99 not in [c.instance_id for c in snap.hand]


def test_untap_all_clears_tapped_set() -> None:
    forest = _make_card(f.forest(), 1)
    state = _new_state(hand=[], library=[])
    state.battlefield_lands.append(forest)
    state.tap(forest)
    assert state.is_tapped(forest)
    state.untap_all()
    assert not state.is_tapped(forest)
