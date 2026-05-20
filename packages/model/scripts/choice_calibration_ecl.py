"""Calibrate the choice-model threshold on held-out ECL kept hands.

The choice model (``models/choice_v3``) was trained on TLA + TMT
mulligan decisions. ECL is held out: we have its win-model game-data
cache (one row per kept hand, with the actual game outcome) but no
17Lands replay data, so we can't compare predicted-keep vs the
player's actual decision on ECL. What we *can* do is:

1. Predict ``P(keep)`` for every cached ECL row (works because the
   feature schema is shared between the win and choice pipelines).
2. Compute the unconditional post-mulligan WR baseline from rows with
   ``mulligan_number >= 1`` — these are games the player did mull and
   then kept the resulting 6-card-or-smaller hand.
3. Bucket the ``mulligan_number == 0`` rows (i.e. hands the player
   decided to keep on the original 7) by ``P(keep)`` and report the
   observed WR per bucket. Where the bucket WR drops below the
   post-mull baseline, the player would have been better off mulling.

The natural binary-classifier threshold for the choice model is 0.5.
This script lets us see whether that threshold matches the empirical
break-even point on ECL, or whether the cutoff should be somewhere
else.

Run:
    .venv/Scripts/python.exe packages/model/scripts/choice_calibration_ecl.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v3"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "processed" / "model_training"


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("choice_calibration_ecl")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_choice_booster(model_dir: Path) -> tuple[xgb.Booster, list[str]]:
    """Load the booster + the feature-name order it was trained with."""
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    feature_names = list(meta["feature_names"])
    return booster, feature_names


def load_cache(cache_dir: Path, set_code: str, event_type: str) -> pd.DataFrame:
    src = cache_dir / set_code / event_type
    paths = sorted(src.glob("chunk_*.parquet"))
    if not paths:
        raise SystemExit(f"no chunks found under {src}")
    parts = [pd.read_parquet(p) for p in paths]
    return pd.concat(parts, ignore_index=True)


def report_bucket(
    df: pd.DataFrame,
    *,
    edges: np.ndarray,
    baseline: float,
    log: logging.Logger,
    label: str,
) -> None:
    """Print per-bucket count + WR + delta-vs-baseline."""
    log.info("\n%s", label)
    log.info(
        "  %-18s  %7s  %8s  %9s  %9s",
        "P(keep) bin",
        "n",
        "WR",
        "vs mull",
        "verdict",
    )
    n_bins = len(edges) - 1
    bucket = np.digitize(df["p_keep_choice"].to_numpy(), edges) - 1
    bucket = np.clip(bucket, 0, n_bins - 1)
    df = df.assign(_bucket=bucket)
    for i in range(n_bins):
        sub = df[df["_bucket"] == i]
        if sub.empty:
            continue
        wr = float(sub["won"].mean())
        diff = wr - baseline
        verdict = "keep > mull" if diff > 0 else "MULL > keep"
        log.info(
            "  [%5.2f, %5.2f)   %7d  %8.4f  %+9.4f  %9s",
            edges[i],
            edges[i + 1],
            len(sub),
            wr,
            diff,
            verdict,
        )


def report_quantile_buckets(
    df: pd.DataFrame,
    *,
    n_buckets: int,
    baseline: float,
    log: logging.Logger,
    label: str,
) -> None:
    """Equal-population buckets — more useful at the tails than equal-width."""
    log.info("\n%s", label)
    log.info(
        "  %-22s  %7s  %8s  %8s  %9s",
        "P(keep) range",
        "n",
        "WR",
        "mean p",
        "vs mull",
    )
    df = df.sort_values("p_keep_choice").reset_index(drop=True)
    n = len(df)
    edges_idx = np.linspace(0, n, n_buckets + 1).astype(int)
    for i in range(n_buckets):
        lo, hi = edges_idx[i], edges_idx[i + 1]
        sub = df.iloc[lo:hi]
        if sub.empty:
            continue
        wr = float(sub["won"].mean())
        mean_p = float(sub["p_keep_choice"].mean())
        log.info(
            "  [%6.4f, %6.4f]   %7d  %8.4f  %8.4f  %+9.4f",
            float(sub["p_keep_choice"].iloc[0]),
            float(sub["p_keep_choice"].iloc[-1]),
            len(sub),
            wr,
            mean_p,
            wr - baseline,
        )


def find_break_even(
    df: pd.DataFrame,
    *,
    baseline: float,
    log: logging.Logger,
) -> None:
    """Sweep the threshold and find where 'below-threshold WR' hits the baseline."""
    log.info("\n==== Threshold sweep (mn=0 kept hands) ====")
    log.info("Goal: find the P(keep) threshold T where, among mn=0 hands with P(keep) < T,")
    log.info("the observed WR equals the post-mull baseline %.4f.", baseline)
    log.info(
        "  %7s  %8s  %10s  %11s  %8s  %10s",
        "thresh",
        "n_below",
        "WR_below",
        "below-mull",
        "n_above",
        "WR_above",
    )
    sorted_df = df.sort_values("p_keep_choice").reset_index(drop=True)
    thresholds = np.arange(0.05, 0.96, 0.05)
    rows = []
    for t in thresholds:
        below = sorted_df[sorted_df["p_keep_choice"] < t]
        above = sorted_df[sorted_df["p_keep_choice"] >= t]
        wr_below = float(below["won"].mean()) if len(below) else float("nan")
        wr_above = float(above["won"].mean()) if len(above) else float("nan")
        rows.append((t, len(below), wr_below, wr_below - baseline, len(above), wr_above))
        log.info(
            "  %7.2f  %8d  %10.4f  %+11.4f  %8d  %10.4f",
            t,
            len(below),
            wr_below,
            wr_below - baseline,
            len(above),
            wr_above,
        )

    # Find where WR_below first crosses up through the baseline.
    sweep = pd.DataFrame(rows, columns=["t", "n_below", "wr_below", "diff", "n_above", "wr_above"])
    crossings = sweep[(sweep["diff"] >= 0) & (sweep["n_below"] >= 100)]
    if not crossings.empty:
        first = crossings.iloc[0]
        log.info(
            "\nFirst threshold where WR_below >= mull baseline (n_below >= 100): T=%.2f  "
            "WR_below=%.4f (n=%d)",
            float(first["t"]),
            float(first["wr_below"]),
            int(first["n_below"]),
        )
    else:
        log.info(
            "\nNo threshold in [0.05, 0.95] crosses up to the mull baseline — "
            "keep WR stays below mull-WR even for very low P(keep) bands."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", dest="set_code", default="ECL")
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument(
        "--n-quantile-buckets",
        type=int,
        default=20,
        help="Number of equal-population buckets for the mn=0 calibration table.",
    )
    ap.add_argument(
        "--log-name",
        default="choice_calibration_ecl.log",
        help="Log filename (saved into --choice-model-dir).",
    )
    args = ap.parse_args()

    log_path = args.choice_model_dir / args.log_name
    log = setup_logger(log_path)
    log.info("==== Choice-model calibration on ECL ====")
    log.info("Set:          %s (%s)", args.set_code, args.event_type)
    log.info("Choice model: %s", args.choice_model_dir)
    log.info("Cache dir:    %s", args.cache_dir)

    booster, feature_names = load_choice_booster(args.choice_model_dir)
    log.info("Loaded booster with %d features", len(feature_names))

    df = load_cache(args.cache_dir, args.set_code, args.event_type)
    log.info("Loaded %d rows from cache", len(df))

    missing = [c for c in feature_names if c not in df.columns]
    if missing:
        raise SystemExit(f"cache missing {len(missing)} feature columns: {missing[:5]}...")

    # Predict P(keep) in one DMatrix call.
    log.info("Running choice booster on all rows...")
    X = df[feature_names].to_numpy(dtype=np.float32)
    dmat = xgb.DMatrix(X, feature_names=feature_names)
    df["p_keep_choice"] = booster.predict(dmat)
    log.info("  done")

    # ---- Headline: WR by mulligan_number ----
    log.info("\n==== WR by mulligan_number (ECL) ====")
    log.info("  %-3s  %8s  %8s", "mn", "n", "WR")
    for mn, sub in df.groupby("mulligan_number"):
        log.info("  %-3d  %8d  %8.4f", int(mn), len(sub), float(sub["won"].mean()))
    log.info("  %-3s  %8d  %8.4f", "ALL", len(df), float(df["won"].mean()))

    mn0 = df[df["mulligan_number"] == 0].copy()
    mn_pos = df[df["mulligan_number"] >= 1]
    n_pos = len(mn_pos)
    if n_pos == 0:
        raise SystemExit("no mn>=1 rows in cache — can't compute mull baseline")
    mull_baseline = float(mn_pos["won"].mean())
    log.info(
        "\nUnconditional MULL baseline (WR across all mn>=1 kept hands): %.4f  (n=%d)",
        mull_baseline,
        n_pos,
    )
    log.info(
        "Naive 'kept original 7' WR (mn=0):                              %.4f  (n=%d)",
        float(mn0["won"].mean()),
        len(mn0),
    )

    # ---- P(keep) distribution ----
    log.info("\n==== Choice-model P(keep) distribution on ECL (all rows) ====")
    for q in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]:
        log.info("  q%02d: %.4f", int(q * 100), float(df["p_keep_choice"].quantile(q)))
    log.info("  mean: %.4f", float(df["p_keep_choice"].mean()))

    log.info("\n==== Choice-model P(keep) distribution on mn=0 only ====")
    for q in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]:
        log.info("  q%02d: %.4f", int(q * 100), float(mn0["p_keep_choice"].quantile(q)))
    log.info("  mean: %.4f", float(mn0["p_keep_choice"].mean()))

    # ---- Equal-width buckets, all rows (so user sees what each bucket looks like) ----
    edges = np.linspace(0.0, 1.0, 11)  # 10 buckets, 0.1 wide
    report_bucket(
        df,
        edges=edges,
        baseline=mull_baseline,
        log=log,
        label="==== Equal-width P(keep) buckets, ALL ECL rows (mn=0 + mn>=1) ====",
    )

    # ---- Equal-width buckets, mn=0 only (the keep-decisions we care about) ----
    report_bucket(
        mn0,
        edges=edges,
        baseline=mull_baseline,
        log=log,
        label="==== Equal-width P(keep) buckets, mn=0 KEPT hands only ====",
    )

    # ---- Equal-population buckets, mn=0 only — granular tails ----
    report_quantile_buckets(
        mn0,
        n_buckets=args.n_quantile_buckets,
        baseline=mull_baseline,
        log=log,
        label=f"==== Equal-population buckets, mn=0 kept hands (n_buckets={args.n_quantile_buckets}) ====",
    )

    # ---- Threshold sweep ----
    find_break_even(mn0, baseline=mull_baseline, log=log)

    # ---- Reference: 0.5 threshold ----
    below_50 = mn0[mn0["p_keep_choice"] < 0.5]
    above_50 = mn0[mn0["p_keep_choice"] >= 0.5]
    log.info("\n==== Reference: 0.5 threshold on mn=0 hands ====")
    log.info(
        "  P(keep) < 0.5 (model said MULL): n=%d  WR=%.4f  (%+.4f vs mull baseline)",
        len(below_50),
        float(below_50["won"].mean()),
        float(below_50["won"].mean()) - mull_baseline,
    )
    log.info(
        "  P(keep) >= 0.5 (model said keep): n=%d  WR=%.4f",
        len(above_50),
        float(above_50["won"].mean()),
    )

    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
