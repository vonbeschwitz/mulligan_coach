"""cProfile companion to the equivalence harness.

Loads the same real-data corpus as ``equivalence_harness.py`` (so the
profile reflects the exact deck shapes the materialiser feeds the
simulator), then profiles only the ``simulate()`` calls — duckdb
loading and row parsing are excluded from the numbers.

Usage::

    .venv/Scripts/python.exe packages/simulation/scripts/profile_hotspots.py
    .venv/Scripts/python.exe packages/simulation/scripts/profile_hotspots.py \
        --n-rows 20 --n-sims-per-row 50 --sort tottime

Prints the top functions by cumulative and by total (self) time and
optionally writes the raw pstats blob (``--pstats out.prof``) for
snakeviz or pstats interactive browsing.
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
import time
from pathlib import Path

# The harness is a sibling script (not an installed module); import it
# by path so we reuse its row loader instead of duplicating it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# mypy can't follow the sys.path insertion above, so it can't find the
# sibling script as a module; the import works fine at runtime.
from equivalence_harness import (  # type: ignore[import-not-found]
    DEFAULT_DUCKDB,
    DEFAULT_EVENT_TYPE,
    DEFAULT_N_ROWS,
    DEFAULT_N_SIMS_PER_ROW,
    DEFAULT_SET,
    _collect_rows,
    _library_from_deck,
)
from mulligan_coach_model.feature_matrix import _row_seed
from mulligan_coach_simulation import simulate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duckdb", type=Path, default=DEFAULT_DUCKDB)
    parser.add_argument("--set", dest="set_code", default=DEFAULT_SET)
    parser.add_argument("--event-type", default=DEFAULT_EVENT_TYPE)
    parser.add_argument("--n-rows", type=int, default=DEFAULT_N_ROWS)
    parser.add_argument("--n-sims-per-row", type=int, default=DEFAULT_N_SIMS_PER_ROW)
    parser.add_argument("--sort", choices=["cumulative", "tottime"], default="cumulative")
    parser.add_argument("--top", type=int, default=35, help="Rows of pstats output to print")
    parser.add_argument("--pstats", type=Path, help="Optionally dump raw pstats here")
    args = parser.parse_args()

    print(f"Loading rows from {args.duckdb} ({args.set_code}/{args.event_type}) ...")
    rows = _collect_rows(args.duckdb, args.set_code, args.event_type, args.n_rows)
    total_games = len(rows) * args.n_sims_per_row
    print(f"Profiling {len(rows)} rows x {args.n_sims_per_row} sims = {total_games} games ...")

    def workload() -> None:
        for tr in rows:
            seed = _row_seed(tr.draft_id, tr.match_number, tr.game_number)
            library = _library_from_deck(tr.hand, tr.deck)
            simulate(
                list(tr.hand),
                library,
                on_the_play=tr.on_the_play,
                n_runs=args.n_sims_per_row,
                seed=seed,
                verbose=False,
            )

    # One untimed warm-up pass keeps one-time costs (per-session cost
    # expansion, imports) out of the profile.
    t0 = time.perf_counter()
    workload()
    warm = time.perf_counter() - t0
    print(f"Warm-up pass: {warm:.2f}s ({warm * 1000 / total_games:.2f} ms/game)")

    profiler = cProfile.Profile()
    profiler.enable()
    workload()
    profiler.disable()

    stats = pstats.Stats(profiler)
    stats.sort_stats("cumulative").print_stats(args.top)
    stats.sort_stats("tottime").print_stats(args.top)
    if args.pstats:
        stats.dump_stats(str(args.pstats))
        print(f"Raw pstats written to {args.pstats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
