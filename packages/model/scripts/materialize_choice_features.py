"""Materialise the choice-model feature parquet for one or more sets.

Thin CLI over
:func:`mulligan_coach_model.choice_feature_matrix.materialize_choice_feature_matrix`.
For each set passed via ``--sets``:

1. Reads the mulligan-decisions parquet at
   ``data/processed/seventeenlands/mulligan_decisions/{SET}.{EVENT}.parquet``
   (override via ``--decisions-dir``).
2. Reads the win-model kept-hand feature cache at
   ``data/processed/model_training/{SET}/{EVENT}/`` to skip re-simulating
   hands that the win-model pipeline has already covered (override via
   ``--cached-features-dir``).
3. Writes the choice-model feature parquet under
   ``data/processed/choice_training/{SET}/{EVENT}/chunk_*.parquet``
   (override via ``--output-dir``).

Resume + atomic chunk writes work identically to the win-model
materialiser: a crashed run leaves only fully-written chunks, and a
re-invocation skips already-completed ``(draft_id, build_index,
match_number, game_number, mulligan_number)`` tuples.

Typical use:

    .venv/Scripts/python.exe \\
      packages/model/scripts/materialize_choice_features.py \\
      --sets TLA TMT \\
      --n-workers 8
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mulligan_coach_model.choice_feature_matrix import (
    materialize_choice_feature_matrix,
)
from mulligan_coach_model.choice_rows import (
    DEFAULT_MIN_N_GAMES_TO_JUDGE,
    DEFAULT_MIN_WIN_RATE,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DECISIONS_DIR = REPO_ROOT / "data" / "processed" / "seventeenlands" / "mulligan_decisions"
DEFAULT_CACHED_FEATURES_ROOT = REPO_ROOT / "data" / "processed" / "model_training"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data" / "processed" / "choice_training"


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
        "--decisions-dir",
        type=Path,
        default=DEFAULT_DECISIONS_DIR,
        help="Directory holding {SET}.{EVENT}.parquet decision files.",
    )
    ap.add_argument(
        "--cached-features-root",
        type=Path,
        default=DEFAULT_CACHED_FEATURES_ROOT,
        help=(
            "Win-model feature-cache root. Per-set sub-dir is "
            "{root}/{SET}/{EVENT}/. Pass an empty string to skip the "
            "cache entirely (slow: every kept hand will be re-simulated)."
        ),
    )
    ap.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Choice-feature output root. Per-set sub-dir is {root}/{SET}/{EVENT}/.",
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
        help="Optional cap on decision rows iterated (post-filter); for smoke tests.",
    )
    ap.add_argument(
        "--min-n-games-to-judge",
        type=int,
        default=DEFAULT_MIN_N_GAMES_TO_JUDGE,
        help="Skip players with at least this many games AND below --min-win-rate.",
    )
    ap.add_argument(
        "--min-win-rate",
        type=float,
        default=DEFAULT_MIN_WIN_RATE,
        help="Win-rate floor for the player-skill filter.",
    )
    args = ap.parse_args()

    use_cache = args.cached_features_root != Path()
    for set_code in args.sets:
        decisions = args.decisions_dir / f"{set_code}.{args.event_type}.parquet"
        if not decisions.exists():
            raise SystemExit(f"Decisions parquet missing: {decisions}")

        cached_dir: Path | None = None
        if use_cache:
            cached_dir = args.cached_features_root / set_code / args.event_type
            if not cached_dir.exists():
                logging.warning(
                    "Cached features dir missing (%s); will re-simulate every kept hand.",
                    cached_dir,
                )
                cached_dir = None

        out_dir = args.output_root / set_code / args.event_type
        logging.info("=== %s -> %s ===", set_code, out_dir)
        stats = materialize_choice_feature_matrix(
            decisions_parquet=decisions,
            cached_features_dir=cached_dir,
            output_dir=out_dir,
            set_code=set_code,
            event_type=args.event_type,
            n_sims_per_row=args.n_sims_per_row,
            n_workers=args.n_workers,
            chunksize=args.chunksize,
            chunk_rows=args.chunk_rows,
            resume=not args.no_resume,
            overwrite=args.overwrite,
            limit=args.limit,
            min_n_games_to_judge=args.min_n_games_to_judge,
            min_win_rate=args.min_win_rate,
        )
        logging.info(
            "%s: %d rows written (via_cache=%d, via_sim=%d, "
            "skipped_player_filter=%d, skipped_resume=%d)",
            set_code,
            stats.rows_written,
            stats.rows_via_cache,
            stats.rows_via_simulation,
            stats.choice_row_stats.skipped_player_filter,
            stats.rows_skipped_resume,
        )


if __name__ == "__main__":
    main()
