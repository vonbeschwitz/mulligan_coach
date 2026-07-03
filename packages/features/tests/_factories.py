"""Synthetic ``ParsedCard`` factories for the feature-builder tests.

The simulation package has its own ``tests/_factories.py`` with a
similar shape; we don't import from it (cross-package test imports
make module-resolution surprises possible). Where the two factories
overlap (basic lands, vanilla creatures) the shapes match by
construction so any divergence is a local bug, not a coupling problem.

Designed for the feature builder's needs specifically:

* :func:`vanilla_creature` and :func:`spell` produce minimal castable
  cards with the role flags set explicitly. The deck-/hand-feature
  computations read ``role_features``, so tests can adjust flags
  directly without going through the parser.
* :func:`with_arena_id` is a tiny helper to set ``arena_id`` after
  construction — needed because the 17Lands lookups key on arena_id.
* :func:`make_shrunk` / :func:`make_zscores` produce the
  ``ShrunkWinRates`` / ``CardZScores`` map shapes the builders expect.
"""

from __future__ import annotations

from mulligan_coach_cards import (
    Cost,
    CreatureBody,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    ManaAbility,
    Mode,
    ParsedCard,
    ParseStatus,
    Predicate,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_cards.models import ManaOption
from mulligan_coach_features import CardZScores, ShrunkWinRates

_NEXT_OID = [0]


def _oid() -> str:
    _NEXT_OID[0] += 1
    return f"00000000-0000-0000-0000-{_NEXT_OID[0]:012d}"


# ---------------------------------------------------------------------------
# Lands
# ---------------------------------------------------------------------------


def basic(name: str, color: ManaOption, arena_id: int | None = None) -> ParsedCard:
    """A WUBRG basic land with a single mana ability."""
    subtype = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}[color]
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land — {subtype}",
        types=["Land"],
        subtypes=[subtype],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        enter_condition=None,
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )


def forest(arena_id: int | None = None) -> ParsedCard:
    return basic("Forest", "G", arena_id=arena_id)


def plains(arena_id: int | None = None) -> ParsedCard:
    return basic("Plains", "W", arena_id=arena_id)


def island(arena_id: int | None = None) -> ParsedCard:
    return basic("Island", "U", arena_id=arena_id)


def nonbasic_dual(
    name: str, c1: ManaOption, c2: ManaOption, arena_id: int | None = None
) -> ParsedCard:
    """ETB-tapped guildgate-style dual. Counts as a nonbasic land."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"This land enters tapped.\n{{T}}: Add {{{c1}}} or {{{c2}}}.",
        type_line="Land",
        types=["Land"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[c1], [c2]])],
        enter_condition=Predicate(kind="always"),
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )


# ---------------------------------------------------------------------------
# Spells (creatures, instants, sorceries)
# ---------------------------------------------------------------------------


def vanilla_creature(
    name: str,
    mana: str,
    power: int = 2,
    toughness: int = 2,
    arena_id: int | None = None,
) -> ParsedCard:
    """A creature whose only role flag is ``is_creature``.

    Useful for hand/deck counts where we want to put N creatures of a
    given MV in without any role-overlap weirdness.
    """
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"Vanilla {power}/{toughness}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost(mana),
        power=str(power),
        toughness=str(toughness),
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def burn_spell(
    name: str,
    mana: str,
    damage: int = 3,
    arena_id: int | None = None,
) -> ParsedCard:
    """An instant burn spell — sets removal_burn_damage."""
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"Deal {damage} damage to any target.",
        type_line="Instant",
        types=["Instant"],
        mana_cost=parse_mana_cost(mana),
        modes=[Mode(kind="cast", cost=cost, effects=[])],
        role_features=RoleFeatures(removal_burn_damage=damage),
        status=ParseStatus.AUTO,
    )


def counterspell_card(
    name: str = "Cancel",
    mana: str = "{1}{U}{U}",
    arena_id: int | None = None,
) -> ParsedCard:
    """Counter target spell. Sets ``is_counterspell``."""
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text="Counter target spell.",
        type_line="Instant",
        types=["Instant"],
        mana_cost=parse_mana_cost(mana),
        modes=[Mode(kind="cast", cost=cost, effects=[])],
        role_features=RoleFeatures(is_counterspell=True),
        status=ParseStatus.AUTO,
    )


def cantrip(name: str, mana: str, arena_id: int | None = None) -> ParsedCard:
    """Draw a card. Sets ``cards_drawn=1``."""
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text="Draw a card.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost(mana),
        modes=[Mode(kind="cast", cost=cost, effects=[DrawCardsEffect(n=1)])],
        role_features=RoleFeatures(cards_drawn=1),
        status=ParseStatus.AUTO,
    )


def cycler(name: str, mana: str, cycle_cost: str, arena_id: int | None = None) -> ParsedCard:
    """Vanilla creature with a cycling mode — exercises ``has_alt_mode``.

    The second Mode is a ``cycle`` whose cost is ``cycle_cost``. The
    parser uses ``Mode(kind="cycle")`` in real cards too.
    """
    cost = Cost(mana=parse_mana_cost(mana))
    cyc = Cost(mana=parse_mana_cost(cycle_cost), discard_self=True)
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"Vanilla creature. Cycling {cycle_cost}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost(mana),
        power="2",
        toughness="2",
        modes=[
            Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()]),
            Mode(kind="cycle", cost=cyc, effects=[DrawCardsEffect(n=1)]),
        ],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def ramp_sorcery(
    name: str = "Cultivate",
    mana: str = "{2}{G}",
    arena_id: int | None = None,
) -> ParsedCard:
    """Cultivate-style ramp: fetches a basic onto the battlefield tapped.

    Exercises ``is_ramp`` via the FetchLandEffect path.
    """
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text="Search your library for a basic land, put it onto the battlefield tapped.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost(mana),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    FetchLandEffect(target_filter="basic", destination="battlefield_tapped"),
                ],
            )
        ],
        role_features=RoleFeatures(),
        status=ParseStatus.AUTO,
    )


def mana_dork(name: str = "Llanowar Elves", arena_id: int | None = None) -> ParsedCard:
    """1-mana creature that taps for one color — mana dork."""
    cost = Cost(mana=parse_mana_cost("{G}"))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
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
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def token_maker(
    name: str = "Token Sorcery",
    mana: str = "{2}{W}",
    arena_id: int | None = None,
) -> ParsedCard:
    """Sorcery that creates a creature token. Sets ``creates_creatures``."""
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        arena_id=arena_id,
        rarity="uncommon",
        raw_oracle_text="Create a 2/2 white Knight creature token.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost(mana),
        modes=[Mode(kind="cast", cost=cost, effects=[])],
        role_features=RoleFeatures(
            creates_creatures=[CreatureBody(power="2", toughness="2", colors=["W"])],
        ),
        status=ParseStatus.AUTO,
    )


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------


def make_shrunk(
    name: str = "Test",
    *,
    mtga_id: int = 0,
    oh: float | None = 0.55,
    gd: float | None = 0.54,
    gih: float | None = 0.545,
    weight: float = 0.8,
) -> ShrunkWinRates:
    """Construct a ShrunkWinRates row for the given card name.

    The stats tables are keyed by folded card name (the arena_id-
    independent join key), so ``name`` is what the feature builder
    looks up — pass the card's name and key the dict by it. ``mtga_id``
    is retained only as an informational field.
    """
    return ShrunkWinRates(
        mtga_id=mtga_id,
        name=name,
        shrunk_opening_hand_win_rate=oh,
        shrunk_drawn_win_rate=gd,
        shrunk_ever_drawn_win_rate=gih,
        weight=weight,
    )


def make_zscores(
    name: str = "Test",
    *,
    mtga_id: int = 0,
    z_oh: float | None = 0.0,
    z_gd: float | None = 0.0,
    z_gih: float | None = 0.0,
) -> CardZScores:
    """Construct a CardZScores row for the given card name.

    Keyed by folded card name like :func:`make_shrunk`; ``mtga_id`` is
    informational only.
    """
    return CardZScores(
        mtga_id=mtga_id,
        name=name,
        z_opening_hand_win_rate=z_oh,
        z_drawn_win_rate=z_gd,
        z_ever_drawn_win_rate=z_gih,
    )
