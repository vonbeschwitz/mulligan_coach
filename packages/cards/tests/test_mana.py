"""Tests for the mana-cost parser."""

from __future__ import annotations

import pytest
from mulligan_coach_cards.mana import parse_mana_cost


def test_empty_cost_is_zero_cmc() -> None:
    cost = parse_mana_cost("")
    assert cost.cmc == 0
    assert cost.color_pips == {}
    assert cost.pips == []
    assert not cost.has_x


def test_simple_two_color_cost() -> None:
    cost = parse_mana_cost("{2}{W}{U}")
    assert cost.cmc == 4
    assert cost.color_pips == {"W": 1, "U": 1}
    assert not cost.has_hybrid
    assert not cost.has_x
    # Per-pip structural breakdown.
    kinds = [p.kind for p in cost.pips]
    assert kinds == ["generic", "color", "color"]
    assert cost.pips[0].amount == 2
    assert cost.pips[1].color == "W"
    assert cost.pips[2].color == "U"


def test_x_cost() -> None:
    cost = parse_mana_cost("{X}{R}")
    assert cost.cmc == 1  # X contributes 0 at parse time
    assert cost.color_pips == {"R": 1}
    assert cost.has_x
    assert [p.kind for p in cost.pips] == ["x", "color"]


def test_zero_cost() -> None:
    cost = parse_mana_cost("{0}")
    assert cost.cmc == 0
    assert cost.color_pips == {}
    assert cost.pips[0].kind == "generic"
    assert cost.pips[0].amount == 0


def test_two_color_hybrid() -> None:
    cost = parse_mana_cost("{W/U}{2}")
    assert cost.cmc == 3
    assert cost.has_hybrid
    # Neither color is mandatory, so neither shows up in pips.
    assert cost.color_pips == {}
    assert cost.pips[0].kind == "hybrid"
    assert cost.pips[0].colors == ["W", "U"]


def test_two_generic_hybrid() -> None:
    cost = parse_mana_cost("{2/W}{2/W}")
    # Worst-case CMC counts the higher side per pip.
    assert cost.cmc == 4
    assert cost.has_hybrid
    assert cost.color_pips == {}
    assert all(p.kind == "two_or_color" for p in cost.pips)
    assert all(p.amount == 2 and p.color == "W" for p in cost.pips)


def test_phyrexian() -> None:
    cost = parse_mana_cost("{B/P}")
    assert cost.cmc == 1
    assert cost.has_phyrexian
    assert cost.pips[0].kind == "phyrexian"
    assert cost.pips[0].colors == ["B"]


def test_colorless_and_snow() -> None:
    cost = parse_mana_cost("{C}{S}")
    assert cost.cmc == 2
    assert cost.has_colorless
    assert cost.has_snow
    assert [p.kind for p in cost.pips] == ["colorless", "snow"]


def test_unrecognised_symbol_raises() -> None:
    with pytest.raises(ValueError):
        parse_mana_cost("{wat}")


def test_text_outside_braces_raises() -> None:
    with pytest.raises(ValueError):
        parse_mana_cost("{2}xx{W}")
