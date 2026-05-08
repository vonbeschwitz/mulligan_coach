"""Tests for the L1-L5 land-drop policy.

Each rule is exercised in isolation by constructing a small hand /
battlefield where exactly one rule is meaningful. A more realistic
end-to-end test (multi-turn play) lives in :mod:`test_engine`.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import RoleFeatures
from mulligan_coach_simulation.policy_land import choose_land
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _state(
    *,
    hand: list[Card],
    lands: list[Card] | None = None,
    library: list[Card] | None = None,
) -> GameState:
    state = GameState.initial(
        hand=hand,
        library=library or [],
        on_the_play=True,
        rng=random.Random(0),
    )
    if lands:
        state.battlefield_lands.extend(lands)
    return state


# ----------------------------------------------------------------------
# No-land hand
# ----------------------------------------------------------------------


def test_no_lands_in_hand_returns_no_land() -> None:
    elf = Card(instance_id=1, parsed=f.llanowar_elves())
    state = _state(hand=[elf])
    chosen, rule = choose_land(state)
    assert chosen is None
    assert rule == "no-land"


def test_single_land_returns_it_with_rule_l1() -> None:
    """One candidate — L1 trivially narrows to it (the only option is
    'best')."""
    forest = Card(instance_id=1, parsed=f.forest())
    state = _state(hand=[forest])
    chosen, rule = choose_land(state)
    assert chosen is forest
    # L1 doesn't *narrow* a single-element list (filter returns same
    # length), so we fall through and the chosen rule defaults to
    # tiebreak. Either is acceptable.
    assert rule in {"L1", "tiebreak"}


# ----------------------------------------------------------------------
# L1 — castability next turn
# ----------------------------------------------------------------------


def test_l1_picks_the_color_that_unlocks_more_spells() -> None:
    """Hand: Plains, Forest, {W}{W} 2-drop creature, {G} 1-drop.
    Battlefield: a Plains already in play.

    * If we play another Plains: at start of next turn we can untap
      both Plains and cast {W}{W}. The {G} 1-drop is still uncastable.
      Spells castable next turn: 1.
    * If we play the Forest: at start of next turn we have Plains + Forest
      → {W} + {G} mana. {W}{W} not castable; {G} 1-drop castable.
      Spells castable next turn: 1.

    Tied count → fall to L2 / further rules. To make L1 fire we need
    the counts to differ, so test that variant separately below.
    """
    plains_battlefield = Card(instance_id=10, parsed=f.plains())
    plains_in_hand = Card(instance_id=20, parsed=f.plains())
    forest_in_hand = Card(instance_id=21, parsed=f.forest())
    bear_ww = Card(instance_id=30, parsed=f.vanilla_creature("Knight", "{W}{W}", 2, 2))
    elf_g = Card(instance_id=31, parsed=f.vanilla_creature("Elf", "{G}", 1, 1))

    state = _state(
        hand=[plains_in_hand, forest_in_hand, bear_ww, elf_g],
        lands=[plains_battlefield],
    )
    chosen, _rule = choose_land(state)
    # With both options unlocking exactly one spell, L1 falls through
    # and L2/L3/etc. take over. Either land is reasonable; the test
    # below covers the asymmetric case.
    assert chosen in (plains_in_hand, forest_in_hand)


def test_l1_picks_the_color_with_more_castable_spells() -> None:
    """Battlefield: a Plains. Hand: Plains, Forest, two {W}{W} spells,
    one {G} spell.

    * Play Plains: Plains+Plains → {W}{W}. Both 2-drops castable. Count = 2.
    * Play Forest: Plains+Forest → {W}{G}. Neither {W}{W} castable; {G}
      1-drop castable. Count = 1.
    L1 picks Plains.
    """
    plains_battlefield = Card(instance_id=10, parsed=f.plains())
    plains_in_hand = Card(instance_id=20, parsed=f.plains())
    forest_in_hand = Card(instance_id=21, parsed=f.forest())
    knight = Card(instance_id=30, parsed=f.vanilla_creature("Knight", "{W}{W}", 2, 2))
    soldier = Card(instance_id=31, parsed=f.vanilla_creature("Soldier", "{W}{W}", 2, 2))
    elf = Card(instance_id=32, parsed=f.vanilla_creature("Elf", "{G}", 1, 1))

    state = _state(
        hand=[plains_in_hand, forest_in_hand, knight, soldier, elf],
        lands=[plains_battlefield],
    )
    chosen, rule = choose_land(state)
    assert chosen is plains_in_hand
    assert rule == "L1"


def test_l1_quality_tiebreak_prefers_creature() -> None:
    """Counts tied (1 each) but the spells differ in role: one is a
    creature, the other a non-creature. L1 quality tiebreak picks the
    land that unlocks the creature.

    Setup: hand has Plains, Forest, a {W} creature, a {G} non-creature
    cantrip. Battlefield: empty.

    * Play Plains: only {W} creature castable next turn.
    * Play Forest: only {G} cantrip castable next turn.

    Creature beats other → Plains wins.
    """
    plains_in_hand = Card(instance_id=20, parsed=f.plains())
    forest_in_hand = Card(instance_id=21, parsed=f.forest())
    soldier = Card(instance_id=30, parsed=f.vanilla_creature("Soldier", "{W}", 1, 1))
    cantrip = Card(instance_id=31, parsed=f.cantrip("Refresh", "{G}"))

    state = _state(hand=[plains_in_hand, forest_in_hand, soldier, cantrip])
    chosen, _rule = choose_land(state)
    assert chosen is plains_in_hand


# ----------------------------------------------------------------------
# L2 — tapped before untapped
# ----------------------------------------------------------------------


def test_l2_picks_etb_tapped_dual_over_basic() -> None:
    """One basic + one always-tapped dual, and they unlock the same set
    of spells next turn (both produce W or R, same single-spell hand).
    L2 should pick the dual.
    """
    plains_basic = Card(instance_id=10, parsed=f.plains())
    dual = Card(instance_id=11, parsed=f.etb_tapped_dual("Boros Guildgate", "R", "W"))
    soldier = Card(instance_id=20, parsed=f.vanilla_creature("Soldier", "{W}", 1, 1))

    state = _state(hand=[plains_basic, dual, soldier])
    chosen, rule = choose_land(state)
    assert chosen is dual
    assert rule == "L2"


def test_l2_recognises_evolving_wilds_as_tapped_equivalent() -> None:
    """Evolving Wilds enters untapped but its eventual basic enters
    tapped. L2 treats it as tapped-equivalent — fires only when L1
    can't differentiate.

    Setup: hand has Forest, Wilds, and a 6-cost spell (uncastable next
    turn from either choice). L1 ties at zero castable spells, so L2
    decides — Wilds wins as tapped-equivalent.
    """
    forest_basic = Card(instance_id=10, parsed=f.forest())
    wilds = Card(instance_id=11, parsed=f.evolving_wilds())
    bomb = Card(instance_id=20, parsed=f.vanilla_creature("Bomb", "{4}{G}{G}", 7, 7))

    state = _state(hand=[forest_basic, wilds, bomb])
    chosen, rule = choose_land(state)
    assert chosen is wilds
    assert rule == "L2"


# ----------------------------------------------------------------------
# L3 — colors not yet available
# ----------------------------------------------------------------------


def test_l3_prefers_unrepresented_color() -> None:
    """Battlefield Plains. Hand has another Plains and a Forest. With
    one no-castable-spell hand, L1 ties, L2 ties (both untapped); L3
    should pick the Forest because Forest's color (G) isn't yet
    available on the battlefield."""
    plains_bf = Card(instance_id=10, parsed=f.plains())
    plains_hand = Card(instance_id=20, parsed=f.plains())
    forest_hand = Card(instance_id=21, parsed=f.forest())

    state = _state(hand=[plains_hand, forest_hand], lands=[plains_bf])
    chosen, rule = choose_land(state)
    assert chosen is forest_hand
    assert rule == "L3"


# ----------------------------------------------------------------------
# L4 — duplicate in hand
# ----------------------------------------------------------------------


def test_l4_prefers_duplicates_in_hand() -> None:
    """Hand: two Plains and one Island, no castable spells. Battlefield
    empty. L1/L2 don't differentiate; L3 doesn't either (no battlefield
    colors yet, so any land "adds a new color"). L4 should fire,
    choosing one of the Plains.
    """
    plains_a = Card(instance_id=20, parsed=f.plains())
    plains_b = Card(instance_id=21, parsed=f.plains())
    island_c = Card(instance_id=22, parsed=f.island())

    state = _state(hand=[plains_a, plains_b, island_c])
    chosen, _rule = choose_land(state)
    assert chosen in (plains_a, plains_b)
    # Should be the lower-index Plains via final hand-index tiebreak
    # if L4 narrows to two Plains.
    assert chosen is plains_a


# ----------------------------------------------------------------------
# L5 — main deck color
# ----------------------------------------------------------------------


def test_l5_prefers_main_color_when_others_tie() -> None:
    """Mostly-G deck (3 G-spells) splashing W (1 W-spell). Hand has one
    Plains and one Forest, no other tiebreak signal. L5 should pick the
    Forest."""
    plains_hand = Card(instance_id=20, parsed=f.plains())
    forest_hand = Card(instance_id=21, parsed=f.forest())
    g_spells = [
        Card(instance_id=30 + i, parsed=f.vanilla_creature(f"Elf {i}", "{1}{G}", 1, 1))
        for i in range(3)
    ]
    w_spell = Card(instance_id=40, parsed=f.vanilla_creature("Soldier", "{1}{W}", 1, 1))

    state = _state(
        hand=[plains_hand, forest_hand, *g_spells, w_spell],
    )
    chosen, rule = choose_land(state)
    assert chosen is forest_hand
    assert rule == "L5"


# ----------------------------------------------------------------------
# Final tiebreaker — hand index
# ----------------------------------------------------------------------


def test_hand_index_tiebreak_when_no_rule_narrows() -> None:
    """Twin basics with no other signals: L1-L5 all tie. Hand index
    decides — first one in the hand wins."""
    plains_a = Card(instance_id=20, parsed=f.plains())
    plains_b = Card(instance_id=21, parsed=f.plains())
    state = _state(hand=[plains_a, plains_b])
    chosen, _rule = choose_land(state)
    assert chosen is plains_a


# ----------------------------------------------------------------------
# L1 quality tiebreak — removal beats other
# ----------------------------------------------------------------------


def test_l1_quality_tiebreak_removal_beats_other() -> None:
    """Counts tied at 1 each, but one option unlocks a removal spell
    and the other unlocks a vanilla cantrip. Removal wins (creature
    > removal > other; here both are 'non-creature', so removal is the
    higher-tier option)."""
    plains_h = Card(instance_id=20, parsed=f.plains())
    forest_h = Card(instance_id=21, parsed=f.forest())

    # A "removal" spell: instant with role_features.removal_destroy_or_exile.
    removal_parsed = f.cantrip("Path", "{W}")
    removal_parsed.role_features = RoleFeatures(removal_destroy_or_exile=True)

    cantrip_g = f.cantrip("Brainstorm", "{G}")

    removal = Card(instance_id=30, parsed=removal_parsed)
    cantrip = Card(instance_id=31, parsed=cantrip_g)

    state = _state(hand=[plains_h, forest_h, removal, cantrip])
    chosen, _rule = choose_land(state)
    assert chosen is plains_h
