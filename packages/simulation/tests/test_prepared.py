"""Tests for the SOS Prepare mechanic.

A pre-prepared creature has TWO modes encoded:

* ``Mode(kind="cast")`` — the creature's normal cast
* ``Mode(kind="prepared")`` — the prepare spell (sorcery-speed) that
  becomes castable after the creature has resolved

The simulator's responsibilities:

1. Casting the cast mode flags the resulting permanent as ``prepared``.
2. The prepared mode is castable from the *battlefield*, not from hand,
   while the source is flagged.
3. The S-tier policy considers prepared modes alongside hand modes, so
   a prepared FetchLandEffect competes with hand-fetchers in S1c.
4. Casting the prepared mode resolves its effects and removes the
   ``prepared`` flag (the source stays on the battlefield).
5. ``S5_cast_prepared_enabler`` ensures hand creatures with mulligan-
   relevant prepared modes get cast even when the policy has no other
   action — without that, Studious First-Year would never enter and
   its prepared Rampant Growth would never fire.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import (
    Cost,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    Mode,
    ParsedCard,
    ParseStatus,
    parse_mana_cost,
)
from mulligan_coach_simulation.policy_spells import cast_main_phase, pick_next_action
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _studious_first_year() -> ParsedCard:
    """{G} 1/1 Bear Wizard. Enters prepared.
    Prepared spell: Rampant Growth ({1}{G}, search basic land tapped to battlefield)."""
    return ParsedCard(
        name="Studious First-Year // Rampant Growth",
        set_code="SOS",
        collector_number="162",
        oracle_id="00000000-0000-0000-0000-000000004001",
        rarity="common",
        raw_oracle_text="(prepare layout — encoded by hand)",
        type_line="Creature — Bear Wizard // Sorcery",
        types=["Creature"],
        subtypes=["Bear", "Wizard"],
        mana_cost=parse_mana_cost("{G}"),
        power="1",
        toughness="1",
        modes=[
            Mode(
                kind="cast",
                cost=Cost(mana=parse_mana_cost("{G}")),
                effects=[EntersBattlefieldEffect()],
            ),
            Mode(
                kind="prepared",
                cost=Cost(mana=parse_mana_cost("{1}{G}")),
                effects=[
                    FetchLandEffect(
                        target_filter="basic",
                        destination="battlefield_tapped",
                    )
                ],
            ),
        ],
        status=ParseStatus.LLM_ENCODED,
    )


def _elite_interceptor() -> ParsedCard:
    """{W} 1/2 Human Wizard. Enters prepared.
    Prepared spell: Rejoinder ({1}{W}, draw a card)."""
    return ParsedCard(
        name="Elite Interceptor // Rejoinder",
        set_code="SOS",
        collector_number="12",
        oracle_id="00000000-0000-0000-0000-000000004002",
        rarity="common",
        raw_oracle_text="(prepare layout — encoded by hand)",
        type_line="Creature — Human Wizard // Sorcery",
        types=["Creature"],
        subtypes=["Human", "Wizard"],
        mana_cost=parse_mana_cost("{W}"),
        power="1",
        toughness="2",
        modes=[
            Mode(
                kind="cast",
                cost=Cost(mana=parse_mana_cost("{W}")),
                effects=[EntersBattlefieldEffect()],
            ),
            Mode(
                kind="prepared",
                cost=Cost(mana=parse_mana_cost("{1}{W}")),
                effects=[DrawCardsEffect(n=1)],
            ),
        ],
        status=ParseStatus.LLM_ENCODED,
    )


def _state(
    *,
    hand: list[Card],
    lands: list[Card] | None = None,
    library: list[Card] | None = None,
    seed: int = 0,
) -> GameState:
    state = GameState.initial(
        hand=hand,
        library=library or [],
        on_the_play=True,
        rng=random.Random(seed),
    )
    if lands:
        state.battlefield_lands.extend(lands)
    return state


# ---------------------------------------------------------------------------
# Prepared flag set on cast.
# ---------------------------------------------------------------------------


def test_casting_pre_prepared_creature_flags_it_prepared() -> None:
    studious = Card(instance_id=10, parsed=_studious_first_year())
    forest_bf = Card(instance_id=1, parsed=f.forest())
    state = _state(hand=[studious], lands=[forest_bf])

    cast_main_phase(state)
    assert studious in state.battlefield_other  # not a mana permanent
    assert studious.instance_id in state.prepared


def test_casting_non_prepared_creature_does_not_flag() -> None:
    """Conditional-prepared cards (no kind='prepared' mode encoded) must
    not be flagged on cast — there's nothing for them to enable."""
    bear = f.vanilla_creature("Bear", "{1}{G}", 2, 2)
    bear_card = Card(instance_id=10, parsed=bear)
    forests = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 3)]
    state = _state(hand=[bear_card], lands=forests)

    cast_main_phase(state)
    assert bear_card.instance_id not in state.prepared


# ---------------------------------------------------------------------------
# S5 enables on T1.
# ---------------------------------------------------------------------------


def test_s5_casts_studious_on_turn_1() -> None:
    """Hand: Studious First-Year. Battlefield: 1 Forest. The policy has
    nothing else to do; S5 should fire and cast Studious so its prepared
    mode is available next turn."""
    studious = Card(instance_id=10, parsed=_studious_first_year())
    forest_bf = Card(instance_id=1, parsed=f.forest())
    state = _state(hand=[studious], lands=[forest_bf])

    action = pick_next_action(state)
    assert action is not None
    assert action.card is studious
    assert action.priority == "S5"


def test_s5_casts_elite_interceptor_on_turn_1() -> None:
    """Same idea for the white prepared cantrip."""
    interceptor = Card(instance_id=10, parsed=_elite_interceptor())
    plains_bf = Card(instance_id=1, parsed=f.plains())
    state = _state(hand=[interceptor], lands=[plains_bf])

    action = pick_next_action(state)
    assert action is not None
    assert action.card is interceptor
    assert action.priority == "S5"


# ---------------------------------------------------------------------------
# Prepared spell fires from battlefield.
# ---------------------------------------------------------------------------


def test_prepared_ramp_fires_on_t2() -> None:
    """T1: cast Studious for G. T2: with 2 lands available, S1c should
    cast Studious's prepared Rampant Growth, fetching a Forest tapped."""
    studious = Card(instance_id=10, parsed=_studious_first_year())
    forest_t1 = Card(instance_id=1, parsed=f.forest())
    forest_t2 = Card(instance_id=2, parsed=f.forest())
    library_forest = Card(instance_id=20, parsed=f.forest())

    # T1
    state = _state(hand=[studious], lands=[forest_t1], library=[library_forest])
    cast_main_phase(state)
    assert studious.instance_id in state.prepared
    assert studious in state.battlefield_other

    # T2 simulated: untap, add second land, run policy.
    state.tapped.clear()
    state.summoning_sick.clear()
    state.battlefield_lands.append(forest_t2)

    action = pick_next_action(state)
    assert action is not None
    # The prepared mode is the fetch-tapped option.
    assert action.card is studious
    assert action.priority == "S1c"
    cast_main_phase(state)

    # After resolution: studious is no longer prepared, fetched Forest
    # is tapped on the battlefield.
    assert studious.instance_id not in state.prepared
    fetched_forests = [c for c in state.battlefield_lands if c.parsed.name == "Forest"]
    # Original 2 forests + fetched 1 = 3.
    assert len(fetched_forests) == 3
    # The fetched one entered tapped.
    assert library_forest.instance_id in state.tapped


def test_prepared_draw_fires_on_t2() -> None:
    """Elite Interceptor's prepared Rejoinder should cast on T2 via S3
    (draw effect)."""
    interceptor = Card(instance_id=10, parsed=_elite_interceptor())
    plains_t1 = Card(instance_id=1, parsed=f.plains())
    plains_t2 = Card(instance_id=2, parsed=f.plains())
    library_card = Card(instance_id=20, parsed=f.vanilla_creature("Bear", "{1}{G}", 2, 2))

    state = _state(hand=[interceptor], lands=[plains_t1], library=[library_card])
    cast_main_phase(state)
    assert interceptor.instance_id in state.prepared

    state.tapped.clear()
    state.summoning_sick.clear()
    state.battlefield_lands.append(plains_t2)

    initial_hand_size = len(state.hand)
    action = pick_next_action(state)
    assert action is not None
    assert action.card is interceptor
    assert action.priority == "S3"
    cast_main_phase(state)

    assert interceptor.instance_id not in state.prepared
    # We drew one card.
    assert len(state.hand) == initial_hand_size + 1
