"""Castability evaluation.

Two functions:

* :func:`is_castable` — given one card *in hand* and the live game
  state, report which of its modes can be paid for right now (using
  only mana sources currently available without playing a new land).
  Per-mode result; an aggregate "any mode castable" is derivable.

* :func:`castability_snapshot` — for each card in hand, walk every
  possible land drop this turn (plus the no-land option) and decide
  whether each mode is castable under the resulting mana availability.
  This is the option-value metric the simulator emits per turn.

Modes we evaluate from hand: ``cast``, ``cycle``, ``land_cycle``,
``channel``. Activated modes (``kind="activated"``) live on permanents
in play, so they're skipped here — they're handled by the spell-casting
policy in step 6.
"""

from __future__ import annotations

from dataclasses import dataclass

from mulligan_coach_cards import Mode

from .mana import ManaPayment, available_mana_abilities, can_pay_cost
from .runtime import Card, GameState
from .trace import CastabilityRecord

_HAND_MODE_KINDS = frozenset({"cast", "cycle", "land_cycle", "channel"})


@dataclass(slots=True)
class CastabilityResult:
    """Summary of one ``is_castable`` call.

    * ``castable`` — True iff at least one hand-mode was payable.
    * ``modes_castable`` — indices into ``card.parsed.modes`` for every
      mode that was payable. Useful when the caller wants per-mode
      attribution (e.g., the snapshot routine populates a
      :class:`CastabilityRecord` per mode).
    * ``witness_payment`` — one valid payment plan for the *first*
      castable mode found, suitable for the engine to actually tap
      sources and cast the spell. ``None`` if uncastable.
    """

    castable: bool
    modes_castable: list[int]
    witness_payment: ManaPayment | None


def _hand_castable_modes(card: Card) -> list[tuple[int, Mode]]:
    """Yield (index, mode) for every mode that's a hand-mode in our
    sense — skipping ``activated`` (battlefield-side) and any with a
    sacrifice cost we can't pay from hand."""
    out: list[tuple[int, Mode]] = []
    for idx, mode in enumerate(card.parsed.modes):
        if mode.kind not in _HAND_MODE_KINDS:
            continue
        sac = mode.cost.sacrifice
        if sac is not None and sac.target != "self":
            # Non-self additional sacrifice on a cast mode: would need
            # an extra creature/land/etc. on the battlefield. Rare, and
            # the v1 simulator doesn't model it — treat as uncastable.
            continue
        out.append((idx, mode))
    return out


def is_castable(card: Card, state: GameState) -> CastabilityResult:
    """Per-mode castability check for a single card in hand.

    The card itself is never tapped (it's in hand, not on the
    battlefield) so ``cost.tap`` on its modes is ignored. ``discard_self``
    on cycling / channel modes is always payable from hand. Mana cost
    is delegated to :func:`mana.can_pay_cost`.
    """
    abilities = available_mana_abilities(state)
    castable_indices: list[int] = []
    witness: ManaPayment | None = None
    for mode_idx, mode in _hand_castable_modes(card):
        payment = can_pay_cost(mode.cost.mana, abilities, state)
        if payment is None:
            continue
        castable_indices.append(mode_idx)
        if witness is None:
            witness = payment
    return CastabilityResult(
        castable=bool(castable_indices),
        modes_castable=castable_indices,
        witness_payment=witness,
    )


def castability_snapshot(state: GameState) -> tuple[list[CastabilityRecord], dict[int, bool]]:
    """Compute the per-card per-mode castability snapshot for the start
    of this turn's main phase.

    For each non-land card in hand, and for each of its hand modes, try
    every legal land drop this turn (plus "no land drop") and record
    whether the mode becomes castable. The result is two values:

    * a list of :class:`CastabilityRecord` (one per (card, mode)) and
    * a per-card aggregate dict mapping ``card.instance_id`` to "any
      mode castable" — convenient for the engine's first-castable-turn
      tracking.

    The function is *pure*: it snapshots state, mutates within try
    blocks, and restores. Callers can rely on ``state`` being unchanged
    on return.
    """
    lands_in_hand = [c for c in state.hand if c.is_land]
    candidates: list[Card | None] = [None, *lands_in_hand]

    records: list[CastabilityRecord] = []
    aggregate: dict[int, bool] = {}

    for card in state.hand:
        if card.is_land:
            continue
        modes = _hand_castable_modes(card)
        if not modes:
            # Card with no hand modes (e.g. an activated-only utility
            # artifact). Mark its instance as not-castable for the
            # aggregate; emit no per-mode rows.
            aggregate[card.instance_id] = False
            continue

        any_castable = False
        for mode_idx, mode in modes:
            castable = False
            witness_id: int | None = None
            for cand in candidates:
                # Manual undo of ``play_land`` instead of full
                # snapshot/restore: ``play_land`` only touches four
                # fields (hand, battlefield_lands, tapped,
                # land_drop_used), and the snapshot's six list-copies
                # + two frozenset constructions are the hottest
                # allocation in the simulator's inner loop.
                if cand is not None:
                    orig_hand_idx = next(
                        i for i, c in enumerate(state.hand) if c.instance_id == cand.instance_id
                    )
                    state.play_land(cand)
                    was_tapped_added = cand.instance_id in state.tapped
                else:
                    orig_hand_idx = -1
                    was_tapped_added = False
                try:
                    abilities = available_mana_abilities(state)
                    if can_pay_cost(mode.cost.mana, abilities, state) is not None:
                        castable = True
                        witness_id = cand.instance_id if cand is not None else None
                        break
                finally:
                    if cand is not None:
                        # Reverse ``play_land``: pop the just-appended
                        # land, put it back at its original hand
                        # position so policy tiebreakers stay stable,
                        # drop the tapped-set entry if play_land added
                        # one, clear the land-drop flag.
                        state.battlefield_lands.pop()
                        state.hand.insert(orig_hand_idx, cand)
                        if was_tapped_added:
                            state.tapped.discard(cand.instance_id)
                        state.land_drop_used = False
            records.append(
                CastabilityRecord(
                    card_instance_id=card.instance_id,
                    card_name=card.name,
                    mode_index=mode_idx,
                    mode_kind=mode.kind,
                    castable=castable,
                    witness_land_choice=witness_id,
                )
            )
            any_castable = any_castable or castable
        aggregate[card.instance_id] = any_castable

    return records, aggregate
