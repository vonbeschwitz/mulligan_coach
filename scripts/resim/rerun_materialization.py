"""Rerun ``materialize_feature_matrix`` on one or more sets to refill
parquet chunks that were emptied by :mod:`invalidate_affected_rows`.

This is a thin CLI over the existing
:func:`mulligan_coach_model.materialize_feature_matrix`. The function
already supports resume: it scans existing chunks for completed
``(draft_id, match_number, game_number)`` triples and only re-simulates
rows that are missing.

Typical sequence after card-data edits:

    .venv/Scripts/python.exe scripts/resim/find_affected_cards.py
    .venv/Scripts/python.exe scripts/resim/invalidate_affected_rows.py
    .venv/Scripts/python.exe scripts/resim/rerun_materialization.py --sets TLA ECL

This will only re-simulate the rows that the invalidation step dropped.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mulligan_coach_model import materialize_feature_matrix

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
DEFAULT_MODEL_TRAINING_DIR = REPO_ROOT / "data" / "processed" / "model_training"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", nargs="+", required=True, help="Set codes to rematerialise.")
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--duckdb-path", type=Path, default=DEFAULT_DUCKDB_PATH)
    ap.add_argument("--model-training-dir", type=Path, default=DEFAULT_MODEL_TRAINING_DIR)
    ap.add_argument(
        "--n-workers",
        type=int,
        default=1,
        help="Worker processes for the simulator pool. The materialiser is "
        "Python-bound, so wall-clock scales near-linearly with cores.",
    )
    ap.add_argument(
        "--n-sims-per-row",
        type=int,
        default=200,
        help="Monte Carlo replicates per training row. Match the value used "
        "for the original materialisation (default 200) so the re-filled rows "
        "are statistically comparable.",
    )
    ap.add_argument("--chunk-rows", type=int, default=5000)
    ap.add_argument("--chunksize", type=int, default=32)
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Delete every existing chunk_*.parquet in the output directory "
        "before starting. Use this for a clean full rerun. Without it, the "
        "function resumes from existing chunks and only re-simulates missing "
        "rows.",
    )
    args = ap.parse_args()

    for set_code in args.sets:
        output_dir = args.model_training_dir / set_code / args.event_type
        output_dir.mkdir(parents=True, exist_ok=True)
        logging.info(
            "==== Rematerialising %s into %s (workers=%d, n_sims=%d) ====",
            set_code,
            output_dir,
            args.n_workers,
            args.n_sims_per_row,
        )
        stats = materialize_feature_matrix(
            set_code=set_code,
            duckdb_path=args.duckdb_path,
            output_dir=output_dir,
            event_type=args.event_type,
            n_sims_per_row=args.n_sims_per_row,
            n_workers=args.n_workers,
            chunksize=args.chunksize,
            chunk_rows=args.chunk_rows,
            resume=not args.overwrite,
            overwrite=args.overwrite,
        )
        logging.info(
            "%s: wrote %d new rows / %d chunks (skipped_resume=%d, failed_sim=%d, failed_build=%d)",
            set_code,
            stats.rows_written,
            stats.chunks_written,
            stats.rows_skipped_resume,
            stats.rows_failed_simulation,
            stats.rows_failed_feature_build,
        )


if __name__ == "__main__":
    main()
