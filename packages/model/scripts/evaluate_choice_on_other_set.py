"""Evaluate a trained choice model on a different set's mulligan-decision parquet.

Mirror of :mod:`evaluate_on_other_set` for the choice (keep/mull)
pipeline. Loads the materialised choice-feature chunks for
``--target-set``, batch-predicts P(keep) with the model from
``--model-dir``, and reports calibration + accuracy against the
recorded ``was_kept`` labels.

Outputs go to ``<model_dir>/transfer_choice_<target_set>.log``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xgboost as xgb
from mulligan_coach_model.choice_inference import ChoiceModelBundle

REPO_ROOT = Path(__file__).resolve().parents[3]


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("transfer_choice_eval")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_parquet_dir(parquet_dir: Path) -> pd.DataFrame:
    chunks = sorted(parquet_dir.glob("chunk_*.parquet"))
    if not chunks:
        raise FileNotFoundError(f"No chunk_*.parquet files in {parquet_dir}")
    frames: list[pd.DataFrame] = []
    for c in chunks:
        frames.append(pq.read_table(c).to_pandas())  # type: ignore[no-untyped-call]
    return pd.concat(frames, ignore_index=True)


def predict_through_bundle(
    bundle: ChoiceModelBundle, df: pd.DataFrame, batch_size: int = 50_000
) -> np.ndarray:
    feature_cols = list(bundle.feature_names)
    n = len(df)
    out = np.empty(n, dtype=float)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        sub = df.iloc[start:end]
        X = sub[feature_cols].astype(np.float32).to_numpy()
        dm = xgb.DMatrix(X, feature_names=feature_cols)
        out[start:end] = bundle.booster.predict(dm, iteration_range=(0, bundle.best_iteration + 1))
    return out


def split_metrics(y_true: np.ndarray, p_pred: np.ndarray) -> dict[str, float]:
    eps = 1e-7
    p = np.clip(p_pred, eps, 1.0 - eps)
    log_loss = float(-(y_true * np.log(p) + (1 - y_true) * np.log(1 - p)).mean())
    brier = float(((p - y_true) ** 2).mean())
    accuracy = float(((p >= 0.5).astype(int) == y_true).mean())
    return {"log_loss": log_loss, "brier": brier, "accuracy": accuracy}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
        help="Trained choice-model directory (e.g. models/choice_tla_v3).",
    )
    parser.add_argument(
        "--target-set",
        type=str,
        required=True,
        help="Set code of the choice parquet to score against (e.g. TMT).",
    )
    parser.add_argument("--event-type", type=str, default="PremierDraft")
    args = parser.parse_args()

    parquet_dir = (
        REPO_ROOT / "data" / "processed" / "choice_training" / args.target_set / args.event_type
    )
    log_path = args.model_dir / f"transfer_choice_{args.target_set}.log"
    log = setup_logger(log_path)

    log.info(f"Loading choice model from {args.model_dir} ...")
    bundle = ChoiceModelBundle.load(args.model_dir)
    log.info(f"  {len(bundle.feature_names)} features; best_iter={bundle.best_iteration}")

    log.info(
        f"\nLoading {args.target_set}/{args.event_type} choice parquets from {parquet_dir} ..."
    )
    t0 = time.time()
    df = load_parquet_dir(parquet_dir)
    log.info(f"  {len(df):,} rows in {time.time() - t0:.1f}s")

    missing_cols = [c for c in bundle.feature_names if c not in df.columns]
    if missing_cols:
        log.warning(f"  WARNING: {len(missing_cols)} feature columns missing from parquet:")
        for c in missing_cols[:10]:
            log.warning(f"    - {c}")

    log.info(f"\nPredicting on {len(df):,} rows...")
    t0 = time.time()
    p = predict_through_bundle(bundle, df)
    df["p_keep_pred"] = p
    log.info(f"  done in {time.time() - t0:.1f}s")

    # ---- Overall metrics ----
    y = df["was_kept"].astype(int).to_numpy()
    overall = split_metrics(y, p)
    log.info(
        f"\n==== Transfer choice evaluation: {args.model_dir.name} -> "
        f"{args.target_set}/{args.event_type} ===="
    )
    log.info(f"n={len(df):,}  base keep_rate={y.mean():.4f}  mean_pred={p.mean():.4f}")
    log.info(
        f"log_loss = {overall['log_loss']:.4f}  "
        f"brier = {overall['brier']:.4f}  "
        f"accuracy = {overall['accuracy']:.4f}"
    )

    # ---- Stratified by mulligan_number ----
    log.info("\nStratified by mulligan_number (candidate-hand mulligan level):")
    log.info(f"{'mull':>4} {'n':>8} {'keep_rate':>10} {'mean_p':>9} {'gap':>8} {'log_loss':>10}")
    for m in sorted(df["mulligan_number"].unique()):
        sub = df[df["mulligan_number"] == m]
        if len(sub) == 0:
            continue
        sy = sub["was_kept"].astype(int).to_numpy()
        sp = sub["p_keep_pred"].to_numpy()
        bm = split_metrics(sy, sp)
        log.info(
            f"{int(m):>4} {len(sub):>8} {sy.mean():>10.4f} {sp.mean():>9.4f} "
            f"{sp.mean() - sy.mean():>+8.4f} {bm['log_loss']:>10.4f}"
        )

    # ---- Decile calibration on mulligan_number == 0 hands ----
    n_m0 = int((df["mulligan_number"] == 0).sum())
    log.info(f"\nDecile calibration on mulligan_number == 0 hands (n={n_m0:,}):")
    sub = df[df["mulligan_number"] == 0].copy()
    sub["decile"] = pd.qcut(sub["p_keep_pred"], 10, labels=False, duplicates="drop")
    log.info(f"{'dec':>3} {'n':>7} {'p_lo':>7} {'p_hi':>7} {'mean_p':>9} {'actual':>9} {'diff':>8}")
    for d in sorted(sub["decile"].unique()):
        s = sub[sub["decile"] == d]
        sp = s["p_keep_pred"]
        sy = s["was_kept"].astype(int)
        log.info(
            f"{int(d) + 1:>3} {len(s):>7} {sp.min():>7.4f} {sp.max():>7.4f} "
            f"{sp.mean():>9.4f} {sy.mean():>9.4f} {sp.mean() - sy.mean():>+8.4f}"
        )

    # ---- In-domain reference ----
    md_path = args.model_dir / "metadata.json"
    if md_path.exists():
        import json

        meta = json.loads(md_path.read_text())
        test = meta.get("test", {})
        if test:
            log.info(
                f"\nFor reference, in-domain test metrics (from {md_path.name}):\n"
                f"  log_loss = {test.get('log_loss'):.4f}  "
                f"brier = {test.get('brier'):.4f}  "
                f"accuracy = {test.get('accuracy'):.4f}  "
                f"n = {test.get('n_rows'):,}"
            )
            log.info(
                f"  delta (target - in-domain): "
                f"log_loss = {overall['log_loss'] - test.get('log_loss', 0):+.4f}  "
                f"brier = {overall['brier'] - test.get('brier', 0):+.4f}"
            )

    log.info(f"\nResult written to {log_path}")


if __name__ == "__main__":
    main()
