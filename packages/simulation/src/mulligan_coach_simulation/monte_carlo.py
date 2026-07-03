"""Monte Carlo wrapper.

Public entry point :func:`simulate` takes a fixed hand and library of
``ParsedCard``s, runs many shuffles of the library through the engine,
and aggregates per-card statistics.

The hand is treated as fixed (it's the user's actual opening hand —
that's the input to the mulligan decision). Only the library is
reshuffled across runs.
"""

from __future__ import annotations

import random
from collections.abc import Iterator

from mulligan_coach_cards import ParsedCard

from .engine import simulate_one_game
from .mana import reset_solver_caches
from .runtime import Card
from .stats import AggregateStats, aggregate
from .trace import GameTrace
from .validate import check_deck_encodings


def simulate(
    hand: list[ParsedCard],
    library: list[ParsedCard],
    *,
    on_the_play: bool = True,
    n_runs: int = 1000,
    seed: int | None = None,
    verbose: bool = False,
) -> AggregateStats:
    """Run *n_runs* shuffled goldfishes and return per-card aggregate stats.

    The library is reshuffled per run using a deterministic seeded
    sequence; with the same *seed*, the same hand/library produce
    bit-identical :class:`AggregateStats`. Calling without *seed*
    uses ``random.Random()``'s default (system-entropy) seeding —
    output won't be reproducible.

    *verbose* is forwarded to :func:`engine.simulate_one_game`. Note
    that aggregate stats don't carry the per-game ``actions`` lists;
    use :func:`simulate_one_game` directly (or :func:`iter_traces`
    below) when you want the verbose transcript.

    Hard-fails (raises :class:`validate.DeckEncodingError`) if any
    card lacks the mechanical encoding the simulator needs. See
    :func:`validate.check_deck_encodings`.
    """
    check_deck_encodings(hand + library)

    rng = random.Random(seed)
    games = list(
        _iter_games(hand, library, on_the_play=on_the_play, n_runs=n_runs, rng=rng, verbose=verbose)
    )
    return aggregate(games, n_runs=n_runs, seed=seed, on_the_play=on_the_play)


def iter_traces(
    hand: list[ParsedCard],
    library: list[ParsedCard],
    *,
    on_the_play: bool = True,
    n_runs: int,
    seed: int | None = None,
    verbose: bool = True,
) -> Iterator[GameTrace]:
    """Yield raw :class:`GameTrace` objects, one per Monte Carlo run.

    Convenient for verbose debugging: pair with
    :func:`pretty_print` to inspect a small handful of runs without
    materialising the full aggregate. ``verbose=True`` by default so
    the actions list is populated.
    """
    check_deck_encodings(hand + library)
    rng = random.Random(seed)
    yield from _iter_games(
        hand, library, on_the_play=on_the_play, n_runs=n_runs, rng=rng, verbose=verbose
    )


def _iter_games(
    hand: list[ParsedCard],
    library: list[ParsedCard],
    *,
    on_the_play: bool,
    n_runs: int,
    rng: random.Random,
    verbose: bool,
) -> Iterator[GameTrace]:
    """Generator: yield one :class:`GameTrace` per Monte Carlo run.

    Each game gets fresh ``Card`` wrappers (so ``instance_id`` is
    stable within a game and doesn't bleed across runs) and a sub-RNG
    derived from the master seed (so fetch-shuffles inside the game
    are reproducible)."""
    # Memory hygiene between unrelated decks; the games of THIS run
    # batch then share solved mana shapes (see ``mana._CSP_CACHE``).
    reset_solver_caches()
    n_hand = len(hand)
    for _ in range(n_runs):
        per_game_hand = [Card(instance_id=i, parsed=p) for i, p in enumerate(hand)]
        per_game_library = [Card(instance_id=n_hand + i, parsed=p) for i, p in enumerate(library)]
        rng.shuffle(per_game_library)
        sub_seed = rng.randrange(1 << 30)
        sub_rng = random.Random(sub_seed)
        trace: GameTrace = simulate_one_game(
            per_game_hand,
            per_game_library,
            on_the_play=on_the_play,
            rng=sub_rng,
            seed=sub_seed,
            verbose=verbose,
        )
        yield trace
