"""Evaluate a trained model on a different set's materialised parquet.

Quick cross-format transfer check: load the chunk parquets for
``--target-set`` (e.g., ECL), batch-predict with the model trained
on ``--model-dir`` (e.g., ``models/tla_v2/`` trained on TLA),
compare to recorded outcomes.

We don't recompute features — the materialised parquet already
carries them. So this is a fast scoring pass: ~30 seconds for
~300k rows on the standard chunk layout.

Caveats inherent to cross-format transfer:

* One-hot context columns (``set_code_TLA``, ``set_code_ECL`` etc.)
  were always 1.0 for the training set and 0.0 elsewhere in
  single-format training, so the model treats the off-format
  one-hots as a constant zero feature it never observed varying.
  No special handling — XGBoost is robust to it.
* Per-card 17Lands z-scores were computed in *target_set's* own
  format distribution at materialisation time. "z = 1.5" still
  means "1.5 standard deviations above the format's own mean shrunk WR" — interpretation
  is consistent across formats; transferability is the question.
* Some target_set rows may still be missing (an in-progress
  materialisation, or rows whose decks contain still-`NEEDS_LLM`
  bonus-sheet cards). The evaluation is on whatever's currently
  in the parquet directory.

Outputs go to ``<model_dir>/transfer_<target_set>.log``.
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
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.train import _per_row_base_margin

REPO_ROOT = Path(__file__).resolve().parents[3]


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("transfer_eval")
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
        frames.append(
            pq.read_table(c).to_pandas()  # type: ignore[no-untyped-call]
        )
    return pd.concat(frames, ignore_index=True)


def predict_through_bundle(
    bundle: ModelBundle, df: pd.DataFrame, batch_size: int = 50_000
) -> np.ndarray:
    """Run feature columns through baseline + booster.

    Processed in row-batches so a 300k-row parquet doesn't allocate a
    single 500 MiB float64 array. Per-batch baseline margins are
    computed against a slice; that's the same path the training loop
    uses on its full dataframe.
    """
    feature_cols = list(bundle.feature_names)
    n = len(df)
    out = np.empty(n, dtype=float)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        sub = df.iloc[start:end]
        # float32 is enough for XGBoost input and halves the allocation.
        X = sub[feature_cols].astype(np.float32).to_numpy()
        margins = _per_row_base_margin(sub, bundle.baseline)
        dm = xgb.DMatrix(X, base_margin=margins, feature_names=feature_cols)
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
        default=REPO_ROOT / "models" / "tla_v2",
        help="Trained model directory (default: models/tla_v2).",
    )
    parser.add_argument(
        "--target-set",
        type=str,
        default="ECL",
        help="Set code of the parquet to score (default: ECL).",
    )
    parser.add_argument(
        "--event-type",
        type=str,
        default="PremierDraft",
        help="Event type subdir (default: PremierDraft).",
    )
    args = parser.parse_args()

    parquet_dir = (
        REPO_ROOT / "data" / "processed" / "model_training" / args.target_set / args.event_type
    )
    log_path = args.model_dir / f"transfer_{args.target_set}.log"
    log = setup_logger(log_path)

    log.info(f"Loading model from {args.model_dir} ...")
    bundle = ModelBundle.load(args.model_dir)
    log.info(f"  {len(bundle.feature_names)} features; best_iter={bundle.best_iteration}")

    log.info(f"\nLoading {args.target_set}/{args.event_type} parquets from {parquet_dir} ...")
    t0 = time.time()
    df = load_parquet_dir(parquet_dir)
    log.info(f"  {len(df):,} rows in {time.time() - t0:.1f}s")
    n_chunks = len(sorted(parquet_dir.glob("chunk_*.parquet")))
    log.info(f"  ({n_chunks} chunk files)")

    # Sanity: confirm every required feature column is present.
    missing_cols = [c for c in bundle.feature_names if c not in df.columns]
    if missing_cols:
        log.warning(f"  WARNING: {len(missing_cols)} feature columns missing from parquet:")
        for c in missing_cols[:10]:
            log.warning(f"    - {c}")
        log.warning(
            "  Predictions will substitute NaN for those columns (XGBoost's missing-value path)."
        )

    log.info(f"\nPredicting on {len(df):,} rows...")
    t0 = time.time()
    p = predict_through_bundle(bundle, df)
    df["p_pred"] = p
    log.info(f"  done in {time.time() - t0:.1f}s")

    # ---- Overall metrics ----
    y = df["won"].astype(int).to_numpy()
    overall = split_metrics(y, p)
    log.info(
        f"\n==== Transfer evaluation: {args.model_dir.name} -> "
        f"{args.target_set}/{args.event_type} ===="
    )
    log.info(f"n={len(df):,}  base WR={y.mean():.4f}  mean_pred={p.mean():.4f}")
    log.info(
        f"log_loss = {overall['log_loss']:.4f}  "
        f"brier = {overall['brier']:.4f}  "
        f"accuracy = {overall['accuracy']:.4f}"
    )

    # ---- Stratified by mulligan_number ----
    log.info("\nStratified by mulligan_number:")
    log.info(f"{'mull':>4} {'n':>8} {'base_wr':>9} {'mean_p':>9} {'gap':>8} {'log_loss':>10}")
    for m in sorted(df["mulligan_number"].unique()):
        sub = df[df["mulligan_number"] == m]
        if len(sub) == 0:
            continue
        sy = sub["won"].astype(int).to_numpy()
        sp = sub["p_pred"].to_numpy()
        bm = split_metrics(sy, sp)
        log.info(
            f"{int(m):>4} {len(sub):>8} {sy.mean():>9.4f} {sp.mean():>9.4f} "
            f"{sp.mean() - sy.mean():>+8.4f} {bm['log_loss']:>10.4f}"
        )

    # ---- Decile calibration on kept-7 hands (largest cell) ----
    log.info(
        f"\nDecile calibration on mulligan_number == 0 hands (n="
        f"{int((df['mulligan_number'] == 0).sum()):,}):"
    )
    sub = df[df["mulligan_number"] == 0].copy()
    sub["decile"] = pd.qcut(sub["p_pred"], 10, labels=False, duplicates="drop")
    log.info(f"{'dec':>3} {'n':>7} {'p_lo':>7} {'p_hi':>7} {'mean_p':>9} {'actual':>9} {'diff':>8}")
    for d in sorted(sub["decile"].unique()):
        s = sub[sub["decile"] == d]
        sp = s["p_pred"]
        sy = s["won"].astype(int)
        log.info(
            f"{int(d) + 1:>3} {len(s):>7} {sp.min():>7.4f} {sp.max():>7.4f} "
            f"{sp.mean():>9.4f} {sy.mean():>9.4f} {sp.mean() - sy.mean():>+8.4f}"
        )

    # ---- Reference: in-domain (TLA) test split metrics ----
    log.info(
        "\nFor reference, TLA test split metrics from training (per metadata.json): "
        f"log_loss={bundle.booster.attributes().get('best_score', '?')}"
    )
    md = bundle.baseline  # noqa: F841 — kept for future expansion
    md_path = args.model_dir / "metadata.json"
    if md_path.exists():
        import json

        meta = json.loads(md_path.read_text())
        test = meta.get("test", {})
        if test:
            log.info(
                f"  In-domain test log_loss = {test.get('log_loss'):.4f}  "
                f"brier = {test.get('brier'):.4f}  "
                f"accuracy = {test.get('accuracy'):.4f}  "
                f"n = {test.get('n_rows'):,}"
            )
            delta_ll = overall["log_loss"] - test.get("log_loss", 0)
            delta_brier = overall["brier"] - test.get("brier", 0)
            log.info(
                f"  delta (target - in-domain): "
                f"log_loss = {delta_ll:+.4f}  brier = {delta_brier:+.4f}"
            )

    log.info(f"\nResult written to {log_path}")


if __name__ == "__main__":
    main()
