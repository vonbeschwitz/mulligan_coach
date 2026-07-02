"""Train one unified XGBoost model across multiple sets' parquet shards.

Thin CLI over :func:`mulligan_coach_model.train_model`. Gathers
``chunk_*.parquet`` paths from each ``--sets`` directory under
``data/processed/model_training/<SET>/<EVENT_TYPE>/`` and hands the
combined list to ``train_model``, which already concatenates
parquets internally.

Typical use:

    .venv/Scripts/python.exe packages/model/scripts/train_multi_set.py \\
        --sets TLA ECL TMT \\
        --output-dir models/all3_v2
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mulligan_coach_model import train_model
from mulligan_coach_model.feature_matrix import feature_parquet_paths

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_MODEL_TRAINING_DIR = REPO_ROOT / "data" / "processed" / "model_training"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", nargs="+", required=True, help="Set codes to train on.")
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument(
        "--model-training-dir",
        type=Path,
        default=DEFAULT_MODEL_TRAINING_DIR,
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Where to write baseline.json / xgboost.json / metadata.json.",
    )
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--calib-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--n-estimators", type=int, default=500)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--learning-rate", type=float, default=0.05)
    ap.add_argument("--early-stopping-rounds", type=int, default=20)
    ap.add_argument("--baseline-l2-c", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help="Train even if some shard's _meta.json pipeline versions differ "
        "from the live simulator/feature code (recorded in metadata.json). "
        "Default refuses the mix.",
    )
    args = ap.parse_args()

    parquet_paths: list[Path] = []
    for set_code in args.sets:
        set_dir = args.model_training_dir / set_code / args.event_type
        chunks = feature_parquet_paths(set_dir)
        if not chunks:
            raise SystemExit(f"No chunk parquets found under {set_dir}")
        logging.info("%s: %d chunk(s) under %s", set_code, len(chunks), set_dir)
        parquet_paths.extend(chunks)

    logging.info(
        "Training on %d total chunk(s) across %d set(s); output -> %s",
        len(parquet_paths),
        len(args.sets),
        args.output_dir,
    )

    result = train_model(
        parquet_paths=parquet_paths,
        output_dir=args.output_dir,
        val_frac=args.val_frac,
        calib_frac=args.calib_frac,
        test_frac=args.test_frac,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        learning_rate=args.learning_rate,
        early_stopping_rounds=args.early_stopping_rounds,
        baseline_l2_C=args.baseline_l2_c,
        seed=args.seed,
        allow_version_mismatch=args.allow_version_mismatch,
    )
    md = result.metadata
    logging.info(
        "test  : log_loss=%.4f brier=%.4f acc=%.4f (n=%d)",
        md.test.log_loss,
        md.test.brier,
        md.test.accuracy,
        md.test.n_rows,
    )
    logging.info(
        "val   : log_loss=%.4f brier=%.4f acc=%.4f (n=%d)",
        md.val.log_loss,
        md.val.brier,
        md.val.accuracy,
        md.val.n_rows,
    )
    logging.info("best_iteration=%d  features=%d", md.best_iteration, len(md.feature_names))


if __name__ == "__main__":
    main()
