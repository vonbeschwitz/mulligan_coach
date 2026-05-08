"""Tests for the deterministic Scryfall card parser.

Each test builds a small dict shaped like a Scryfall ``oracle_cards`` row.
We hand-construct rather than load from disk so the tests are fast and
don't depend on a particular data snapshot.

Assertions target the new datatype shape: ``modes`` (with structured
``Cost`` and a list of ``Effect``s), ``mana_abilities`` for permanents
that produce mana, ``enter_condition`` for lands, and ``role_features``
for the XGBoost feature stage downstream.
"""

from __future__ import annotations

from typing import Any

from mulligan_coach_cards.models import (
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    NoopEffect,
    ParseStatus,
)
from mulligan_coach_cards.parser import parse_card


def _scryfall(**overrides: Any) -> dict[str, Any]:
    """Build a minimal Scryfall-shaped card dict with sensible defaults."""
    base: dict[str, Any] = {
        "name": "Test Card",
        "set": "TST",
        "collector_number": "1",
        "oracle_id": "00000000-0000-0000-0000-000000000001",
        "type_line": "Creature — Human",
        "oracle_text": "",
        "mana_cost": "{1}",
        "rarity": "common",
        "lang": "en",
        "layout": "normal",
        "colors": [],
        "keywords": [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Lands
# ---------------------------------------------------------------------------


def test_basic_plains_auto() -> None:
    card = _scryfall(
        name="Plains",
        type_line="Basic Land — Plains",
        oracle_text="",
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "Land" in p.types
    assert p.enter_condition is None
    assert len(p.mana_abilities) == 1
    ab = p.mana_abilities[0]
    assert ab.cost.tap is True
    assert ab.produces == [["W"]]


def test_dual_land_enters_tapped_auto() -> None:
    card = _scryfall(
        name="Boros Guildgate",
        type_line="Land — Gate",
        oracle_text="Boros Guildgate enters tapped.\n{T}: Add {R} or {W}.",
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.enter_condition is not None
    assert p.enter_condition.kind == "always"
    # Two payment options on a single ability.
    assert p.mana_abilities[0].produces == [["R"], ["W"]]


def test_deathcap_style_conditional_etb_tapped_auto() -> None:
    card = _scryfall(
        name="Deathcap Glade",
        type_line="Land",
        oracle_text=(
            "Deathcap Glade enters tapped unless you control two or more other lands.\n"
            "{T}: Add {B} or {G}."
        ),
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.enter_condition is not None
    assert p.enter_condition.kind == "controls_lands_lt"
    assert p.enter_condition.n == 2
    assert p.mana_abilities[0].produces == [["B"], ["G"]]


def test_fetch_land_needs_llm() -> None:
    # Sac-fetches still bail in v1 — they live on the activated-cost path
    # which the land parser doesn't yet recognise.
    card = _scryfall(
        name="Evolving Wilds",
        type_line="Land",
        oracle_text=(
            "{T}, Sacrifice this land: Search your library for a basic land card, "
            "put it onto the battlefield tapped, then shuffle."
        ),
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert "Land" in p.types


# ---------------------------------------------------------------------------
# Creatures
# ---------------------------------------------------------------------------


def test_vanilla_creature_auto() -> None:
    card = _scryfall(
        name="Grizzly Bear",
        type_line="Creature — Bear",
        oracle_text="",
        mana_cost="{1}{G}",
        power="2",
        toughness="2",
        colors=["G"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_creature
    assert p.power == "2"
    assert p.toughness == "2"
    # Cast Mode with EntersBattlefieldEffect.
    assert len(p.modes) == 1
    assert p.modes[0].kind == "cast"
    assert isinstance(p.modes[0].effects[0], EntersBattlefieldEffect)


def test_flying_creature_auto() -> None:
    card = _scryfall(
        name="Storm Crow",
        type_line="Creature — Bird",
        oracle_text="Flying",
        mana_cost="{1}{U}",
        power="1",
        toughness="2",
        keywords=["Flying"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.evergreen_keywords == ["flying"]


def test_compound_keyword_with_ward_cost_auto() -> None:
    # "Flying, ward {1}" should still count as a pure-keyword line.
    card = _scryfall(
        name="Dragonfly",
        type_line="Creature — Dragon Insect",
        oracle_text="Flying, ward {1}",
        mana_cost="{2}{U}",
        power="2",
        toughness="2",
        keywords=["Flying", "Ward"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "flying" in p.evergreen_keywords
    assert "ward" in p.evergreen_keywords


def test_etb_draw_creature_auto() -> None:
    card = _scryfall(
        name="Sage of the Library",
        type_line="Creature — Human Wizard",
        oracle_text="When this creature enters, draw a card.",
        mana_cost="{2}{U}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cast_effects = p.modes[0].effects
    # Effects are EntersBattlefieldEffect followed by DrawCardsEffect(n=1).
    assert any(isinstance(e, DrawCardsEffect) and e.n == 1 for e in cast_effects)
    assert p.role_features.cards_drawn == 1


def test_etb_exile_creature_auto() -> None:
    card = _scryfall(
        name="Banisher Priest",
        type_line="Creature — Human Cleric",
        oracle_text="When this creature enters, exile target creature.",
        mana_cost="{1}{W}{W}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.removal_destroy_or_exile is True


def test_mana_dork_auto() -> None:
    card = _scryfall(
        name="Llanowar Elves",
        type_line="Creature — Elf Druid",
        oracle_text="{T}: Add {G}.",
        mana_cost="{G}",
        power="1",
        toughness="1",
        colors=["G"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.mana_abilities) == 1
    assert p.mana_abilities[0].cost.tap is True
    assert p.mana_abilities[0].produces == [["G"]]


def test_creature_with_cycling_emits_extra_mode() -> None:
    card = _scryfall(
        name="Cycling Beast",
        type_line="Creature — Beast",
        oracle_text="Cycling {2}",
        mana_cost="{4}{G}",
        power="4",
        toughness="4",
        keywords=["Cycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    kinds = [m.kind for m in p.modes]
    assert "cast" in kinds
    assert "cycle" in kinds
    cycle_mode = next(m for m in p.modes if m.kind == "cycle")
    assert cycle_mode.cost.discard_self is True
    assert cycle_mode.cost.mana.cmc == 2
    assert any(isinstance(e, DrawCardsEffect) for e in cycle_mode.effects)


def test_creature_with_landcycling_emits_fetch_mode() -> None:
    card = _scryfall(
        name="Mountaincycler",
        type_line="Creature — Goblin",
        oracle_text="Mountaincycling {2}",
        mana_cost="{3}{R}",
        power="3",
        toughness="3",
        keywords=["Mountaincycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cycle_mode = next(m for m in p.modes if m.kind == "land_cycle")
    fetch = cycle_mode.effects[0]
    assert isinstance(fetch, FetchLandEffect)
    assert fetch.target_filter == "specific_subtype"
    assert fetch.subtype == "Mountain"
    assert fetch.destination == "hand"


def test_activated_destroy_artifact_auto_with_other_role() -> None:
    # Generalized destroy/exile regex picks up the artifact target.
    # Creature stats parse fine, the death-trigger life-gain is ignored
    # (it's a triggered ability), and the activated ability becomes a
    # Mode whose effect is a noop. role_features.is_other=True because
    # the removal isn't creature-targeting.
    card = _scryfall(
        name="Curious Farm Animals",
        type_line="Creature — Boar",
        oracle_text=(
            "When this creature dies, you gain 3 life.\n"
            "{2}, Sacrifice this creature: Destroy target artifact."
        ),
        mana_cost="{W}",
        power="1",
        toughness="1",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_creature
    assert p.role_features.is_other  # non-creature removal → "other"
    assert not p.role_features.removal_destroy_or_exile
    activated = next(m for m in p.modes if m.kind == "activated")
    assert activated.cost.sacrifice is not None
    assert activated.cost.sacrifice.target == "self"


def test_kicker_creature_needs_llm() -> None:
    card = _scryfall(
        name="Kicker Goblin",
        type_line="Creature — Goblin",
        oracle_text="Kicker {2}\nWhen this creature enters, if it was kicked, draw a card.",
        mana_cost="{R}",
        power="1",
        toughness="1",
        keywords=["Kicker"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert any("alternative-cost" in r for r in p.reasons)


def test_etb_token_creator_creature_auto() -> None:
    # Token creation is recognised even when the broader ETB phrasing
    # doesn't match _match_spell_effect cleanly.
    card = _scryfall(
        name="Token Maker",
        type_line="Creature — Human Warrior",
        oracle_text="Flash\nWhen this creature enters, create a 1/1 white Soldier creature token.",
        mana_cost="{1}{W}",
        power="1",
        toughness="1",
        keywords=["Flash"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.role_features.creates_creatures) == 1
    body = p.role_features.creates_creatures[0]
    assert body.power == "1"
    assert body.toughness == "1"
    assert body.colors == ["W"]


# ---------------------------------------------------------------------------
# Instants and sorceries
# ---------------------------------------------------------------------------


def test_pure_card_draw_auto() -> None:
    card = _scryfall(
        name="Divination",
        type_line="Sorcery",
        oracle_text="Draw two cards.",
        mana_cost="{2}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cast = p.modes[0]
    assert any(isinstance(e, DrawCardsEffect) and e.n == 2 for e in cast.effects)
    assert p.role_features.cards_drawn == 2


def test_destroy_target_creature_auto() -> None:
    card = _scryfall(
        name="Murder",
        type_line="Instant",
        oracle_text="Destroy target creature.",
        mana_cost="{1}{B}{B}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cast = p.modes[0]
    assert any(isinstance(e, NoopEffect) and e.role_tag == "removal_destroy" for e in cast.effects)
    assert p.role_features.removal_destroy_or_exile is True


def test_lightning_bolt_auto() -> None:
    card = _scryfall(
        name="Lightning Bolt",
        type_line="Instant",
        oracle_text="Lightning Bolt deals 3 damage to any target.",
        mana_cost="{R}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.removal_burn_damage == 3


def test_combat_trick_pump_auto() -> None:
    # Combat trick: "target creature gets +N/+M until end of turn".
    card = _scryfall(
        name="Giant Growth",
        type_line="Instant",
        oracle_text="Target creature gets +3/+3 until end of turn.",
        mana_cost="{G}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.combat_trick_power == 3
    assert p.role_features.combat_trick_toughness == 3


def test_combat_trick_granted_keywords_auto() -> None:
    # Combat trick: "target creature gains <keywords> until end of turn".
    card = _scryfall(
        name="Quick Swap",
        type_line="Instant",
        oracle_text="Target creature gains flying and lifelink until end of turn.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "flying" in p.role_features.combat_trick_granted_keywords
    assert "lifelink" in p.role_features.combat_trick_granted_keywords


def test_enchantment_with_triggered_ability_auto() -> None:
    # Per the v1 design rule we silently drop static / triggered abilities
    # we don't model. An enchantment with only a triggered ability now
    # auto-classifies (the static/triggered tolerance absorbs the line).
    card = _scryfall(
        name="Some Enchantment",
        type_line="Enchantment",
        oracle_text="Whenever a creature you control attacks, it gets +1/+0.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_dfc_layout_needs_llm() -> None:
    card = _scryfall(
        layout="modal_dfc",
        type_line="Creature — Human",
        oracle_text="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert any("layout" in r.lower() for r in p.reasons)


def test_life_gain_auto() -> None:
    card = _scryfall(
        name="Healing Salve",
        type_line="Instant",
        oracle_text="You gain three life.",
        mana_cost="{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cast = p.modes[0]
    assert any(isinstance(e, NoopEffect) and e.role_tag == "life_gain" for e in cast.effects)


def test_scry_auto() -> None:
    card = _scryfall(
        name="Telling Time",
        type_line="Sorcery",
        oracle_text="Scry 2.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_manipulated == 2


def test_fetch_land_to_hand_spell_auto() -> None:
    # Environmental Scientist-style: ETB → search basic → hand.
    # We model this as a sorcery for the test to keep the example focused
    # on the fetch matcher itself.
    card = _scryfall(
        name="Land Tutor",
        type_line="Sorcery",
        oracle_text=(
            "Search your library for a basic land card, "
            "reveal it, put it into your hand, then shuffle."
        ),
        mana_cost="{1}{G}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    cast = p.modes[0]
    fetches = [e for e in cast.effects if isinstance(e, FetchLandEffect)]
    assert len(fetches) == 1
    assert fetches[0].destination == "hand"
    assert fetches[0].target_filter == "basic"


def test_fetch_land_to_battlefield_tapped_spell_auto() -> None:
    # Cultivate-style first half — search basic, put onto battlefield tapped.
    card = _scryfall(
        name="Cultivate Half",
        type_line="Sorcery",
        oracle_text=(
            "Search your library for a basic land card, "
            "put it onto the battlefield tapped, then shuffle."
        ),
        mana_cost="{2}{G}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    fetches = [e for e in p.modes[0].effects if isinstance(e, FetchLandEffect)]
    assert len(fetches) == 1
    assert fetches[0].destination == "battlefield_tapped"


def test_token_creating_sorcery_auto() -> None:
    # Plain "create N tokens" sorceries auto-classify and populate
    # role_features.creates_creatures.
    card = _scryfall(
        name="Raise the Alarm",
        type_line="Instant",
        oracle_text="Create two 1/1 white Soldier creature tokens.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.role_features.creates_creatures) == 1
    body = p.role_features.creates_creatures[0]
    assert body.power == "1" and body.toughness == "1" and body.colors == ["W"]


# ---------------------------------------------------------------------------
# Bounce / tuck (XGBoost-only categories — sim treats as noop).
# ---------------------------------------------------------------------------


def test_bounce_creature_auto() -> None:
    card = _scryfall(
        name="Unsummon",
        type_line="Instant",
        oracle_text="Return target creature to its owner's hand.",
        mana_cost="{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_bounce is True


def test_bounce_nonland_permanent_auto() -> None:
    card = _scryfall(
        name="Aether Tradewinds",
        type_line="Instant",
        oracle_text="Return target nonland permanent to its owner's hand.",
        mana_cost="{2}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_bounce is True


def test_tuck_creature_auto() -> None:
    card = _scryfall(
        name="Tuck Spell",
        type_line="Instant",
        oracle_text="Put target creature on top of its owner's library.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_top_library is True


# ---------------------------------------------------------------------------
# Variable-amount ETB damage and counter distribution.
# ---------------------------------------------------------------------------


def test_etb_variable_damage_creature_auto() -> None:
    # Cat-Gator pattern: ETB deals damage equal to a board-state count.
    card = _scryfall(
        name="Cat-Gator",
        type_line="Creature — Fish Crocodile",
        oracle_text=(
            "Lifelink\n"
            "When this creature enters, it deals damage equal to the number "
            "of Swamps you control to any target."
        ),
        mana_cost="{6}{B}",
        power="3",
        toughness="2",
        keywords=["Lifelink"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_other  # variable damage → "other"
    assert p.role_features.removal_burn_damage is None  # no fixed amount


def test_etb_counter_distribution_creature_auto() -> None:
    card = _scryfall(
        name="Earth King's Lieutenant",
        type_line="Creature — Human Soldier Ally",
        oracle_text=(
            "Trample\n"
            "When this creature enters, put a +1/+1 counter on each other "
            "Ally creature you control.\n"
            "Whenever another Ally you control enters, put a +1/+1 counter "
            "on this creature."
        ),
        mana_cost="{G}{W}",
        power="1",
        toughness="1",
        keywords=["Trample"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_other  # counter distribution → "other"


# ---------------------------------------------------------------------------
# Static self-modifiers + cards that previously bailed unnecessarily.
# ---------------------------------------------------------------------------


def test_static_self_modifier_creature_auto() -> None:
    # "This creature gets +1/+1 as long as <condition>" — static line we
    # ignore as noise. The creature still auto-classifies.
    card = _scryfall(
        name="First-Time Flyer",
        type_line="Creature — Human Pilot Ally",
        oracle_text=(
            "Flying\nThis creature gets +1/+1 as long as there's a Lesson card in your graveyard."
        ),
        mana_cost="{1}{U}",
        power="1",
        toughness="2",
        keywords=["Flying"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "flying" in p.evergreen_keywords


def test_variable_pt_creature_auto() -> None:
    # "This creature's power is equal to ..." — static, ignored.
    card = _scryfall(
        name="Dragonfly Swarm",
        type_line="Creature — Dragon Insect",
        oracle_text=(
            "Flying, ward {1}\n"
            "This creature's power is equal to the number of noncreature, "
            "nonland cards in your graveyard."
        ),
        mana_cost="{1}{U}{R}",
        power="*",
        toughness="3",
        keywords=["Flying", "Ward"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.power == "*"


def test_prowess_creature_auto() -> None:
    # Prowess is now in EVERGREEN_KEYWORDS — pure-keyword line accepted.
    card = _scryfall(
        name="Monastery Swiftspear",
        type_line="Creature — Human Monk",
        oracle_text="Haste\nProwess",
        mana_cost="{R}",
        power="1",
        toughness="2",
        keywords=["Haste", "Prowess"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "prowess" in p.evergreen_keywords


# ---------------------------------------------------------------------------
# Set-specific keyword bail.
# ---------------------------------------------------------------------------


def test_airbend_in_etb_trigger_treated_as_bounce_auto() -> None:
    # TLA's airbend mechanic is now treated as bounce (per design call).
    card = _scryfall(
        name="Aang",
        type_line="Legendary Creature — Avatar",
        oracle_text=("Flying\nWhen Aang enters, airbend up to one other target nonland permanent."),
        mana_cost="{3}{W}",
        power="3",
        toughness="2",
        keywords=["Flying"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_bounce is True


# ---------------------------------------------------------------------------
# Lands: non-mana activated abilities, "unless you control a basic land".
# ---------------------------------------------------------------------------


def test_land_with_nonmana_activated_ability_auto() -> None:
    # Agna Qel'a-style: tap-for-color + a separate utility activation.
    card = _scryfall(
        name="Agna Qel'a",
        type_line="Land",
        oracle_text=(
            "This land enters tapped unless you control a basic land.\n"
            "{T}: Add {U}.\n"
            "{2}{U}, {T}: Draw a card, then discard a card."
        ),
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.enter_condition is not None
    assert p.enter_condition.kind == "controls_basic_any"
    assert any(ab.produces == [["U"]] for ab in p.mana_abilities)
    # The non-mana activated ability shows up as a Mode.
    assert any(m.kind == "activated" for m in p.modes)


def test_land_sac_for_effect_auto() -> None:
    # Airship Engine Room-style: always-tapped + tap-for + sac-for-draw.
    card = _scryfall(
        name="Airship Engine Room",
        type_line="Land",
        oracle_text=(
            "This land enters tapped.\n"
            "{T}: Add {U} or {R}.\n"
            "{4}, {T}, Sacrifice this land: Draw a card."
        ),
        mana_cost="",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.enter_condition is not None
    assert p.enter_condition.kind == "always"
    activated = next(m for m in p.modes if m.kind == "activated")
    assert activated.cost.sacrifice is not None
    assert activated.cost.sacrifice.target == "self"


# ---------------------------------------------------------------------------
# Auras.
# ---------------------------------------------------------------------------


def test_removal_aura_auto() -> None:
    card = _scryfall(
        name="Pacifism",
        type_line="Enchantment — Aura",
        oracle_text="Enchant creature\nEnchanted creature can't attack or block.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_removal_aura is True
    assert p.role_features.is_pump_aura is False


def test_pump_aura_auto() -> None:
    card = _scryfall(
        name="Holy Strength",
        type_line="Enchantment — Aura",
        oracle_text="Enchant creature\nEnchanted creature gets +1/+2.",
        mana_cost="{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_pump_aura is True
    assert p.role_features.aura_pump_power == 1
    assert p.role_features.aura_pump_toughness == 2


# ---------------------------------------------------------------------------
# Vehicles / Equipment.
# ---------------------------------------------------------------------------


def test_vehicle_with_crew_auto() -> None:
    # Vehicle with evergreen keyword + dies-trigger + Crew. All three
    # categories of line are now recognised, so the card auto-classifies.
    card = _scryfall(
        name="Fire Nation Warship",
        type_line="Artifact — Vehicle",
        oracle_text=("Reach\nWhen this Vehicle dies, create a Clue token.\nCrew 2"),
        mana_cost="{3}",
        power="4",
        toughness="4",
        keywords=["Reach"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_vehicle is True
    # Clue tokens are artifacts, not creatures, so creates_creatures stays empty.
    assert p.role_features.creates_creatures == []


def test_equipment_with_equip_cost_auto() -> None:
    card = _scryfall(
        name="Bonesplitter",
        type_line="Artifact — Equipment",
        oracle_text="Equipped creature gets +2/+0.\nEquip {1}",
        mana_cost="{1}",
    )
    p = parse_card(card)
    # Equipment with a static "Equipped creature gets..." line still bails
    # in v1 — we don't yet model equipment buffs separately. Equip {N}
    # itself is recognised and ignored.
    # The role_features.is_equipment flag is set regardless.
    assert p.role_features.is_equipment is True


# ---------------------------------------------------------------------------
# Static / triggered ability tolerance (v2 design rule).
#
# Per the project owner: we generally ignore static and triggered abilities.
# Exceptions: triggered card-draw is captured in role_features.cards_drawn.
# Triggered mana production is acknowledged but not yet modelled in the
# simulator-side schema.
# ---------------------------------------------------------------------------


def test_creature_with_static_self_ref_auto() -> None:
    # Static "this creature can't ..." prose is silently dropped.
    card = _scryfall(
        name="Quiet Soldier",
        type_line="Creature — Soldier",
        oracle_text="This creature can't be the target of red spells.",
        mana_cost="{1}{W}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_creature_with_triggered_draw_captures_role_feature() -> None:
    card = _scryfall(
        name="Curious Tactician",
        type_line="Creature — Human",
        oracle_text="Whenever this creature attacks, draw a card.",
        mana_cost="{2}{U}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_drawn == 1


def test_creature_with_triggered_mana_auto() -> None:
    card = _scryfall(
        name="Upkeep Druid",
        type_line="Creature — Druid",
        oracle_text="At the beginning of your upkeep, add {G}.",
        mana_cost="{1}{G}",
        power="1",
        toughness="2",
    )
    p = parse_card(card)
    # Triggered mana production isn't blocked even though the schema doesn't
    # currently capture it on the simulator side.
    assert p.status is ParseStatus.AUTO


def test_creature_with_static_passive_prose_auto() -> None:
    card = _scryfall(
        name="Aggro Lord",
        type_line="Creature — Warrior",
        oracle_text="As long as you control three or more attackers, opponents lose 1 life.",
        mana_cost="{1}{R}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_enchantment_with_passive_static_auto() -> None:
    card = _scryfall(
        name="Banner",
        type_line="Enchantment",
        oracle_text="Creatures you control get +1/+0.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


# ---------------------------------------------------------------------------
# TLA bending mechanics.
# ---------------------------------------------------------------------------


def test_airbend_spell_treated_as_bounce_auto() -> None:
    card = _scryfall(
        name="Gust of Wind",
        type_line="Instant",
        oracle_text="Airbend up to one target nonland permanent.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_bounce is True


def test_earthbend_creates_creature_role_feature_auto() -> None:
    card = _scryfall(
        name="Earth Village Ruffians",
        type_line="Creature — Human Warrior",
        oracle_text="When this creature dies, earthbend 2.",
        mana_cost="{2}{G}",
        power="3",
        toughness="1",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    bodies = p.role_features.creates_creatures
    assert len(bodies) == 1
    assert bodies[0].power == "2"
    assert bodies[0].toughness == "2"


def test_waterbend_activated_mode_with_generic_cost_auto() -> None:
    card = _scryfall(
        name="Katara",
        type_line="Legendary Creature — Avatar",
        oracle_text="Waterbend 2 — {2}{U}, {T}: Draw a card.",
        mana_cost="{2}{U}",
        power="2",
        toughness="3",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    # Waterbend mode should appear as activated; its mana cost is the
    # demoted generic-only form (the {U} pip becomes generic, so the
    # activation cost is {3} rather than {2}{U}).
    activated = [m for m in p.modes if m.kind == "activated"]
    assert len(activated) == 1
    assert activated[0].cost.tap is True
    assert activated[0].cost.mana.cmc == 3
    assert activated[0].cost.mana.color_pips == {}


def test_firebending_silently_ignored_auto() -> None:
    card = _scryfall(
        name="Zuko, Banished Prince",
        type_line="Legendary Creature — Human Warrior",
        oracle_text=(
            "Haste\nFirebending — Whenever this creature attacks, it deals 1 damage to any target."
        ),
        mana_cost="{R}",
        power="1",
        toughness="2",
        keywords=["Haste"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


# ---------------------------------------------------------------------------
# MV >= 4 fast-path.
# ---------------------------------------------------------------------------


def test_high_mv_unrecognized_promoted_to_auto() -> None:
    # A 5-mana sorcery whose effect we don't recognize. Without the fast-path
    # it would NEEDS_LLM; with the fast-path it auto-classifies and gets
    # is_other set as a catchall flag.
    card = _scryfall(
        name="Some Big Spell",
        type_line="Sorcery",
        oracle_text=(
            "Until end of turn, target opponent's creatures attack each turn "
            "if able and can't block."
        ),
        mana_cost="{4}{R}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert any("fast-path" in r for r in p.reasons)
    assert p.role_features.is_other is True


def test_low_mv_unrecognized_still_needs_llm() -> None:
    card = _scryfall(
        name="Cryptic Trick",
        type_line="Instant",
        oracle_text="Until end of turn, target creature gains some weird effect we don't model.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


def test_high_mv_modal_card_still_needs_llm() -> None:
    card = _scryfall(
        name="Modal Choice",
        type_line="Sorcery",
        oracle_text=("Choose one —\n• Some weird effect.\n• Some other weird effect."),
        mana_cost="{3}{R}",
    )
    p = parse_card(card)
    # Modal text excludes the fast-path even at MV>=4.
    assert p.status is ParseStatus.NEEDS_LLM


def test_high_mv_with_alt_cost_still_needs_llm() -> None:
    card = _scryfall(
        name="Flashback Spell",
        type_line="Sorcery",
        oracle_text=("Some weird effect.\nFlashback {3}{B}"),
        mana_cost="{3}{B}{B}",
        keywords=["Flashback"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


def test_high_mv_with_cost_reduction_still_needs_llm() -> None:
    card = _scryfall(
        name="Affinity Beast",
        type_line="Creature — Beast",
        oracle_text=("Some unrecognized effect.\nAffinity for artifacts"),
        mana_cost="{4}{R}",
        power="4",
        toughness="4",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


def test_high_mv_creature_normal_path_still_auto() -> None:
    # Vanilla 4-mana creature was already AUTO before the fast-path; the
    # fast-path is a no-op for cards the normal path handles.
    card = _scryfall(
        name="Big Vanilla",
        type_line="Creature — Beast",
        oracle_text="",
        mana_cost="{3}{G}",
        power="4",
        toughness="4",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    # Should NOT have the fast-path reason — it parsed cleanly.
    assert not any("fast-path" in r for r in p.reasons)


# ---------------------------------------------------------------------------
# Wider effect coverage uncovered by the iterative LLM-encoding loop.
# ---------------------------------------------------------------------------


def test_combat_trick_until_eot_first_then_grants_keywords_auto() -> None:
    # Order-agnostic: "Until end of turn, ... gains <keywords>" should now
    # fire the combat-trick matcher (previously required "gains ... until
    # end of turn" in that order).
    card = _scryfall(
        name="Enter the Avatar State",
        type_line="Instant",
        oracle_text=(
            "Until end of turn, target creature you control becomes an Avatar "
            "in addition to its other types and gains flying, first strike, "
            "lifelink, and hexproof."
        ),
        mana_cost="{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert "flying" in p.role_features.combat_trick_granted_keywords
    assert "first strike" in p.role_features.combat_trick_granted_keywords
    assert "lifelink" in p.role_features.combat_trick_granted_keywords


def test_damage_to_attacking_or_blocking_creature_auto() -> None:
    card = _scryfall(
        name="Razor Rings",
        type_line="Instant",
        oracle_text=(
            "Razor Rings deals 4 damage to target attacking or blocking "
            "creature. You gain life equal to the excess damage dealt this way."
        ),
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.removal_burn_damage == 4


def test_counter_target_spell_auto() -> None:
    card = _scryfall(
        name="Cancel",
        type_line="Instant",
        oracle_text="Counter target spell.",
        mana_cost="{1}{U}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_other is True


def test_counter_pump_on_target_creature_auto() -> None:
    card = _scryfall(
        name="Jeong Jeong's Deserters",
        type_line="Creature — Human Rebel Ally",
        oracle_text="When this creature enters, put a +1/+1 counter on target creature.",
        mana_cost="{1}{W}",
        power="1",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_exile_until_leaves_treated_as_removal_auto() -> None:
    card = _scryfall(
        name="Earth Kingdom Jailer",
        type_line="Creature — Human Soldier Ally",
        oracle_text=(
            "When this creature enters, exile up to one target artifact, "
            "creature, or enchantment an opponent controls with mana value "
            "3 or greater until this creature leaves the battlefield."
        ),
        mana_cost="{2}{W}",
        power="3",
        toughness="3",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.removal_destroy_or_exile is True


def test_waterbend_bare_cost_form_auto() -> None:
    # Some TLA cards use "Waterbend {N}: <effect>" without a number/dash.
    card = _scryfall(
        name="Aang's Iceberg",
        type_line="Enchantment",
        oracle_text=("Flash\nWaterbend {3}: Sacrifice this enchantment. If you do, scry 2."),
        mana_cost="{2}{W}",
        keywords=["Flash"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_modal_choose_up_to_one_target_not_blocked() -> None:
    # "Choose up to one target X" is a target specification, NOT a modal
    # selection — the MV-fast-path should still apply for high-MV cards
    # with this phrasing.
    card = _scryfall(
        name="Targeted Spell",
        type_line="Sorcery",
        oracle_text="Choose up to one target creature, then deal 5 damage to it.",
        mana_cost="{4}{R}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_modal_choose_one_with_bullets_still_blocked_at_low_mv() -> None:
    card = _scryfall(
        name="Modal Spell",
        type_line="Sorcery",
        oracle_text=("Choose one —\n• Some weird effect.\n• Some other weird effect."),
        mana_cost="{1}{R}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
