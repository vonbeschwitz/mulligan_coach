"""Tests for the card-name stats join (``stats_join``).

Covers the two helpers the arena_id → folded-name migration introduced:

* :func:`fold_card_name` — identity on ASCII, strips combining marks.
* :func:`stats_for_card` — exact folded-name hit, DFC front-face
  fallback, diacritic-fold hit, and miss → None.
"""

from __future__ import annotations

import _factories as f  # type: ignore[import-not-found]
from mulligan_coach_features import fold_card_name, stats_for_card

# ---------------------------------------------------------------------------
# fold_card_name
# ---------------------------------------------------------------------------


def test_fold_identity_on_ascii() -> None:
    assert fold_card_name("Bespoke Bo") == "Bespoke Bo"
    assert fold_card_name("Lightning Bolt") == "Lightning Bolt"
    assert fold_card_name("") == ""


def test_fold_strips_macron() -> None:
    # 17Lands spells "Bespoke Bō" without the macron; folding both sides
    # makes them match.
    assert fold_card_name("Bespoke Bō") == "Bespoke Bo"


def test_fold_strips_various_diacritics() -> None:
    assert fold_card_name("Juzám Djinn") == "Juzam Djinn"
    assert fold_card_name("Æther") == "Æther"  # Æ is not a combining mark → unchanged


# ---------------------------------------------------------------------------
# stats_for_card
# ---------------------------------------------------------------------------


def test_exact_folded_name_hit() -> None:
    card = f.vanilla_creature("Bear", "{1}{G}")
    table = {"Bear": "hit"}
    assert stats_for_card(card, table) == "hit"


def test_dfc_front_face_fallback() -> None:
    # ParsedCard.name is the joint "Front // Back"; 17Lands names only
    # the front face, so the join falls back to the front.
    card = f.vanilla_creature("Front // Back", "{G}")
    table = {"Front": "hit"}
    assert stats_for_card(card, table) == "hit"


def test_diacritic_fold_hit() -> None:
    # Card carries the macron; the ratings row (table key) is the folded,
    # macron-free spelling.
    card = f.vanilla_creature("Bespoke Bō", "{1}")
    table = {"Bespoke Bo": "hit"}
    assert stats_for_card(card, table) == "hit"


def test_miss_returns_none() -> None:
    card = f.vanilla_creature("Unknown Card", "{G}")
    assert stats_for_card(card, {"Bear": "hit"}) is None
    assert stats_for_card(card, {}) is None


def test_non_dfc_miss_does_not_refetch_full_name() -> None:
    # A plain (non-DFC) name has no " // " to split on, so the fallback
    # branch is skipped and the miss is clean.
    card = f.vanilla_creature("Solo", "{G}")
    assert stats_for_card(card, {"Solo // Extra": "hit"}) is None
