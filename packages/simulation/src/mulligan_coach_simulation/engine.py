"""Per-game turn loop.

:func:`simulate_one_game` sets up a :class:`GameState`, runs four
turns under the policy modules, and returns a :class:`GameTrace`
summarising what happened. The Monte Carlo wrapper in
:mod:`monte_carlo` calls this once per run.

The turn structure follows ``packages/simulation/CLAUDE.md``:

1. **Begin turn**: untap, clear summoning sickness, reset land-drop flag.
2. **Draw step**: draw 1, except turn 1 on the play.
3. **Castability snapshot**: per-card per-mode "castable this turn?"
   recorded *before* any actions.
4. **Land-drop decision** via :func:`policy_land.choose_land`.
5. **Main phase** via :func:`policy_spells.cast_main_phase`.
6. **End turn**: hook for future cleanup.

When *verbose* is True, the trace's ``actions`` list is populated
with structured events (``DrawEvent`` / ``LandDropEvent`` /
``SpellCastEvent`` / ``ScryEvent``). With *verbose* False the
list stays empty — saves a bit of allocation per game.
"""

from __future__ import annotations

import random

from .castability import castability_snapshot
from .policy_land import choose_land
from .policy_spells import cast_main_phase
from .runtime import Card, GameState
from .trace import (
    ActionEvent,
    DrawEvent,
    GameTrace,
    InstanceOutcome,
    LandDropEvent,
    ModeFirstTurn,
    ScryEvent,
    SpellCastEvent,
    TurnSnapshot,
)

_HAND_MODE_KINDS = frozenset({"cast", "cycle", "land_cycle", "channel"})

# Sentinel used in trace bookkeeping. ``5`` slots into the
# first-castable-turn distribution's "5+" bucket.
_NEVER = 5


def simulate_one_game(
    hand: list[Card],
    library: list[Card],
    on_the_play: bool,
    rng: random.Random,
    seed: int = 0,
    verbose: bool = False,
) -> GameTrace:
    """Run one full 4-turn goldfish.

    *hand* and *library* are already-shuffled lists of :class:`Card`
    instances with unique ``instance_id`` values. *seed* is recorded
    on the trace for reproducibility but doesn't affect execution
    (the caller already seeded *rng*).
    """
    state = GameState.initial(hand=hand, library=library, on_the_play=on_the_play, rng=rng)

    # Per-instance bookkeeping. We pre-populate with first_in_hand=0
    # for opening-hand cards and 5 (never) for everything in the
    # library; we'll update first_in_hand for drawn cards as the game
    # progresses, and first_castable each time the snapshot reports it.
    outcomes: dict[int, InstanceOutcome] = {}
    for c in hand:
        outcomes[c.instance_id] = _initial_outcome(c, first_in_hand=0)
    for c in library:
        outcomes[c.instance_id] = _initial_outcome(c, first_in_hand=_NEVER)

    turn_snapshots: list[TurnSnapshot] = []
    actions: list[ActionEvent] = []

    for turn in range(1, 5):
        state.begin_turn()
        cards_drawn: list[Card] = []
        if not (turn == 1 and on_the_play):
            cards_drawn = state.draw(1)
        for c in cards_drawn:
            outcome = outcomes[c.instance_id]
            if outcome.first_in_hand_turn == _NEVER:
                outcome.first_in_hand_turn = turn
            if verbose:
                actions.append(DrawEvent(turn=turn, card_name=c.parsed.name))

        records, aggregate = castability_snapshot(state)
        for record in records:
            if not record.castable:
                continue
            outcome = outcomes[record.card_instance_id]
            if outcome.first_castable_turn == _NEVER:
                outcome.first_castable_turn = turn
            mode_record = _mode_outcome(outcome, record.mode_index, record.mode_kind)
            if mode_record.first_castable_turn == _NEVER:
                mode_record.first_castable_turn = turn

        # Append the snapshot pre-drop, then patch in the game-level
        # counters after the drop. Pydantic BaseModel isn't frozen, so
        # in-place mutation is fine and avoids a copy.
        snapshot = TurnSnapshot(
            turn=turn,
            cards_drawn=[c.parsed.name for c in cards_drawn],
            castability=records,
            aggregate_castable=aggregate,
        )
        turn_snapshots.append(snapshot)

        lands_in_hand_before = [c.instance_id for c in state.hand if c.is_land]
        land, rule = choose_land(state)
        if land is not None:
            state.play_land(land)
        if verbose:
            actions.append(
                LandDropEvent(
                    turn=turn,
                    chosen_instance_id=land.instance_id if land is not None else None,
                    chosen_name=land.parsed.name if land is not None else None,
                    rule_fired=rule,
                    candidates=lands_in_hand_before,
                )
            )

        # Game-level counters: lands and mana sources usable at start of
        # main phase, after the land drop, before any spells go off.
        # All permanents are untapped at this point (just untapped this
        # turn) and the summoning-sick set was cleared by begin_turn(),
        # so the available mana count is just lands + mana_perms.
        snapshot.lands_in_play_after_drop = len(state.battlefield_lands)
        snapshot.mana_sources_at_start_of_main = len(state.battlefield_lands) + len(
            state.battlefield_mana_perms
        )

        cast_results = cast_main_phase(state)
        if verbose:
            for result in cast_results:
                action = result.action
                payment_sources = [ref.source.instance_id for ref, _opt in action.payment]
                actions.append(
                    SpellCastEvent(
                        turn=turn,
                        card_instance_id=action.card.instance_id,
                        card_name=action.card.parsed.name,
                        mode_index=action.mode_idx,
                        mode_kind=action.card.parsed.modes[action.mode_idx].kind,
                        priority=action.priority,
                        payment_sources=payment_sources,
                    )
                )
                if result.scry is not None:
                    actions.append(
                        ScryEvent(
                            turn=turn,
                            saw=result.scry.saw,
                            kept_on_top=result.scry.kept_on_top,
                            bottomed=result.scry.bottomed,
                        )
                    )

        state.end_turn()

    # Turn 5 lookahead: draw + land drop only. Lets the game-level
    # aggregator compute ``p_land_drop_by_turn_5`` without growing the
    # full castability / spell-casting pipeline to a fifth turn (which
    # would also collide with the ``_NEVER = 5`` "never castable" sentinel).
    # Castability / mana-source recording stays at zero here on purpose.
    # We deliberately do NOT update ``first_in_hand_turn`` for cards
    # drawn on turn 5 — the per-card aggregate's ``range(1, 5)``
    # never reads beyond turn 4, and leaving the ``5 == _NEVER``
    # convention intact avoids muddling the "never drawn in the
    # castability window" semantic.
    state.begin_turn()
    turn5_drawn = state.draw(1)
    if verbose:
        for c in turn5_drawn:
            actions.append(DrawEvent(turn=5, card_name=c.parsed.name))
    lands_in_hand_before_t5 = [c.instance_id for c in state.hand if c.is_land]
    land_t5, rule_t5 = choose_land(state)
    if land_t5 is not None:
        state.play_land(land_t5)
    if verbose:
        actions.append(
            LandDropEvent(
                turn=5,
                chosen_instance_id=land_t5.instance_id if land_t5 is not None else None,
                chosen_name=land_t5.parsed.name if land_t5 is not None else None,
                rule_fired=rule_t5,
                candidates=lands_in_hand_before_t5,
            )
        )
    turn_snapshots.append(
        TurnSnapshot(
            turn=5,
            cards_drawn=[c.parsed.name for c in turn5_drawn],
            castability=[],
            aggregate_castable={},
            lands_in_play_after_drop=len(state.battlefield_lands),
            # mana_sources_at_start_of_main stays 0 — turn 5 has no
            # main phase in this simulator. Feature spec doesn't ask
            # for an expected_mana_count_turn_5 either.
            mana_sources_at_start_of_main=0,
        )
    )
    state.end_turn()

    return GameTrace(
        seed=seed,
        on_the_play=on_the_play,
        turns=turn_snapshots,
        instances=list(outcomes.values()),
        actions=actions,
    )


def _initial_outcome(card: Card, *, first_in_hand: int) -> InstanceOutcome:
    """Pre-populate per-mode entries for the modes the snapshot will
    evaluate. The aggregator expects a row per evaluated mode, even if
    that mode never became castable (so the 5+ bucket in the
    distribution counts correctly)."""
    mode_rows = [
        ModeFirstTurn(
            mode_index=idx,
            mode_kind=mode.kind,
            first_castable_turn=_NEVER,
        )
        for idx, mode in enumerate(card.parsed.modes)
        if mode.kind in _HAND_MODE_KINDS
    ]
    return InstanceOutcome(
        instance_id=card.instance_id,
        name=card.parsed.name,
        oracle_id=card.parsed.oracle_id,
        first_in_hand_turn=first_in_hand,
        first_castable_turn=_NEVER,
        modes=mode_rows,
    )


def _mode_outcome(outcome: InstanceOutcome, mode_index: int, mode_kind: str) -> ModeFirstTurn:
    for m in outcome.modes:
        if m.mode_index == mode_index:
            return m
    # Unexpected — the snapshot reported a mode we didn't pre-populate.
    # Append it on the fly so we don't lose data.
    new = ModeFirstTurn(mode_index=mode_index, mode_kind=mode_kind, first_castable_turn=_NEVER)
    outcome.modes.append(new)
    return new
