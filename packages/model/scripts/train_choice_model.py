"""Train the choice (keep/mull) XGBoost model from materialised features.

Thin CLI over
:func:`mulligan_coach_model.choice_train.train_choice_model`. Gathers
``chunk_*.parquet`` paths from each ``--sets`` directory under
``data/processed/choice_training/<SET>/<EVENT_TYPE>/`` and hands the
combined list to ``train_choice_model``.

Typical use:

    .venv/Scripts/python.exe packages/model/scripts/train_choice_model.py \\
        --sets TLA TMT \\
        --output-dir models/choice_tla_tmt_v1
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mulligan_coach_model.choice_feature_matrix import choice_parquet_paths
from mulligan_coach_model.choice_train import train_choice_model

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHOICE_TRAINING_DIR = REPO_ROOT / "data" / "processed" / "choice_training"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", nargs="+", required=True, help="Set codes to train on.")
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument(
        "--choice-training-dir",
        type=Path,
        default=DEFAULT_CHOICE_TRAINING_DIR,
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Where to write xgboost.json / metadata.json for the choice model.",
    )
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--n-estimators", type=int, default=500)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--learning-rate", type=float, default=0.05)
    ap.add_argument("--early-stopping-rounds", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    parquet_paths: list[Path] = []
    for set_code in args.sets:
        set_dir = args.choice_training_dir / set_code / args.event_type
        chunks = choice_parquet_paths(set_dir)
        if not chunks:
            raise SystemExit(
                f"No choice-feature chunk parquets found under {set_dir}; "
                f"run materialize_choice_features.py first."
            )
        logging.info("%s: %d chunk(s) under %s", set_code, len(chunks), set_dir)
        parquet_paths.extend(chunks)

    logging.info(
        "Training choice model on %d total chunk(s) across %d set(s); output -> %s",
        len(parquet_paths),
        len(args.sets),
        args.output_dir,
    )

    result = train_choice_model(
        parquet_paths=parquet_paths,
        output_dir=args.output_dir,
        val_frac=args.val_frac,
        test_frac=args.test_frac,
        n_estimators=args.n_estimators,
        max_depth=args.max_depth,
        learning_rate=args.learning_rate,
        early_stopping_rounds=args.early_stopping_rounds,
        seed=args.seed,
    )
    md = result.metadata
    logging.info(
        "test : log_loss=%.4f brier=%.4f acc=%.4f keep_rate=%.3f (n=%d)",
        md.test.log_loss,
        md.test.brier,
        md.test.accuracy,
        md.test.keep_rate,
        md.test.n_rows,
    )
    logging.info(
        "val  : log_loss=%.4f brier=%.4f acc=%.4f keep_rate=%.3f (n=%d)",
        md.val.log_loss,
        md.val.brier,
        md.val.accuracy,
        md.val.keep_rate,
        md.val.n_rows,
    )
    logging.info("best_iteration=%d  features=%d", md.best_iteration, len(md.feature_names))


if __name__ == "__main__":
    main()
