"""Hand-built ParsedCard fixtures for the website tests.

We don't load anything from `data/processed/parsed_cards/` here —
the persisted JSON is environment-dependent (only present after a
detector run) and we want the tests to pass on a clean checkout.
Instead, the fixtures construct a tiny self-contained card store via
:func:`make_store` that is sufficient for the decklist parser, hand
mutations, and route smoke tests.
"""

from __future__ import annotations

from mulligan_coach_cards import (
    Cost,
    EntersBattlefieldEffect,
    Mode,
    ParsedCard,
    ParseStatus,
    parse_mana_cost,
)
from mulligan_coach_website.data import CardStore, synthetic_basics


def vanilla_one_drop() -> ParsedCard:
    """A simple {R} 1/1 creature for testing the deck parser / hand builder."""
    mana = parse_mana_cost("{R}")
    cost = Cost(mana=mana)
    return ParsedCard(
        name="Test Goblin",
        set_code="TST",
        collector_number="001",
        oracle_id="11111111-1111-1111-1111-111111111111",
        rarity="common",
        raw_oracle_text="Vanilla 1/1.",
        type_line="Creature — Goblin",
        types=["Creature"],
        subtypes=["Goblin"],
        supertypes=[],
        mana_cost=mana,
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        power="1",
        toughness="1",
        status=ParseStatus.AUTO,
    )


def make_store() -> CardStore:
    """Return a tiny CardStore: one test creature + synthetic basics.

    The store is built by hand (not via ``CardStore.build``) because
    we don't want to depend on whatever sets the developer happens
    to have parsed on disk.
    """
    entries = [vanilla_one_drop(), *synthetic_basics()]
    by_id: dict[tuple[str, str], ParsedCard] = {}
    by_name: dict[str, ParsedCard] = {}
    for card in entries:
        if card.set_code != "BASIC":
            by_id.setdefault((card.set_code.upper(), card.collector_number), card)
        by_name.setdefault(card.name.lower(), card)
    return CardStore(entries=entries, by_id=by_id, by_name=by_name, loaded_sets=["TST"])


def goblin_decklist() -> str:
    """Returns a 40-card mono-R MTGA-format decklist using the test fixtures."""
    return "Deck\n17 Mountain (TST) 270\n23 Test Goblin (TST) 001\n"


def goblin_decklist_with_one_bad_line() -> str:
    """40-card decklist with one unresolvable card; the rest parse cleanly."""
    return "Deck\n17 Mountain (TST) 270\n22 Test Goblin (TST) 001\n1 Nonexistent Card (TST) 999\n"
