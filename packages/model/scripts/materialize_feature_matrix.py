"""Materialise the win-model feature parquet for one or more sets.

Thin CLI over
:func:`mulligan_coach_model.feature_matrix.materialize_feature_matrix`.
For each set passed via ``--sets``:

1. Opens the DuckDB games view at
   ``data/processed/games.duckdb`` (override via ``--duckdb-path``).
2. Streams every Premier Draft row for the set, runs the goldfish
   simulator, builds the 200-column feature row, and writes chunked
   parquets under
   ``data/processed/model_training/{SET}/{EVENT}/chunk_*.parquet``
   (override via ``--output-root``).

Resume + atomic chunk writes work identically to the choice
materialiser: a crashed run leaves only fully-written chunks, and a
re-invocation skips already-completed ``(draft_id, game_number)``
tuples.

Typical use:

    .venv/Scripts/python.exe \\
      packages/model/scripts/materialize_feature_matrix.py \\
      --sets TLA TMT ECL \\
      --overwrite \\
      --n-workers 8
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mulligan_coach_model.feature_matrix import materialize_feature_matrix

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "processed" / "model_training"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sets",
        nargs="+",
        required=True,
        help="Three-letter set codes to materialise (e.g. TLA TMT ECL SOS).",
    )
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument(
        "--duckdb-path",
        type=Path,
        default=DEFAULT_DUCKDB_PATH,
        help="Path to the games DuckDB.",
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Output root. Per-set sub-dir is {root}/{SET}/{EVENT}/.",
    )
    ap.add_argument("--n-sims-per-row", type=int, default=200)
    ap.add_argument("--n-workers", type=int, default=1)
    ap.add_argument(
        "--chunksize",
        type=int,
        default=32,
        help="multiprocessing.Pool.imap_unordered chunksize; ignored when n-workers == 1.",
    )
    ap.add_argument("--chunk-rows", type=int, default=5000)
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete existing chunks in the output dir before starting.",
    )
    ap.add_argument(
        "--no-resume",
        action="store_true",
        help="Refuse to resume from an existing output dir (errors instead).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional cap on training rows iterated (post-filter); for smoke tests.",
    )
    args = ap.parse_args()

    if not args.duckdb_path.exists():
        raise SystemExit(f"DuckDB path missing: {args.duckdb_path}")

    for set_code in args.sets:
        out_dir = args.output_root / set_code / args.event_type
        logging.info("=== %s -> %s ===", set_code, out_dir)
        stats = materialize_feature_matrix(
            set_code=set_code,
            duckdb_path=args.duckdb_path,
            output_dir=out_dir,
            event_type=args.event_type,
            n_sims_per_row=args.n_sims_per_row,
            n_workers=args.n_workers,
            chunksize=args.chunksize,
            chunk_rows=args.chunk_rows,
            resume=not args.no_resume,
            overwrite=args.overwrite,
            limit=args.limit,
        )
        logging.info(
            "%s: %d rows written (skipped_resume=%d, failed_sim=%d, failed_feature_build=%d)",
            set_code,
            stats.rows_written,
            stats.rows_skipped_resume,
            stats.rows_failed_simulation,
            stats.rows_failed_feature_build,
        )


if __name__ == "__main__":
    main()
