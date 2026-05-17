"""Unit tests for the hand-state helpers.

Pure functions over `(ParseResult, hand_ids) -> ...`. The decklist
parser tests already cover the decklist plumbing; here we exercise
just the hand-side mutations and resolution.
"""

from __future__ import annotations

import random

# Absolute import — see test_app.py for the namespace-collision rationale.
from _website_factories import goblin_decklist, make_store  # type: ignore[import-not-found]
from mulligan_coach_website.data import CardStore
from mulligan_coach_website.decklist import ParseResult, parse_mtga_decklist
from mulligan_coach_website.hand import (
    HAND_SIZE,
    add_card_by_name,
    card_to_id,
    random_hand,
    remove_at,
    resolve_hand,
)


def _parsed() -> tuple[CardStore, ParseResult]:
    """Tiny helper: returns (CardStore, ParseResult) for the test decklist."""
    store = make_store()
    return store, parse_mtga_decklist(goblin_decklist(), store)


def test_random_hand_returns_seven_distinct_slots() -> None:
    """``random_hand`` samples without replacement and matches HAND_SIZE."""
    _, parsed = _parsed()
    rng = random.Random(0)
    hand = random_hand(parsed, rng=rng)
    assert len(hand) == HAND_SIZE


def test_random_hand_fails_when_deck_is_smaller_than_hand_size() -> None:
    """Sampling 7 from <7 cards raises a ValueError (caller surfaces it)."""
    store = make_store()
    parsed = parse_mtga_decklist("Deck\n3 Mountain", store)
    raised = False
    try:
        random_hand(parsed)
    except ValueError:
        raised = True
    assert raised


def test_add_card_by_name_appends_one_copy() -> None:
    """Adding a card name appends a matching id to the hand-id list."""
    _, parsed = _parsed()
    hand, err = add_card_by_name(parsed, [], "Mountain")
    assert err is None
    assert len(hand) == 1


def test_add_card_by_name_rejects_unknown_name() -> None:
    """Unknown names surface a user-facing error rather than blowing up."""
    _, parsed = _parsed()
    hand, err = add_card_by_name(parsed, [], "Definitely Not A Card")
    assert hand == []
    assert err is not None
    assert "Definitely Not A Card" in err


def test_add_card_by_name_rejects_overflow_of_available_copies() -> None:
    """Adding more copies than the deck contains is blocked."""
    _, parsed = _parsed()
    # 23 Test Goblins in deck; try adding 4 (we only have 7 slots anyway).
    hand: list[str] = []
    for _ in range(4):
        hand, err = add_card_by_name(parsed, hand, "Test Goblin")
        assert err is None
    assert len(hand) == 4


def test_remove_at_drops_the_indexed_slot() -> None:
    """``remove_at`` returns a new list with the indexed entry removed."""
    out = remove_at(["a", "b", "c"], 1)
    assert out == ["a", "c"]


def test_remove_at_silently_ignores_out_of_range() -> None:
    """Out-of-range indices are a no-op (stale click protection)."""
    out = remove_at(["a", "b"], 5)
    assert out == ["a", "b"]


def test_resolve_hand_returns_card_and_library() -> None:
    """``resolve_hand`` returns aligned hand entries and the remaining library."""
    _, parsed = _parsed()
    hand_ids = [
        card_to_id(parsed.cards[1].card),  # Test Goblin
        card_to_id(parsed.cards[0].card),  # Mountain
    ]
    entries, library, errors = resolve_hand(parsed, hand_ids)
    assert errors == []
    assert len(entries) == 2
    assert entries[0].card is not None
    assert entries[0].card.name == "Test Goblin"
    assert entries[1].card is not None
    assert entries[1].card.name == "Mountain"
    assert len(library) == 38


def test_resolve_hand_surfaces_unknown_ids_as_broken_entries() -> None:
    """An id with no remaining copies becomes a broken entry + error."""
    _, parsed = _parsed()
    entries, _library, errors = resolve_hand(parsed, ["FAKE:999"])
    assert len(entries) == 1
    assert entries[0].card is None
    assert len(errors) == 1
