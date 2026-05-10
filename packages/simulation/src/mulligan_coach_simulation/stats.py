"""Aggregated Monte Carlo statistics.

Per-card outputs computed across N games. The simulator emits one
:class:`AggregateStats` per :func:`monte_carlo.simulate` call; the
downstream model package ingests this directly as one of its features.

Distribution conventions (matching the design CLAUDE.md):

* ``p_in_hand_by_turn`` is a list of length 4 — index ``T-1`` is
  P(in hand by turn T) over instances of the named card.
* ``p_castable_by_turn`` is also length 4, conditional on ever being
  in hand by turn T. If the card is never in hand in any game, the
  entries are all 0.
* ``first_castable_turn_distribution`` is length 5: indices 0..3 are
  first-castable-turn 1, 2, 3, 4; index 4 is the "5+" bucket
  (instance never became castable in the 4-turn window).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pydantic import BaseModel, Field

from .trace import GameTrace


class ModeStats(BaseModel):
    """Per-(card, mode) aggregate. One ``ModeStats`` per evaluated mode
    (cast / cycle / land_cycle / channel)."""

    mode_index: int
    mode_kind: str
    p_castable_by_turn: list[float] = Field(default_factory=lambda: [0.0] * 4)
    first_castable_turn_distribution: list[float] = Field(default_factory=lambda: [0.0] * 5)


class CardStats(BaseModel):
    """Per-card aggregate, keyed by ``name`` in :class:`AggregateStats`."""

    name: str
    oracle_id: str
    n_copies_in_deck: int = Field(
        ge=0, description="Number of copies of this name in the deck (constant across games)."
    )
    p_in_hand_by_turn: list[float] = Field(default_factory=lambda: [0.0] * 4)
    p_castable_by_turn: list[float] = Field(default_factory=lambda: [0.0] * 4)
    first_castable_turn_distribution: list[float] = Field(default_factory=lambda: [0.0] * 5)
    by_mode: list[ModeStats] = Field(default_factory=list)


class GameLevelStats(BaseModel):
    """Game-level Monte Carlo summaries — counters that aren't keyed by
    card name. Feeds the XGBoost feature stage's "mana availability"
    family (per ``packages/features/features_list.md``).

    ``p_land_drop_by_turn[T]`` indices: position 0 = P(land drop turn 2),
    position 1 = T3, position 2 = T4, position 3 = T5. "Made the
    Nth land drop" = "had at least N lands in play after this turn's
    land drop, before any spells went off". Turn 5 comes from the
    simulator's land-drop-only lookahead step.

    ``expected_mana_count_turn[T]`` indices: position 0 = T2,
    position 1 = T3, position 2 = T4. Each entry is the average number
    of mana sources (lands + non-creature mana permanents + mana dorks
    that aren't summoning-sick) available at start of that turn's main
    phase. Turn 1 is excluded — it's almost always 1, and the
    information is captured by other features.
    """

    p_land_drop_by_turn: list[float] = Field(default_factory=lambda: [0.0] * 4)
    expected_mana_count_turn: list[float] = Field(default_factory=lambda: [0.0] * 3)


class AggregateStats(BaseModel):
    """Top-level Monte Carlo output. Indexed by card name."""

    n_runs: int
    seed: int | None
    on_the_play: bool
    by_card_name: dict[str, CardStats] = Field(default_factory=dict)
    game_level: GameLevelStats = Field(default_factory=GameLevelStats)


def aggregate(
    games: Iterable[GameTrace],
    *,
    n_runs: int,
    seed: int | None,
    on_the_play: bool,
) -> AggregateStats:
    """Roll up per-game traces into per-card aggregates."""
    games_list = list(games)

    # Counters bucketed by name. Each entry tracks every instance ever
    # seen with that name — for a 17-Forest deck across N games that's
    # 17*N instance rows.
    in_hand_by_turn: dict[str, list[int]] = defaultdict(lambda: [0] * 4)
    castable_by_turn: dict[str, list[int]] = defaultdict(lambda: [0] * 4)
    first_castable_dist: dict[str, list[int]] = defaultdict(lambda: [0] * 5)
    n_total: dict[str, int] = defaultdict(int)
    n_in_hand_by_turn: dict[str, list[int]] = defaultdict(lambda: [0] * 4)
    oracle_ids: dict[str, str] = {}
    # Per-mode counters: keyed by (name, mode_index).
    mode_kinds: dict[tuple[str, int], str] = {}
    mode_castable_by_turn: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0] * 4)
    mode_first_castable_dist: dict[tuple[str, int], list[int]] = defaultdict(lambda: [0] * 5)

    n_in_deck: dict[str, int] = defaultdict(int)
    if games_list:
        for instance in games_list[0].instances:
            n_in_deck[instance.name] += 1

    for game in games_list:
        for inst in game.instances:
            n_total[inst.name] += 1
            oracle_ids.setdefault(inst.name, inst.oracle_id)

            # In-hand counts: any turn T such that first_in_hand_turn ≤ T.
            for t in range(1, 5):
                if inst.first_in_hand_turn <= t:
                    in_hand_by_turn[inst.name][t - 1] += 1
                    n_in_hand_by_turn[inst.name][t - 1] += 1

            # Castable counts: any turn T such that first_castable_turn ≤ T.
            for t in range(1, 5):
                if inst.first_castable_turn <= t:
                    castable_by_turn[inst.name][t - 1] += 1

            # First-castable-turn distribution: bucket 1..4, plus 5 for "never".
            bucket = min(inst.first_castable_turn, 5) - 1
            first_castable_dist[inst.name][bucket] += 1

            # Per-mode.
            for mode_record in inst.modes:
                key = (inst.name, mode_record.mode_index)
                mode_kinds[key] = mode_record.mode_kind
                for t in range(1, 5):
                    if mode_record.first_castable_turn <= t:
                        mode_castable_by_turn[key][t - 1] += 1
                bucket = min(mode_record.first_castable_turn, 5) - 1
                mode_first_castable_dist[key][bucket] += 1

    by_name: dict[str, CardStats] = {}
    for name, total in n_total.items():
        cs = CardStats(
            name=name,
            oracle_id=oracle_ids.get(name, ""),
            n_copies_in_deck=n_in_deck.get(name, 0),
        )
        if total > 0:
            cs.p_in_hand_by_turn = [in_hand_by_turn[name][t] / total for t in range(4)]
            cs.first_castable_turn_distribution = [
                first_castable_dist[name][b] / total for b in range(5)
            ]
        for t in range(4):
            denom = n_in_hand_by_turn[name][t]
            cs.p_castable_by_turn[t] = castable_by_turn[name][t] / denom if denom > 0 else 0.0

        # Per-mode rollup.
        per_mode: list[ModeStats] = []
        keys = sorted(k for k in mode_kinds if k[0] == name)
        for name_again, mode_idx in keys:
            ms = ModeStats(mode_index=mode_idx, mode_kind=mode_kinds[(name_again, mode_idx)])
            mode_total = total  # one mode-row per instance per name
            if mode_total > 0:
                ms.first_castable_turn_distribution = [
                    mode_first_castable_dist[(name_again, mode_idx)][b] / mode_total
                    for b in range(5)
                ]
            for t in range(4):
                denom = n_in_hand_by_turn[name][t]
                ms.p_castable_by_turn[t] = (
                    mode_castable_by_turn[(name_again, mode_idx)][t] / denom if denom > 0 else 0.0
                )
            per_mode.append(ms)
        cs.by_mode = per_mode
        by_name[name] = cs

    game_level = aggregate_game_level(games_list)

    return AggregateStats(
        n_runs=n_runs,
        seed=seed,
        on_the_play=on_the_play,
        by_card_name=by_name,
        game_level=game_level,
    )


def aggregate_game_level(games: Iterable[GameTrace]) -> GameLevelStats:
    """Roll up turn-snapshot counters into game-level averages.

    ``p_land_drop_by_turn[i]`` is the fraction of games where the
    player had at least N lands in play after turn N's land drop, for
    N = 2, 3, 4, 5 (i.e. ``i = N - 2``). The turn-5 entry comes from
    the engine's land-drop-only lookahead step.

    ``expected_mana_count_turn[i]`` is the average mana count at start
    of turn N's main phase, for N = 2, 3, 4 (``i = N - 2``).

    Games with no snapshot for a given turn (shouldn't happen in
    normal use, but defensively handled) don't contribute to that
    turn's numerator or denominator.
    """
    games_list = list(games)
    if not games_list:
        return GameLevelStats()

    # Index maps: position in the output list → turn number.
    LAND_DROP_TURNS = (2, 3, 4, 5)
    MANA_TURNS = (2, 3, 4)

    land_drop_hits = [0] * len(LAND_DROP_TURNS)
    land_drop_total = [0] * len(LAND_DROP_TURNS)
    mana_sum = [0] * len(MANA_TURNS)
    mana_total = [0] * len(MANA_TURNS)

    for game in games_list:
        by_turn = {snap.turn: snap for snap in game.turns}
        for i, t in enumerate(LAND_DROP_TURNS):
            snap = by_turn.get(t)
            if snap is None:
                continue
            land_drop_total[i] += 1
            if snap.lands_in_play_after_drop >= t:
                land_drop_hits[i] += 1
        for i, t in enumerate(MANA_TURNS):
            snap = by_turn.get(t)
            if snap is None:
                continue
            mana_total[i] += 1
            mana_sum[i] += snap.mana_sources_at_start_of_main

    return GameLevelStats(
        p_land_drop_by_turn=[
            (land_drop_hits[i] / land_drop_total[i]) if land_drop_total[i] > 0 else 0.0
            for i in range(len(LAND_DROP_TURNS))
        ],
        expected_mana_count_turn=[
            (mana_sum[i] / mana_total[i]) if mana_total[i] > 0 else 0.0
            for i in range(len(MANA_TURNS))
        ],
    )
