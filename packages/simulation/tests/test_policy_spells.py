"""Tests for the S1-S4 spell-casting policy.

Each test sets up a small hand + battlefield, calls
``cast_main_phase``, and asserts on which cards moved to which zones
and in which priority order.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import (
    Cost,
    DrawCardsEffect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    LookAtTopEffect,
    Mode,
    ParsedCard,
    ParseStatus,
    ScryEffect,
    parse_mana_cost,
)
from mulligan_coach_simulation.policy_spells import (
    CastAction,
    cast_main_phase,
    pick_next_action,
)
from mulligan_coach_simulation.runtime import Card, GameState

from . import _factories as f


def _state(
    *,
    hand: list[Card],
    lands: list[Card] | None = None,
    library: list[Card] | None = None,
    mana_perms: list[Card] | None = None,
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
    if mana_perms:
        state.battlefield_mana_perms.extend(mana_perms)
    return state


def _three_visits_card() -> ParsedCard:
    """{1}{G} sorcery: search for a Forest, put it onto battlefield untapped."""
    cost = Cost(mana=parse_mana_cost("{1}{G}"))
    return ParsedCard(
        name="Three Visits",
        set_code="TST",
        collector_number="3v",
        oracle_id="00000000-0000-0000-0000-000000003001",
        rarity="common",
        raw_oracle_text="Search your library for a Forest, put it onto the battlefield, then shuffle.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{1}{G}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    FetchLandEffect(
                        target_filter="specific_subtype",
                        subtype="Forest",
                        destination="battlefield_untapped",
                    )
                ],
            )
        ],
        status=ParseStatus.AUTO,
    )


def _scry_card() -> ParsedCard:
    """{1}{U} instant: scry 2."""
    cost = Cost(mana=parse_mana_cost("{1}{U}"))
    return ParsedCard(
        name="Preordain",
        set_code="TST",
        collector_number="po",
        oracle_id="00000000-0000-0000-0000-000000003002",
        rarity="common",
        raw_oracle_text="Scry 2, then draw a card.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{1}{U}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[ScryEffect(n=2), DrawCardsEffect(n=1)],
            )
        ],
        status=ParseStatus.AUTO,
    )


def _hand_fetch_creature() -> ParsedCard:
    """{1}{W} creature: ETB → search for a basic, put into hand."""
    cost = Cost(mana=parse_mana_cost("{1}{W}"))
    return ParsedCard(
        name="Environmental Scientist",
        set_code="TST",
        collector_number="es",
        oracle_id="00000000-0000-0000-0000-000000003003",
        rarity="common",
        raw_oracle_text="When this enters, search for a basic and put it into your hand.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost("{1}{W}"),
        power="1",
        toughness="2",
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    EntersBattlefieldEffect(),
                    FetchLandEffect(target_filter="basic", destination="hand"),
                ],
            )
        ],
        status=ParseStatus.AUTO,
    )


# ----------------------------------------------------------------------
# S1
# ----------------------------------------------------------------------


def test_s1a_three_visits_beats_other_options() -> None:
    """Hand: Three Visits ({1}{G}), Llanowar ({G}), Cultivate ({2}{G}).
    Battlefield: Forest, Forest, Forest. All castable. S1a should fire
    first → cast Three Visits."""
    forests_bf = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 4)]
    three_visits = Card(instance_id=10, parsed=_three_visits_card())
    elf = Card(instance_id=11, parsed=f.llanowar_elves())
    cult = Card(instance_id=12, parsed=f.cultivate())
    library = [Card(instance_id=20, parsed=f.forest())]

    state = _state(hand=[three_visits, elf, cult], lands=forests_bf, library=library)
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S1a"
    assert action.card is three_visits


def test_s1b_mana_dork_when_no_battlefield_fetcher_available() -> None:
    """Hand: Llanowar Elves, vanilla bear. Battlefield: Forest. S1a
    has nothing (no battlefield fetcher in hand); S1b fires for the elf."""
    forest_bf = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=10, parsed=f.llanowar_elves())
    bear = Card(instance_id=11, parsed=f.vanilla_creature("Bear", "{1}{G}", 2, 2))
    state = _state(hand=[elf, bear], lands=[forest_bf])
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S1b"
    assert action.card is elf


def test_s1c_cultivate_when_no_other_fetcher() -> None:
    forests_bf = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 4)]
    cult = Card(instance_id=10, parsed=f.cultivate())
    bear = Card(instance_id=11, parsed=f.vanilla_creature("Bear", "{1}{G}", 2, 2))
    state = _state(
        hand=[cult, bear], lands=forests_bf, library=[Card(instance_id=20, parsed=f.forest())]
    )
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S1c"
    assert action.card is cult


def test_s1d_evolving_wilds_activation() -> None:
    """Battlefield: Evolving Wilds. Hand: nothing castable, no lands.
    S1d should activate Wilds (it ETBs untapped, can immediately tap+sac)."""
    wilds = Card(instance_id=1, parsed=f.evolving_wilds())
    state = _state(hand=[], lands=[wilds], library=[Card(instance_id=20, parsed=f.forest())])
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S1d"
    assert action.card is wilds


# ----------------------------------------------------------------------
# Mana-budget respected within a turn
# ----------------------------------------------------------------------


def test_two_drop_blocks_three_drop_when_only_two_lands() -> None:
    """Two Forests, hand has Llanowar ({G}) and Cultivate ({2}{G}).
    Only one is castable per turn given 2 mana."""
    forests = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 3)]
    elf = Card(instance_id=10, parsed=f.llanowar_elves())
    cult = Card(instance_id=11, parsed=f.cultivate())
    state = _state(
        hand=[elf, cult], lands=forests, library=[Card(instance_id=20, parsed=f.forest())]
    )
    results = cast_main_phase(state)
    assert len(results) == 1
    # S1b fires for the elf; cult is {2}{G} which needs 3 mana, but
    # by the time we'd consider S1c the elf has tapped one Forest
    # AND introduced summoning-sickness on itself.
    assert results[0].action.card is elf
    assert results[0].action.priority == "S1b"


def test_cultivate_runs_with_three_lands() -> None:
    forests = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 4)]
    cult = Card(instance_id=10, parsed=f.cultivate())
    library = [Card(instance_id=20, parsed=f.forest()), Card(instance_id=21, parsed=f.forest())]
    state = _state(hand=[cult], lands=forests, library=library)
    results = cast_main_phase(state)
    assert any(r.action.card is cult for r in results)
    # After Cultivate resolves: one basic on battlefield_tapped, one in hand.
    assert any(
        c.parsed.name == "Forest" and c.instance_id in state.tapped for c in state.battlefield_lands
    )
    # The land that went to hand is still in hand (we didn't play it).
    assert any(c.parsed.name == "Forest" for c in state.hand)


# ----------------------------------------------------------------------
# S2 vs S4 hand-fetch precondition
# ----------------------------------------------------------------------


def test_s2_fires_when_no_land_in_hand() -> None:
    """Hand has only the hand-fetch creature (no land). S2 should fire."""
    plains_bf = Card(instance_id=1, parsed=f.plains())
    es = Card(instance_id=10, parsed=_hand_fetch_creature())
    library = [Card(instance_id=20, parsed=f.plains())]
    state = _state(
        hand=[es], lands=[plains_bf, Card(instance_id=2, parsed=f.plains())], library=library
    )
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S2"


def test_s2_skipped_when_land_in_hand_falls_to_s4() -> None:
    """Hand has an extra basic; the hand-fetch creature still gets cast
    but only after S1/S2/S3 don't apply — falls to S4."""
    plains_bf = [Card(instance_id=i, parsed=f.plains()) for i in range(1, 3)]
    extra_plains = Card(instance_id=20, parsed=f.plains())
    es = Card(instance_id=10, parsed=_hand_fetch_creature())
    library = [Card(instance_id=30, parsed=f.plains())]
    state = _state(hand=[es, extra_plains], lands=plains_bf, library=library)
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S4"
    assert action.card is es


def _midnight_tilling_card() -> ParsedCard:
    """{1}{G} sorcery: top 4, may put a permanent in hand. Encoded as a
    LookAtTopEffect with accepts_land=True so the policy treats it as a
    hand-fetch."""
    cost = Cost(mana=parse_mana_cost("{1}{G}"))
    return ParsedCard(
        name="Midnight Tilling",
        set_code="TST",
        collector_number="mt",
        oracle_id="00000000-0000-0000-0000-000000003004",
        rarity="common",
        raw_oracle_text="Mill four cards, then put a permanent card from among them into your hand.",
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{1}{G}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[LookAtTopEffect(n=4, accepts_land=True, accepts_nonland=True)],
            )
        ],
        status=ParseStatus.LLM_ENCODED,
    )


def test_s2_fires_for_look_at_top_card_when_no_land_in_hand() -> None:
    """A LookAtTopEffect(accepts_land=True) card behaves as a hand-fetch:
    when the hand has no lands, S2 should pick it."""
    forests_bf = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 3)]
    tilling = Card(instance_id=10, parsed=_midnight_tilling_card())
    library = [Card(instance_id=20, parsed=f.forest())]
    state = _state(hand=[tilling], lands=forests_bf, library=library)
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S2"
    assert action.card is tilling


def test_s4_fires_for_look_at_top_card_when_land_in_hand() -> None:
    """When the hand already has a land, the LookAtTop card falls through
    to S4 just like a FetchLandEffect-based hand-fetch."""
    forests_bf = [Card(instance_id=i, parsed=f.forest()) for i in range(1, 3)]
    extra_forest = Card(instance_id=20, parsed=f.forest())
    tilling = Card(instance_id=10, parsed=_midnight_tilling_card())
    library = [Card(instance_id=30, parsed=f.forest())]
    state = _state(hand=[tilling, extra_forest], lands=forests_bf, library=library)
    action = pick_next_action(state)
    assert action is not None
    assert action.priority == "S4"
    assert action.card is tilling


# ----------------------------------------------------------------------
# S3 — scry
# ----------------------------------------------------------------------


def test_scry_bottoms_uncastable_keeps_castable() -> None:
    """Cast Preordain (Scry 2, draw 1). Top of library: a useless 5-drop
    and a castable 1-drop. The 5-drop should be bottomed; the 1-drop
    kept on top, then drawn into hand by the draw-1 effect."""
    island_bf = [Card(instance_id=i, parsed=f.island()) for i in range(1, 3)]
    five_drop = Card(instance_id=20, parsed=f.vanilla_creature("Hydra", "{4}{U}", 4, 4))
    one_drop = Card(instance_id=21, parsed=f.vanilla_creature("Bird", "{U}", 1, 1))
    library = [five_drop, one_drop]
    preordain = Card(instance_id=10, parsed=_scry_card())
    state = _state(hand=[preordain], lands=island_bf, library=library)

    cast_main_phase(state)

    # The 1-drop was kept on top by scry (5-drop bottomed) and then
    # drawn into hand by the draw-1 effect that fires after scry.
    assert any(c.instance_id == one_drop.instance_id for c in state.hand)
    # The 5-drop is at the bottom of the library.
    assert state.library[-1].instance_id == five_drop.instance_id


def test_scry_keeps_lands_when_no_land_in_hand() -> None:
    """If hand has no land, the scry policy keeps any land seen on top.
    The kept land is then drawn (Preordain draws after scry)."""
    island_bf = [Card(instance_id=i, parsed=f.island()) for i in range(1, 3)]
    plains_top = Card(instance_id=20, parsed=f.plains())
    library = [plains_top, Card(instance_id=21, parsed=f.vanilla_creature("Hydra", "{4}{U}", 4, 4))]
    preordain = Card(instance_id=10, parsed=_scry_card())
    state = _state(hand=[preordain], lands=island_bf, library=library)
    cast_main_phase(state)
    # The Plains was kept on top by scry (the Hydra gets bottomed since
    # we had a land in hand-zone... wait, we don't). Hmm, actually since
    # hand has no land, the Plains is kept on top, then drawn.
    assert any(c.instance_id == plains_top.instance_id for c in state.hand)


# ----------------------------------------------------------------------
# Loop terminates
# ----------------------------------------------------------------------


def test_main_phase_terminates_when_nothing_castable() -> None:
    """Hand has only an uncastable 5-drop on a 1-land battlefield."""
    forest_bf = Card(instance_id=1, parsed=f.forest())
    bomb = Card(instance_id=10, parsed=f.vanilla_creature("Bomb", "{4}{G}", 5, 5))
    state = _state(hand=[bomb], lands=[forest_bf])
    results = cast_main_phase(state)
    assert results == []


def test_main_phase_executes_action_with_correct_cast_object() -> None:
    """Smoke test: the returned CastResult.action.card matches the card
    actually moved out of hand."""
    forest_bf = Card(instance_id=1, parsed=f.forest())
    elf = Card(instance_id=10, parsed=f.llanowar_elves())
    state = _state(hand=[elf], lands=[forest_bf])
    results = cast_main_phase(state)
    assert isinstance(results[0].action, CastAction)
    assert results[0].action.card is elf
    assert elf in state.battlefield_mana_perms
    assert elf.instance_id in state.summoning_sick
    assert elf not in state.hand
