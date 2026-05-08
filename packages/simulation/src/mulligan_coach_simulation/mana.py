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
returned without enumerating all of them.
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


@dataclass(slots=True)
class ManaPool:
    """Live mana available during cost payment.

    Counts are non-negative integers per bucket. Adding an output with
    color ``"any"`` increments the ``any`` bucket (a wildcard usable
    for any later pip). Generic pips drain from any bucket.
    """

    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(_BUCKETS, 0))

    @classmethod
    def empty(cls) -> ManaPool:
        return cls(counts=dict.fromkeys(_BUCKETS, 0))

    def add(self, color: str, n: int = 1) -> None:
        if color not in self.counts:
            raise ValueError(f"unknown mana color {color!r}")
        self.counts[color] += n

    def total(self) -> int:
        return sum(self.counts.values())

    def copy(self) -> ManaPool:
        return ManaPool(counts=self.counts.copy())


# An ``AbilityRef`` ties one ``ManaAbility`` back to the permanent that
# owns it — needed because the engine taps the source after a payment
# is selected, and tapping is per-permanent, not per-ability.
@dataclass(slots=True)
class AbilityRef:
    """A pointer to one ManaAbility on a specific permanent in play."""

    source: Card
    ability: ManaAbility


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
    refs.sort(key=lambda r: r.ability.cost.mana.cmc)
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


def _expand_cost(cost: ManaCost) -> list[_Requirement]:
    """Turn a parsed ``ManaCost`` into a flat list of payment requirements,
    applying our v1 simplifications:

    * ``{X}`` → ``+1 generic`` (we model X = 1).
    * ``{S}`` (snow) → 1 generic.
    * ``{W/P}`` (phyrexian) → treated as the colored side only.
    * ``{C}`` raises ``NotImplementedError`` — colorless requirements
      need a real card to motivate the work.
    """
    reqs: list[_Requirement] = []
    for pip in cost.pips:
        reqs.extend(_expand_pip(pip))
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


def _try_satisfy(reqs: list[_Requirement], pool: ManaPool) -> bool:
    """Greedy allocation: walk requirements from most to least restrictive
    and try to pay each from the pool. Returns True iff every requirement
    is paid.

    The greedy is correct for the cost shapes we model: colored pips
    claim their colored bucket first, hybrids fall back to ``any``,
    generic drains whichever bucket remains. Pathological hybrid
    combinations exist in theory but not in current Limited card data;
    if one shows up, the test suite will reveal the gap before it
    silently miscalculates.
    """
    pool = pool.copy()
    for req in sorted(reqs, key=lambda r: _REQ_PRIORITY[r.kind]):
        if not _pay_requirement(pool, req):
            return False
    return True


def _pay_requirement(pool: ManaPool, req: _Requirement) -> bool:
    if req.kind == "color":
        return _take_color(pool, req.color)
    if req.kind == "hybrid":
        assert req.colors is not None
        return _take_hybrid(pool, req.colors)
    if req.kind == "two_or_color":
        # Cheapest path: 1 mana of the colored side. Falls back to N generic.
        assert req.color is not None
        if _take_color(pool, req.color):
            return True
        return _take_generic(pool, req.amount)
    if req.kind == "generic":
        return _take_generic(pool, req.amount)
    if req.kind == "colorless":
        # Reserved for the future {C} support; reachable only if
        # _expand_pip is extended.
        return _take_colorless(pool)
    raise AssertionError(f"unknown requirement kind {req.kind!r}")


def _take_color(pool: ManaPool, color: str | None) -> bool:
    assert color is not None
    if pool.counts.get(color, 0) > 0:
        pool.counts[color] -= 1
        return True
    if pool.counts["any"] > 0:
        pool.counts["any"] -= 1
        return True
    return False


def _take_hybrid(pool: ManaPool, colors: tuple[str, ...]) -> bool:
    for c in colors:
        if pool.counts.get(c, 0) > 0:
            pool.counts[c] -= 1
            return True
    if pool.counts["any"] > 0:
        pool.counts["any"] -= 1
        return True
    return False


def _take_colorless(pool: ManaPool) -> bool:
    if pool.counts.get("C", 0) > 0:
        pool.counts["C"] -= 1
        return True
    if pool.counts["any"] > 0:
        pool.counts["any"] -= 1
        return True
    return False


def _take_generic(pool: ManaPool, amount: int) -> bool:
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
    for c in ("any", "C", "W", "U", "B", "R", "G"):
        if remaining == 0:
            break
        take = min(pool.counts.get(c, 0), remaining)
        pool.counts[c] -= take
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
    sorted_abilities = sorted(abilities, key=lambda r: r.ability.cost.mana.cmc)
    return _search(reqs, sorted_abilities, 0, ManaPool.empty(), [])


def _search(
    reqs: list[_Requirement],
    abilities: list[AbilityRef],
    idx: int,
    pool: ManaPool,
    payment: ManaPayment,
) -> ManaPayment | None:
    # Goal check first: with the current pool, can we pay everything?
    if _try_satisfy(reqs, pool):
        return payment
    if idx >= len(abilities):
        return None
    head = abilities[idx]
    # Branch 1: skip this ability.
    result = _search(reqs, abilities, idx + 1, pool, payment)
    if result is not None:
        return result
    # Branch 2: activate. Only legal if its own cost is payable now.
    head_cost = head.ability.cost.mana
    if head_cost.cmc > 0 and not _try_satisfy(_expand_cost(head_cost), pool):
        return None
    for option in head.ability.produces:
        new_pool = pool.copy()
        if head_cost.cmc > 0:
            # We've already proven the cost is payable; pay it now.
            # _try_satisfy was a feasibility check that mutated a copy,
            # so we apply the deduction to ``new_pool`` in-place here.
            assert _try_satisfy_inplace(_expand_cost(head_cost), new_pool)
        for color in option:
            new_pool.add(color)
        result = _search(reqs, abilities, idx + 1, new_pool, [*payment, (head, option)])
        if result is not None:
            return result
    return None


def _try_satisfy_inplace(reqs: list[_Requirement], pool: ManaPool) -> bool:
    """Like :func:`_try_satisfy` but mutates *pool* directly. Used to
    actually deduct an activated ability's own mana cost from the live
    pool after we've branched into the activate path."""
    for req in sorted(reqs, key=lambda r: _REQ_PRIORITY[r.kind]):
        if not _pay_requirement(pool, req):
            return False
    return True
