"""Synthetic ``ParsedCard`` factories used by the simulation tests.

Hand-built Pydantic constructions, deliberately decoupled from the
real card data on disk: the tests must run on a fresh checkout without
having downloaded any sets, and they should exercise specific shapes
(filter lands, mana dorks, channel modes, …) that may or may not
appear in the current AUTO subset.

Add a new factory here when a test needs a card shape that doesn't yet
have one, rather than parameterising existing ones into a tangle.
"""

from __future__ import annotations

from mulligan_coach_cards import (
    Cost,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    ManaAbility,
    Mode,
    ParsedCard,
    ParseStatus,
    Predicate,
    SacrificeSpec,
    parse_mana_cost,
)
from mulligan_coach_cards.models import ManaOption

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_NEXT_OID = [0]


def _oid() -> str:
    """Return a fresh fake oracle_id. Tests don't care about uniqueness
    semantics, only that each card has a distinct id."""
    _NEXT_OID[0] += 1
    return f"00000000-0000-0000-0000-{_NEXT_OID[0]:012d}"


# ----------------------------------------------------------------------
# Basic lands
# ----------------------------------------------------------------------


def _basic(name: str, color: ManaOption, subtype: str) -> ParsedCard:
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land — {subtype}",
        types=["Land"],
        subtypes=[subtype],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def forest() -> ParsedCard:
    return _basic("Forest", "G", "Forest")


def plains() -> ParsedCard:
    return _basic("Plains", "W", "Plains")


def island() -> ParsedCard:
    return _basic("Island", "U", "Island")


def swamp() -> ParsedCard:
    return _basic("Swamp", "B", "Swamp")


def mountain() -> ParsedCard:
    return _basic("Mountain", "R", "Mountain")


# ----------------------------------------------------------------------
# Non-basic lands
# ----------------------------------------------------------------------


def etb_tapped_dual(name: str, c1: ManaOption, c2: ManaOption) -> ParsedCard:
    """A guildgate-style dual: enters tapped, taps for one of two colors."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"This land enters tapped.\n{{T}}: Add {{{c1}}} or {{{c2}}}.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[c1], [c2]])],
        enter_condition=Predicate(kind="always"),
        status=ParseStatus.AUTO,
    )


def evolving_wilds() -> ParsedCard:
    """Enters untapped; tap, sacrifice, search library for a basic land
    and put it onto the battlefield tapped."""
    return ParsedCard(
        name="Evolving Wilds",
        set_code="TST",
        collector_number="ew",
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text="{T}, Sacrifice this land: Search your library for a basic land card, "
        "put it onto the battlefield tapped, then shuffle.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        modes=[
            Mode(
                kind="activated",
                cost=Cost(tap=True, sacrifice=SacrificeSpec(target="self")),
                effects=[FetchLandEffect(target_filter="basic", destination="battlefield_tapped")],
            )
        ],
        mana_abilities=[],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def filter_land(name: str = "Filter Land") -> ParsedCard:
    """An untapped filter land with the canonical {1}, {T}: Add any color
    pattern. Enters untapped, has no other abilities."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="{1}, {T}: Add one mana of any color.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        mana_abilities=[
            ManaAbility(
                cost=Cost(mana=parse_mana_cost("{1}"), tap=True),
                produces=[["any"]],
            )
        ],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def two_ability_land(name: str = "Two-Ability Land") -> ParsedCard:
    """Gleaming Bastion (MSH) shape: one land with two separate {T}
    mana abilities — "{T}: Add {C}." and "{T}: Add {W} or {U}.". Only
    one can be activated per turn; the solver must never use both in
    a single payment."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="{T}: Add {C}.\n{T}: Add {W} or {U}.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        mana_abilities=[
            ManaAbility(cost=Cost(tap=True), produces=[["C"]]),
            ManaAbility(cost=Cost(tap=True), produces=[["W"], ["U"]]),
        ],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def colorless_plus_filter_land(name: str = "Colorless Filter Land") -> ParsedCard:
    """Surveillance Room (MSH) shape: "{T}: Add {C}." plus the filter
    ability "{1}, {T}: Add one mana of any color." on the same land.
    The {C} ability must not feed the filter's {1} — that would need
    two taps of one land."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text="{T}: Add {C}.\n{1}, {T}: Add one mana of any color.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        mana_abilities=[
            ManaAbility(cost=Cost(tap=True), produces=[["C"]]),
            ManaAbility(
                cost=Cost(mana=parse_mana_cost("{1}"), tap=True),
                produces=[["any"]],
            ),
        ],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


# ----------------------------------------------------------------------
# Creatures and spells
# ----------------------------------------------------------------------


def vanilla_creature(name: str, mana: str, power: int, toughness: int) -> ParsedCard:
    """Plain creature with a single cast Mode and an ETB effect.

    Useful for tests that just want "a card that costs X and can be
    cast" without the parser pulling in role features etc.
    """
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"Vanilla {power}/{toughness}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost(mana),
        power=str(power),
        toughness=str(toughness),
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        status=ParseStatus.AUTO,
    )


def cantrip(name: str, mana: str) -> ParsedCard:
    """Sorcery that draws one card."""
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text="Draw a card.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost(mana),
        modes=[Mode(kind="cast", cost=cost, effects=[DrawCardsEffect(n=1)])],
        status=ParseStatus.AUTO,
    )


def cultivate() -> ParsedCard:
    """{2}{G} sorcery: search for a basic, put one onto the battlefield
    tapped and one into your hand."""
    cost = Cost(mana=parse_mana_cost("{2}{G}"))
    return ParsedCard(
        name="Cultivate",
        set_code="TST",
        collector_number="cultivate",
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=(
            "Search your library for up to two basic land cards, reveal those "
            "cards, put one onto the battlefield tapped and the other into your "
            "hand, then shuffle."
        ),
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{2}{G}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    FetchLandEffect(target_filter="basic", destination="battlefield_tapped"),
                    FetchLandEffect(target_filter="basic", destination="hand"),
                ],
            )
        ],
        status=ParseStatus.AUTO,
    )


def llanowar_elves() -> ParsedCard:
    """{G} 1/1 mana dork."""
    cost = Cost(mana=parse_mana_cost("{G}"))
    return ParsedCard(
        name="Llanowar Elves",
        set_code="TST",
        collector_number="llanowar",
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text="{T}: Add {G}.",
        type_line="Creature — Elf Druid",
        types=["Creature"],
        subtypes=["Elf", "Druid"],
        mana_cost=parse_mana_cost("{G}"),
        power="1",
        toughness="1",
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[["G"]])],
        status=ParseStatus.AUTO,
    )


# ----------------------------------------------------------------------
# Cards that should fail validation
# ----------------------------------------------------------------------


def needs_llm(name: str = "Mystery Card") -> ParsedCard:
    """A card the deterministic parser couldn't classify."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="Do something inscrutable.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{1}{B}"),
        modes=[],
        status=ParseStatus.NEEDS_LLM,
        reasons=["weird sorcery, parser bailed"],
    )


def needs_human(name: str = "Borderline Card") -> ParsedCard:
    """An LLM-reviewed card flagged for human attention."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="Some borderline effect.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{2}"),
        modes=[],
        status=ParseStatus.NEEDS_HUMAN,
        reasons=["LLM uncertain"],
    )


def empty_modes_castable(name: str = "Half-encoded") -> ParsedCard:
    """An AUTO card that somehow ended up with mana_cost but no modes —
    the validator's other failure path."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="Should have at least a cast Mode.",
        type_line="Instant",
        types=["Instant"],
        mana_cost=parse_mana_cost("{R}"),
        modes=[],
        status=ParseStatus.AUTO,
    )
