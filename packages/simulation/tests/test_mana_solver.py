"""Tests for the mana cost-payment CSP solver.

Each test builds a :class:`GameState` with a small handful of permanents
in play and asks the solver whether a given target cost is payable.
The solver returns either ``None`` (infeasible) or a ``ManaPayment``
listing the abilities it would activate; the tests assert on the
boolean and, where the choice matters (filter lands, OR options), on
the structure of the payment.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import parse_mana_cost
from mulligan_coach_simulation.mana import (
    ManaPool,
    available_mana_abilities,
    can_pay_cost,
)
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _state_with_battlefield(
    *,
    lands: list[Card] | None = None,
    mana_perms: list[Card] | None = None,
    summoning_sick: list[int] | None = None,
    tapped: list[int] | None = None,
) -> GameState:
    state = GameState.initial(hand=[], library=[], on_the_play=True, rng=random.Random(0))
    if lands:
        state.battlefield_lands.extend(lands)
    if mana_perms:
        state.battlefield_mana_perms.extend(mana_perms)
    if summoning_sick:
        state.summoning_sick.update(summoning_sick)
    if tapped:
        state.tapped.update(tapped)
    return state


def test_mana_pool_basics() -> None:
    pool = ManaPool.empty()
    assert pool.total() == 0
    pool.add("G", 2)
    pool.add("any", 1)
    assert pool.total() == 3
    snapshot = pool.copy()
    pool.counts["G"] = 0
    assert snapshot.counts["G"] == 2  # copy is independent


def test_basic_land_pays_single_color() -> None:
    plains = Card(instance_id=1, parsed=f.plains())
    state = _state_with_battlefield(lands=[plains])
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{W}"), abilities, state)
    assert payment is not None
    assert len(payment) == 1
    assert payment[0][0].source.instance_id == plains.instance_id
    assert payment[0][1] == ["W"]


def test_two_basics_pay_one_generic_one_color() -> None:
    forest1 = Card(instance_id=1, parsed=f.forest())
    forest2 = Card(instance_id=2, parsed=f.forest())
    state = _state_with_battlefield(lands=[forest1, forest2])
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{1}{G}"), abilities, state)
    assert payment is not None
    assert {pair[0].source.instance_id for pair in payment} == {1, 2}


def test_dual_picks_correct_or_option() -> None:
    """Boros Guildgate has produces=[[R], [W]]. Casting {R}{W} with two
    Guildgates requires picking R for one and W for the other."""
    g1 = Card(instance_id=1, parsed=f.etb_tapped_dual("Boros Guildgate", "R", "W"))
    g2 = Card(instance_id=2, parsed=f.etb_tapped_dual("Boros Guildgate", "R", "W"))
    state = _state_with_battlefield(lands=[g1, g2])
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{R}{W}"), abilities, state)
    assert payment is not None
    options = sorted([opt for _, opt in payment], key=lambda o: o[0])
    assert options == [["R"], ["W"]]


def test_mana_dork_summoning_sick_cannot_tap_for_mana() -> None:
    """A creature mana dork that entered this turn can't tap for mana
    (rule 302.1). Once cleared at begin_turn it works."""
    elf = Card(instance_id=1, parsed=f.llanowar_elves())
    state = _state_with_battlefield(mana_perms=[elf], summoning_sick=[elf.instance_id])
    abilities = available_mana_abilities(state)
    assert abilities == []
    assert can_pay_cost(parse_mana_cost("{G}"), abilities, state) is None

    # Next turn: not summoning sick.
    state.summoning_sick.clear()
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{G}"), abilities, state)
    assert payment is not None
    assert payment[0][0].source.instance_id == elf.instance_id


def test_tapped_source_is_skipped() -> None:
    plains = Card(instance_id=1, parsed=f.plains())
    state = _state_with_battlefield(lands=[plains], tapped=[plains.instance_id])
    assert available_mana_abilities(state) == []


def test_filter_land_with_basic_input_pays_off_color() -> None:
    """Plains + filter land: pay {U} by tapping Plains for {W}, paying
    {1} into the filter, getting {any} out, using it for {U}."""
    plains = Card(instance_id=1, parsed=f.plains())
    filt = Card(instance_id=2, parsed=f.filter_land())
    state = _state_with_battlefield(lands=[plains, filt])
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{U}"), abilities, state)
    assert payment is not None
    activated_ids = [ref.source.instance_id for ref, _ in payment]
    assert plains.instance_id in activated_ids
    assert filt.instance_id in activated_ids


def test_filter_land_alone_cannot_pay_anything() -> None:
    """A filter land without a feeder source can't pay its own cost."""
    filt = Card(instance_id=1, parsed=f.filter_land())
    state = _state_with_battlefield(lands=[filt])
    abilities = available_mana_abilities(state)
    assert can_pay_cost(parse_mana_cost("{U}"), abilities, state) is None


def test_hybrid_pip_pays_with_either_color() -> None:
    """{W/U} should accept a Plains alone, or an Island alone."""
    plains = Card(instance_id=1, parsed=f.plains())
    state_p = _state_with_battlefield(lands=[plains])
    abilities = available_mana_abilities(state_p)
    cost = parse_mana_cost("{W/U}")
    assert can_pay_cost(cost, abilities, state_p) is not None

    island = Card(instance_id=2, parsed=f.island())
    state_i = _state_with_battlefield(lands=[island])
    abilities = available_mana_abilities(state_i)
    assert can_pay_cost(cost, abilities, state_i) is not None


def test_x_costs_treated_as_one() -> None:
    """{X}{R}: the locked-in plan rule is X = 1, so this should require
    *two* mana sources, not one."""
    mountain1 = Card(instance_id=1, parsed=f.mountain())
    state_one = _state_with_battlefield(lands=[mountain1])
    abilities = available_mana_abilities(state_one)
    assert can_pay_cost(parse_mana_cost("{X}{R}"), abilities, state_one) is None

    mountain2 = Card(instance_id=2, parsed=f.mountain())
    state_two = _state_with_battlefield(lands=[mountain1, mountain2])
    abilities = available_mana_abilities(state_two)
    payment = can_pay_cost(parse_mana_cost("{X}{R}"), abilities, state_two)
    assert payment is not None
    assert len(payment) == 2


def test_infeasible_cost_returns_none() -> None:
    forest = Card(instance_id=1, parsed=f.forest())
    state = _state_with_battlefield(lands=[forest])
    abilities = available_mana_abilities(state)
    # 1 G can't pay {2}{G}.
    assert can_pay_cost(parse_mana_cost("{2}{G}"), abilities, state) is None
    # 1 G can't pay {U} (wrong color, no any-source).
    assert can_pay_cost(parse_mana_cost("{U}"), abilities, state) is None


def test_free_cost_is_trivially_payable() -> None:
    state = _state_with_battlefield()
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost(""), abilities, state)
    assert payment == []


def test_payment_returns_actual_sources() -> None:
    """The returned ManaPayment must reference real AbilityRef pointers
    that the engine can later use to tap permanents."""
    forest = Card(instance_id=42, parsed=f.forest())
    state = _state_with_battlefield(lands=[forest])
    abilities = available_mana_abilities(state)
    payment = can_pay_cost(parse_mana_cost("{G}"), abilities, state)
    assert payment is not None and len(payment) == 1
    ref, option = payment[0]
    assert ref.source is forest
    assert ref.ability is forest.parsed.mana_abilities[0]
    assert option == ["G"]
