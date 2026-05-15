"""Main-phase spell-casting and ability-activation policy.

The simulator goldfishes a turn by repeatedly picking the *next* useful
action under a fixed priority order:

* **S1** — mana sources and battlefield-fetch effects, in this
  preference order:
    a. Cast a spell that fetches a land to ``battlefield_untapped``
       (Three Visits): the land is available the same turn.
    b. Cast a mana-permanent (a non-land card whose ``mana_abilities``
       is non-empty — dorks and rocks).
    c. Cast a spell that fetches to ``battlefield_tapped`` (Cultivate).
    d. Activate a battlefield ability that fetches to battlefield
       (Evolving Wilds: ``kind=activated`` + ``sac=self`` + fetch).
* **S2** — *only when no land is in hand*: cast or activate hand-fetch
  effects (Environmental Scientist, land-cycling).
* **S3** — card draw, cycling, scry. Includes ``DrawCardsEffect``,
  ``ScryEffect``, and the ``cycle`` mode kind.
* **S4** — hand-fetch even if a land is in hand (lower priority because
  we already have a land drop secured).

Within a tier, candidates are sorted by their mode's CMC ascending
(cheapest first to leave room for more actions in the same turn).

Mana availability is recomputed from scratch between actions — there's
no persistent floating mana pool across actions in v1. Each candidate's
castability is decided fresh by :func:`mana.can_pay_cost` against the
current untapped sources.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mulligan_coach_cards import (
    DiscardCardEffect,
    DrawCardsEffect,
    Effect,
    FetchLandEffect,
    LookAtTopEffect,
    Mode,
    ScryEffect,
)

from .mana import AbilityRef, available_mana_abilities, can_pay_cost
from .runtime import Card, GameState

if TYPE_CHECKING:
    from .mana import ManaPayment


@dataclass(slots=True)
class CastAction:
    """One action the policy has decided to take this turn.

    The engine uses :class:`CastAction` to invoke ``state.cast`` and to
    log a structured ``SpellCastEvent`` (step 8). ``priority`` carries
    the sub-tier label ("S1a", "S1b", ...) for traces.
    """

    card: Card
    mode_idx: int
    payment: ManaPayment
    priority: str


@dataclass(slots=True)
class ScryOutcome:
    """Per-cast scry resolution, captured for the verbose trace.

    Names are used (not Card objects) so the result is JSON-friendly
    once it's been folded into a :class:`trace.ScryEvent`.
    """

    saw: list[str]
    kept_on_top: list[str]
    bottomed: list[str]


@dataclass(slots=True)
class CastResult:
    """A single executed action plus any scry outcome it produced.

    ``cast_main_phase`` returns a list of these in execution order.
    The engine reads them into structured trace events when verbose
    mode is on.
    """

    action: CastAction
    scry: ScryOutcome | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def cast_main_phase(state: GameState) -> list[CastResult]:
    """Run the policy until no improving action remains. Returns the
    list of executed actions in order; each entry pairs the action
    with the scry outcome it produced (if any), for the verbose trace."""
    taken: list[CastResult] = []
    # Bound the loop defensively; in v1 each cast either consumes a card
    # from hand or sacrifices a permanent, so progress is guaranteed,
    # but this guards against future bugs that loop on no-op actions.
    for _ in range(64):
        action = pick_next_action(state)
        if action is None:
            break
        mode = action.card.parsed.modes[action.mode_idx]
        state.cast(action.card, action.mode_idx, action.payment)
        # The Scry effect on a just-cast mode triggers the policy
        # heuristic; ``apply_mode_effects`` itself doesn't know about
        # the heuristic and skips ScryEffect, so we apply it here.
        scry: ScryOutcome | None = None
        if _first_scry(mode.effects) is not None:
            scry = _resolve_scry(state, mode)
        # Plain DrawCardsEffect is also deferred by apply_mode_effects
        # (the loot pattern is the only case it fires there). Fire it
        # here, AFTER scry, so cards bottomed by scry aren't drawn back.
        is_loot_mode = any(isinstance(e, DiscardCardEffect) for e in mode.effects) and any(
            isinstance(e, DrawCardsEffect) for e in mode.effects
        )
        if not is_loot_mode:
            for fx in mode.effects:
                if isinstance(fx, DrawCardsEffect):
                    state.draw(fx.n)
        taken.append(CastResult(action=action, scry=scry))
    return taken


def pick_next_action(state: GameState) -> CastAction | None:
    """Return the next action the policy wants to take, or ``None`` if
    nothing matches the priority chain."""
    for picker in (
        _pick_s1a_cast_fetch_battlefield_untapped,
        _pick_s1b_cast_mana_permanent,
        _pick_s1c_cast_fetch_battlefield_tapped,
        _pick_s1d_activate_battlefield_fetch,
        _pick_s2_hand_fetch_when_no_land,
        _pick_s3_card_draw_or_scry,
        _pick_s4_hand_fetch,
        _pick_s5_cast_prepared_enabler,
    ):
        action = picker(state)
        if action is not None:
            return action
    return None


# ---------------------------------------------------------------------------
# Hand-mode helpers
# ---------------------------------------------------------------------------


_HAND_MODE_KINDS = frozenset({"cast", "cycle", "land_cycle", "channel"})


def _hand_castable_options(
    state: GameState,
    predicate: Callable[[Mode], bool],
) -> Iterator[tuple[Card, int, Mode, ManaPayment]]:
    abilities = available_mana_abilities(state)
    for card in state.hand:
        if card.is_land:
            continue
        for mode_idx, mode in enumerate(card.parsed.modes):
            if mode.kind not in _HAND_MODE_KINDS:
                continue
            if not predicate(mode):
                continue
            payment = can_pay_cost(mode.cost.mana, abilities, state)
            if payment is not None:
                yield card, mode_idx, mode, payment


def _battlefield_activated_options(
    state: GameState,
    predicate: Callable[[Mode], bool],
) -> Iterator[tuple[Card, int, Mode, ManaPayment]]:
    """Walk every untapped (and non-summoning-sick, for creatures)
    permanent and yield any activated mode whose cost we can pay.

    The mana solver gets a copy of the available abilities with the
    *source's* own mana abilities removed — activating the source taps
    it as part of the cost, so the source can't also tap to produce
    mana for that same cost.
    """
    full_abilities = available_mana_abilities(state)
    sources = (
        list(state.battlefield_lands)
        + list(state.battlefield_mana_perms)
        + list(state.battlefield_other)
    )
    for card in sources:
        if card.instance_id in state.tapped:
            continue
        if card.is_creature and card.instance_id in state.summoning_sick:
            continue
        for mode_idx, mode in enumerate(card.parsed.modes):
            if mode.kind != "activated":
                continue
            if not predicate(mode):
                continue
            filtered = _without_source(full_abilities, card.instance_id)
            payment = can_pay_cost(mode.cost.mana, filtered, state)
            if payment is not None:
                yield card, mode_idx, mode, payment


def _battlefield_prepared_options(
    state: GameState,
    predicate: Callable[[Mode], bool],
) -> Iterator[tuple[Card, int, Mode, ManaPayment]]:
    """Walk prepared permanents and yield any prepared mode whose cost
    we can pay.

    Unlike activated abilities, prepared spells don't tap the source —
    they just remove the "prepared" designation. So we don't filter out
    the source's mana abilities. The source itself can be summoning-sick
    (the prepared spell is sorcery-speed; whether the source has summoning
    sickness doesn't matter — we're not attacking with it).
    """
    if not state.prepared:
        return
    abilities = available_mana_abilities(state)
    # Build a quick lookup: instance_id -> Card across battlefield zones.
    by_id: dict[int, Card] = {}
    for zone in (state.battlefield_mana_perms, state.battlefield_other):
        for c in zone:
            by_id[c.instance_id] = c
    for instance_id in state.prepared:
        card = by_id.get(instance_id)
        if card is None:
            continue
        for mode_idx, mode in enumerate(card.parsed.modes):
            if mode.kind != "prepared":
                continue
            if not predicate(mode):
                continue
            payment = can_pay_cost(mode.cost.mana, abilities, state)
            if payment is not None:
                yield card, mode_idx, mode, payment


def _without_source(abilities: list[AbilityRef], source_instance_id: int) -> list[AbilityRef]:
    return [a for a in abilities if a.source.instance_id != source_instance_id]


def _has_effect(mode: Mode, predicate: Callable[[Effect], bool]) -> bool:
    return any(predicate(e) for e in mode.effects)


def _has_fetch_to(mode: Mode, destination: str) -> bool:
    return _has_effect(
        mode, lambda e: isinstance(e, FetchLandEffect) and e.destination == destination
    )


def _has_land_to_hand(mode: Mode) -> bool:
    """True if *mode* may put a land in hand on resolution.

    Covers ``FetchLandEffect(destination="hand")`` (Environmental Scientist,
    land-cycling) AND ``LookAtTopEffect(accepts_land=True)`` (Midnight
    Tilling, Cowabunga!, etc.). The latter only finds a land
    probabilistically — when one happens to be in the top N — but for
    the policy's purposes that's still a hand-fetch shape.
    """
    return _has_effect(
        mode,
        lambda e: (
            (isinstance(e, FetchLandEffect) and e.destination == "hand")
            or (isinstance(e, LookAtTopEffect) and e.accepts_land)
        ),
    )


def _has_draw_or_scry(mode: Mode) -> bool:
    return _has_effect(mode, lambda e: isinstance(e, DrawCardsEffect | ScryEffect))


def _cmc(card: Card, mode: Mode) -> int:
    return mode.cost.mana.cmc


def _first_or_none(
    candidates: Iterator[tuple[Card, int, Mode, ManaPayment]],
    label: str,
) -> CastAction | None:
    """Sort the candidates by mode CMC and return the first as a
    :class:`CastAction`, or ``None`` if the iterator is empty."""
    sorted_cands = sorted(candidates, key=lambda c: _cmc(c[0], c[2]))
    if not sorted_cands:
        return None
    card, mode_idx, _mode, payment = sorted_cands[0]
    return CastAction(card=card, mode_idx=mode_idx, payment=payment, priority=label)


# ---------------------------------------------------------------------------
# S1 sub-tiers
# ---------------------------------------------------------------------------


def _chain(
    *its: Iterator[tuple[Card, int, Mode, ManaPayment]],
) -> Iterator[tuple[Card, int, Mode, ManaPayment]]:
    for it in its:
        yield from it


def _pick_s1a_cast_fetch_battlefield_untapped(state: GameState) -> CastAction | None:
    return _first_or_none(
        _chain(
            _hand_castable_options(
                state,
                lambda m: m.kind == "cast" and _has_fetch_to(m, "battlefield_untapped"),
            ),
            _battlefield_prepared_options(
                state,
                lambda m: _has_fetch_to(m, "battlefield_untapped"),
            ),
        ),
        "S1a",
    )


def _pick_s1b_cast_mana_permanent(state: GameState) -> CastAction | None:
    """Cast a non-land hand card whose ``parsed.mana_abilities`` is
    non-empty (mana dorks, mana rocks). The cast mode of these cards
    typically just has an ``EntersBattlefieldEffect``; the value is in
    the post-cast ``mana_abilities`` they bring to the battlefield.

    We filter in the comprehension rather than via a Mode predicate
    because the property we want lives on the ``Card`` (``has_mana_ability``),
    not on its current mode.
    """
    sorted_cands = sorted(
        (
            (card, idx, mode, payment)
            for card, idx, mode, payment in _hand_castable_options(
                state, lambda m: m.kind == "cast"
            )
            if card.has_mana_ability
        ),
        key=lambda c: _cmc(c[0], c[2]),
    )
    if not sorted_cands:
        return None
    card, mode_idx, _mode, payment = sorted_cands[0]
    return CastAction(card=card, mode_idx=mode_idx, payment=payment, priority="S1b")


def _pick_s1c_cast_fetch_battlefield_tapped(state: GameState) -> CastAction | None:
    return _first_or_none(
        _chain(
            _hand_castable_options(
                state,
                lambda m: m.kind == "cast" and _has_fetch_to(m, "battlefield_tapped"),
            ),
            _battlefield_prepared_options(
                state,
                lambda m: _has_fetch_to(m, "battlefield_tapped"),
            ),
        ),
        "S1c",
    )


def _pick_s1d_activate_battlefield_fetch(state: GameState) -> CastAction | None:
    """Activate any in-play ability that fetches a land to battlefield
    (Evolving Wilds is the canonical example: tap, sac, fetch tapped)."""
    return _first_or_none(
        _battlefield_activated_options(
            state,
            lambda m: (
                _has_fetch_to(m, "battlefield_tapped") or _has_fetch_to(m, "battlefield_untapped")
            ),
        ),
        "S1d",
    )


# ---------------------------------------------------------------------------
# S2 / S3 / S4
# ---------------------------------------------------------------------------


def _hand_has_land(state: GameState) -> bool:
    return any(c.is_land for c in state.hand)


def _pick_s2_hand_fetch_when_no_land(state: GameState) -> CastAction | None:
    """Cast or activate a hand-fetch ONLY when hand has zero lands.
    Otherwise fall through to S3 / S4."""
    if _hand_has_land(state):
        return None
    # From hand: cast modes (Environmental Scientist, etc.) and the
    # land-cycling alt mode all qualify. LookAtTopEffect with
    # accepts_land=True (Midnight Tilling) is treated as a hand-fetch
    # too — it only finds a land probabilistically, but the shape is
    # the same from the policy's perspective.
    hand_action = _first_or_none(
        _hand_castable_options(state, _has_land_to_hand),
        "S2",
    )
    if hand_action is not None:
        return hand_action
    # From battlefield: any activated or prepared mode that fetches to hand.
    return _first_or_none(
        _chain(
            _battlefield_activated_options(state, _has_land_to_hand),
            _battlefield_prepared_options(state, _has_land_to_hand),
        ),
        "S2",
    )


def _pick_s3_card_draw_or_scry(state: GameState) -> CastAction | None:
    """Cast / activate effects that draw, cycle, or scry."""
    hand_action = _first_or_none(
        _hand_castable_options(state, _has_draw_or_scry),
        "S3",
    )
    if hand_action is not None:
        return hand_action
    return _first_or_none(
        _chain(
            _battlefield_activated_options(state, _has_draw_or_scry),
            _battlefield_prepared_options(state, _has_draw_or_scry),
        ),
        "S3",
    )


def _pick_s4_hand_fetch(state: GameState) -> CastAction | None:
    hand_action = _first_or_none(
        _hand_castable_options(state, _has_land_to_hand),
        "S4",
    )
    if hand_action is not None:
        return hand_action
    return _first_or_none(
        _chain(
            _battlefield_activated_options(state, _has_land_to_hand),
            _battlefield_prepared_options(state, _has_land_to_hand),
        ),
        "S4",
    )


def _card_has_useful_prepared_mode(card: Card) -> bool:
    """True if *card* has at least one prepared mode whose effect is
    mulligan-relevant — fetch_land, look_at_top with land, or
    draw_cards. Used by S5 to decide which hand creatures are worth
    casting so their prepared mode becomes available next turn.
    """
    for m in card.parsed.modes:
        if m.kind != "prepared":
            continue
        for fx in m.effects:
            if isinstance(fx, (FetchLandEffect, DrawCardsEffect)):
                return True
            if isinstance(fx, LookAtTopEffect) and fx.accepts_land:
                return True
    return False


def _pick_s5_cast_prepared_enabler(state: GameState) -> CastAction | None:
    """Cast a hand creature whose prepared mode is mulligan-relevant
    (fetch / draw / land-find), so the prepared spell is castable on a
    later turn. Last resort: only fires if no other tier matched.

    Casting the creature pays its mana cost; the engine flags the
    resulting permanent as prepared via ``runtime._place_after_cast``.
    """
    candidates = (
        (card, idx, mode, payment)
        for card, idx, mode, payment in _hand_castable_options(state, lambda m: m.kind == "cast")
        if _card_has_useful_prepared_mode(card)
    )
    return _first_or_none(candidates, "S5")


# ---------------------------------------------------------------------------
# Scry (S3 sub-effect — applied after the spell resolves)
#
# The ScryEffect itself is a no-op in :mod:`effects.apply_mode_effects`
# (which doesn't have access to the policy heuristic). When the casting
# loop spots a ScryEffect on a just-cast mode, it calls _resolve_scry
# to apply the heuristic: bottom anything that won't be playable,
# keep useful cards on top in the order ``[lands, castable_spells,
# rest]``.
# ---------------------------------------------------------------------------


def _first_scry(effects: list[Effect]) -> Effect | None:
    for e in effects:
        if isinstance(e, ScryEffect):
            return e
    return None


def _resolve_scry(state: GameState, mode: Mode) -> ScryOutcome:
    """Look at the top N of the library, decide bottom vs. keep, and
    rearrange. Bottoming preserves library order at the bottom; we
    don't model the order distinction further (any ordering on bottom
    is equivalent for our purposes).

    The "would this be castable" heuristic looks one turn ahead: we
    snapshot, simulate the upcoming untap step (so currently-tapped
    sources are usable), and evaluate castability against that.
    Otherwise a Preordain cast on turn 2 with both lands tapped to
    pay it would always conclude that *nothing* is castable next
    turn — too pessimistic.

    Returns a :class:`ScryOutcome` describing what happened, even
    when the scry is a no-op (empty library); the caller distinguishes
    via empty ``saw`` rather than ``None``.
    """
    n = 0
    for fx in mode.effects:
        if isinstance(fx, ScryEffect):
            n = max(n, fx.n)
    n = min(n, len(state.library))
    if n == 0:
        return ScryOutcome(saw=[], kept_on_top=[], bottomed=[])
    seen = list(state.library[:n])
    rest = list(state.library[n:])

    snap = state.snapshot()
    keep_top: list[Card] = []
    bottom: list[Card] = []
    try:
        state.untap_all()
        state.summoning_sick.clear()
        abilities = available_mana_abilities(state)
        has_land_in_hand = _hand_has_land(state)
        for card in seen:
            if card.is_land and not has_land_in_hand:
                keep_top.append(card)
                continue
            if _castable_under_mode(card, abilities, state):
                keep_top.append(card)
            else:
                bottom.append(card)
    finally:
        state.restore(snap)

    state.library = keep_top + rest + bottom
    return ScryOutcome(
        saw=[c.parsed.name for c in seen],
        kept_on_top=[c.parsed.name for c in keep_top],
        bottomed=[c.parsed.name for c in bottom],
    )


def _castable_under_mode(card: Card, abilities: list[AbilityRef], state: GameState) -> bool:
    if card.is_land:
        return False
    for mode in card.parsed.modes:
        if mode.kind not in _HAND_MODE_KINDS:
            continue
        if can_pay_cost(mode.cost.mana, abilities, state) is not None:
            return True
    return False
