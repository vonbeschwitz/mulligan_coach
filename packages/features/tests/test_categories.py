"""Tests for the card-classification predicates in :mod:`categories`."""

from __future__ import annotations

import _factories as f  # type: ignore[import-not-found]
from mulligan_coach_features import categories as cat


def test_is_land_includes_basics_and_duals() -> None:
    assert cat.is_land(f.forest())
    assert cat.is_land(f.nonbasic_dual("Test Dual", "R", "G"))
    assert not cat.is_land(f.vanilla_creature("Bear", "{1}{G}"))


def test_is_spell_is_complement_of_is_land() -> None:
    """``is_spell`` is the complement of ``is_land`` across our test
    factories. Useful invariant for the deck-feature partition."""
    cards = [
        f.forest(),
        f.vanilla_creature("Bear", "{1}{G}"),
        f.burn_spell("Bolt", "{R}"),
        f.cantrip("Opt", "{U}"),
        f.token_maker(),
    ]
    for c in cards:
        assert cat.is_spell(c) != cat.is_land(c)


def test_nonbasic_land_excludes_basics() -> None:
    assert not cat.is_nonbasic_land(f.forest())
    assert cat.is_nonbasic_land(f.nonbasic_dual("Test Dual", "R", "G"))


def test_cmc_from_mana_cost() -> None:
    """``cmc`` reads through ``ParsedCard.mana_cost.cmc`` and falls
    back to 0 when mana_cost is None (lands)."""
    assert cat.cmc(f.forest()) == 0
    assert cat.cmc(f.vanilla_creature("Bear", "{1}{G}")) == 2
    assert cat.cmc(f.burn_spell("Inferno", "{4}{R}{R}")) == 6


def test_is_creature_strict_vs_for_castability() -> None:
    """Token-makers count as creatures for castability but not strict."""
    bear = f.vanilla_creature("Bear", "{1}{G}")
    token = f.token_maker()
    assert cat.is_creature_strict(bear)
    assert cat.is_creature_for_castability(bear)
    assert not cat.is_creature_strict(token)
    assert cat.is_creature_for_castability(token)


def test_is_removal_union() -> None:
    """Burn and counterspell both classify as removal, alongside
    destroy/exile etc."""
    assert cat.is_removal(f.burn_spell("Bolt", "{R}"))
    assert cat.is_removal(f.counterspell_card())
    assert not cat.is_removal(f.vanilla_creature("Bear", "{1}{G}"))
    assert not cat.is_removal(f.cantrip("Opt", "{U}"))


def test_is_ramp_via_mana_ability() -> None:
    """Mana dorks (creatures with mana abilities) are ramp."""
    assert cat.is_ramp(f.mana_dork())
    assert not cat.is_ramp(f.vanilla_creature("Bear", "{1}{G}"))


def test_is_ramp_via_fetch_to_battlefield() -> None:
    """Cultivate-style fetch-to-battlefield is ramp; cantrip is not."""
    assert cat.is_ramp(f.ramp_sorcery())
    assert not cat.is_ramp(f.cantrip("Opt", "{U}"))


def test_is_card_manipulation() -> None:
    assert cat.is_card_manipulation(f.cantrip("Opt", "{U}"))
    assert not cat.is_card_manipulation(f.vanilla_creature("Bear", "{1}{G}"))


def test_has_alt_mode() -> None:
    """Cycling creatures have an alt mode; vanilla creatures don't."""
    assert cat.has_alt_mode(f.cycler("Looter", "{1}{U}", "{1}"))
    assert not cat.has_alt_mode(f.vanilla_creature("Bear", "{1}{G}"))


def test_mv_buckets_partition() -> None:
    """Every non-X printed cost lands in exactly one of the disjoint
    buckets le_2 / eq_3 / 4_5 / ge_6."""
    cards = [
        f.vanilla_creature("Cub", "{G}"),  # 1
        f.vanilla_creature("Bear", "{1}{G}"),  # 2
        f.vanilla_creature("Knight", "{2}{W}"),  # 3
        f.vanilla_creature("Wyvern", "{3}{U}"),  # 4
        f.vanilla_creature("Dragon", "{4}{R}{R}"),  # 6
    ]
    for c in cards:
        n_buckets = sum([cat.mv_le_2(c), cat.mv_eq_3(c), cat.mv_4_5(c), cat.mv_ge_6(c)])
        assert n_buckets == 1, f"{c.name} (CMC={cat.cmc(c)}) in {n_buckets} buckets"


def test_open_ended_buckets() -> None:
    """mv_3_plus and mv_4_plus overlap with the closed buckets."""
    c5 = f.vanilla_creature("Beast", "{3}{G}{G}")  # CMC 5
    assert cat.mv_3_plus(c5)
    assert cat.mv_4_plus(c5)
    assert cat.mv_4_5(c5)  # still in closed bucket too
