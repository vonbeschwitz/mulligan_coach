"""Bottoming heuristic for London mulligans.

When a player mulligans to N (for N < 7) under the London rule, they
draw 7 cards, then put 7-N of them on the bottom of the library in any
order. To simulate the resulting (7-N)-card hand for win-probability
estimation, we need a rule that picks which card(s) to bottom.

Evaluating all C(7, 7-N) possibilities by full simulation is too
expensive (7x sim cost for N=6 alone, 21x for N=5). Instead, this
module implements a hand-crafted heuristic ladder that mirrors how
strong Limited players reason about bottoming decisions. The rules
are spelled out in ``packages/model/bottoming_heuristics.md``; this
file is the executable form.

The heuristic, in priority order, decides:

1. **Land vs. spell.** Based on hand land count, with a special case
   for 4-land hands that have a real early play.
2. **If land**: keep duals, keep lands needed for color requirements,
   bottom the most over-represented color, tiebreak by which color
   has fewer cards in the deck.
3. **If spell**: bottom uncastable spells first (uncastable = not
   payable with all hand-lands in play, including color requirements);
   among uncastable prefer those whose colors aren't met (a "needs
   more mana" spell is keep-worthy because any draw helps); then
   higher CMC; then lower Opening-Hand Win Rate.

All ties fall back to hand index for determinism.

This is the v1 heuristic; a future iteration may swap it for an
optimal "evaluate all 7 bottoms" routine on the offline benchmark
once the ceiling matters.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from mulligan_coach_cards import ManaCost, Pip

from .castability import is_castable
from .mana import can_pay_cost
from .runtime import Card, GameState

# A small WUBRG-ordered tuple — used for stable iteration / tiebreakers.
_WUBRG: tuple[str, ...] = ("W", "U", "B", "R", "G")

# Set of "broad removal-or-threat" role flags that count as an "early
# play" for the 4-land rule. Combat tricks are excluded — they're
# reactive, not board-developing, and the heuristic asks "do I have
# something that actually uses the lands?".
_EARLY_PLAY_ROLES: frozenset[str] = frozenset(
    {
        "is_creature",
        "removal_destroy_or_exile",
        "is_counterspell",
        "is_bounce",
        "is_top_library",
        "is_removal_aura",
    }
)


# Type alias: a callable that returns the **shrunk** Opening-Hand Win
# Rate for a card. Callers should pass the shrinkage-adjusted value
# (from ``mulligan_coach_features.seventeenlands_shrinkage``), not the
# raw 17Lands OH WR — shrinkage fills in low-sample / never-played
# cards, which would otherwise be a frequent source of ``None``. The
# return type permits ``None`` only as a defensive fallback (e.g.,
# arena_id mismatch on a freshly-released set whose MTGJSON join key
# hasn't landed yet); cards with ``None`` are treated as unrankable
# for the purposes of rule S4 and skipped from its narrowing.
OhWrLookup = Callable[[Card], float | None]


def bottom_card(
    hand: list[Card],
    deck: list[Card],
    *,
    oh_wr: OhWrLookup | None = None,
) -> Card:
    """Return the ``Card`` from *hand* to put on the bottom for a London
    mulligan-to-(len(hand)-1).

    *hand* is the 7 cards drawn; *deck* is the full 40-card deck (used
    for the deck-color-count tiebreaker on lands). *oh_wr*, if given,
    is the OH WR lookup used as the lowest-priority spell tiebreaker.

    Returns one card from *hand*. Caller removes it from the hand and
    appends to the bottom of the library before simulating.

    The function is pure — it doesn't mutate *hand* or *deck*.
    """
    if not hand:
        raise ValueError("bottom_card: hand is empty")
    lands = [c for c in hand if c.is_land]
    spells = [c for c in hand if not c.is_land]
    # Stable hand-index map for tiebreakers.
    hand_index = {id(c): i for i, c in enumerate(hand)}

    if _should_bottom_land(lands, spells):
        return _pick_land(lands, spells, deck, hand_index)
    return _pick_spell(spells, lands, deck, hand_index, oh_wr)


# ---------------------------------------------------------------------------
# Step 1 — land vs. spell decision.
# ---------------------------------------------------------------------------


def _should_bottom_land(lands: list[Card], spells: list[Card]) -> bool:
    """Decide land-vs-spell per the heuristic's first ladder.

    * No spells in hand → must bottom a land.
    * No lands in hand → must bottom a spell.
    * 5+ lands → bottom a land.
    * 0-3 lands → bottom a spell.
    * Exactly 4 lands → bottom a land iff there's a castable ≤3-CMC
      "early play" in hand (creature / removal / counter / bounce /
      removal-aura), evaluated against all 4 hand-lands in play.
    """
    n_lands = len(lands)
    if not spells:
        return True
    if n_lands == 0:
        return False
    if n_lands >= 5:
        return True
    if n_lands <= 3:
        return False
    # n_lands == 4
    state = _state_with_lands_in_play(lands)
    return any(_is_early_play(spell) and _is_castable_in(spell, state) for spell in spells)


def _is_early_play(card: Card) -> bool:
    """True iff the card is a creature, targeted removal, counter,
    bounce, removal aura, etc., AND its printed CMC is <= 3.

    Combat tricks (`combat_trick_power` populated) are excluded — they
    don't develop the board and aren't what the 4-land rule cares
    about. Burn that hits creatures (`removal_burn_damage`) IS
    included.
    """
    cost = card.parsed.mana_cost
    if cost is None or cost.cmc > 3:
        return False
    rf = card.parsed.role_features
    if rf.removal_burn_damage is not None:
        return True
    return any(getattr(rf, flag) for flag in _EARLY_PLAY_ROLES)


# ---------------------------------------------------------------------------
# Step 2a — which land to bottom.
# ---------------------------------------------------------------------------


def _pick_land(
    lands: list[Card],
    spells: list[Card],
    deck: list[Card],
    hand_index: dict[int, int],
) -> Card:
    """Apply the 4-rule ladder to choose a land. Falls back to hand
    index when every rule leaves a tie."""
    candidates = list(lands)

    # Rule L1: prefer to keep duals — bottom basic-equivalent (single-
    # color) lands first. Only narrow if there's at least one dual
    # AND at least one single-color land among candidates.
    by_dual = [c for c in candidates if _is_dual(c)]
    by_single = [c for c in candidates if not _is_dual(c)]
    if by_dual and by_single:
        candidates = by_single

    # Rule L2: bottom lands not needed for hand color requirements.
    needed_pip_max = _hand_color_pip_max(spells)
    in_hand_color_counts = _land_color_counts(lands)
    expendable = [
        c for c in candidates if _is_land_expendable(c, in_hand_color_counts, needed_pip_max)
    ]
    if expendable:
        candidates = expendable

    # Rule L3: bottom the color most over-represented in hand.
    if len(candidates) > 1:
        by_oversupply = _filter_by_oversupply(candidates, in_hand_color_counts)
        if by_oversupply:
            candidates = by_oversupply

    # Rule L4: bottom the color that has fewest cards in deck supporting it.
    if len(candidates) > 1:
        deck_color_counts = _deck_color_counts(deck)
        candidates = _filter_by_fewest_in_deck(candidates, deck_color_counts)

    candidates.sort(key=lambda c: hand_index[id(c)])
    return candidates[0]


def _is_dual(land: Card) -> bool:
    """True iff the land can produce more than one distinct named color.

    Filter lands with `produces=[["any"]]` count as duals (they can
    produce any of the five colors). Basics produce one color and
    return False.
    """
    colors: set[str] = set()
    for ability in land.parsed.mana_abilities:
        for option in ability.produces:
            for color in option:
                if color in _WUBRG or color == "any":
                    colors.add(color)
    if "any" in colors:
        return True
    return len(colors) >= 2


def _producible_colors(land: Card) -> set[str]:
    """Return the set of named colors the land can produce. Wildcards
    (``"any"``) expand to all five WUBRG colors."""
    colors: set[str] = set()
    for ability in land.parsed.mana_abilities:
        for option in ability.produces:
            for color in option:
                if color == "any":
                    colors.update(_WUBRG)
                elif color in _WUBRG:
                    colors.add(color)
    return colors


def _land_color_counts(lands: list[Card]) -> dict[str, int]:
    """For each WUBRG color, count how many lands in *lands* can produce
    that color. Duals contribute to all colors they can produce."""
    counts = {c: 0 for c in _WUBRG}
    for land in lands:
        for color in _producible_colors(land):
            counts[color] += 1
    return counts


def _hand_color_pip_max(spells: list[Card]) -> dict[str, int]:
    """For each WUBRG color, the maximum colored-pip requirement of any
    single spell in hand. This is the binding constraint: a spell
    needing WW needs 2 white sources, regardless of how many other
    spells share the W.
    """
    max_pips = {c: 0 for c in _WUBRG}
    for spell in spells:
        cost = spell.parsed.mana_cost
        if cost is None:
            continue
        for color in _WUBRG:
            need = cost.color_pips.get(color, 0)
            if need > max_pips[color]:
                max_pips[color] = need
    return max_pips


def _is_land_expendable(
    land: Card,
    hand_counts: dict[str, int],
    needed_max: dict[str, int],
) -> bool:
    """A land is expendable iff removing it would still leave at least
    ``needed_max[c]`` lands in hand producing each color *c* it
    contributed to.

    This is an approximation: it treats colors independently and
    pretends each land of color X contributes one toward the X
    requirement. For duals, "contributes" means the land counted
    toward all colors it can produce, so removing it decrements every
    such color count by 1. The approximation is fine for the hand
    sizes the heuristic operates on.
    """
    produced = _producible_colors(land)
    return all(hand_counts[color] - 1 >= needed_max[color] for color in produced)


def _filter_by_oversupply(
    candidates: list[Card],
    hand_counts: dict[str, int],
) -> list[Card]:
    """Narrow *candidates* to those producing the color most
    over-represented in the in-hand land mix.

    A land's "oversupply score" is the *max* count across the colors
    it can produce — bottoming a land of the most-frequent color
    relieves the worst over-representation. Returns the lands tied
    at the maximum oversupply score, or all candidates if none have
    a colored output.
    """
    scored: list[tuple[int, Card]] = []
    for c in candidates:
        produced = _producible_colors(c)
        if not produced:
            # Colorless utility land — nothing to compare on. Treat
            # as score 0 so it's deprioritised vs lands with a clear
            # over-supplied color.
            scored.append((0, c))
            continue
        score = max(hand_counts[col] for col in produced)
        scored.append((score, c))
    best = max(s for s, _ in scored)
    return [c for s, c in scored if s == best]


def _deck_color_counts(deck: list[Card]) -> dict[str, int]:
    """For each WUBRG color, count how many cards in *deck* have that
    color as part of their color identity. Used by rule L4 — "bottom
    the land whose color supports fewer cards in the deck."

    Land cards themselves don't add to color counts here — the
    intuition is "support for spells", and the spells are what need
    the colors. (A heavily-{G} land base that nevertheless only feeds
    a few green spells should still let us drop a Forest.)
    """
    counts = {c: 0 for c in _WUBRG}
    for card in deck:
        if card.is_land:
            continue
        cost = card.parsed.mana_cost
        if cost is None:
            continue
        for color in _WUBRG:
            if cost.color_pips.get(color, 0) > 0:
                counts[color] += 1
    return counts


def _filter_by_fewest_in_deck(
    candidates: list[Card],
    deck_counts: dict[str, int],
) -> list[Card]:
    """Among *candidates*, prefer lands whose producible colors are
    represented by the fewest cards in the deck. Score a land by the
    minimum deck-count across its producible colors; pick the lowest
    such score (most expendable color)."""
    scored: list[tuple[int, Card]] = []
    for c in candidates:
        produced = _producible_colors(c)
        if not produced:
            # Colorless land — neutral.
            scored.append((0, c))
            continue
        scored.append((min(deck_counts[col] for col in produced), c))
    best = min(s for s, _ in scored)
    return [c for s, c in scored if s == best]


# ---------------------------------------------------------------------------
# Step 2b — which spell to bottom.
# ---------------------------------------------------------------------------


def _pick_spell(
    spells: list[Card],
    lands: list[Card],
    deck: list[Card],
    hand_index: dict[int, int],
    oh_wr: OhWrLookup | None,
) -> Card:
    """Apply the 4-rule ladder for spells.

    Rule S1 narrows to "not fully castable assuming all hand lands in
    play" (i.e., mana count AND colors). If at least one spell is
    uncastable, those become the candidates; otherwise all spells
    remain.

    Rule S2 (only if S1 narrowed to >= 2 uncastable spells): among
    uncastable, prefer to bottom the ones whose color requirements
    are also unmet by hand lands. The kept spells are those that only
    need more mana — any future land helps.

    Rule S3: higher mana value.
    Rule S4: lower OH WR (if lookup available).
    Tiebreak: hand index.
    """
    del deck  # not currently used in spell rules; reserved for future
    state = _state_with_lands_in_play(lands)
    candidates = list(spells)

    # Rule S1.
    uncastable = [s for s in candidates if not _is_castable_in(s, state)]
    if uncastable:
        candidates = uncastable

    # Rule S2 — only meaningful when comparing among uncastable.
    if uncastable and len(candidates) > 1:
        colors_unmet = [s for s in candidates if not _colors_met_in(s, state)]
        if colors_unmet:
            candidates = colors_unmet

    # Rule S3 — higher CMC bottomed first.
    if len(candidates) > 1:
        max_cmc = max(_cmc(s) for s in candidates)
        candidates = [s for s in candidates if _cmc(s) == max_cmc]

    # Rule S4 — lower OH WR. If `oh_wr` is None, skip. If only some
    # cards have a value, treat missing as worst (== bottom first).
    if oh_wr is not None and len(candidates) > 1:
        candidates = _filter_by_lowest_oh_wr(candidates, oh_wr)

    candidates.sort(key=lambda c: hand_index[id(c)])
    return candidates[0]


def _cmc(card: Card) -> int:
    cost = card.parsed.mana_cost
    return cost.cmc if cost is not None else 0


def _filter_by_lowest_oh_wr(
    candidates: list[Card],
    oh_wr: OhWrLookup,
) -> list[Card]:
    """Narrow to the candidate(s) with the lowest shrunk OH WR.

    Cards whose lookup returns ``None`` are considered unrankable and
    are NOT bottomed via this rule — they remain in the candidate
    pool, but the narrowing only retains the worst-WR card(s) among
    those *with* a value. If every candidate has ``None``, the rule
    doesn't fire and all candidates pass through (the hand-index
    tiebreak then decides).

    Rationale: with the shrunk OH WR (which the caller is expected to
    supply), ``None`` should only arise from a card lookup miss
    (e.g., arena_id not yet in MTGJSON). Treating a miss as "worst"
    would bottom the card on no evidence; treating it as "best" would
    protect it on no evidence. Excluding it from this rule is the
    least-opinionated choice.
    """
    scored: list[tuple[float, Card]] = []
    unranked: list[Card] = []
    for c in candidates:
        wr = oh_wr(c)
        if wr is None:
            unranked.append(c)
        else:
            scored.append((wr, c))
    if not scored:
        return candidates
    worst = min(s for s, _ in scored)
    survivors = [c for s, c in scored if s == worst]
    # Unranked cards pass through alongside the worst-WR survivors —
    # they're indistinguishable from "tied at worst" given missing data.
    return survivors + unranked


# ---------------------------------------------------------------------------
# Castability helpers.
# ---------------------------------------------------------------------------


def _state_with_lands_in_play(lands: list[Card]) -> GameState:
    """Build a minimal :class:`GameState` with *lands* on the battlefield,
    untapped, no summoning sickness, no other state. Sufficient for
    ``is_castable`` to enumerate mana abilities and try to pay costs
    via the existing CSP solver.
    """
    return GameState(
        library=[],
        hand=[],
        battlefield_lands=list(lands),
        battlefield_mana_perms=[],
        battlefield_other=[],
        graveyard=[],
        tapped=set(),
        summoning_sick=set(),
        turn=1,
        on_the_play=True,
        land_drop_used=True,
        rng=random.Random(0),  # never invoked by is_castable
    )


def _is_castable_in(spell: Card, state: GameState) -> bool:
    """True iff at least one hand-mode of *spell* is fully payable with
    the mana available in *state* (mana count AND colors)."""
    return is_castable(spell, state).castable


def _colors_met_in(spell: Card, state: GameState) -> bool:
    """True iff at least one hand-mode of *spell* has its *colored* pip
    requirements satisfiable from *state* — i.e., the spell could be
    cast if we had unlimited generic mana but the same colored sources.

    Implemented by stripping generic / X / snow / colorless pips and
    re-running the CSP. ``hybrid`` and ``two_or_color`` pips are
    retained — they're genuine color choices.
    """
    from .mana import available_mana_abilities

    abilities = available_mana_abilities(state)
    for mode in spell.parsed.modes:
        if mode.kind not in {"cast", "cycle", "land_cycle", "channel"}:
            continue
        colored_cost = _strip_generic(mode.cost.mana)
        # ``can_pay_cost`` returns ``[]`` (truthy as "no payment needed")
        # for an empty cost, so an all-generic spell vacuously "meets
        # its colors". Compare to None explicitly.
        if can_pay_cost(colored_cost, abilities, state) is not None:
            return True
    return False


def _strip_generic(cost: ManaCost) -> ManaCost:
    """Return a copy of *cost* with only colored pips retained.

    Drops: ``generic`` (count), ``x``, ``snow``, ``colorless`` (the
    dedicated ``{C}``). Keeps: ``color``, ``hybrid``, ``two_or_color``,
    ``phyrexian``. The resulting ``cmc`` is recomputed from what
    survives.
    """
    kept: list[Pip] = []
    cmc = 0
    color_pips: dict[str, int] = {}
    for pip in cost.pips:
        if pip.kind in {"generic", "x", "snow", "colorless"}:
            continue
        kept.append(pip)
        if pip.kind == "color":
            assert pip.color is not None
            color_pips[pip.color] = color_pips.get(pip.color, 0) + 1
            cmc += 1
        elif pip.kind == "two_or_color":
            # We're stripping generic, so the cheap path here is "1 of
            # the colored side"; cmc contribution becomes 1.
            cmc += 1
        else:
            cmc += 1
    return ManaCost(
        raw=cost.raw,
        pips=kept,
        cmc=cmc,
        color_pips=color_pips,
        has_x=False,
        has_hybrid=any(p.kind in {"hybrid", "two_or_color"} for p in kept),
        has_phyrexian=any(p.kind == "phyrexian" for p in kept),
        has_colorless=False,
        has_snow=False,
    )


# ---------------------------------------------------------------------------
# Audit / debug helpers (not used by the bottoming function itself).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BottomingTrace:
    """Optional debug trace returned by :func:`explain_bottom`. Records
    which rule fired and what the candidate set looked like at each
    step. Not used in production — feeds the validation script that
    compares heuristic to brute-force."""

    decision: str  # "land" or "spell"
    rule_fired: str  # e.g. "L2: not needed for color requirements"
    candidates_considered: tuple[str, ...]
    chosen: str
    n_lands: int
    n_spells: int


def explain_bottom(
    hand: list[Card],
    deck: list[Card],
    *,
    oh_wr: OhWrLookup | None = None,
) -> tuple[Card, BottomingTrace]:
    """Like :func:`bottom_card` but also returns a trace describing which
    rule decided the bottom. Intended for the brute-force validation
    script and for ad-hoc debugging — not on the critical path.
    """
    # The heuristic's actual decisions are made inside the private
    # helpers; reconstructing a precise rule attribution would require
    # plumbing through each filter. For v1, we re-derive the
    # candidate-set evolution at this top level.
    lands = [c for c in hand if c.is_land]
    spells = [c for c in hand if not c.is_land]
    chosen = bottom_card(hand, deck, oh_wr=oh_wr)
    decision = "land" if chosen.is_land else "spell"
    # Pretty names for inspection.
    names = tuple(c.parsed.name for c in (lands if decision == "land" else spells))
    return chosen, BottomingTrace(
        decision=decision,
        rule_fired="(see bottoming.py for ladder)",
        candidates_considered=names,
        chosen=chosen.parsed.name,
        n_lands=len(lands),
        n_spells=len(spells),
    )
