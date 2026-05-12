"""Mulligan-from-deck Monte Carlo.

The base :func:`simulate` in ``monte_carlo`` takes a *fixed* opening
hand (the user's actual draw) and reshuffles only the library. That
shape doesn't answer the question "what would happen if I mulliganed
this deck?" because the hand isn't fixed — it's whatever the next
draw produces under Arena's smoother, minus whatever we put on the
bottom.

This module wraps the same per-game engine but, for each Monte Carlo
run:

1. Builds a fresh ``Card``-wrapped deck (unique ``instance_id`` per
   copy).
2. Draws a smoothed 7-card opening hand via
   :func:`smoother.draw_smoothed_hand`.
3. Iteratively bottoms cards via :func:`bottoming.bottom_card` until
   the hand is *target_hand_size* cards. Bottomed cards are appended
   to the library (so they sit beneath every future draw in the 4-turn
   window, which makes them effectively unreachable).
4. Calls :func:`engine.simulate_one_game` on the resulting
   (hand, library) pair.

The aggregate stats this returns are directly comparable to the
output of :func:`simulate` — same :class:`AggregateStats` shape — so
downstream feature builders / model inference paths don't need to
distinguish "fresh hand" vs "post-mulligan hand" simulations.

Why this lives in ``simulation`` and not ``model``: the per-deck
mulligan benchmark is a property of the deck + game rules, not of
the model. Keeping the simulator's public surface "give me a hand,
get an aggregate" + "give me a deck, get a post-mulligan aggregate"
keeps the model layer focused on prediction.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from mulligan_coach_cards import ParsedCard

from .bottoming import OhWrLookup, bottom_card
from .engine import simulate_one_game
from .runtime import Card
from .smoother import (
    ARENA_SMOOTHER_NUM_CANDIDATES,
    ARENA_SMOOTHER_TEMPERATURE,
    draw_smoothed_hand,
)
from .stats import AggregateStats, aggregate
from .trace import GameTrace
from .validate import check_deck_encodings

_FULL_HAND_SIZE: int = 7


def simulate_mulligan_from_deck(
    deck: list[ParsedCard],
    *,
    target_hand_size: int = 6,
    on_the_play: bool = True,
    n_runs: int = 1000,
    seed: int | None = None,
    verbose: bool = False,
    oh_wr: OhWrLookup | None = None,
    smoother_num_candidates: int = ARENA_SMOOTHER_NUM_CANDIDATES,
    smoother_temperature: float = ARENA_SMOOTHER_TEMPERATURE,
) -> AggregateStats:
    """Run *n_runs* mulligan-to-*target_hand_size* simulations from
    *deck* and return aggregate stats.

    *deck* is the full decklist as ``ParsedCard`` instances (e.g., the
    40-card deck reconstructed by ``training_rows``). For each run, a
    fresh 7-card opening hand is drawn via Arena's smoother, then
    ``7 - target_hand_size`` cards are placed on the bottom via the
    heuristic in :mod:`bottoming`. *target_hand_size=7* short-circuits
    bottoming — useful for "what would a fresh draw look like?"
    baselines without the mulligan step.

    *oh_wr* should be the **shrunk** Opening-Hand Win Rate lookup
    (see :class:`OhWrLookup`). Passing ``None`` skips rule S4 from
    bottoming; the heuristic still runs through rules S1-S3 and falls
    back to hand index, so the function works without 17Lands data
    (results will just be slightly noisier for hands where every
    candidate ties at S3).

    Hard-fails (raises :class:`validate.DeckEncodingError`) if any
    card lacks the mechanical encoding the simulator needs.
    """
    if not (1 <= target_hand_size <= _FULL_HAND_SIZE):
        raise ValueError(
            f"target_hand_size must be in [1, {_FULL_HAND_SIZE}], got {target_hand_size}"
        )
    if len(deck) < _FULL_HAND_SIZE:
        raise ValueError(f"deck has only {len(deck)} cards; need at least {_FULL_HAND_SIZE}")
    check_deck_encodings(deck)

    rng = random.Random(seed)
    games = list(
        _iter_mulligan_games(
            deck=deck,
            target_hand_size=target_hand_size,
            on_the_play=on_the_play,
            n_runs=n_runs,
            rng=rng,
            verbose=verbose,
            oh_wr=oh_wr,
            smoother_num_candidates=smoother_num_candidates,
            smoother_temperature=smoother_temperature,
        )
    )
    return aggregate(games, n_runs=n_runs, seed=seed, on_the_play=on_the_play)


def post_mulligan_hand(
    deck: list[ParsedCard],
    rng: random.Random,
    *,
    target_hand_size: int = 6,
    oh_wr: OhWrLookup | None = None,
    smoother_num_candidates: int = ARENA_SMOOTHER_NUM_CANDIDATES,
    smoother_temperature: float = ARENA_SMOOTHER_TEMPERATURE,
) -> tuple[list[Card], list[Card]]:
    """Build one (hand, library) pair representing a single mulligan
    outcome from *deck*. Exposed so the brute-force validation script
    can call the smoother + bottoming pipeline without running a full
    game simulation.

    Returns ``(hand, library)`` where ``hand`` has *target_hand_size*
    cards and ``library`` has ``len(deck) - target_hand_size`` cards
    in draw order (``library[0]`` is the next card drawn; bottomed
    cards are at the end).
    """
    cards = [Card(instance_id=i, parsed=p) for i, p in enumerate(deck)]
    hand, library = draw_smoothed_hand(
        cards,
        rng,
        num_candidates=smoother_num_candidates,
        temperature=smoother_temperature,
    )
    n_to_bottom = _FULL_HAND_SIZE - target_hand_size
    for _ in range(n_to_bottom):
        chosen = bottom_card(hand, cards, oh_wr=oh_wr)
        hand.remove(chosen)
        library.append(chosen)
    return hand, library


def _iter_mulligan_games(
    *,
    deck: list[ParsedCard],
    target_hand_size: int,
    on_the_play: bool,
    n_runs: int,
    rng: random.Random,
    verbose: bool,
    oh_wr: OhWrLookup | None,
    smoother_num_candidates: int,
    smoother_temperature: float,
) -> Iterator[GameTrace]:
    """Yield one :class:`GameTrace` per Monte Carlo run.

    Mirrors ``monte_carlo._iter_games``: each run gets fresh ``Card``
    wrappers (so ``instance_id`` is stable within the run, not across
    runs) and a sub-RNG derived from the master seed (so any in-game
    shuffles — e.g. fetch lands — are reproducible).
    """
    for _ in range(n_runs):
        hand, library = post_mulligan_hand(
            deck,
            rng,
            target_hand_size=target_hand_size,
            oh_wr=oh_wr,
            smoother_num_candidates=smoother_num_candidates,
            smoother_temperature=smoother_temperature,
        )
        sub_seed = rng.randrange(1 << 30)
        sub_rng = random.Random(sub_seed)
        trace = simulate_one_game(
            hand,
            library,
            on_the_play=on_the_play,
            rng=sub_rng,
            seed=sub_seed,
            verbose=verbose,
        )
        yield trace
