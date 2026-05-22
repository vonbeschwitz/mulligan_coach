"""Tests for the overlay's stats panel HTML builder.

The module under test (:mod:`stats_html`) is intentionally Qt-free so
these tests run on headless CI — importing the gui module would pull
in PyQt6 and fail to load libEGL on the Linux runner.

Covers:

* :func:`_format_mana_cost` — Scryfall mana-cost strings render
  compactly, with braces preserved only for multi-character pips.
* :func:`build_stats_html` — given a synthetic
  :class:`RecommendationExplanation`, the produced HTML contains the
  4x3 turn grid, drops lands from the per-card table, sorts by mana
  value, and surfaces formatted mana cost + OH WR.
"""

from __future__ import annotations

from mulligan_coach_overlay.stats_html import _format_mana_cost, build_stats_html
from mulligan_coach_recommend import RecommendationExplanation
from mulligan_coach_recommend.service import HandCardPlayability


def test_format_mana_cost_single_char_pips_drop_braces() -> None:
    """The common case (digit + colors) renders as a flat ``"1G"``."""
    assert _format_mana_cost("{1}{G}") == "1G"
    assert _format_mana_cost("{3}{W}{W}") == "3WW"
    assert _format_mana_cost("{R}") == "R"
    assert _format_mana_cost("{X}{R}") == "XR"


def test_format_mana_cost_keeps_braces_around_multi_char_pips() -> None:
    """Hybrid / phyrexian / 2-brid keep braces so they stay readable."""
    assert _format_mana_cost("{2/W}{R}") == "{2/W}R"
    assert _format_mana_cost("{W/U}{B}") == "{W/U}B"
    assert _format_mana_cost("{W/P}{W}") == "{W/P}W"


def test_format_mana_cost_handles_empty_and_none() -> None:
    """No cost / ``None`` / empty string all collapse to the empty string."""
    assert _format_mana_cost(None) == ""
    assert _format_mana_cost("") == ""


def test_format_mana_cost_malformed_input_does_not_crash() -> None:
    """An unclosed brace passes through verbatim instead of raising."""
    out = _format_mana_cost("{1")
    # Just confirm the input substring is preserved (defensive
    # pass-through) and no exception was raised.
    assert "1" in out


def _make_hand_card(
    *,
    name: str,
    is_land: bool,
    mana_value: int | None,
    mana_cost: str | None = None,
    oh_wr: float | None = None,
    castable: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0),
) -> HandCardPlayability:
    return HandCardPlayability(
        name=name,
        is_land=is_land,
        mana_value=mana_value,
        p_castable_by_turn=castable,
        mana_cost=mana_cost,
        opening_hand_win_rate=oh_wr,
    )


def _make_explanation() -> RecommendationExplanation:
    return RecommendationExplanation(
        p_make_2nd_land_by_t2=0.90,
        p_make_3rd_land_by_t3=0.80,
        p_make_4th_land_by_t4=0.70,
        expected_mana_at_t4=3.5,
        p_cast_any_spell_t1=0.40,
        p_cast_any_creature_t2=0.75,
        p_cast_any_removal_t2=0.20,
        p_cast_small_creature_by_t3=0.60,
        p_cast_3drop_by_t4=0.55,
        color_fix_by_t4=0.85,
        hand_cards=(
            _make_hand_card(name="Forest", is_land=True, mana_value=None),
            _make_hand_card(
                name="Big Threat",
                is_land=False,
                mana_value=5,
                mana_cost="{3}{G}{G}",
                oh_wr=0.58,
                castable=(0.0, 0.0, 0.1, 0.45),
            ),
            _make_hand_card(
                name="Cheap Creature",
                is_land=False,
                mana_value=2,
                mana_cost="{1}{G}",
                oh_wr=0.51,
                castable=(0.0, 0.85, 0.92, 0.95),
            ),
            _make_hand_card(
                name="One-Drop",
                is_land=False,
                mana_value=1,
                mana_cost="{G}",
                oh_wr=None,
                castable=(0.65, 0.95, 0.98, 1.0),
            ),
        ),
        p_make_land_drop_by_turn=(1.0, 0.92, 0.78, 0.68),
        p_cast_any_creature_by_turn=(0.20, 0.78, 0.91, 0.95),
        p_cast_any_spell_by_turn=(0.42, 0.82, 0.94, 0.97),
    )


def testbuild_stats_html_has_turn_grid_with_all_turns() -> None:
    """The 4x3 grid surfaces T1..T4 rows and Land/Creature/Spell cols."""
    html = build_stats_html(_make_explanation())
    for label in ("T1", "T2", "T3", "T4", "Land", "Creature", "Spell"):
        assert label in html, f"missing {label!r} in stats HTML"


def testbuild_stats_html_drops_lands_from_per_card_table() -> None:
    """Lands never appear in the per-card playability table."""
    html = build_stats_html(_make_explanation())
    # "Forest" appears nowhere — neither as a row in the grid (only
    # turn labels) nor in the per-card section.
    assert "Forest" not in html


def testbuild_stats_html_sorts_spells_by_mana_value_ascending() -> None:
    """Cheapest spell appears before the most expensive one in the HTML."""
    html = _make_explanation()
    rendered = build_stats_html(html)
    pos_one = rendered.find("One-Drop")
    pos_two = rendered.find("Cheap Creature")
    pos_big = rendered.find("Big Threat")
    assert pos_one != -1 and pos_two != -1 and pos_big != -1
    assert pos_one < pos_two < pos_big


def testbuild_stats_html_renders_formatted_mana_cost() -> None:
    """Mana cost prints in compact ``"1G"`` form (not ``"{1}{G}"``)."""
    html = build_stats_html(_make_explanation())
    assert ">G<" in html  # the bare "G" cell for One-Drop
    assert ">1G<" in html  # Cheap Creature
    assert ">3GG<" in html  # Big Threat


def testbuild_stats_html_oh_wr_dash_for_missing() -> None:
    """Cards with no shrunk OH WR render a muted dash, not a percentage."""
    html = build_stats_html(_make_explanation())
    # The One-Drop row carries the only oh_wr=None card — the &mdash;
    # cell must be present at least once.
    assert "&mdash;" in html


def testbuild_stats_html_oh_wr_color_classes() -> None:
    """OH WR cells colour-code: >=55% green, >=50% amber, else red."""
    html = build_stats_html(_make_explanation())
    # Big Threat at 58% is green (_KEEP_COLOR #7be57b)
    assert "#7be57b" in html
    # Cheap Creature at 51% is amber (_MARGINAL_COLOR #e5c87b)
    assert "#e5c87b" in html
