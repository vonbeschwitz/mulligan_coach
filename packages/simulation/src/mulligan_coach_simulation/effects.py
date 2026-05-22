"""Predicate evaluation, ETB-tapped checks, and fetch-effect resolution.

The simulator's "what changes when a card enters / is cast" logic
lives here. Three interlocking pieces:

* :func:`predicate_holds` — evaluates one ``Predicate`` against the
  current battlefield. Used by ``enter_condition`` (does this land ETB
  tapped?), by mana-ability conditions, and by future
  ``ProduceManaEffect.condition`` checks. Closed enum, so adding a new
  predicate kind in the cards package is a single ``elif`` branch here
  and a covering test.

* :func:`enters_tapped` — small helper that wraps the ETB predicate
  for lands. ``None`` means "never enters tapped".

* :func:`apply_mode_effects` and :func:`resolve_fetch` — when a card is
  cast or its activated ability resolves, walk its mode's effects and
  apply each one. Step 4 wires up the FetchLandEffect path; the
  DrawCardsEffect and ScryEffect handlers are stubbed and become
  policy-driven in step 6.

The ``excluding`` argument on the predicate evaluator handles the
"counts other lands you control" subtlety — when a land's ETB
predicate is being checked, the land itself is technically on the
stack, not the battlefield, so it doesn't count in ``controls_lands_*``
checks. Callers pass ``excluding=that_land`` to subtract it. In the
current simulator we evaluate before appending, so ``excluding`` is
mostly belt-and-braces, but it's exposed for clarity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mulligan_coach_cards import (
    DiscardCardEffect,
    DrawCardsEffect,
    Effect,
    FetchLandEffect,
    LookAtTopEffect,
    ParsedCard,
    Predicate,
)

if TYPE_CHECKING:
    from .runtime import Card, GameState


# ---------------------------------------------------------------------------
# Predicate evaluation
# ---------------------------------------------------------------------------


_BASIC_TYPES = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest"})


def predicate_holds(
    p: Predicate | None,
    state: GameState,
    *,
    excluding: Card | None = None,
) -> bool:
    """Evaluate *p* against *state*.

    ``None`` is interpreted as "no condition" (always true), which is
    appropriate for ``ManaAbility.condition`` defaults. Note: callers
    that interpret ``None`` differently (e.g. ``enter_condition=None``
    means "land never enters tapped") should check that case
    themselves before calling this.

    *excluding* names a card to ignore when counting lands. Used for
    Deathcap-style ETB predicates that ask about "other" lands.
    """
    if p is None:
        return True

    if p.kind == "always":
        return True

    if p.kind == "controls_lands_ge":
        return _land_count(state, excluding) >= (p.n or 0)
    if p.kind == "controls_lands_lt":
        return _land_count(state, excluding) < (p.n or 0)

    if p.kind == "controls_basic":
        target = p.basic_type
        if target is None:
            return False
        for land in state.battlefield_lands:
            if excluding is not None and land.instance_id == excluding.instance_id:
                continue
            if target in land.parsed.subtypes:
                return True
        return False

    if p.kind == "controls_basic_any":
        for land in state.battlefield_lands:
            if excluding is not None and land.instance_id == excluding.instance_id:
                continue
            if any(s in _BASIC_TYPES for s in land.parsed.subtypes):
                return True
        return False

    if p.kind == "controls_color":
        target = p.color
        if target is None:
            return False
        sources = list(state.battlefield_lands) + list(state.battlefield_mana_perms)
        for source in sources:
            if excluding is not None and source.instance_id == excluding.instance_id:
                continue
            for ab in source.parsed.mana_abilities:
                # An ability can produce target color via any of its
                # OR-options. Filter outputs ``"any"`` count too.
                for option in ab.produces:
                    if target in option or "any" in option:
                        return True
        return False

    raise AssertionError(f"unknown predicate kind {p.kind!r}")


def _land_count(state: GameState, excluding: Card | None) -> int:
    if excluding is None:
        return len(state.battlefield_lands)
    return sum(1 for c in state.battlefield_lands if c.instance_id != excluding.instance_id)


def enters_tapped(parsed: ParsedCard, state: GameState) -> bool:
    """Does the land *parsed* enter tapped under the current state?

    Convention from the cards package: ``enter_condition=None`` means
    the land has no ETB-tapped clause (basics and untapped duals).
    Anything else is a predicate that *says when the land enters
    tapped*; the land enters untapped iff the predicate is *false*
    in the resulting state.
    """
    cond = parsed.enter_condition
    if cond is None:
        return False
    return predicate_holds(cond, state)


# ---------------------------------------------------------------------------
# Effect resolution
# ---------------------------------------------------------------------------


def apply_mode_effects(state: GameState, effects: list[Effect]) -> None:
    """Resolve every effect in a mode's effects list against *state*.

    ``EntersBattlefieldEffect`` is a marker — the engine has already
    moved the permanent to the battlefield by this point, so there's
    nothing for this handler to do.

    ``FetchLandEffect`` is implemented here (lands move per the fx's
    target / destination axes). ``DrawCardsEffect`` and ``ScryEffect``
    are deferred — the spell-casting policy in step 6 owns them
    because draws and scrys interact with the policy's "what should I
    look for" heuristic.

    ``NoopEffect`` and ``ProduceManaEffect`` are no-ops here:
    ``ProduceManaEffect`` would matter for spells that produce mana on
    cast (rare in Limited), but v1 doesn't model the live mana pool
    persistently across cast actions, so the floated mana evaporates.
    """
    # Loot — DrawCardsEffect paired with DiscardCardEffect — is resolved
    # eagerly here so the sim actually draws and discards (matching the
    # cards-package emission for "draw N, then discard M" patterns). For
    # the plain draw / scry-then-draw shape (cantrips), DrawCardsEffect
    # is still deferred — the spell-casting policy applies scry's
    # heuristic between cast resolution and the player's "next draw step",
    # so resolving draw eagerly here would put scry's just-bottomed cards
    # on top of the drawn card. Once the policy gains a unified "resolve
    # cast effects in order" pathway, this special case goes away.
    is_loot = any(isinstance(fx, DiscardCardEffect) for fx in effects) and any(
        isinstance(fx, DrawCardsEffect) for fx in effects
    )
    for fx in effects:
        if isinstance(fx, FetchLandEffect):
            resolve_fetch(state, fx)
        elif isinstance(fx, LookAtTopEffect):
            resolve_look_at_top(state, fx)
        elif isinstance(fx, DrawCardsEffect):
            if is_loot:
                state.draw(fx.n)
        elif isinstance(fx, DiscardCardEffect):
            resolve_discard(state, fx)
        # Other effects: deliberately ignored at this layer in v1.


def resolve_fetch(state: GameState, fx: FetchLandEffect) -> None:
    """Implement the FetchLandEffect: search the library for *count*
    lands matching *target_filter*, send each to *destination*, and
    shuffle the library if any cards were found.

    If no matching land is in the library, the effect is a no-op for
    that count. The shuffle happens once at the end (Magic's
    convention), regardless of how many were found.
    """
    found_any = False
    for _ in range(fx.count):
        candidate = _pick_fetch_target(state, fx)
        if candidate is None:
            break
        state.library.remove(candidate)
        _put_fetched_card(state, candidate, fx.destination)
        found_any = True
    if found_any:
        state.rng.shuffle(state.library)


def _pick_fetch_target(state: GameState, fx: FetchLandEffect) -> Card | None:
    """Return the best library card that matches *fx*, or None.

    For ``target_filter="specific_subtype"`` there's only one
    subtype to match — first land found wins.

    For ``target_filter="basic"`` / ``"any"`` the choice matters:
    a real player picks the basic whose color fills a gap. The
    simulator's earlier "first basic in library order" pick was
    effectively random (the library is shuffled), so Terramorphic
    Expanse / Evolving Wilds / etc. only landed on the needed color
    by chance. Now we score every matching candidate by how much
    its color helps hand spells become castable, and break ties on
    library order.
    """
    candidates: list[Card] = []
    for card in state.library:
        if not card.is_land:
            continue
        parsed = card.parsed
        if fx.target_filter == "basic" and "Basic" not in parsed.supertypes:
            continue
        if fx.target_filter == "specific_subtype" and (
            fx.subtype is None or fx.subtype not in parsed.subtypes
        ):
            continue
        candidates.append(card)
    if not candidates:
        return None
    # Specific-subtype fetches only have one valid subtype — score
    # collapses to library order anyway, so skip the work.
    if fx.target_filter == "specific_subtype":
        return candidates[0]
    needed = _needed_colors_from_hand(state)
    if not needed:
        return candidates[0]
    # Score each candidate by colors-produced ∩ needed-colors.
    best = candidates[0]
    best_score = _score_basic_for_needs(best, needed)
    for cand in candidates[1:]:
        score = _score_basic_for_needs(cand, needed)
        if score > best_score:
            best = cand
            best_score = score
    return best


def _needed_colors_from_hand(state: GameState) -> dict[str, int]:
    """Map color -> "how badly we need this color" for fetch selection.

    Restricted to colors NOT already producible from the current
    battlefield + lands in hand — fetching a redundant color is
    wasted. The weight is ``max(0, 6 - cmc)`` for each hand spell
    that has a colored pip in the color, so a 1- or 2-mana spell
    (the kind we'd cast on T2 / T3 off the fetch) dominates a
    4-mana spell (which needs four lands and can be paid for by
    a later turn's land drop anyway). Pip counts are folded in by
    summing across pips (e.g. ``{U}{U}`` contributes twice). The
    weight curve is intentionally simple: any priority order
    favouring cheap spells over expensive ones over "don't care"
    is enough to break the realistic ties.

    Returns an empty dict when every colored pip in hand spells is
    already covered — caller falls back to library order.
    """
    available: set[str] = set()
    sources: list[Card] = list(state.battlefield_lands) + list(state.battlefield_mana_perms)
    sources += [c for c in state.hand if c.is_land]
    for src in sources:
        for ab in src.parsed.mana_abilities:
            for option in ab.produces:
                for option_color in option:
                    if option_color != "any":
                        available.add(option_color)
    needs: dict[str, int] = {}
    for card in state.hand:
        if card.is_land:
            continue
        cost = card.parsed.mana_cost
        if cost is None:
            continue
        # Cheaper spells get more weight: a 2-drop fetch enables T2
        # play; a 5-drop fetch barely beats just drawing a basic.
        weight = max(1, 6 - cost.cmc)
        for color, count in cost.color_pips.items():
            if color in available:
                continue
            needs[color] = needs.get(color, 0) + weight * count
    return needs


def _score_basic_for_needs(card: Card, needs: dict[str, int]) -> int:
    """Sum needs[c] over every concrete color *card* can produce."""
    score = 0
    seen: set[str] = set()
    for ab in card.parsed.mana_abilities:
        for option in ab.produces:
            for color in option:
                if color != "any" and color not in seen:
                    seen.add(color)
                    score += needs.get(color, 0)
    return score


def _put_fetched_card(state: GameState, card: Card, destination: str) -> None:
    if destination == "hand":
        state.hand.append(card)
        return
    # Both battlefield destinations: append to lands and set tapped iff
    # destination says so. We deliberately do NOT consult the card's
    # ``enter_condition`` here — the fetcher specifies the entering
    # state. (e.g. Cultivate's tapped half always enters tapped, even
    # if the fetched basic would normally ETB untapped.)
    state.battlefield_lands.append(card)
    if destination == "battlefield_tapped":
        state.tapped.add(card.instance_id)
    elif destination != "battlefield_untapped":
        raise AssertionError(f"unknown fetch destination {destination!r}")


def resolve_discard(state: GameState, fx: DiscardCardEffect) -> None:
    """Discard up to ``fx.n`` cards from hand.

    Heuristic — Magic doesn't choose a discard for you; the simulator
    picks the card with the *least* near-term value:

    1. Extra lands when the hand already has 3+ lands (lands beyond the
       fourth aren't useful in a four-turn window).
    2. The highest-CMC non-land card otherwise (least likely to be
       cast in the remaining mulligan-relevant turns).
    3. Final tiebreak: hand-end index (LIFO).

    Library shorter than the discard count is fine — we discard what
    we can. The chosen card moves from hand to graveyard.
    """
    for _ in range(fx.n):
        if not state.hand:
            return
        n_lands_in_hand = sum(1 for c in state.hand if c.is_land)
        choice: Card | None = None
        if n_lands_in_hand >= 3:
            for card in reversed(state.hand):
                if card.is_land:
                    choice = card
                    break
        if choice is None:
            non_lands = [c for c in state.hand if not c.is_land]
            if non_lands:

                def _cmc(card: Card) -> int:
                    cost = card.parsed.mana_cost
                    return cost.cmc if cost is not None else 0

                choice = max(non_lands, key=_cmc)
        if choice is None:
            choice = state.hand[-1]
        state.hand.remove(choice)
        state.graveyard.append(choice)


def resolve_look_at_top(state: GameState, fx: LookAtTopEffect) -> None:
    """Pop the top *fx.n* cards of the library; if a land is among them
    and the effect ``accepts_land``, take the first land; else if the
    effect ``accepts_nonland`` take the first nonland; else nothing.
    The taken card goes to ``state.hand``; the rest are placed at the
    bottom of the library in original order.

    Library shorter than n is fine — we just look at what's there. No
    shuffle (the cards we pass over are bottomed in place; no reorder).
    """
    n = min(fx.n, len(state.library))
    if n == 0:
        return
    top = state.library[:n]
    state.library = state.library[n:]
    chosen: Card | None = None
    if fx.accepts_land:
        for card in top:
            if card.is_land:
                chosen = card
                break
    if chosen is None and fx.accepts_nonland:
        for card in top:
            if not card.is_land:
                chosen = card
                break
    if chosen is not None:
        top.remove(chosen)
        state.hand.append(chosen)
    state.library.extend(top)
