"""Equivalence-and-speed harness for the goldfish simulator.

Replays a deterministic sample of **real** 17Lands (deck, hand) pairs
through :func:`simulate` and snapshots the result, so optimisations
can be verified bit-for-bit against the pre-change baseline.

Why real data: the materialised feature parquets at
``data/processed/model_training/TLA/PremierDraft/`` were produced by
running the simulator against rows from
``data/processed/games.duckdb``. The same (deck, hand, seed) triple,
when re-simulated, must produce the same
:class:`AggregateStats` — otherwise any "speedup" silently corrupted
the training signal. Synthetic decks would miss real-world shapes
(ETB-tapped duals, fetch lands, mana dorks, multi-mode spells, …)
that the live materialiser hits all the time.

Two modes:

* ``--save baseline.json`` — run the corpus and write a JSON blob
  containing every :class:`AggregateStats` plus a hash of every
  :class:`GameTrace`. Use this once on the current (pre-change) code.
* ``--check baseline.json`` — re-run the corpus and assert that
  every aggregate + every trace hash matches the saved file
  bit-for-bit. Any mismatch is printed with a short diff and exits 1.

Both modes also report total elapsed time for before/after speedup.

The corpus is the first ``--n-rows`` rows the duckdb cursor yields
for ``(set_code, event_type)``; deterministic because the SQL doesn't
ORDER BY but the source is a static file and the cursor's traversal
is repeatable across runs. Per-row seed matches the materialiser's
:func:`mulligan_coach_model.feature_matrix._row_seed`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

import duckdb
from mulligan_coach_cards import ParsedCard
from mulligan_coach_model.feature_matrix import _row_seed
from mulligan_coach_model.training_rows import (
    TrainingRow,
    TrainingRowStats,
    build_name_lookup,
    iter_training_rows,
)
from mulligan_coach_simulation import AggregateStats, iter_traces, simulate

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DUCKDB = REPO_ROOT / "data" / "processed" / "games.duckdb"

# Corpus shape. 50 real rows × 20 sims/row = 1,000 games — meets the
# user's "at least 1000" bar with a healthy spread of real decks.
DEFAULT_N_ROWS = 50
DEFAULT_N_SIMS_PER_ROW = 20
DEFAULT_SET = "TLA"
DEFAULT_EVENT_TYPE = "PremierDraft"


def _library_from_deck(
    hand: tuple[ParsedCard, ...], deck: tuple[ParsedCard, ...]
) -> list[ParsedCard]:
    """``deck - hand`` by name (matches materialiser's helper)."""
    hand_counts: Counter[str] = Counter(c.name for c in hand)
    library: list[ParsedCard] = []
    for c in deck:
        if hand_counts[c.name] > 0:
            hand_counts[c.name] -= 1
            continue
        library.append(c)
    return library


def _hash_trace(trace_json: str) -> str:
    return hashlib.sha256(trace_json.encode("utf-8")).hexdigest()


def _collect_rows(
    duckdb_path: Path, set_code: str, event_type: str, n_rows: int
) -> list[TrainingRow]:
    """Pull the first ``n_rows`` valid TrainingRows from the duckdb."""
    if not duckdb_path.exists():
        raise FileNotFoundError(
            f"Games duckdb not found at {duckdb_path}. "
            "Run `packages/data-download` to materialise it first."
        )
    name_lookup = build_name_lookup(set_code)
    rows: list[TrainingRow] = []
    stats = TrainingRowStats()
    # ``limit`` controls the SQL pull; we still apply the iterator's
    # row-quality filters in Python and stop once we've gathered
    # ``n_rows`` *valid* rows. A 2x SQL multiplier gives us headroom
    # over typical skip rates (~5-15% of rows fail validation).
    sql_limit = max(n_rows * 2, n_rows + 100)
    with duckdb.connect(str(duckdb_path), read_only=True) as conn:
        for tr in iter_training_rows(
            connection=conn,
            set_code=set_code,
            event_type=event_type,
            name_lookup=name_lookup,
            limit=sql_limit,
            stats=stats,
        ):
            rows.append(tr)
            if len(rows) >= n_rows:
                break
    if len(rows) < n_rows:
        raise RuntimeError(
            f"Only got {len(rows)} valid rows from duckdb; asked for {n_rows}. "
            f"Try a larger --n-rows-sql-multiplier or a different set."
        )
    return rows


def _run_corpus(
    rows: list[TrainingRow], n_sims_per_row: int
) -> dict[str, object]:
    """Run every row through :func:`simulate` (for AggregateStats) and
    :func:`iter_traces` (for trace-hash equivalence).

    Per row we record:

    * ``aggregate`` — the full :class:`AggregateStats` dump.
    * ``trace_hashes`` — one SHA-256 per :class:`GameTrace` (full
      traces would balloon the baseline file).
    """
    out: list[dict[str, object]] = []
    for i, tr in enumerate(rows):
        seed = _row_seed(tr.draft_id, tr.match_number, tr.game_number)
        library = _library_from_deck(tr.hand, tr.deck)

        agg: AggregateStats = simulate(
            list(tr.hand),
            library,
            on_the_play=tr.on_the_play,
            n_runs=n_sims_per_row,
            seed=seed,
            verbose=False,
        )
        trace_hashes: list[str] = []
        for trace in iter_traces(
            list(tr.hand),
            library,
            on_the_play=tr.on_the_play,
            n_runs=n_sims_per_row,
            seed=seed,
            verbose=True,
        ):
            trace_hashes.append(_hash_trace(trace.model_dump_json()))

        out.append(
            {
                "row_index": i,
                "draft_id": tr.draft_id,
                "match_number": tr.match_number,
                "game_number": tr.game_number,
                "on_the_play": tr.on_the_play,
                "mulligan_number": tr.mulligan_number,
                "seed": seed,
                "aggregate": json.loads(agg.model_dump_json()),
                "trace_hashes": trace_hashes,
            }
        )
    return {
        "n_rows": len(rows),
        "n_sims_per_row": n_sims_per_row,
        "total_games": len(rows) * n_sims_per_row,
        "rows": out,
    }


def _diff_blobs(baseline: dict[str, object], current: dict[str, object]) -> list[str]:
    """Return a list of human-readable mismatch descriptions, empty
    when the two blobs are equivalent. We surface the first divergent
    field per row to keep debugging tractable.
    """
    problems: list[str] = []
    base_rows: list[dict] = baseline["rows"]  # type: ignore[assignment]
    curr_rows: list[dict] = current["rows"]  # type: ignore[assignment]
    if len(base_rows) != len(curr_rows):
        problems.append(f"row count differs: {len(base_rows)} vs {len(curr_rows)}")
        return problems
    for i, (b, c) in enumerate(zip(base_rows, curr_rows)):
        # Identity check: same source row?
        if (b["draft_id"], b["match_number"], b["game_number"]) != (
            c["draft_id"],
            c["match_number"],
            c["game_number"],
        ):
            problems.append(
                f"row {i}: source identity differs "
                f"(baseline={b['draft_id']}/{b['match_number']}/{b['game_number']} "
                f"vs current={c['draft_id']}/{c['match_number']}/{c['game_number']})"
            )
            continue
        if b["aggregate"] != c["aggregate"]:
            problems.append(f"row {i}: AggregateStats differs (seed={b['seed']})")
            ba = b["aggregate"]
            ca = c["aggregate"]
            for k in sorted(set(ba) | set(ca)):
                if ba.get(k) != ca.get(k):
                    problems.append(f"    └─ field '{k}' differs")
                    if k == "by_card_name":
                        for name in sorted(set(ba[k]) | set(ca[k])):
                            if ba[k].get(name) != ca[k].get(name):
                                problems.append(f"        └─ card '{name}' differs")
                                if len(problems) > 60:
                                    break
                    elif k == "game_level":
                        problems.append(f"        baseline: {ba[k]}")
                        problems.append(f"        current:  {ca[k]}")
        if b["trace_hashes"] != c["trace_hashes"]:
            differs = [
                j for j in range(len(b["trace_hashes"]))
                if b["trace_hashes"][j] != c["trace_hashes"][j]
            ]
            problems.append(
                f"row {i}: {len(differs)} of {len(b['trace_hashes'])} trace hashes differ "
                f"(first divergent run indices: {differs[:5]})"
            )
        if len(problems) > 200:
            problems.append("(truncated — too many mismatches)")
            return problems
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", type=Path, help="Write baseline to this path")
    parser.add_argument("--check", type=Path, help="Compare against this baseline")
    parser.add_argument(
        "--duckdb",
        type=Path,
        default=DEFAULT_DUCKDB,
        help=f"Games duckdb path (default: {DEFAULT_DUCKDB})",
    )
    parser.add_argument("--set", dest="set_code", default=DEFAULT_SET, help="Set code (default TLA)")
    parser.add_argument(
        "--event-type", default=DEFAULT_EVENT_TYPE, help="Event type (default PremierDraft)"
    )
    parser.add_argument(
        "--n-rows", type=int, default=DEFAULT_N_ROWS, help=f"Real rows to sample (default {DEFAULT_N_ROWS})"
    )
    parser.add_argument(
        "--n-sims-per-row",
        type=int,
        default=DEFAULT_N_SIMS_PER_ROW,
        help=f"Monte Carlo runs per row (default {DEFAULT_N_SIMS_PER_ROW})",
    )
    args = parser.parse_args()

    if not args.save and not args.check:
        parser.error("must pass either --save or --check")

    print(f"Loading real TrainingRows from {args.duckdb} ({args.set_code}/{args.event_type}) ...")
    rows = _collect_rows(args.duckdb, args.set_code, args.event_type, args.n_rows)
    print(f"  {len(rows)} rows; first row draft_id={rows[0].draft_id} game={rows[0].game_number}")

    total_games = args.n_rows * args.n_sims_per_row
    print(f"Running {args.n_rows} rows × {args.n_sims_per_row} sims = {total_games} games...")
    t0 = time.time()
    blob = _run_corpus(rows, args.n_sims_per_row)
    elapsed = time.time() - t0
    print(f"  done in {elapsed:.2f}s ({elapsed * 1000 / total_games:.2f} ms/game)")

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(blob, indent=2), encoding="utf-8")
        print(f"Baseline saved to {args.save}")
        return 0

    print(f"Comparing against {args.check} ...")
    baseline_blob = json.loads(args.check.read_text(encoding="utf-8"))
    problems = _diff_blobs(baseline_blob, blob)
    if problems:
        print(f"\nFAIL — {len(problems)} mismatch lines:")
        for p in problems[:50]:
            print(f"  {p}")
        return 1
    print("OK — every aggregate + every trace matches the baseline bit-for-bit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
