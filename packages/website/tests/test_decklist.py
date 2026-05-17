"""Unit tests for the MTGA decklist parser.

The parser is a pure function over ``(text, CardStore) -> ParseResult``
so these tests don't need FastAPI's TestClient or any of the model /
simulation stack. They run on a clean checkout in milliseconds.
"""

from __future__ import annotations

# Absolute import — see test_app.py for the namespace-collision rationale.
from _website_factories import (  # type: ignore[import-not-found]
    goblin_decklist,
    goblin_decklist_with_one_bad_line,
    make_store,
)
from mulligan_coach_website.decklist import (
    expand_deck,
    parse_mtga_decklist,
    unique_card_names,
)


def test_parses_simple_decklist() -> None:
    """Happy path: 40 cards, no issues, valid."""
    result = parse_mtga_decklist(goblin_decklist(), make_store())
    assert result.is_valid
    assert result.total_cards == 40
    assert result.unique_cards == 2
    assert not result.unknown_lines


def test_keeps_good_lines_when_one_line_is_bad() -> None:
    """A typoed line surfaces as an issue but the rest still parses."""
    result = parse_mtga_decklist(goblin_decklist_with_one_bad_line(), make_store())
    assert not result.is_valid
    assert result.total_cards == 39  # the resolvable lines
    assert len(result.unknown_lines) == 1
    assert result.unknown_lines[0].reason == "not_found"


def test_stops_at_sideboard_header() -> None:
    """Lines past ``Sideboard`` don't shuffle into the goldfishing library."""
    text = "Deck\n17 Mountain\nSideboard\n1 Test Goblin\n"
    result = parse_mtga_decklist(text, make_store())
    assert result.total_cards == 17


def test_skips_about_and_deck_headers() -> None:
    """``About``, ``Deck``, ``Maindeck`` are silently consumed."""
    text = "About\nName Mono-R\nDeck\n40 Mountain\n"
    result = parse_mtga_decklist(text, make_store())
    assert result.total_cards == 40


def test_flags_bad_format() -> None:
    """Lines that don't match the ``<count> <name>`` shape become issues."""
    text = "Deck\nMountain\n"  # missing count
    result = parse_mtga_decklist(text, make_store())
    assert len(result.unknown_lines) == 1
    assert result.unknown_lines[0].reason == "bad_format"


def test_expand_deck_duplicates_by_count() -> None:
    """``expand_deck`` flattens ``count=N`` rows to N ParsedCard instances."""
    result = parse_mtga_decklist(goblin_decklist(), make_store())
    expanded = expand_deck(result.cards)
    assert len(expanded) == 40


def test_unique_card_names_is_deduplicated_in_deck_order() -> None:
    """The autocomplete datalist gets each name exactly once."""
    result = parse_mtga_decklist(goblin_decklist(), make_store())
    names = unique_card_names(result)
    assert names == ["Mountain", "Test Goblin"]
