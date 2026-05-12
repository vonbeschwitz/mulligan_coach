"""MTG Arena BO1 hand smoother.

Arena's best-of-one queue does not draw a flat hypergeometric opening
hand. Instead, it draws ``num_candidates`` independent 7-card hands
from fresh shuffles of the deck and selects one with a softmax weight
that favours hands whose land count matches the deck's land ratio.

The formula was reverse-engineered against 700k+ FIN Premier-Draft
games at ``\\\\wsl$\\Ubuntu\\home\\basti\\hand_smoother``:

    weight = exp(land_diff ** 2 / temperature)
    land_diff = | lands_in_hand / hand_size - lands_in_deck / deck_size |
    temperature = -0.015            (empirically calibrated)
    num_candidates = 3

A hand whose land ratio equals the deck's gets weight 1; any deviation
exponentially decays. The selected hand's remaining cards become the
library — i.e. only one of the three shuffles is "real".

This module is intentionally land-only: a 6-land hand looks the same
as a 5-land + 1-vehicle hand to the smoother. Card identity and roles
play no part in Arena's selection rule.
"""

from __future__ import annotations

import math
import random

from .runtime import Card

# Empirically calibrated against the FIN dataset; see module docstring.
ARENA_SMOOTHER_TEMPERATURE: float = -0.015
ARENA_SMOOTHER_NUM_CANDIDATES: int = 3


def draw_smoothed_hand(
    deck: list[Card],
    rng: random.Random,
    *,
    num_candidates: int = ARENA_SMOOTHER_NUM_CANDIDATES,
    temperature: float = ARENA_SMOOTHER_TEMPERATURE,
    hand_size: int = 7,
) -> tuple[list[Card], list[Card]]:
    """Sample an Arena-smoothed opening hand from *deck*.

    Generates *num_candidates* independent shuffles of *deck*, takes
    the top *hand_size* cards of each as a candidate hand, then selects
    one candidate via the softmax weights described in the module
    docstring. Returns the chosen ``(hand, library)`` pair: *library*
    is whatever was below the selected shuffle's top *hand_size*,
    preserving that shuffle's order.

    The ``Card`` instances passed in are reused (no copies); their
    ``instance_id`` values flow through to the returned hand and
    library unchanged.

    *temperature* must be strictly negative — a positive value would
    select *against* deck-matching hands, which is the opposite of
    Arena's behaviour.
    """
    if temperature >= 0:
        raise ValueError(f"temperature must be negative, got {temperature}")
    if num_candidates < 1:
        raise ValueError(f"num_candidates must be >= 1, got {num_candidates}")
    if hand_size < 1:
        raise ValueError(f"hand_size must be >= 1, got {hand_size}")
    if hand_size > len(deck):
        raise ValueError(f"hand_size {hand_size} > deck size {len(deck)}")

    deck_land_frac = sum(1 for c in deck if c.is_land) / len(deck)

    candidates: list[tuple[list[Card], list[Card]]] = []
    weights: list[float] = []
    for _ in range(num_candidates):
        shuffled = list(deck)
        rng.shuffle(shuffled)
        hand = shuffled[:hand_size]
        library = shuffled[hand_size:]
        hand_land_frac = sum(1 for c in hand if c.is_land) / hand_size
        land_diff = abs(hand_land_frac - deck_land_frac)
        weights.append(math.exp((land_diff**2) / temperature))
        candidates.append((hand, library))

    total = sum(weights)
    # All weights are in (0, 1] (exp of non-positive numbers), so the
    # sum is strictly positive — no defensive check needed.
    r = rng.random() * total
    cum = 0.0
    for idx, w in enumerate(weights):
        cum += w
        if r < cum:
            return candidates[idx]
    # Floating-point rounding fallback: last candidate.
    return candidates[-1]
