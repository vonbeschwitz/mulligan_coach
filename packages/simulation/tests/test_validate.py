"""Tests for the deck-encoding validator.

The validator is the simulator's hard-fail gate: a deck with any card
that the cards parser couldn't fully classify must be rejected before
any sim runs, so we never silently treat a draw spell as a vanilla
creature.
"""

from __future__ import annotations

import pytest
from mulligan_coach_cards import ParseStatus
from mulligan_coach_simulation.validate import DeckEncodingError, check_deck_encodings

from . import _factories as f


def test_passes_on_fully_encoded_deck() -> None:
    deck = [
        f.forest(),
        f.forest(),
        f.llanowar_elves(),
        f.cultivate(),
        f.cantrip("Brainstorm", "{U}"),
    ]
    check_deck_encodings(deck)  # should not raise


def test_passes_on_llm_encoded_card() -> None:
    card = f.vanilla_creature("Hand-encoded", "{2}", 2, 2)
    card.status = ParseStatus.LLM_ENCODED
    check_deck_encodings([card])


def test_rejects_needs_llm() -> None:
    deck = [f.forest(), f.needs_llm("Inscrutable")]
    with pytest.raises(DeckEncodingError) as exc:
        check_deck_encodings(deck)
    assert "Inscrutable" in str(exc.value)
    assert "needs_llm" in str(exc.value)


def test_rejects_needs_human() -> None:
    deck = [f.needs_human("Borderline")]
    with pytest.raises(DeckEncodingError) as exc:
        check_deck_encodings(deck)
    assert "Borderline" in str(exc.value)
    assert "needs_human" in str(exc.value)


def test_rejects_castable_with_empty_modes() -> None:
    deck = [f.empty_modes_castable("Half")]
    with pytest.raises(DeckEncodingError) as exc:
        check_deck_encodings(deck)
    assert "Half" in str(exc.value)
    assert "modes is empty" in str(exc.value)


def test_lists_all_offenders_at_once() -> None:
    deck = [f.needs_llm("A"), f.needs_human("B"), f.empty_modes_castable("C")]
    with pytest.raises(DeckEncodingError) as exc:
        check_deck_encodings(deck)
    msg = str(exc.value)
    for n in ("A", "B", "C"):
        assert n in msg


def test_lands_without_mana_cost_pass() -> None:
    """Lands have ``mana_cost=None`` and an empty ``modes`` list — that's
    expected, not a failure. The empty-modes check only fires on cards
    that have a mana_cost (i.e. they're castable from hand)."""
    check_deck_encodings([f.forest(), f.etb_tapped_dual("Boros Guildgate", "R", "W")])
