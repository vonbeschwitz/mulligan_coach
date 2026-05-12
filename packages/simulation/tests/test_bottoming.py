"""Tests for the bottoming heuristic.

Hand-crafted scenarios cover each rule and tiebreaker in
``bottoming.py``. We assemble cards via the existing simulation
factories and patch ``role_features`` for cases where role flags
are load-bearing (creature / removal / counter detection).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from mulligan_coach_cards import ParsedCard, RoleFeatures
from mulligan_coach_simulation.bottoming import bottom_card
from mulligan_coach_simulation.runtime import Card

from . import _factories as f

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _card(parsed: ParsedCard, iid: int) -> Card:
    """Wrap a ParsedCard as a Card with the given instance_id."""
    return Card(instance_id=iid, parsed=parsed)


def _creature(name: str, mana: str, p: int = 2, t: int = 2) -> Card:
    """Vanilla creature with ``role_features.is_creature=True`` set so
    the 4-land rule's "early play" detection picks it up. The
    simulation factory's vanilla_creature() leaves role_features at
    default (is_creature=False) because it doesn't run the parser's
    type-flag seeder."""
    parsed = f.vanilla_creature(name, mana, p, t)
    parsed.role_features = RoleFeatures(is_creature=True)
    return _card(parsed, iid=len(name))


def _removal(name: str, mana: str) -> Card:
    """A removal-tagged instant. The base shape is a cantrip (single
    cast mode, the cantrip's draw effect doesn't matter for the
    bottoming logic) with role_features set."""
    parsed = f.cantrip(name, mana)
    parsed.role_features = RoleFeatures(removal_destroy_or_exile=True)
    return _card(parsed, iid=hash(name) & 0xFFFF)


def _spell(name: str, mana: str) -> Card:
    """Vanilla spell without role flags (so it's NOT a 4-land early
    play)."""
    return _card(f.cantrip(name, mana), iid=hash(name) & 0xFFFF)


def _land(parsed_factory: Callable[[], ParsedCard], iid: int) -> Card:
    return _card(parsed_factory(), iid=iid)


def _build_deck(spells_by_color: dict[str, int]) -> list[Card]:
    """Build a synthetic 40-card deck used as the deck argument to
    ``bottom_card``. The deck only matters for the L4 tiebreaker
    (deck colour counts), so we just pad with the requested per-colour
    spell counts and basic Forests for the rest of the slots.

    ``spells_by_color`` maps a single-colour mana cost like ``"W"`` to
    the number of {1}-cost spells of that colour to include.
    """
    deck: list[Card] = []
    iid = 1_000_000
    for color, n in spells_by_color.items():
        for _ in range(n):
            deck.append(_card(f.cantrip(f"deck-{color}", "{" + color + "}"), iid))
            iid += 1
    # Pad with Forests so the deck is non-empty even if all colors are 0.
    while len(deck) < 40:
        deck.append(_card(f.forest(), iid))
        iid += 1
    return deck


# ---------------------------------------------------------------------------
# Step 1 — land vs. spell
# ---------------------------------------------------------------------------


def test_six_lands_bottoms_a_land() -> None:
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.plains, 2),
        _land(f.plains, 3),
        _land(f.mountain, 4),
        _land(f.mountain, 5),
        _creature("Bear", "{G}{G}"),
    ]
    deck = _build_deck({"G": 8, "W": 8, "R": 8})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land


def test_one_land_bottoms_a_spell() -> None:
    hand = [
        _land(f.forest, 0),
        _creature("Bear", "{G}"),
        _creature("Wolf", "{G}{G}"),
        _spell("Pulse", "{2}{G}"),
        _spell("Burn", "{R}"),
        _spell("Wrath", "{3}{W}"),
        _creature("Elephant", "{4}{G}"),
    ]
    deck = _build_deck({"G": 12, "W": 6, "R": 4})
    chosen = bottom_card(hand, deck)
    assert not chosen.is_land


def test_four_lands_with_castable_early_play_bottoms_land() -> None:
    """4 lands and a castable 2-drop creature in colour → bottom a land."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.plains, 2),
        _land(f.plains, 3),
        _creature("Bear", "{G}"),  # castable 2-mv creature in colour
        _spell("BigSpell", "{4}{G}{G}"),
        _spell("AnotherBig", "{5}{W}"),
    ]
    deck = _build_deck({"G": 10, "W": 10})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land


def test_four_lands_with_no_early_plays_bottoms_spell() -> None:
    """4 lands but every spell is CMC>=4 → no early play → bottom a spell."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.plains, 2),
        _land(f.plains, 3),
        _spell("BigSpell", "{4}{G}{G}"),
        _spell("AnotherBig", "{5}{W}"),
        _spell("AlsoBig", "{4}{W}{G}"),
    ]
    deck = _build_deck({"G": 10, "W": 10})
    chosen = bottom_card(hand, deck)
    assert not chosen.is_land


def test_four_lands_with_uncastable_early_play_bottoms_spell() -> None:
    """A 2-mana early play in a colour the hand can't produce shouldn't
    trigger the 4-land rule — bottom a spell."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _land(f.forest, 3),
        _creature("UrzaConstruct", "{U}{B}"),  # uncastable on mono-G
        _spell("BigSpell", "{4}{G}{G}"),
        _spell("AnotherBig", "{5}{G}"),
    ]
    deck = _build_deck({"G": 10})
    chosen = bottom_card(hand, deck)
    assert not chosen.is_land


def test_four_lands_with_removal_early_play_bottoms_land() -> None:
    """4 lands and a 2-mv removal spell that's castable → bottom a land."""
    hand = [
        _land(f.plains, 0),
        _land(f.plains, 1),
        _land(f.plains, 2),
        _land(f.plains, 3),
        _removal("PathExile", "{W}"),  # cast: {W}, removal
        _spell("BigSpell", "{5}{W}"),
        _spell("AlsoBig", "{4}{W}{W}"),
    ]
    deck = _build_deck({"W": 14})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land


# ---------------------------------------------------------------------------
# Step 2a — which land to bottom
# ---------------------------------------------------------------------------


def test_land_rule_l1_prefers_to_keep_duals() -> None:
    """A basic and a Boros-style dual are both candidates → bottom the basic."""
    plains = _land(f.plains, 0)
    forest1 = _land(f.forest, 1)
    forest2 = _land(f.forest, 2)
    rw_dual_parsed = f.etb_tapped_dual("Boros Guildgate", "R", "W")
    rw_dual = _card(rw_dual_parsed, iid=3)
    mountain1 = _land(f.mountain, 4)
    mountain2 = _land(f.mountain, 5)
    spell = _creature("Mammoth", "{4}{G}")  # makes hand 7 cards

    hand = [plains, forest1, forest2, rw_dual, mountain1, mountain2, spell]
    deck = _build_deck({"G": 5, "R": 5, "W": 5})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land
    # Dual must survive.
    assert chosen is not rw_dual
    # The chosen land's color set must be a single named WUBRG.
    chosen_colors = {
        c
        for ab in chosen.parsed.mana_abilities
        for opt in ab.produces
        for c in opt
        if c in ("W", "U", "B", "R", "G")
    }
    assert len(chosen_colors) == 1


def test_land_rule_l2_bottoms_excess_color() -> None:
    """Hand has 2 swamps + 2 mountains. Spells require 1{B}{B} and 1{R}.
    Color B needs 2 sources, R needs 1. One mountain is excess →
    bottom a mountain."""
    swamp1 = _land(f.swamp, 0)
    swamp2 = _land(f.swamp, 1)
    mountain1 = _land(f.mountain, 2)
    mountain2 = _land(f.mountain, 3)
    bb_spell = _creature("BB-Creature", "{1}{B}{B}")
    r_spell = _spell("R-Burn", "{1}{R}")
    extra = _creature("BigDude", "{6}{B}")  # 7th card; not relevant

    hand = [swamp1, swamp2, mountain1, mountain2, bb_spell, r_spell, extra]
    deck = _build_deck({"B": 12, "R": 6})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land
    chosen_colors = {
        c
        for ab in chosen.parsed.mana_abilities
        for opt in ab.produces
        for c in opt
        if c in ("W", "U", "B", "R", "G")
    }
    assert chosen_colors == {"R"}, f"expected Mountain to be bottomed, got {chosen_colors}"


# ---------------------------------------------------------------------------
# Step 2b — which spell to bottom
# ---------------------------------------------------------------------------


def test_spell_rule_s1_bottoms_uncastable() -> None:
    """3 lands, one castable spell, one uncastable spell (high CMC).
    Uncastable bottomed."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _creature("Two-drop", "{G}"),  # castable
        _spell("Castable-3", "{2}{G}"),
        _spell("Uncastable", "{4}{G}{G}"),  # needs 6 mana, hand has 3 lands
        _creature("Bear-2", "{G}"),
    ]
    deck = _build_deck({"G": 17})
    chosen = bottom_card(hand, deck)
    assert chosen.parsed.name == "Uncastable"


def test_spell_rule_s2_prefers_uncastable_with_unmet_colors() -> None:
    """Hand: 3 forests + NeedsMana ({5}{G} uncastable: mana count short
    but colors OK) + NeedsBlue ({2}{U} uncastable: colors not met) +
    three castable cheap spells. Both uncastable, S2 prefers to bottom
    the colors-unmet one (NeedsBlue) because the mana-short one becomes
    castable with any future land draw."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _spell("NeedsMana", "{5}{G}"),
        _spell("NeedsBlue", "{2}{U}"),
        _creature("Bear", "{G}"),
        _creature("Mammoth", "{2}{G}"),
    ]
    deck = _build_deck({"G": 17})
    chosen = bottom_card(hand, deck)
    assert chosen.parsed.name == "NeedsBlue"


def test_spell_rule_s3_higher_cmc_bottomed() -> None:
    """All spells castable; bottom the higher-CMC one."""
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _land(f.forest, 3),
        _land(f.forest, 4),
        _creature("Cheap", "{G}"),
        _creature("Expensive", "{3}{G}"),
    ]
    deck = _build_deck({"G": 17})
    chosen = bottom_card(hand, deck)
    # Five lands → land rule fires, but only Forests in hand. Make spell-
    # decision happen: drop a land and add a spell.
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _creature("Cheap", "{G}"),
        _creature("Medium", "{2}{G}"),
        _creature("Expensive", "{3}{G}"),
        _creature("Extra", "{1}{G}"),
    ]
    chosen = bottom_card(hand, deck)
    assert chosen.parsed.name == "Expensive"


def test_spell_rule_s4_lower_oh_wr_bottomed() -> None:
    """All else equal between two same-CMC castable spells, the one with
    lower OH WR is bottomed."""
    bad = _creature("BadGuy", "{G}")
    good = _creature("GoodGuy", "{G}")
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        bad,
        good,
        _creature("Filler1", "{2}{G}"),
        _creature("Filler2", "{G}"),
    ]
    deck = _build_deck({"G": 17})

    def oh_wr(c: Card) -> float | None:
        if c.parsed.name == "BadGuy":
            return 0.45
        if c.parsed.name == "GoodGuy":
            return 0.55
        return None

    # With three lands and four 1-mv spells (all castable), filler2 and
    # the two named are all CMC=1 ties. Filler1 (CMC=3) bottoms first
    # under rule S3. Strip Filler1 to force the S4 path:
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        bad,
        good,
        _creature("Filler-A", "{G}"),
        _creature("Filler-B", "{G}"),
    ]

    def oh_wr_full(c: Card) -> float | None:
        if c.parsed.name == "BadGuy":
            return 0.45
        if c.parsed.name == "GoodGuy":
            return 0.55
        if c.parsed.name == "Filler-A":
            return 0.50
        if c.parsed.name == "Filler-B":
            return 0.50
        return None

    chosen = bottom_card(hand, deck, oh_wr=oh_wr_full)
    assert chosen.parsed.name == "BadGuy"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_hand_raises() -> None:
    with pytest.raises(ValueError, match="hand is empty"):
        bottom_card([], _build_deck({}))


def test_hand_all_lands_bottoms_a_land() -> None:
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.plains, 2),
        _land(f.plains, 3),
        _land(f.mountain, 4),
        _land(f.mountain, 5),
        _land(f.swamp, 6),
    ]
    deck = _build_deck({"G": 5, "W": 5, "R": 5, "B": 5})
    chosen = bottom_card(hand, deck)
    assert chosen.is_land


def test_hand_all_spells_bottoms_a_spell() -> None:
    hand = [_creature(f"Bear-{i}", "{G}") for i in range(7)]
    deck = _build_deck({"G": 17})
    chosen = bottom_card(hand, deck)
    assert not chosen.is_land


def test_bottom_card_does_not_mutate_inputs() -> None:
    hand = [
        _land(f.forest, 0),
        _land(f.forest, 1),
        _land(f.forest, 2),
        _creature("Bear", "{G}"),
        _creature("Wolf", "{G}{G}"),
        _spell("Pulse", "{2}{G}"),
        _creature("Mammoth", "{4}{G}"),
    ]
    hand_copy = list(hand)
    deck = _build_deck({"G": 17})
    deck_copy = list(deck)
    bottom_card(hand, deck)
    assert hand == hand_copy
    assert deck == deck_copy
