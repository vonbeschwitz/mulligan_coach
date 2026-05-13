"""Land-drop policy.

The simulator follows a fixed five-rule decision procedure for which
land to play each turn. Rules are applied in priority order; each is
a *filter* over the candidate set, and we keep narrowing until only
one candidate remains (or until all filters have been tried, in which
case we fall back to hand index).

The exact rules are spelled out in ``packages/simulation/CLAUDE.md``;
in short:

* **L1**: play whichever land maximises the number of spells in hand
  that become castable next turn (lands-only lookahead — we don't
  model the spells we'd cast this turn). On tied counts, prefer the
  set whose best castable spell ranks higher (creature > removal >
  other; within a tier, higher CMC > lower).
* **L2**: prefer lands that ETB tapped or are sac-for-tapped
  (Evolving Wilds-style). Counterintuitive but correct: tapped lands
  give up mana now and earn it back later, which raises later-turn
  castability without hurting this turn.
* **L3**: prefer lands that produce a color we don't yet have access
  to on the battlefield.
* **L4**: prefer lands where another copy of the same name is in hand.
* **L5**: prefer lands that produce the deck's main color (the color
  with the most colored pips across the deck).
* Final tiebreak: hand index, lowest first.

The chosen land plus the rule that decided it is returned, so the
trace can explain the decision.
"""

from __future__ import annotations

from collections.abc import Iterable

from mulligan_coach_cards import FetchLandEffect

from .castability import is_castable
from .effects import enters_tapped
from .runtime import Card, GameState

LandRule = str  # one of "L1" / "L2" / "L3" / "L4" / "L5" / "tiebreak" / "no-land"


def choose_land(state: GameState) -> tuple[Card | None, LandRule]:
    """Return ``(chosen, rule_fired)``. ``chosen`` is ``None`` iff hand
    has no land. ``rule_fired`` names the last rule that narrowed the
    candidates to the chosen one — useful for the verbose trace.
    """
    lands_in_hand = [c for c in state.hand if c.is_land]
    if not lands_in_hand:
        return None, "no-land"

    cands: list[Card] = lands_in_hand
    last_narrowing_rule: str | None = None
    for fname, label in (
        (filter_l1, "L1"),
        (filter_l2, "L2"),
        (filter_l3, "L3"),
        (filter_l4, "L4"),
        (filter_l5, "L5"),
    ):
        narrowed = fname(cands, state)
        if 0 < len(narrowed) < len(cands):
            cands = narrowed
            last_narrowing_rule = label
            if len(cands) == 1:
                return cands[0], label
    # Multiple candidates survived all five filters — fall back to hand index.
    cands.sort(key=state.hand.index)
    return cands[0], last_narrowing_rule or "tiebreak"


# ---------------------------------------------------------------------------
# Rule L1 — castability next turn
# ---------------------------------------------------------------------------


def filter_l1(candidates: list[Card], state: GameState) -> list[Card]:
    """Pick the candidate(s) that make the most spells in hand castable
    next turn. Ties on count are broken by best-spell quality."""
    scored: list[tuple[Card, int, tuple[int, int]]] = []
    for cand in candidates:
        spells = _castable_spells_next_turn(cand, state)
        scored.append((cand, len(spells), _best_spell_quality(spells)))

    max_count = max(c for _, c, _ in scored)
    by_count = [(card, q) for card, c, q in scored if c == max_count]
    if len(by_count) == 1:
        return [by_count[0][0]]

    max_q = max(q for _, q in by_count)
    return [card for card, q in by_count if q == max_q]


def _castable_spells_next_turn(cand: Card, state: GameState) -> list[Card]:
    """Hypothetically play *cand*, advance to start of next turn, count
    castable hand cards. Pure: applies a manual undo on the way out.

    Manual undo instead of full ``state.snapshot()/restore()``: this
    function is called once per L1 candidate per land-drop turn (so
    ~3-7 times per land drop in a mana-base-heavy opener), and the
    snapshot's six list-copies + two frozenset constructions were
    among the hottest allocations in the simulator. We only mutate
    four pieces of state — hand / battlefield_lands / tapped /
    summoning_sick (plus the land_drop_used flag) — so a hand-tuned
    save+restore is much cheaper.
    """
    orig_hand_idx = next(i for i, c in enumerate(state.hand) if c.instance_id == cand.instance_id)
    # ``play_land`` may add ``cand.instance_id`` to ``tapped``;
    # ``untap_all`` then clears the entire set in-place. We snapshot
    # the pre-call sets so the inner ``tapped``/``summoning_sick``
    # mutations can be reversed by wholesale rebind at the end.
    orig_tapped = state.tapped.copy()
    orig_summoning_sick = state.summoning_sick.copy()
    state.play_land(cand)
    # Start of next turn: untap, summoning-sickness clears. We do
    # NOT simulate a draw step (per the locked-in plan: "without
    # considering the card drawn") and we do NOT cast spells (the
    # locked-in "lands-only" L1 lookahead).
    state.untap_all()
    state.summoning_sick.clear()
    try:
        out: list[Card] = []
        for card in state.hand:
            if card.is_land:
                continue
            if is_castable(card, state).castable:
                out.append(card)
        return out
    finally:
        # Reverse the four mutations. battlefield_lands.pop() works
        # because play_land appends; restoring hand at its original
        # index keeps the lowest-index tiebreaker stable for L1
        # callers above.
        state.battlefield_lands.pop()
        state.hand.insert(orig_hand_idx, cand)
        state.tapped = orig_tapped
        state.summoning_sick = orig_summoning_sick
        state.land_drop_used = False


def _best_spell_quality(spells: Iterable[Card]) -> tuple[int, int]:
    """``(role_tier, cmc)`` of the best castable spell, or ``(0, 0)`` if
    none. Higher is better.

    Role tier:
      * 3 — creature
      * 2 — creature-targeted destroy/exile/burn (i.e., removal)
      * 1 — anything else
    """
    best = (0, 0)
    for c in spells:
        rf = c.parsed.role_features
        if rf.is_creature:
            tier = 3
        elif rf.removal_destroy_or_exile or (
            rf.removal_burn_damage is not None and rf.removal_burn_damage > 0
        ):
            tier = 2
        else:
            tier = 1
        cmc = c.parsed.mana_cost.cmc if c.parsed.mana_cost is not None else 0
        if (tier, cmc) > best:
            best = (tier, cmc)
    return best


# ---------------------------------------------------------------------------
# Rule L2 — tapped before untapped
# ---------------------------------------------------------------------------


def filter_l2(candidates: list[Card], state: GameState) -> list[Card]:
    """Prefer lands that ETB tapped, including sac-for-tapped lands
    (Evolving Wilds — the eventual basic enters tapped, so the same
    'forfeit mana now to gain it later' logic applies)."""
    tapped_eq = [c for c in candidates if _is_tapped_equivalent(c, state)]
    return tapped_eq if tapped_eq else candidates


def _is_tapped_equivalent(card: Card, state: GameState) -> bool:
    if enters_tapped(card.parsed, state):
        return True
    # Evolving Wilds-shape: an activated mode that sacrifices the land
    # to fetch a basic to ``battlefield_tapped``. The eventual basic
    # enters tapped, so this is "tapped equivalent" for L2.
    for mode in card.parsed.modes:
        if mode.kind != "activated":
            continue
        sac = mode.cost.sacrifice
        if sac is None or sac.target != "self":
            continue
        for fx in mode.effects:
            if isinstance(fx, FetchLandEffect) and fx.destination == "battlefield_tapped":
                return True
    return False


# ---------------------------------------------------------------------------
# Rule L3 — colors not yet available
# ---------------------------------------------------------------------------


def filter_l3(candidates: list[Card], state: GameState) -> list[Card]:
    """Prefer lands that produce a *concrete* color we don't already
    have on the battlefield. Filter outputs of ``"any"`` don't count
    as adding a new color (they require an input we may not have)."""
    available = _battlefield_concrete_colors(state)
    new_color = [c for c in candidates if _land_adds_new_concrete_color(c, available)]
    return new_color if new_color else candidates


def _battlefield_concrete_colors(state: GameState) -> set[str]:
    colors: set[str] = set()
    sources = list(state.battlefield_lands) + list(state.battlefield_mana_perms)
    for source in sources:
        for ab in source.parsed.mana_abilities:
            for option in ab.produces:
                for c in option:
                    if c != "any":
                        colors.add(c)
    return colors


def _land_adds_new_concrete_color(card: Card, available: set[str]) -> bool:
    for ab in card.parsed.mana_abilities:
        for option in ab.produces:
            for c in option:
                if c != "any" and c not in available:
                    return True
    return False


# ---------------------------------------------------------------------------
# Rule L4 — duplicate in hand
# ---------------------------------------------------------------------------


def filter_l4(candidates: list[Card], state: GameState) -> list[Card]:
    """Prefer lands with another copy of the same name in hand."""
    duplicates = [c for c in candidates if _has_duplicate_in_hand(c, state)]
    return duplicates if duplicates else candidates


def _has_duplicate_in_hand(card: Card, state: GameState) -> bool:
    """A "duplicate" exists if hand contains 2+ copies of *card*'s name
    (the candidate plus at least one other)."""
    return sum(1 for c in state.hand if c.parsed.name == card.parsed.name) >= 2


# ---------------------------------------------------------------------------
# Rule L5 — main deck color
# ---------------------------------------------------------------------------


def filter_l5(candidates: list[Card], state: GameState) -> list[Card]:
    """Prefer lands that can produce the deck's main color (the color
    with the most colored pips across cards in hand+library+battlefield)."""
    main = _compute_main_color(state)
    if main is None:
        return candidates
    main_lands = [c for c in candidates if _land_produces_color(c, main)]
    return main_lands if main_lands else candidates


def _compute_main_color(state: GameState) -> str | None:
    counts = dict.fromkeys(("W", "U", "B", "R", "G"), 0)
    everything = (
        list(state.hand)
        + list(state.library)
        + list(state.battlefield_lands)
        + list(state.battlefield_mana_perms)
        + list(state.battlefield_other)
    )
    for card in everything:
        if card.parsed.mana_cost is None:
            continue
        for color, n in card.parsed.mana_cost.color_pips.items():
            if color in counts:
                counts[color] += n
    if not any(counts.values()):
        return None
    return max(counts, key=lambda c: counts[c])


def _land_produces_color(card: Card, color: str) -> bool:
    for ab in card.parsed.mana_abilities:
        for option in ab.produces:
            if color in option or "any" in option:
                return True
    return False
