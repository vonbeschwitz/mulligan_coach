"""Tests for predicate evaluation, ETB-tapped checks, and fetch-effect
resolution.

These cover step 4 of the implementation plan:

* All six ``Predicate`` kinds against synthetic battlefields.
* ``enters_tapped`` for the conditional ETB pattern (Deathcap-style).
* ``resolve_fetch`` for all three target_filter / destination axes.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import (
    Cost,
    FetchLandEffect,
    ManaAbility,
    ParsedCard,
    ParseStatus,
    Predicate,
)
from mulligan_coach_simulation.effects import (
    apply_mode_effects,
    enters_tapped,
    predicate_holds,
    resolve_fetch,
)
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _state(
    *,
    library: list[Card] | None = None,
    lands: list[Card] | None = None,
    mana_perms: list[Card] | None = None,
    seed: int = 0,
) -> GameState:
    state = GameState.initial(
        hand=[], library=library or [], on_the_play=True, rng=random.Random(seed)
    )
    if lands:
        state.battlefield_lands.extend(lands)
    if mana_perms:
        state.battlefield_mana_perms.extend(mana_perms)
    return state


# ----------------------------------------------------------------------
# predicate_holds
# ----------------------------------------------------------------------


def test_predicate_none_is_true() -> None:
    state = _state()
    assert predicate_holds(None, state) is True


def test_predicate_always() -> None:
    state = _state()
    assert predicate_holds(Predicate(kind="always"), state) is True


def test_predicate_controls_lands_thresholds() -> None:
    forests = [Card(instance_id=i, parsed=f.forest()) for i in range(3)]
    state = _state(lands=forests)
    assert predicate_holds(Predicate(kind="controls_lands_ge", n=3), state)
    assert not predicate_holds(Predicate(kind="controls_lands_ge", n=4), state)
    assert predicate_holds(Predicate(kind="controls_lands_lt", n=4), state)
    assert not predicate_holds(Predicate(kind="controls_lands_lt", n=3), state)


def test_predicate_controls_lands_excluding() -> None:
    """``excluding`` lets a Deathcap-style ETB predicate ignore the new
    land itself when counting other lands."""
    forests = [Card(instance_id=i, parsed=f.forest()) for i in range(2)]
    new_land = Card(instance_id=99, parsed=f.forest())
    state = _state(lands=[*forests, new_land])
    # Without excluding: 3 lands.
    assert predicate_holds(Predicate(kind="controls_lands_ge", n=3), state)
    # With excluding: 2 lands.
    assert not predicate_holds(Predicate(kind="controls_lands_ge", n=3), state, excluding=new_land)


def test_predicate_controls_basic_named_type() -> None:
    forest = Card(instance_id=1, parsed=f.forest())
    plains = Card(instance_id=2, parsed=f.plains())
    state = _state(lands=[forest, plains])
    assert predicate_holds(Predicate(kind="controls_basic", basic_type="Forest"), state)
    assert predicate_holds(Predicate(kind="controls_basic", basic_type="Plains"), state)
    assert not predicate_holds(Predicate(kind="controls_basic", basic_type="Island"), state)


def test_predicate_controls_basic_any() -> None:
    forest = Card(instance_id=1, parsed=f.forest())
    state_with = _state(lands=[forest])
    state_without = _state(
        lands=[Card(instance_id=2, parsed=f.etb_tapped_dual("Boros Guildgate", "R", "W"))]
    )
    assert predicate_holds(Predicate(kind="controls_basic_any"), state_with)
    assert not predicate_holds(Predicate(kind="controls_basic_any"), state_without)


def test_predicate_controls_color_via_lands_and_mana_perms() -> None:
    forest = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=2, parsed=f.llanowar_elves())
    state = _state(lands=[forest], mana_perms=[elf])
    assert predicate_holds(Predicate(kind="controls_color", color="G"), state)
    assert not predicate_holds(Predicate(kind="controls_color", color="W"), state)


def test_predicate_controls_color_filter_any_counts() -> None:
    """A filter land producing 'any' should satisfy controls_color for
    any specific color — it can produce that color in principle."""
    filt = Card(instance_id=1, parsed=f.filter_land())
    state = _state(lands=[filt])
    for c in ("W", "U", "B", "R", "G"):
        assert predicate_holds(Predicate(kind="controls_color", color=c), state), c


# ----------------------------------------------------------------------
# enters_tapped
# ----------------------------------------------------------------------


def test_enters_tapped_basic_land_returns_false() -> None:
    plains = f.plains()
    state = _state()
    assert enters_tapped(plains, state) is False


def test_enters_tapped_unconditional_predicate() -> None:
    dual = f.etb_tapped_dual("Boros Guildgate", "R", "W")
    state = _state()
    assert enters_tapped(dual, state) is True


def test_enters_tapped_conditional_predicate() -> None:
    """Synthesise a Deathcap-style land: enters tapped iff fewer than
    2 lands are already in play."""
    deathcap = ParsedCard(
        name="Deathcap Glade",
        set_code="TST",
        collector_number="deathcap",
        oracle_id="00000000-0000-0000-0000-000000009999",
        rarity="rare",
        raw_oracle_text="enters tapped unless you control two or more other lands",
        type_line="Land",
        types=["Land"],
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[["B"], ["G"]])],
        enter_condition=Predicate(kind="controls_lands_lt", n=2),
        status=ParseStatus.AUTO,
    )
    state_few = _state(lands=[Card(instance_id=1, parsed=f.forest())])
    state_many = _state(lands=[Card(instance_id=i, parsed=f.forest()) for i in range(3)])
    assert enters_tapped(deathcap, state_few) is True
    assert enters_tapped(deathcap, state_many) is False


# ----------------------------------------------------------------------
# resolve_fetch
# ----------------------------------------------------------------------


def test_fetch_basic_to_battlefield_tapped() -> None:
    """Cultivate's first effect: search for a basic, put it onto the
    battlefield tapped."""
    library = [
        Card(instance_id=10, parsed=f.forest()),
        Card(instance_id=11, parsed=f.forest()),
        Card(instance_id=12, parsed=f.cantrip("Brainstorm", "{U}")),
    ]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="basic", destination="battlefield_tapped")
    resolve_fetch(state, fx)
    assert any(c.instance_id == 10 or c.instance_id == 11 for c in state.battlefield_lands)
    found = next(c for c in state.battlefield_lands if c.parsed.name == "Forest")
    assert found.instance_id in state.tapped


def test_fetch_basic_to_hand() -> None:
    library = [Card(instance_id=10, parsed=f.forest())]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="basic", destination="hand")
    resolve_fetch(state, fx)
    assert state.library == []
    assert len(state.hand) == 1
    assert state.hand[0].instance_id == 10


def test_fetch_basic_to_battlefield_untapped() -> None:
    library = [Card(instance_id=10, parsed=f.forest())]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="basic", destination="battlefield_untapped")
    resolve_fetch(state, fx)
    found = next(c for c in state.battlefield_lands if c.instance_id == 10)
    assert found.instance_id not in state.tapped


def test_fetch_specific_subtype_skips_wrong_basics() -> None:
    """``specific_subtype`` with subtype='Forest' should grab the Forest
    even if a Plains is closer to the top."""
    library = [
        Card(instance_id=10, parsed=f.plains()),
        Card(instance_id=11, parsed=f.forest()),
    ]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="specific_subtype", subtype="Forest", destination="hand")
    resolve_fetch(state, fx)
    assert state.hand[0].instance_id == 11
    assert any(c.instance_id == 10 for c in state.library)


def test_fetch_no_match_is_noop() -> None:
    library = [Card(instance_id=10, parsed=f.cantrip("Brainstorm", "{U}"))]
    pre_lib_ids = [c.instance_id for c in library]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="basic", destination="hand")
    resolve_fetch(state, fx)
    assert [c.instance_id for c in state.library] == pre_lib_ids
    assert state.hand == []


def test_fetch_count_more_than_one() -> None:
    library = [Card(instance_id=i, parsed=f.forest()) for i in range(10, 13)]
    state = _state(library=library)
    fx = FetchLandEffect(target_filter="basic", destination="hand", count=2)
    resolve_fetch(state, fx)
    assert len(state.hand) == 2
    assert len(state.library) == 1


def test_fetch_shuffles_library() -> None:
    """Resolving a fetch should shuffle the remaining library."""
    library = [Card(instance_id=i, parsed=f.forest()) for i in range(10, 30)]
    state = _state(library=library, seed=123)
    pre_order = [c.instance_id for c in state.library]
    fx = FetchLandEffect(target_filter="basic", destination="hand")
    resolve_fetch(state, fx)
    # First card was fetched; the rest must be in *some* order. With
    # seed=123 over 19 remaining cards, the chance of accidentally
    # producing the original order is astronomically small.
    post_order = [c.instance_id for c in state.library]
    assert post_order != pre_order[1:]


# ----------------------------------------------------------------------
# apply_mode_effects (FetchLandEffect path)
# ----------------------------------------------------------------------


def test_apply_mode_effects_runs_only_fetch_in_v1() -> None:
    """``apply_mode_effects`` should resolve FetchLandEffect and ignore
    EntersBattlefieldEffect / NoopEffect / DrawCardsEffect (the latter
    being deferred to step 6's casting policy)."""
    library = [Card(instance_id=10, parsed=f.forest())]
    state = _state(library=library)
    cultivate = f.cultivate()
    apply_mode_effects(state, cultivate.modes[0].effects)
    # Cultivate's first effect: basic → battlefield_tapped.
    # Cultivate's second effect: basic → hand. Library has only one.
    assert any(c.instance_id == 10 for c in state.battlefield_lands)
    assert state.library == []
    # No draw cards effect was on Cultivate, but the test ensures we
    # don't crash on FetchLandEffects with count=1 each.
