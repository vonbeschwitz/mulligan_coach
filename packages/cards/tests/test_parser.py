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
    Cost,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    LookAtTopEffect,
    Mode,
    NoopEffect,
    ParseStatus,
)
from mulligan_coach_cards.parser import collect_drops, parse_card


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
    # is_land catchall: every Land-type card gets the flag from the
    # type-driven seeder, and is_other does NOT fire as a catchall.
    assert p.role_features.is_land
    assert not p.role_features.is_other


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
    assert p.role_features.is_land
    assert not p.role_features.is_other


def test_typed_dual_land_sets_is_land() -> None:
    # Shock-land shape: type line carries both basic subtypes, no oracle
    # mana ability — the parser synthesises one from the typed subtypes.
    card = _scryfall(
        name="Blood Crypt",
        type_line="Land — Swamp Mountain",
        oracle_text=(
            "({T}: Add {B} or {R}.)\n"
            "As Blood Crypt enters, you may pay 2 life. If you don't, it enters tapped."
        ),
        mana_cost="",
    )
    p = parse_card(card)
    assert "Land" in p.types
    assert p.role_features.is_land
    assert not p.role_features.is_other


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


def test_sac_fetch_land_auto() -> None:
    """A land whose only ability is sac-to-fetch (Evolving Wilds) now
    auto-classifies as a utility land with an activated mode and no
    mana ability. The activated mode carries a ``FetchLandEffect`` so
    the simulator can evaluate playability."""
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
    assert p.status is ParseStatus.AUTO
    assert "Land" in p.types
    assert p.role_features.is_land
    # No tap-for-mana — only the sac-fetch activation.
    assert p.mana_abilities == []
    assert len(p.modes) == 1
    activated = p.modes[0]
    assert activated.kind == "activated"
    assert activated.cost.tap is True
    assert activated.cost.sacrifice is not None
    assert activated.cost.sacrifice.target == "self"
    assert len(activated.effects) == 1
    fetch = activated.effects[0]
    assert fetch.kind == "fetch_land"
    assert fetch.target_filter == "basic"
    assert fetch.destination == "battlefield_tapped"


def test_land_with_no_abilities_at_all_needs_llm() -> None:
    """A land with neither a mana ability nor an activated ability is
    something weird — still NEEDS_LLM. Lands at minimum should do
    *something*, so the bail is meaningful."""
    card = _scryfall(
        name="Inert Plot",
        type_line="Land",
        oracle_text="",
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


def test_any_color_mana_dork_auto() -> None:
    # Great Forest Druid (ECL): the prose "Add one mana of any color"
    # form. Simulator treats "any" as a wildcard ManaOption.
    card = _scryfall(
        name="Great Forest Druid",
        type_line="Creature — Treefolk Druid",
        oracle_text="{T}: Add one mana of any color.",
        mana_cost="{1}{G}",
        power="0",
        toughness="4",
        colors=["G"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.mana_abilities) == 1
    assert p.mana_abilities[0].cost.tap is True
    assert p.mana_abilities[0].cost.mana.cmc == 0
    assert p.mana_abilities[0].produces == [["any"]]


def test_conditional_mana_dork_encodes_baseline_only() -> None:
    # Raucous Audience (TLA): unconditional baseline + a "creature with
    # power N+" conditional buff. Predicate has no kind for that, so we
    # encode the baseline ({G}) and silently drop the conditional half.
    card = _scryfall(
        name="Raucous Audience",
        type_line="Creature — Goblin",
        oracle_text=(
            "{T}: Add {G}. If you control a creature with power 4 or greater, add {G}{G} instead."
        ),
        mana_cost="{2}{G}",
        power="2",
        toughness="2",
        colors=["G"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.mana_abilities) == 1
    assert p.mana_abilities[0].produces == [["G"]]


def test_cost_prefix_mana_artifact_auto() -> None:
    # Filter mana rock: "{1}, {T}: Add one mana of any color." The
    # captured cost prefix is fed back through _parse_cost_string so
    # cost.mana carries the {1}.
    card = _scryfall(
        name="Filter Stone",
        type_line="Artifact",
        oracle_text="{1}, {T}: Add one mana of any color.",
        mana_cost="{1}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.mana_abilities) == 1
    assert p.mana_abilities[0].cost.tap is True
    assert p.mana_abilities[0].cost.mana.cmc == 1
    assert p.mana_abilities[0].produces == [["any"]]
    # Non-equipment, non-vehicle artifact with a mana ability → mana rock.
    assert p.role_features.is_mana_rock
    assert not p.role_features.is_equipment
    assert not p.role_features.is_other


def test_cost_only_no_tap_mana_artifact_auto() -> None:
    # Barrels of Blasting Jelly (TLA) shape: cost-only mana ability
    # without {T}, limited by a trailing "Activate only once each turn."
    # line we don't model. The mana ability must still be picked up so
    # the simulator counts the artifact as a mana source for castability,
    # and is_mana_rock must fire so the model sees the role flag.
    card = _scryfall(
        name="Barrels of Blasting Jelly",
        type_line="Artifact",
        oracle_text=(
            "{1}: Add one mana of any color. Activate only once each turn.\n"
            "{5}, {T}, Sacrifice this artifact: It deals 5 damage to target creature."
        ),
        mana_cost="{3}",
    )
    p = parse_card(card)
    assert len(p.mana_abilities) == 1
    ab = p.mana_abilities[0]
    assert ab.cost.tap is False
    assert ab.cost.mana.cmc == 1
    assert ab.produces == [["any"]]
    assert p.role_features.is_mana_rock
    assert not p.role_features.is_other


def test_artifact_mana_rock_with_untap_static_auto() -> None:
    # Bender's Waterskin (TLA) shape: static "Untap this artifact ..."
    # line tolerated via _STATIC_PREFIXES, plus an any-color mana
    # ability picked up by _extract_mana_ability and routed through
    # _parse_other_permanent's mana_abilities accumulator.
    card = _scryfall(
        name="Bender's Waterskin",
        type_line="Artifact",
        oracle_text=(
            "Untap this artifact during each other player's untap step.\n"
            "{T}: Add one mana of any color."
        ),
        mana_cost="{2}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.mana_abilities) == 1
    assert p.mana_abilities[0].cost.tap is True
    assert p.mana_abilities[0].produces == [["any"]]
    assert p.role_features.is_mana_rock
    assert not p.role_features.is_other


def test_restricted_mana_ability_dropped() -> None:
    # Hydro-Channeler (SOS) shape: a creature whose mana abilities are
    # gated by "Spend this mana only to cast an instant or sorcery spell".
    # The simulator can't model the restriction, so encoding the
    # unrestricted baseline would silently over-count mana availability
    # whenever the source is on the battlefield (a 4-drop becomes
    # castable on T3 off a 2-drop creature). We drop the ability instead.
    card = _scryfall(
        name="Hydro-Channeler",
        type_line="Creature — Merfolk Wizard",
        oracle_text=(
            "{T}: Add {U}. Spend this mana only to cast an instant or sorcery spell.\n"
            "{1}, {T}: Add one mana of any color. "
            "Spend this mana only to cast an instant or sorcery spell."
        ),
        mana_cost="{1}{U}",
        power="1",
        toughness="3",
    )
    p = parse_card(card)
    # Both abilities are restricted; the simulator should see Hydro as
    # a vanilla 1/3 with no usable mana production.
    assert p.mana_abilities == []
    # The role_features signal is unaffected — the mulligan-coach model
    # still gets the creature signal through other paths.
    assert p.role_features.is_creature
    assert not p.role_features.is_mana_rock


def test_partially_restricted_mana_ability_keeps_unrestricted_one() -> None:
    # White Lotus Hideout (TLA) shape: a land with three mana abilities,
    # one of which is restricted ("Spend this mana only to cast a Lesson
    # or Shrine spell."). The unrestricted abilities should survive.
    card = _scryfall(
        name="White Lotus Hideout",
        type_line="Land",
        oracle_text=(
            "{T}: Add {C}.\n"
            "{T}: Add one mana of any color. "
            "Spend this mana only to cast a Lesson or Shrine spell.\n"
            "{1}, {T}: Add one mana of any color."
        ),
    )
    p = parse_card(card)
    # The two unrestricted abilities survive; the middle one is dropped.
    produces = [a.produces for a in p.mana_abilities]
    assert produces == [[["C"]], [["any"]]]
    # And the {1} cost made it onto the surviving filter ability.
    costs_with_one = [a for a in p.mana_abilities if a.cost.mana.cmc == 1]
    assert len(costs_with_one) == 1


def test_vanilla_enchantment_falls_back_to_is_other() -> None:
    # Static "Players can't gain life." style enchantment: parses cleanly
    # (the _is_likely_static_or_triggered tolerance accepts it), no role
    # flag fires, so the universal catchall sets is_other=True.
    card = _scryfall(
        name="Lifeless Realm",
        type_line="Enchantment",
        oracle_text="Players can't gain life.",
        mana_cost="{2}{B}",
    )
    p = parse_card(card)
    # The exact status doesn't matter — what matters is that no role flag
    # ends up empty. The catchall should ensure is_other=True regardless.
    assert p.role_features.is_other
    # And no positive role flags should have spuriously fired.
    assert not p.role_features.is_creature
    assert not p.role_features.is_land
    assert not p.role_features.is_mana_rock


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


def test_basic_landcycling_two_word_fetches_basic() -> None:
    # "Basic landcycling {2}" is spelled as two words by Scryfall; it
    # previously fell to "unrecognised line" + MV fast-path, dropping the
    # cheap mana-fixing mode. It should emit a land_cycle fetching a basic.
    card = _scryfall(
        name="Savage Land Dinosaur",
        type_line="Creature — Dinosaur",
        oracle_text=(
            "Trample\n"
            "Basic landcycling {2} ({2}, Discard this card: Search your library "
            "for a basic land card, reveal it, put it into your hand, then shuffle.)"
        ),
        mana_cost="{5}{G}",
        power="6",
        toughness="6",
        keywords=["Basic landcycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert not any("fast-path" in r for r in p.reasons)
    cycle_mode = next(m for m in p.modes if m.kind == "land_cycle")
    fetch = cycle_mode.effects[0]
    assert isinstance(fetch, FetchLandEffect)
    assert fetch.target_filter == "basic"
    assert fetch.destination == "hand"


def test_plain_landcycling_fetches_any_land() -> None:
    card = _scryfall(
        name="Anyland Cycler",
        type_line="Creature — Scout",
        oracle_text="Landcycling {2}",
        mana_cost="{3}{G}",
        power="2",
        toughness="2",
        keywords=["Landcycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    fetch = next(m for m in p.modes if m.kind == "land_cycle").effects[0]
    assert isinstance(fetch, FetchLandEffect)
    assert fetch.target_filter == "any"


def test_sorcery_with_basic_landcycling_emits_mode() -> None:
    # The cycling mode must be recognised on non-creature types too — this
    # sorcery previously fast-pathed with the landcycling mode dropped.
    card = _scryfall(
        name="Borough Backup",
        type_line="Sorcery",
        oracle_text=(
            "Create two 3/2 white Hero creature tokens with vigilance.\n"
            "Basic landcycling {2} ({2}, Discard this card: Search your library "
            "for a basic land card, reveal it, put it into your hand, then shuffle.)"
        ),
        mana_cost="{4}{W}",
        keywords=["Basic landcycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert not any("fast-path" in r for r in p.reasons)
    assert "land_cycle" in [m.kind for m in p.modes]


def test_enchantment_with_cycling_emits_mode() -> None:
    # Plain cycling on a non-creature enchantment (was "enchantment:
    # unrecognised line: 'Cycling {2}'" + fast-path before the fix).
    card = _scryfall(
        name="Reconnaissance Mission",
        type_line="Enchantment",
        oracle_text=(
            "Whenever a creature you control deals combat damage to a player, "
            "you may draw a card.\n"
            "Cycling {2} ({2}, Discard this card: Draw a card.)"
        ),
        mana_cost="{3}{U}",
        keywords=["Cycling"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert not any("fast-path" in r for r in p.reasons)
    cycle_mode = next(m for m in p.modes if m.kind == "cycle")
    assert any(isinstance(e, DrawCardsEffect) for e in cycle_mode.effects)


def test_high_mv_discard_self_ability_not_fast_pathed() -> None:
    # Visionary's Dance: a 7-MV sorcery whose cheap "{2}, Discard this card:
    # look at top 2" filter mode is mulligan-relevant. The un-keyworded
    # discard-self ability isn't a cycling/channel keyword and the look-at-top
    # effect isn't auto-recognised, so the card must route to NEEDS_LLM for
    # hand review rather than being silently fast-pathed past it.
    card = _scryfall(
        name="Visionary's Dance",
        type_line="Sorcery",
        oracle_text=(
            "Create two 3/3 blue and red Elemental creature tokens with flying.\n"
            "{2}, Discard this card: Look at the top two cards of your library. "
            "Put one of them into your hand and the other into your graveyard."
        ),
        mana_cost="{5}{U}{R}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert not any("fast-path" in r for r in p.reasons)


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
    # role_features.creates_creatures — one entry PER TOKEN per the
    # revised guide §4 rule (2026-07-06).
    card = _scryfall(
        name="Raise the Alarm",
        type_line="Instant",
        oracle_text="Create two 1/1 white Soldier creature tokens.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.role_features.creates_creatures) == 2
    for body in p.role_features.creates_creatures:
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


def test_creature_with_triggered_draw_no_longer_credited() -> None:
    # §16 (enforced deterministically 2026-07-06): attack-trigger draw is
    # a recurring trigger — never credited. The card still auto-classifies.
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
    assert p.role_features.cards_drawn == 0


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
    # Non-death earthbend trigger still credits a body. (A *death*-trigger
    # earthbend does NOT — see test_death_trigger_earthbend_not_credited.)
    card = _scryfall(
        name="Earth Village Ruffians",
        type_line="Creature — Human Warrior",
        oracle_text="When this creature enters, earthbend 2.",
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
    assert p.role_features.is_counterspell is True
    assert p.role_features.is_other is False


def test_counter_target_creature_spell_auto() -> None:
    card = _scryfall(
        name="Essence Scatter",
        type_line="Instant",
        oracle_text="Counter target creature spell.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_counterspell is True
    assert p.role_features.is_other is False


def test_counter_target_spell_with_clause_auto() -> None:
    """Counterspells with trailing clauses ('unless …', 'with mana value …')
    still match — the regex anchors on the leading 'counter target spell'
    and ``\\b`` allows arbitrary continuation."""
    card = _scryfall(
        name="Mana Leak",
        type_line="Instant",
        oracle_text="Counter target spell unless its controller pays {3}.",
        mana_cost="{1}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_counterspell is True


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


# ---------------------------------------------------------------------------
# Sagas — encode chapter I only.
# ---------------------------------------------------------------------------


def test_saga_chapter_one_token_creation_auto() -> None:
    card = _scryfall(
        name="Test Saga",
        type_line="Enchantment — Saga",
        layout="saga",
        mana_cost="{2}{W}",
        oracle_text=(
            "(As this Saga enters and after your draw step, add a lore counter. "
            "Sacrifice after III.)\n"
            "I — Create two 1/1 white Soldier creature tokens.\n"
            "II — Search your library for a Plains card and put it onto the battlefield.\n"
            "III — Draw three cards."
        ),
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_saga is True
    # Chapter I creates two 1/1 Soldiers — one CreatureBody per token per
    # the revised guide §4 rule (2026-07-06).
    assert len(p.role_features.creates_creatures) == 2
    for body in p.role_features.creates_creatures:
        assert body.power == "1" and body.toughness == "1"
    # Chapter II / III effects are NOT carried — encoding chapter I only.
    assert p.role_features.cards_drawn == 0


def test_saga_combined_chapter_label_treated_as_chapter_one() -> None:
    card = _scryfall(
        name="Combined Saga",
        type_line="Enchantment — Saga",
        layout="saga",
        mana_cost="{1}{G}",
        oracle_text=(
            "(reminder)\nI, II — Create a 2/2 green Bear creature token.\nIII — Draw a card."
        ),
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_saga is True
    assert len(p.role_features.creates_creatures) == 1
    assert p.role_features.creates_creatures[0].power == "2"


def test_saga_with_unparseable_chapter_one_falls_to_mv4_fast_path() -> None:
    card = _scryfall(
        name="Big Saga",
        type_line="Enchantment — Saga",
        layout="saga",
        mana_cost="{3}{W}{W}",
        oracle_text=(
            "(reminder)\n"
            "I — Starting with you, each player chooses up to one permanent "
            "with mana value 3 or greater from among permanents your "
            "opponents control. Exile those permanents.\n"
            "II — Draw three cards.\n"
            "III — Exile this Saga, then return it to the battlefield "
            "transformed under your control."
        ),
    )
    p = parse_card(card)
    # MV=5 ≥ 4, no modal, no alt cost → fast-path promotes to AUTO.
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_saga is True
    # is_saga already counts as a category — fast-path shouldn't also set is_other.
    assert p.role_features.is_other is False


# ---------------------------------------------------------------------------
# Classes — encode level-1 (always-on) effect only.
# ---------------------------------------------------------------------------


def test_class_level_one_etb_token_creation_auto() -> None:
    card = _scryfall(
        name="Test Class",
        type_line="Enchantment — Class",
        layout="class",
        mana_cost="{G}",
        oracle_text=(
            "(Gain the next level as a sorcery to add its ability.)\n"
            "When this Class enters, create a 1/1 green Squirrel creature token.\n"
            "{1}{G}: Level 2\n"
            "Creatures you control get +1/+1.\n"
            "{4}{G}: Level 3\n"
            "Whenever a creature enters under your control, you gain 2 life."
        ),
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_class is True
    # Only level-1 ETB token-creation should be captured.
    assert len(p.role_features.creates_creatures) == 1
    body = p.role_features.creates_creatures[0]
    assert (body.power, body.toughness) == ("1", "1")


def test_class_level_one_triggered_draw_auto() -> None:
    card = _scryfall(
        name="Loot Class",
        type_line="Enchantment — Class",
        layout="class",
        mana_cost="{1}{R}",
        oracle_text=(
            "(reminder text)\n"
            "Whenever you attack, you may discard a card. If you do, draw a card.\n"
            "{1}{R}: Level 2\n"
            "Whenever you discard a card, this Class deals 2 damage to each opponent."
        ),
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_class is True


# ---------------------------------------------------------------------------
# Transform DFCs — collapse to front face when the back side is uncastable.
# ---------------------------------------------------------------------------


def test_transform_dfc_with_uncastable_back_collapses_to_front() -> None:
    card = _scryfall(
        name="Front Face // Back Face",
        type_line="Legendary Creature — Human // Legendary Creature — Avatar",
        layout="transform",
        mana_cost="",
        card_faces=[
            {
                "name": "Front Face",
                "mana_cost": "{2}{W}",
                "type_line": "Legendary Creature — Human",
                "oracle_text": (
                    "When this creature enters, create a 1/1 green and white "
                    "Kithkin creature token.\n"
                    "At the beginning of your first main phase, you may pay "
                    "{G}. If you do, transform this creature."
                ),
                "power": "2",
                "toughness": "2",
                "colors": ["W"],
                "keywords": [],
            },
            {
                "name": "Back Face",
                "mana_cost": "",  # uncastable — only enters via transform
                "type_line": "Legendary Creature — Avatar",
                "oracle_text": "{T}: Add {G} or {W}.",
                "power": "3",
                "toughness": "3",
                "colors": [],
                "keywords": [],
            },
        ],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    # Front-face ETB token captured.
    assert len(p.role_features.creates_creatures) >= 1
    body = p.role_features.creates_creatures[0]
    assert (body.power, body.toughness) == ("1", "1")
    # Display fields preserve the joint name / type line.
    assert p.name == "Front Face // Back Face"


def test_transform_dfc_with_castable_back_still_needs_llm() -> None:
    card = _scryfall(
        name="Front // Castable Back",
        type_line="Creature — Human // Creature — Werewolf",
        layout="transform",
        mana_cost="",
        card_faces=[
            {
                "name": "Front",
                "mana_cost": "{1}{R}",
                "type_line": "Creature — Human",
                "oracle_text": "Vanilla 2/1.",
                "power": "2",
                "toughness": "1",
                "colors": ["R"],
                "keywords": [],
            },
            {
                "name": "Castable Back",
                "mana_cost": "{2}{R}",  # castable in its own right
                "type_line": "Creature — Werewolf",
                "oracle_text": "Trample.",
                "power": "4",
                "toughness": "4",
                "colors": ["R"],
                "keywords": ["Trample"],
            },
        ],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


def test_transform_dfc_back_is_land_collapses_to_front() -> None:
    card = _scryfall(
        name="Front Saga // Back Land",
        type_line="Enchantment — Saga // Land",
        layout="transform",
        mana_cost="",
        card_faces=[
            {
                "name": "Front Saga",
                "mana_cost": "{1}{W}",
                "type_line": "Enchantment — Saga",
                "oracle_text": (
                    "(reminder)\n"
                    "I — Create a 1/1 white Soldier creature token.\n"
                    "II — Draw a card.\n"
                    "III — Exile this Saga, then return it transformed."
                ),
                "colors": ["W"],
                "keywords": [],
            },
            {
                "name": "Back Land",
                "mana_cost": "",
                "type_line": "Land",
                "oracle_text": "{T}: Add {W}.",
                "colors": [],
                "keywords": [],
            },
        ],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.is_saga is True
    assert len(p.role_features.creates_creatures) == 1


def test_creature_name_static_modifier_silently_dropped_auto() -> None:
    """A creature whose oracle text refers to it by its own name in a
    static line ("Sygg can't be blocked.") should auto-classify."""
    card = _scryfall(
        name="Sygg, Wanderwine Wisdom",
        type_line="Legendary Creature — Merfolk Wizard",
        oracle_text="Sygg can't be blocked.",
        mana_cost="{1}{U}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_creature_static_creature_spells_grant_silently_dropped_auto() -> None:
    """A creature with a static affecting other creature spells ("Creature
    spells you cast have convoke.") should auto-classify."""
    card = _scryfall(
        name="Big Convoke Lord",
        type_line="Creature — God",
        oracle_text=("Flying, lifelink\nCreature spells you cast have convoke."),
        mana_cost="{3}{W}{W}",
        power="5",
        toughness="5",
        keywords=["Flying", "Lifelink"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO


def test_unrelated_non_normal_layout_still_needs_llm() -> None:
    """Layouts we still don't model (split, adventure, modal_dfc, …) should
    keep the NEEDS_LLM bail with a clear reason."""
    card = _scryfall(
        name="Some Adventure",
        type_line="Creature — Human Adventurer",
        layout="adventure",
        mana_cost="{2}{R}",
        oracle_text="Some text.",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


# ---------------------------------------------------------------------------
# arena_id population from the MTGJSON-derived index
# ---------------------------------------------------------------------------


def test_arena_id_set_from_index() -> None:
    card = _scryfall(name="Test", oracle_id="oracle-a", set="TST")
    idx = {("oracle-a", "TST"): 12345}
    p = parse_card(card, arena_id_index=idx)
    assert p.arena_id == 12345


def test_arena_id_none_when_no_index() -> None:
    """Default behaviour preserves existing call sites — arena_id is None."""
    card = _scryfall(name="Test", oracle_id="oracle-a", set="TST")
    p = parse_card(card)
    assert p.arena_id is None


def test_arena_id_none_when_index_misses() -> None:
    """When MTGJSON hasn't ingested the printing yet, arena_id stays None."""
    card = _scryfall(name="Test", oracle_id="oracle-missing", set="TST")
    idx = {("oracle-other", "TST"): 99999}
    p = parse_card(card, arena_id_index=idx)
    assert p.arena_id is None


def test_arena_id_propagates_through_transform_dfc() -> None:
    """For transform DFCs we recurse with the synthetic front face — the
    recursion must thread the index through so arena_id still populates."""
    card = _scryfall(
        name="Front // Back",
        oracle_id="oracle-dfc",
        set="TST",
        layout="transform",
        type_line="Creature — Human // Land",
        mana_cost="{1}{G}",
        oracle_text="",
        # The synthetic front-face needs card_faces to recurse cleanly.
        card_faces=[
            {
                "name": "Front",
                "type_line": "Creature — Human",
                "mana_cost": "{1}{G}",
                "oracle_text": "",
                "colors": ["G"],
                "power": "2",
                "toughness": "2",
            },
            {
                "name": "Back",
                "type_line": "Land",
                "oracle_text": "{T}: Add {G}.",
                "colors": [],
            },
        ],
    )
    idx = {("oracle-dfc", "TST"): 55555}
    p = parse_card(card, arena_id_index=idx)
    assert p.arena_id == 55555


# ---------------------------------------------------------------------------
# LookAtTopEffect — round-trip through Pydantic to confirm the
# discriminated union picks it up from JSON and that field defaults work.
# ---------------------------------------------------------------------------


def test_look_at_top_effect_round_trip() -> None:
    fx = LookAtTopEffect(n=4, accepts_land=True)
    # Default for accepts_nonland.
    assert fx.accepts_nonland is True
    assert fx.destination == "hand"
    # Round-trip through JSON dump/load — exercises the discriminator.
    dumped = fx.model_dump()
    assert dumped["kind"] == "look_at_top"
    reloaded = LookAtTopEffect.model_validate(dumped)
    assert reloaded == fx


def test_mode_with_look_at_top_round_trip_via_discriminator() -> None:
    """A Mode whose effects list contains a LookAtTopEffect must
    deserialise back to the same concrete type via the Effect
    discriminated union."""
    mode = Mode(
        kind="cast",
        cost=Cost(),
        effects=[LookAtTopEffect(n=3, accepts_land=True, accepts_nonland=False)],
    )
    dumped = mode.model_dump()
    reloaded = Mode.model_validate(dumped)
    assert isinstance(reloaded.effects[0], LookAtTopEffect)
    assert reloaded.effects[0].n == 3
    assert reloaded.effects[0].accepts_land is True
    assert reloaded.effects[0].accepts_nonland is False


# ---------------------------------------------------------------------------
# Change 1 — token keywords captured from a trailing "with <keywords>" clause.
# ---------------------------------------------------------------------------


def test_token_keyword_single_menace() -> None:
    card = _scryfall(
        name="Villain Maker",
        type_line="Sorcery",
        oracle_text="Create a 2/1 black Villain creature token with menace.",
        mana_cost="{1}{B}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    body = p.role_features.creates_creatures[0]
    assert body.colors == ["B"]
    assert body.subtypes == ["Villain"]
    assert body.keywords == ["menace"]


def test_token_keyword_multiple_flying_and_haste() -> None:
    card = _scryfall(
        name="Elemental Caller",
        type_line="Sorcery",
        oracle_text="Create a 1/1 white Soldier creature token with flying and haste.",
        mana_cost="{2}{W}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.creates_creatures[0].keywords == ["flying", "haste"]


def test_token_keyword_comma_list_vigilance_reach() -> None:
    card = _scryfall(
        name="Guardian Grove",
        type_line="Sorcery",
        oracle_text="Create a 2/2 green Bear creature token with vigilance, reach.",
        mana_cost="{2}{G}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.creates_creatures[0].keywords == ["vigilance", "reach"]


def test_token_keyword_stops_at_first_non_keyword() -> None:
    # "with menace, then creatures you control get +1/+0" -> only ["menace"].
    card = _scryfall(
        name="Rally the Villains",
        type_line="Sorcery",
        oracle_text=(
            "Create a 2/1 black Villain creature token with menace, "
            "then creatures you control get +1/+0 until end of turn."
        ),
        mana_cost="{2}{B}",
    )
    p = parse_card(card)
    assert p.role_features.creates_creatures[0].keywords == ["menace"]


def test_token_keyword_stops_at_they_gain_prose() -> None:
    # "with haste and they gain lifelink." -> only ["haste"] (the "and they
    # gain ..." clause is prose, not a second keyword).
    card = _scryfall(
        name="Swift Recruits",
        type_line="Sorcery",
        oracle_text="Create a 1/1 white Soldier creature token with haste and they gain lifelink.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.role_features.creates_creatures[0].keywords == ["haste"]


def test_token_without_with_clause_has_no_keywords() -> None:
    card = _scryfall(
        name="Plain Token",
        type_line="Sorcery",
        oracle_text="Create a 1/1 white Soldier creature token.",
        mana_cost="{1}{W}",
    )
    p = parse_card(card)
    assert p.role_features.creates_creatures[0].keywords == []


# ---------------------------------------------------------------------------
# Change 2 — death triggers ("... dies") credit nothing.
# ---------------------------------------------------------------------------


def test_death_trigger_token_not_credited() -> None:
    # MSH "Agents of HYDRA" shape: a dies-trigger that creates a token must
    # NOT record a creates_creatures entry (owner convention 2026-07-06).
    card = _scryfall(
        name="Agents of HYDRA",
        type_line="Creature - Human Soldier",
        oracle_text="When this creature dies, create a 2/1 black Villain creature token with menace.",
        mana_cost="{2}{B}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.creates_creatures == []


def test_death_trigger_earthbend_not_credited() -> None:
    # TLA "When this dies, earthbend 2" - no creature body credited.
    card = _scryfall(
        name="Earth Village Ruffians",
        type_line="Creature - Human Warrior",
        oracle_text="When this creature dies, earthbend 2.",
        mana_cost="{2}{G}",
        power="3",
        toughness="1",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.creates_creatures == []


def test_death_trigger_draw_not_credited() -> None:
    # A dies-trigger draw does not bump cards_drawn.
    card = _scryfall(
        name="Grim Bequest",
        type_line="Creature - Zombie",
        oracle_text="When this creature dies, draw a card.",
        mana_cost="{2}{B}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_drawn == 0


def test_various_dies_phrasings_are_death_triggers() -> None:
    # "Whenever another ... dies" and "Whenever one or more ... die" count.
    for text in (
        "Whenever another Villain you control dies, create a 1/1 white Soldier creature token.",
        "Whenever one or more creatures die, draw a card.",
    ):
        card = _scryfall(
            name="Death Watcher",
            type_line="Creature - Cleric",
            oracle_text=text,
            mana_cost="{1}{B}",
            power="1",
            toughness="1",
        )
        p = parse_card(card)
        assert p.role_features.creates_creatures == []
        assert p.role_features.cards_drawn == 0


def test_non_death_enter_trigger_still_credits() -> None:
    # Contrast: an ETB (not a death trigger) still credits its token.
    card = _scryfall(
        name="Recruiter",
        type_line="Creature - Human Soldier",
        oracle_text="When this creature enters, create a 1/1 white Soldier creature token.",
        mana_cost="{2}{W}",
        power="2",
        toughness="2",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert len(p.role_features.creates_creatures) == 1


# ---------------------------------------------------------------------------
# Change 3 — _MODAL_RE recognises the period ("Choose one.") form.
# ---------------------------------------------------------------------------


def test_high_mv_modal_period_form_still_needs_llm() -> None:
    # MSH "Atlantis Attacks" templates modal as "Choose one." (period).
    # The MV>=4 fast-path must still exclude it.
    card = _scryfall(
        name="Atlantis Attacks",
        type_line="Sorcery",
        oracle_text="Choose one.\n* Some weird effect.\n* Some other weird effect.",
        mana_cost="{3}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert not any("fast-path" in r for r in p.reasons)


def test_high_mv_modal_end_of_line_form_still_needs_llm() -> None:
    card = _scryfall(
        name="Bare Modal",
        type_line="Sorcery",
        oracle_text="Choose one\n* Some weird effect.\n* Some other weird effect.",
        mana_cost="{3}{U}",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM


# ---------------------------------------------------------------------------
# Change 4 — unknown-keyword tripwire routes new mechanics to review.
# ---------------------------------------------------------------------------


def test_unknown_keyword_connive_demotes_to_needs_llm() -> None:
    # A card that would otherwise be AUTO (vanilla body) is demoted because
    # it carries a keyword the parser doesn't know.
    card = _scryfall(
        name="Conniving Rogue",
        type_line="Creature - Human Rogue",
        oracle_text="",
        mana_cost="{1}{U}",
        power="2",
        toughness="1",
        keywords=["Connive"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert any("unrecognised keyword 'connive'" in r for r in p.reasons)


def test_unknown_keyword_teamwork_trips() -> None:
    card = _scryfall(
        name="Team Player",
        type_line="Creature - Hero",
        oracle_text="",
        mana_cost="{2}{W}",
        power="3",
        toughness="3",
        keywords=["Teamwork"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert any("unrecognised keyword 'teamwork'" in r for r in p.reasons)


def test_known_extra_keyword_does_not_trip() -> None:
    # "surveil" is grandfathered in KNOWN_KEYWORDS_EXTRA - no demotion.
    card = _scryfall(
        name="Quiet Watcher",
        type_line="Creature - Human",
        oracle_text="",
        mana_cost="{1}{U}",
        power="1",
        toughness="3",
        keywords=["Surveil"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert not any("unrecognised keyword" in r for r in p.reasons)


def test_unknown_keyword_blocks_mv4_fast_path() -> None:
    # A high-MV card that would normally be fast-pathed to AUTO must stay
    # NEEDS_LLM when it carries an unknown keyword.
    card = _scryfall(
        name="Big Conniver",
        type_line="Creature - Beast",
        oracle_text="Some unrecognized effect.",
        mana_cost="{4}{G}",
        power="5",
        toughness="5",
        keywords=["Connive"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.NEEDS_LLM
    assert not any("fast-path" in r for r in p.reasons)
    assert any("unrecognised keyword 'connive'" in r for r in p.reasons)


# ---------------------------------------------------------------------------
# Change 5 / Change 6 — activated-ability mulligan-relevance (cmc) gate and
# the "power-up" label prefix.
# ---------------------------------------------------------------------------


def test_cheap_activated_loot_credits_role_features() -> None:
    # cmc <= 3 activation (Agna Qel'a precedent) credits its loot.
    card = _scryfall(
        name="Tidecaller",
        type_line="Creature - Merfolk Wizard",
        oracle_text="{1}{U}, {T}: Draw a card, then discard a card.",
        mana_cost="{2}{U}",
        power="1",
        toughness="1",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_manipulated == 1
    assert any(m.kind == "activated" for m in p.modes)


def test_expensive_activated_draw_not_credited_but_mode_built() -> None:
    # cmc > 3 activation builds a Mode for the simulator but credits NO
    # role_features (Bold Biochemist precedent, minus the label).
    card = _scryfall(
        name="Costly Cantripper",
        type_line="Creature - Wizard",
        oracle_text="{5}{U}: Draw two cards.",
        mana_cost="{3}{U}",
        power="3",
        toughness="3",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_drawn == 0
    activated = [m for m in p.modes if m.kind == "activated"]
    assert len(activated) == 1
    assert activated[0].cost.mana.cmc == 6


def test_power_up_label_strips_and_expensive_draw_not_credited() -> None:
    # MSH Bold Biochemist: "Power-up - {5}{U}: ... draw two cards" (cmc 6).
    # The label strips to a normal activated ability, the Mode is built, and
    # the cmc>3 gate keeps the draw off role_features.
    card = _scryfall(
        name="Bold Biochemist",
        type_line="Creature - Human Scientist",
        oracle_text="Power-up — {5}{U}: Draw two cards.",
        mana_cost="{3}{U}",
        power="3",
        toughness="3",
        keywords=["Power-up"],
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_drawn == 0
    activated = [m for m in p.modes if m.kind == "activated"]
    assert len(activated) == 1
    assert activated[0].cost.mana.cmc == 6


def test_unparseable_activated_cost_drops_draw_signal() -> None:
    # An activation whose cost we can't parse ("Pay 3 life") must not credit
    # its draw into role_features (the token scan is kept, but there's none).
    card = _scryfall(
        name="Blood Scholar",
        type_line="Creature - Vampire Wizard",
        oracle_text="{T}, Pay 3 life: Draw a card.",
        mana_cost="{1}{B}",
        power="1",
        toughness="1",
    )
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    assert p.role_features.cards_drawn == 0


# ---------------------------------------------------------------------------
# Change 7 — silent-drop census collector.
# ---------------------------------------------------------------------------


def test_collect_drops_records_death_trigger() -> None:
    card = _scryfall(
        name="Agents of HYDRA",
        type_line="Creature - Human Soldier",
        oracle_text="When this creature dies, create a 2/1 black Villain creature token with menace.",
        mana_cost="{2}{B}",
        power="2",
        toughness="2",
    )
    with collect_drops() as drops:
        parse_card(card)
    sites = [site for site, _ in drops]
    assert "death_trigger" in sites


def test_collect_drops_records_trigger_ignored() -> None:
    card = _scryfall(
        name="Aggressive Scout",
        type_line="Creature - Human Scout",
        oracle_text="Whenever this creature attacks, scry 1.",
        mana_cost="{1}{W}",
        power="2",
        toughness="1",
    )
    with collect_drops() as drops:
        parse_card(card)
    sites = [site for site, _ in drops]
    assert "trigger_ignored" in sites


def test_collect_drops_records_activated_cost_unparsed() -> None:
    card = _scryfall(
        name="Blood Scholar",
        type_line="Creature - Vampire Wizard",
        oracle_text="{T}, Pay 3 life: Draw a card.",
        mana_cost="{1}{B}",
        power="1",
        toughness="1",
    )
    with collect_drops() as drops:
        parse_card(card)
    sites = [site for site, _ in drops]
    assert "activated_cost_unparsed" in sites


def test_collect_drops_inactive_by_default() -> None:
    # Outside a collector context, parsing records nothing (no crash).
    card = _scryfall(
        name="Aggressive Scout",
        type_line="Creature - Human Scout",
        oracle_text="Whenever this creature attacks, scry 1.",
        mana_cost="{1}{W}",
        power="2",
        toughness="1",
    )
    # No collector installed - should simply parse without error.
    p = parse_card(card)
    assert p.status is ParseStatus.AUTO
    # A freshly-installed collector starts empty.
    with collect_drops() as drops:
        assert drops == []
        parse_card(card)
        assert len(drops) >= 1


# ---------------------------------------------------------------------------
# 2026-07-06 batch-2 parser fixes: multi-token counts, recurring-trigger
# draw skip, non-creature ETB effect wiring, vehicle mana abilities.
# ---------------------------------------------------------------------------


def test_multi_token_phrase_emits_one_body_per_token() -> None:
    """Guide §4: 'create two … tokens' emits two CreatureBody entries."""
    card = _scryfall(
        name="Borough Backup",
        type_line="Sorcery",
        mana_cost="{4}{W}",
        oracle_text="Create two 3/2 white Hero creature tokens with vigilance.",
    )
    parsed = parse_card(card)
    bodies = parsed.role_features.creates_creatures
    assert len(bodies) == 2
    assert all(b.power == "3" and b.toughness == "2" for b in bodies)
    assert all(b.keywords == ["vigilance"] for b in bodies)


def test_single_token_phrase_still_emits_one_body() -> None:
    card = _scryfall(
        name="Lone Token Maker",
        type_line="Sorcery",
        mana_cost="{1}{W}",
        oracle_text="Create a 1/1 white Soldier creature token.",
    )
    parsed = parse_card(card)
    assert len(parsed.role_features.creates_creatures) == 1


def test_recurring_trigger_draw_not_credited() -> None:
    """Guide §16: 'Whenever …' draw/scry never credits role_features."""
    card = _scryfall(
        name="Engine Creature",
        type_line="Creature — Human",
        mana_cost="{2}{U}",
        power="2",
        toughness="2",
        oracle_text="Whenever a creature you control enters, scry 1.",
    )
    parsed = parse_card(card)
    assert parsed.role_features.cards_manipulated == 0
    assert parsed.role_features.cards_drawn == 0


def test_upkeep_trigger_draw_not_credited() -> None:
    card = _scryfall(
        name="Upkeep Engine",
        type_line="Enchantment",
        mana_cost="{1}{U}",
        oracle_text="At the beginning of your upkeep, draw a card, then discard a card.",
    )
    parsed = parse_card(card)
    assert parsed.role_features.cards_drawn == 0
    assert parsed.role_features.cards_manipulated == 0


def test_one_shot_etb_scry_still_credited() -> None:
    """One-shot 'When … enters' triggers keep crediting (A.I.M. Synthoids)."""
    card = _scryfall(
        name="Scrying Golem",
        type_line="Artifact Creature — Golem",
        mana_cost="{2}",
        power="1",
        toughness="3",
        oracle_text="When this creature enters, scry 2.",
    )
    parsed = parse_card(card)
    assert parsed.role_features.cards_manipulated == 2


def test_artifact_etb_draw_wired_onto_cast_mode() -> None:
    """Non-creature permanents wire self-ETB effects (MSH Futurist Forge)."""
    card = _scryfall(
        name="Futurist Forge",
        type_line="Artifact",
        mana_cost="{1}{U}",
        oracle_text="When this artifact enters, draw a card.",
    )
    parsed = parse_card(card)
    assert parsed.status is ParseStatus.AUTO
    cast = next(m for m in parsed.modes if m.kind == "cast")
    kinds = [e.kind for e in cast.effects]
    assert "enters_battlefield" in kinds
    assert "draw_cards" in kinds
    assert parsed.role_features.cards_drawn == 1


def test_vehicle_mana_ability_recognised() -> None:
    """Vehicles with '{T}: Add …' get mana_abilities (MSH Dependable Quinjet)."""
    card = _scryfall(
        name="Dependable Quinjet",
        type_line="Artifact — Vehicle",
        mana_cost="{3}",
        power="3",
        toughness="3",
        keywords=["Flying", "Crew"],
        oracle_text=(
            "Flying\n{T}: Add one mana of any color.\n"
            "Crew 4 (Tap any number of creatures you control with total power "
            "4 or more: This Vehicle becomes an artifact creature until end of turn.)"
        ),
    )
    parsed = parse_card(card)
    assert parsed.status is ParseStatus.AUTO
    assert len(parsed.mana_abilities) == 1
    assert parsed.mana_abilities[0].produces == [["any"]]
    # §1: vehicles are excluded from is_mana_rock by definition.
    assert parsed.role_features.is_mana_rock is False


def test_equipment_trigger_token_not_double_counted() -> None:
    """The removed top-level scan must not double-count trigger tokens."""
    card = _scryfall(
        name="Banner of Troops",
        type_line="Artifact — Equipment",
        mana_cost="{2}",
        oracle_text=(
            "When this Equipment enters, create a 1/1 white Soldier creature token.\nEquip {1}"
        ),
    )
    parsed = parse_card(card)
    assert len(parsed.role_features.creates_creatures) == 1


def test_negative_phrasing_restricted_mana_dropped() -> None:
    """ "This mana can't be spent to cast a nonartifact spell." is the same
    restriction as "Spend this mana only …" — drop the ability (MSH
    Hydraulic Helper, batch-3 commons audit 2026-07-07)."""
    card = _scryfall(
        name="Hydraulic Helper",
        type_line="Artifact Creature — Robot",
        mana_cost="{1}{U}",
        power="2",
        toughness="3",
        keywords=["Defender"],
        oracle_text=(
            "Defender\n{T}: Add {U}. This mana can't be spent to cast a nonartifact spell."
        ),
    )
    parsed = parse_card(card)
    assert parsed.status is ParseStatus.AUTO
    assert parsed.mana_abilities == []


def test_cast_trigger_token_not_credited() -> None:
    """Owner ruling 2026-07-07 (guide §4): only self-ETB triggers credit
    tokens — Sokka, Tenacious Tactician's cast trigger no longer counts."""
    card = _scryfall(
        name="Sokka, Tenacious Tactician",
        type_line="Legendary Creature — Human Ally",
        mana_cost="{2}{W}",
        power="3",
        toughness="3",
        oracle_text="Whenever you cast a noncreature spell, create a 1/1 white Ally creature token.",
    )
    parsed = parse_card(card)
    assert parsed.role_features.creates_creatures == []


def test_attack_trigger_token_not_credited() -> None:
    card = _scryfall(
        name="Suki, Kyoshi Warrior",
        type_line="Legendary Creature — Human Ally",
        mana_cost="{1}{W}",
        power="2",
        toughness="2",
        oracle_text=(
            "Whenever Suki attacks, create a 1/1 white Ally creature token "
            "that's tapped and attacking."
        ),
    )
    parsed = parse_card(card)
    assert parsed.role_features.creates_creatures == []


def test_upkeep_trigger_token_not_credited() -> None:
    card = _scryfall(
        name="Bitterblossom",
        type_line="Tribal Enchantment — Faerie",
        mana_cost="{1}{B}",
        oracle_text=(
            "At the beginning of your upkeep, you lose 1 life and create a "
            "1/1 black Faerie Rogue creature token with flying."
        ),
    )
    parsed = parse_card(card)
    assert parsed.role_features.creates_creatures == []


def test_self_etb_whenever_form_still_credits() -> None:
    """ECL Brigid templating: 'Whenever this creature enters or transforms
    into …' is the permanent's OWN entry — tokens and draws still count."""
    card = _scryfall(
        name="Brigid, Clachan's Heart",
        type_line="Creature — Kithkin",
        mana_cost="{2}{W}",
        power="2",
        toughness="2",
        oracle_text=(
            "Whenever this creature enters or transforms into Brigid, Clachan's "
            "Heart, create a 1/1 green and white Kithkin creature token."
        ),
    )
    parsed = parse_card(card)
    assert len(parsed.role_features.creates_creatures) == 1


def test_plain_self_etb_token_still_credits() -> None:
    card = _scryfall(
        name="Drone Carrier",
        type_line="Artifact Creature — Robot",
        mana_cost="{2}{U}",
        power="2",
        toughness="2",
        oracle_text="When this creature enters, create a 1/1 white Soldier creature token.",
    )
    parsed = parse_card(card)
    assert len(parsed.role_features.creates_creatures) == 1


def test_attack_trigger_earthbend_not_credited() -> None:
    """Attack-trigger earthbend bodies no longer count either (the old
    'Sokka precedent' carve-out is superseded by the 2026-07-07 ruling)."""
    card = _scryfall(
        name="Boulder Slinger",
        type_line="Creature — Human",
        mana_cost="{2}{G}",
        power="2",
        toughness="3",
        oracle_text="Whenever this creature attacks, earthbend 2.",
    )
    parsed = parse_card(card)
    assert parsed.role_features.creates_creatures == []


def test_enters_or_leaves_compound_self_etb_token_credits() -> None:
    """'When this artifact enters or leaves the battlefield, create …'
    fires on the permanent's own entry (TMT Mouser Foundry) — the token
    counts even though the plain _ETB_RE shape doesn't match."""
    card = _scryfall(
        name="Mouser Foundry",
        type_line="Artifact",
        mana_cost="{2}",
        oracle_text=(
            "When this artifact enters or leaves the battlefield, create a "
            "1/1 colorless Robot artifact creature token."
        ),
    )
    parsed = parse_card(card)
    assert len(parsed.role_features.creates_creatures) == 1
