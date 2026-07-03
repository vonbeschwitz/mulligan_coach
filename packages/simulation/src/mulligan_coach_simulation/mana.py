"""Mana pool and the cost-payment CSP solver.

The simulator's hottest function: given a ``ManaCost`` and the list of
available ``ManaAbility`` instances on the battlefield, decide whether
the cost is payable, and if so return one valid payment plan.

The plan is a list of ``(AbilityRef, chosen_OR_option)`` pairs in
*activation order* — that order matters when filter lands (which
consume mana from the pool to produce mana) are involved.

Design choices fixed in the plan:

* ``X`` costs are treated as ``X = 1`` (most X-spells are useless at 0).
* ``snow`` and ``phyrexian`` are treated as plain generic / colored —
  Limited cards almost never put us in a position where the difference
  matters.
* Filter outputs are encoded as the literal color ``"any"`` and treated
  as a wildcard that satisfies any pip.
* The dedicated colorless symbol ``{C}`` is not yet supported; if a
  cost contains one, :func:`can_pay_cost` raises ``NotImplementedError``
  rather than silently mis-counting. Add support when a real card
  forces the issue.

Performance: the CSP is small (<= ~7 sources, <= ~6 pips in Limited).
A plain DFS with a good ordering is fine; the first valid payment is
returned without enumerating all of them. The same ``(cost,
available-mana-in-kind)`` shape recurs many times per game AND across
the games of one Monte Carlo run (same deck, different shuffles), so
:func:`can_pay_cost` is memoised on ``(id(cost), tuple of ability
identities in DFS order)`` with the payment stored as *positions* into
the sorted ability list — see the comment on ``_CSP_CACHE`` for why
that key is exact and how ``id()`` reuse is ruled out. The cache is
valid indefinitely; :func:`reset_solver_caches` clears it between
unrelated decks purely to bound memory. The per-cost ``_expand_cost``
lookup is memoised for the whole session (``ManaCost`` objects outlive
any single game).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from mulligan_coach_cards import ManaAbility, ManaCost, Pip
from mulligan_coach_cards.models import ManaOption

from .runtime import Card, GameState

# Buckets the mana pool tracks. Keep WUBRG order to make printouts and
# tests stable. ``"any"`` is a wildcard added by filter outputs;
# ``"C"`` is the dedicated colorless from sources that produce {C}.
_BUCKETS: Final[tuple[str, ...]] = ("W", "U", "B", "R", "G", "C", "any")
_BUCKET_IDX: Final[dict[str, int]] = {c: i for i, c in enumerate(_BUCKETS)}
_ANY_IDX: Final[int] = _BUCKET_IDX["any"]
_N_BUCKETS: Final[int] = len(_BUCKETS)
# Drain order for ``_take_generic``: pull from ``any`` first (wasted on
# generic anyway), then ``C``, then WUBRG. Precomputed as indices so
# the hot loop avoids a per-iteration dict lookup.
_GENERIC_DRAIN: Final[tuple[int, ...]] = tuple(
    _BUCKET_IDX[c] for c in ("any", "C", "W", "U", "B", "R", "G")
)


@dataclass(slots=True)
class ManaPool:
    """Live mana available during cost payment.

    Counts are non-negative integers stored in a fixed 7-element list
    keyed by ``_BUCKET_IDX``. The list representation is meaningfully
    faster than a ``dict[str, int]``: ``list.copy()`` on a 7-element
    list is a tight C-level memcpy, and the inner ``pool.counts[idx]``
    is an O(1) index-by-int instead of a hashed lookup.

    Adding an output with color ``"any"`` increments the wildcard
    bucket. Generic pips drain from every bucket in ``_GENERIC_DRAIN``
    order.
    """

    counts: list[int] = field(default_factory=lambda: [0] * _N_BUCKETS)

    @classmethod
    def empty(cls) -> ManaPool:
        return cls(counts=[0] * _N_BUCKETS)

    def add(self, color: str, n: int = 1) -> None:
        idx = _BUCKET_IDX.get(color)
        if idx is None:
            raise ValueError(f"unknown mana color {color!r}")
        self.counts[idx] += n

    def total(self) -> int:
        return sum(self.counts)

    def copy(self) -> ManaPool:
        return ManaPool(counts=self.counts.copy())

    def __getitem__(self, color: str) -> int:
        """Read a bucket by color name. Used outside the hot path
        (tests, debugging); inside the solver we use ``counts[idx]``
        with a precomputed index."""
        return self.counts[_BUCKET_IDX[color]]

    def __setitem__(self, color: str, value: int) -> None:
        self.counts[_BUCKET_IDX[color]] = value


# An ``AbilityRef`` ties one ``ManaAbility`` back to the permanent that
# owns it — needed because the engine taps the source after a payment
# is selected, and tapping is per-permanent, not per-ability.
@dataclass(slots=True)
class AbilityRef:
    """A pointer to one ManaAbility on a specific permanent in play.

    ``cmc`` caches ``ability.cost.mana.cmc`` (the activation cost of the
    ability itself — 0 for basics/dorks, >0 for filter lands). The two
    hot sorts in this module key on it constantly, and reading it once
    at construction avoids chasing three pydantic attributes per
    comparison.
    """

    source: Card
    ability: ManaAbility
    cmc: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.cmc = self.ability.cost.mana.cmc


def _ref_cmc(ref: AbilityRef) -> int:
    """Sort key for ability lists: activation cost ascending, so
    producers (cost 0) come before filter lands. Stable sort keeps
    battlefield order within equal costs."""
    return ref.cmc


# A payment plan: ``(ability_ref, option_chosen)`` pairs in activation
# order. The simulator uses the order to handle filter lands (whose
# input cost must be paid from earlier-activated sources).
ManaPayment = list[tuple[AbilityRef, list[ManaOption]]]


# ---------------------------------------------------------------------------
# Source enumeration.
# ---------------------------------------------------------------------------


def available_mana_abilities(state: GameState) -> list[AbilityRef]:
    """Enumerate every mana ability currently usable.

    Inclusion rules:

    * The source permanent is in play (lands or mana-permanents bucket).
    * The source is not tapped — every encoded mana ability requires
      ``{T}`` in v1, so we skip tapped sources entirely.
    * For *creatures* (mana dorks), the source is not summoning-sick.
      Mana rocks (artifacts) ignore summoning sickness per the rules.
    * The ability's condition predicate holds (stub — see comment above).

    The order returned is "lowest mana cost on the ability first"
    (basic lands and dorks before filter lands). The CSP solver
    relies on this — it tries to satisfy cost greedily as it walks
    the list, and filters can only be paid once earlier producers have
    filled the pool.
    """
    # Imported here to avoid a circular import: ``effects`` references
    # ``Card`` / ``GameState`` from ``runtime`` (lazily, via TYPE_CHECKING),
    # which in turn imports this module via :func:`is_castable` upstream.
    from .effects import predicate_holds

    refs: list[AbilityRef] = []
    for card in state.battlefield_lands:
        if card.instance_id in state.tapped:
            continue
        for ab in card.parsed.mana_abilities:
            if not predicate_holds(ab.condition, state):
                continue
            refs.append(AbilityRef(source=card, ability=ab))
    for card in state.battlefield_mana_perms:
        if card.instance_id in state.tapped:
            continue
        if card.is_creature and card.instance_id in state.summoning_sick:
            continue
        for ab in card.parsed.mana_abilities:
            if not predicate_holds(ab.condition, state):
                continue
            refs.append(AbilityRef(source=card, ability=ab))
    refs.sort(key=_ref_cmc)
    return refs


# ---------------------------------------------------------------------------
# Cost payment — the CSP solver.
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class _Requirement:
    """One concrete payment requirement, derived from a Pip.

    * ``kind`` is one of ``color`` / ``hybrid`` / ``two_or_color`` /
      ``generic`` / ``colorless``. ``snow`` and ``phyrexian`` and ``x``
      pips are normalised into one of these by :func:`_expand_cost`.
    * ``color`` / ``colors`` / ``amount`` carry the kind-specific data.
    """

    kind: str
    color: str | None = None
    colors: tuple[str, ...] | None = None
    amount: int = 1


# Memoise the pip-expansion of every ``ManaCost`` we see. Costs live on
# immutable ``ParsedCard`` objects loaded once at startup, so the same
# ``ManaCost`` instance recurs across every castability check and every
# search branch — caching pays off enormously. The dict is keyed by
# ``id(cost)`` and the stored cost is checked with ``is`` so that if
# Python reuses an id after garbage collection we don't return a stale
# expansion. The cache is never explicitly cleared; entries naturally
# stay valid as long as the originating ``ManaCost`` object lives.
_EXPAND_COST_CACHE: dict[int, tuple[ManaCost, list[_Requirement]]] = {}

# Memoisation of :func:`can_pay_cost`. Same ``(cost, available mana in
# kind)`` repeats many times within a game (the castability snapshot
# walks every card x every land x every mode, the L1 lookahead repeats
# the same next-turn check for every candidate land, …) AND across the
# games of one Monte Carlo run (same deck, different shuffles).
#
# Key: ``(id(cost), tuple(id(ref.ability) for ref in sorted_abilities))``
# where ``sorted_abilities`` is the cmc-sorted list the DFS walks. Two
# calls with the same key present the DFS with position-for-position
# identical inputs — ``_search`` reads abilities only through
# ``ability.cost.mana`` and ``ability.produces``, never through the
# source ``Card`` — so the winning activation pattern and option
# choices are the same. The key is deliberately the exact post-sort
# *sequence* (not a canonicalised multiset): a canonical key would let
# two states with different DFS walk orders share an entry, and a
# position-pattern from one order can select different abilities in
# the other.
#
# Value: ``(cost, abilities, payment_positions | None)``. The payment
# is stored as ``(position_in_sorted_abilities, chosen_option)`` pairs
# and rebound to the *current* call's AbilityRefs on a hit, so the
# engine always taps live Card instances — this is what makes the
# cache safe across games (instance_ids and Card objects reset every
# game; positions don't). The ``cost`` / ``abilities`` refs in the
# value are never read: they pin the keyed objects alive so their
# ``id()``s can't be recycled while the entry exists (same guard idea
# as ``_EXPAND_COST_CACHE``). Correctness therefore does NOT depend on
# ever clearing this cache; :func:`reset_solver_caches` exists purely
# to bound memory between unrelated decks.
_CspValue = tuple[
    ManaCost,
    tuple[ManaAbility, ...],
    tuple[tuple[int, list[ManaOption]], ...] | None,
]
_CSP_CACHE: dict[tuple[int, tuple[int, ...]], _CspValue] = {}


def reset_solver_caches() -> None:
    """Drop the CSP payment cache. Memory hygiene, not correctness —
    the cache's identity-pinned keys stay valid indefinitely (see the
    ``_CSP_CACHE`` comment), but its useful lifetime is one deck, so
    the Monte Carlo entry points (``monte_carlo._iter_games``,
    ``mulligan._iter_mulligan_games``) clear it before each run batch.

    The expand-cost cache is deliberately NOT cleared: ``ManaCost``
    objects outlive any individual deck and re-expanding their pips
    wastes per-session work.
    """
    _CSP_CACHE.clear()


def _expand_cost(cost: ManaCost) -> list[_Requirement]:
    """Turn a parsed ``ManaCost`` into a flat list of payment requirements,
    applying our v1 simplifications:

    * ``{X}`` → ``+1 generic`` (we model X = 1).
    * ``{S}`` (snow) → 1 generic.
    * ``{W/P}`` (phyrexian) → treated as the colored side only.
    * ``{C}`` raises ``NotImplementedError`` — colorless requirements
      need a real card to motivate the work.

    The returned list is pre-sorted by ``_REQ_PRIORITY`` (most- to
    least-restrictive), which is the order the greedy allocator
    consumes requirements in. Sorting once here instead of on every
    ``_try_satisfy`` call was one of the simulator's hottest lines;
    ``sorted`` is stable, so the pre-sorted list is exactly the
    sequence the per-call sort used to produce.
    """
    cached = _EXPAND_COST_CACHE.get(id(cost))
    if cached is not None and cached[0] is cost:
        return cached[1]
    reqs: list[_Requirement] = []
    for pip in cost.pips:
        reqs.extend(_expand_pip(pip))
    reqs.sort(key=lambda r: _REQ_PRIORITY[r.kind])
    _EXPAND_COST_CACHE[id(cost)] = (cost, reqs)
    return reqs


def _expand_pip(pip: Pip) -> list[_Requirement]:
    if pip.kind == "color":
        assert pip.color is not None
        return [_Requirement(kind="color", color=pip.color)]
    if pip.kind == "generic":
        assert pip.amount is not None
        return [_Requirement(kind="generic", amount=pip.amount)]
    if pip.kind == "x":
        # X = 1 per the locked-in plan decision.
        return [_Requirement(kind="generic", amount=1)]
    if pip.kind == "snow":
        return [_Requirement(kind="generic", amount=1)]
    if pip.kind == "hybrid":
        assert pip.colors is not None
        return [_Requirement(kind="hybrid", colors=tuple(pip.colors))]
    if pip.kind == "two_or_color":
        assert pip.color is not None and pip.amount is not None
        return [_Requirement(kind="two_or_color", color=pip.color, amount=pip.amount)]
    if pip.kind == "phyrexian":
        # Treat as colored — life payment isn't worth modeling in v1.
        assert pip.colors is not None
        return [_Requirement(kind="hybrid", colors=tuple(pip.colors))]
    if pip.kind == "colorless":
        raise NotImplementedError(
            "Colorless mana requirements ({C}) aren't supported in the v1 "
            "simulator. Add support when a Limited card actually requires "
            "one — most cards in current sets do not."
        )
    raise AssertionError(f"unknown pip kind {pip.kind!r}")


# Priority for greedy allocation: lower number = handled first. Single
# colors are the most-restrictive so they claim their bucket first;
# generic is the least-restrictive and is left for last so it can pull
# from whichever bucket survived.
_REQ_PRIORITY: Final[dict[str, int]] = {
    "color": 0,
    "colorless": 1,
    "hybrid": 2,
    "two_or_color": 3,
    "generic": 4,
}


def _try_satisfy(reqs: list[_Requirement], counts: list[int]) -> bool:
    """Greedy allocation: walk requirements from most to least restrictive
    (``reqs`` comes from ``_expand_cost`` already priority-sorted) and
    try to pay each from a scratch copy of *counts*. Returns True iff
    every requirement is paid; *counts* itself is never mutated.

    The greedy is correct for the cost shapes we model: colored pips
    claim their colored bucket first, hybrids fall back to ``any``,
    generic drains whichever bucket remains. Pathological hybrid
    combinations exist in theory but not in current Limited card data;
    if one shows up, the test suite will reveal the gap before it
    silently miscalculates.

    The hot path works on raw 7-int lists (see ``_BUCKET_IDX``) rather
    than :class:`ManaPool` objects — this runs once per DFS node and
    the wrapper allocation was measurable. ``ManaPool`` remains the
    public-facing type.
    """
    scratch = counts.copy()
    # Plain loop, not all(...): this runs once per DFS node and the
    # generator-expression allocation is measurable at that frequency.
    for req in reqs:  # noqa: SIM110
        if not _pay_requirement(scratch, req):
            return False
    return True


def _pay_requirement(counts: list[int], req: _Requirement) -> bool:
    if req.kind == "color":
        return _take_color(counts, req.color)
    if req.kind == "hybrid":
        assert req.colors is not None
        return _take_hybrid(counts, req.colors)
    if req.kind == "two_or_color":
        # Cheapest path: 1 mana of the colored side. Falls back to N generic.
        assert req.color is not None
        if _take_color(counts, req.color):
            return True
        return _take_generic(counts, req.amount)
    if req.kind == "generic":
        return _take_generic(counts, req.amount)
    if req.kind == "colorless":
        # Reserved for the future {C} support; reachable only if
        # _expand_pip is extended.
        return _take_colorless(counts)
    raise AssertionError(f"unknown requirement kind {req.kind!r}")


def _take_color(counts: list[int], color: str | None) -> bool:
    assert color is not None
    idx = _BUCKET_IDX[color]
    if counts[idx] > 0:
        counts[idx] -= 1
        return True
    if counts[_ANY_IDX] > 0:
        counts[_ANY_IDX] -= 1
        return True
    return False


def _take_hybrid(counts: list[int], colors: tuple[str, ...]) -> bool:
    for c in colors:
        idx = _BUCKET_IDX[c]
        if counts[idx] > 0:
            counts[idx] -= 1
            return True
    if counts[_ANY_IDX] > 0:
        counts[_ANY_IDX] -= 1
        return True
    return False


def _take_colorless(counts: list[int]) -> bool:
    c_idx = _BUCKET_IDX["C"]
    if counts[c_idx] > 0:
        counts[c_idx] -= 1
        return True
    if counts[_ANY_IDX] > 0:
        counts[_ANY_IDX] -= 1
        return True
    return False


def _take_generic(counts: list[int], amount: int) -> bool:
    """Drain *amount* mana from the pool, regardless of color.

    We pull from ``any`` first (a wildcard wasted on generic anyway),
    then ``C``, then colors WUBRG. The order doesn't change correctness
    but makes the search bias toward saving named-color buckets for the
    colored pips (which are processed *first* by ``_try_satisfy``, so
    by the time we get here the colored claims have already been made
    — but the convention also helps when greedy backtracking explores
    nearby states).
    """
    remaining = amount
    for idx in _GENERIC_DRAIN:
        if remaining == 0:
            break
        avail = counts[idx]
        if avail == 0:
            continue
        take = avail if avail < remaining else remaining
        counts[idx] -= take
        remaining -= take
    return remaining == 0


# ---------------------------------------------------------------------------
# The DFS.
# ---------------------------------------------------------------------------


def can_pay_cost(
    cost: ManaCost,
    abilities: list[AbilityRef],
    state: GameState,
) -> ManaPayment | None:
    """Return one valid payment for *cost* using *abilities*, or ``None``.

    The return value is suitable for the engine to actually tap the
    sources during main-phase casting: each entry is the ability that
    was activated and the OR-option that was chosen.

    *state* is currently unused — it's threaded through so step 4 can
    wire predicate evaluation (e.g. for the future ``ProduceManaEffect``
    conditions) without changing the signature again.

    The first valid payment found is returned; the search does not
    enumerate alternatives.
    """
    del state  # see docstring; reserved for step 4
    reqs = _expand_cost(cost)
    if not reqs:
        # Free spells / abilities — payable trivially.
        return []
    # Sort abilities so producers (no self-cost) come before filters.
    # The DFS branches "skip vs. activate", and a filter activated too
    # early would fail its own cost check; the producers-first order
    # ensures the pool is fed before filters consider activation.
    # Inputs from ``available_mana_abilities`` arrive pre-sorted (and
    # order-preserving filters keep them that way), so first verify
    # sortedness with a plain int-compare walk — much cheaper at this
    # call frequency than re-running timsort with a key function —
    # and only fall back to a real sort for unsorted direct callers.
    sorted_abilities = abilities
    for i in range(1, len(abilities)):
        if abilities[i].cmc < abilities[i - 1].cmc:
            sorted_abilities = sorted(abilities, key=_ref_cmc)
            break
    # Memoisation on the identity sequence — see the _CSP_CACHE comment
    # for the full correctness argument.
    key = (id(cost), tuple([id(ref.ability) for ref in sorted_abilities]))
    entry = _CSP_CACHE.get(key)
    if entry is not None:
        positions = entry[2]
        if positions is None:
            return None
        return [(sorted_abilities[pos], option) for pos, option in positions]
    result = _search(reqs, sorted_abilities, 0, [0] * _N_BUCKETS, [])
    _CSP_CACHE[key] = (
        cost,
        tuple(ref.ability for ref in sorted_abilities),
        None if result is None else tuple(result),
    )
    if result is None:
        return None
    return [(sorted_abilities[pos], option) for pos, option in result]


def _search(
    reqs: list[_Requirement],
    abilities: list[AbilityRef],
    idx: int,
    counts: list[int],
    payment: list[tuple[int, list[ManaOption]]],
) -> list[tuple[int, list[ManaOption]]] | None:
    """DFS over skip/activate decisions. The payment is built as
    ``(position_in_abilities, chosen_option)`` pairs so the caller can
    cache it independently of which ``Card`` instances own the
    abilities this time around; :func:`can_pay_cost` rebinds positions
    to live :class:`AbilityRef`\\ s."""
    # Goal check first: with the current pool, can we pay everything?
    if _try_satisfy(reqs, counts):
        return payment
    if idx >= len(abilities):
        return None
    head = abilities[idx]
    # Branch 1: skip this ability.
    result = _search(reqs, abilities, idx + 1, counts, payment)
    if result is not None:
        return result
    # Branch 2: activate. Only legal if its own cost is payable now.
    head_cost = head.ability.cost.mana
    if head.cmc > 0 and not _try_satisfy(_expand_cost(head_cost), counts):
        return None
    for option in head.ability.produces:
        new_counts = counts.copy()
        if head.cmc > 0:
            # We've already proven the cost is payable; pay it now.
            # _try_satisfy was a feasibility check on a scratch copy,
            # so we apply the deduction to ``new_counts`` in-place here.
            assert _try_satisfy_inplace(_expand_cost(head_cost), new_counts)
        for color in option:
            new_counts[_BUCKET_IDX[color]] += 1
        result = _search(reqs, abilities, idx + 1, new_counts, [*payment, (idx, option)])
        if result is not None:
            return result
    return None


def _try_satisfy_inplace(reqs: list[_Requirement], counts: list[int]) -> bool:
    """Like :func:`_try_satisfy` but mutates *counts* directly. Used to
    actually deduct an activated ability's own mana cost from the live
    pool after we've branched into the activate path. ``reqs`` comes
    from ``_expand_cost`` already priority-sorted."""
    # Plain loop, not all(...) — same hot-path reasoning as _try_satisfy.
    for req in reqs:  # noqa: SIM110
        if not _pay_requirement(counts, req):
            return False
    return True
