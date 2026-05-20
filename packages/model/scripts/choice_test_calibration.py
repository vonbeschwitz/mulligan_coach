"""Decile-calibration of the choice model on its held-out test split.

The choice model is trained with a draft-grouped 80/10/10 split
(:mod:`mulligan_coach_model.choice_train`). The persisted metadata
records one scalar log-loss / Brier / accuracy per split, but doesn't
say anything about whether the predicted ``P(keep)`` is calibrated
across the probability spectrum.

This script reproduces the exact test split (same ``seed=0``, same
``val_frac=test_frac=0.10`` defaults that ``choice_v3`` was trained
with), runs the saved booster on those rows, and bins the test rows
into deciles of predicted ``P(keep)``. For each decile we report:

* mean predicted ``P(keep)``
* observed keep rate (the fraction of rows with ``was_kept=True``)
* the calibration gap (observed - predicted, in percentage points)
* the on-play / on-draw / mulligan-number breakdown so we can spot
  whether miscalibration is concentrated in any context bucket.

A well-calibrated decile-by-decile diagnostic should land each
decile's observed rate within sampling error (~1-2pp at n=6.7k per
decile) of its mean predicted probability.

Run:
    .venv/Scripts/python.exe packages/model/scripts/choice_test_calibration.py
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

# Reuse the exact split logic the training pipeline used so we get
# byte-identical row assignment to "test" as in the trained model.
from mulligan_coach_model.choice_train import _grouped_split

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v3"
DEFAULT_CACHE_DIR = REPO_ROOT / "data" / "processed" / "choice_training"
DEFAULT_SETS = ("TLA", "TMT")


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("choice_test_calibration")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_choice_training(
    cache_dir: Path,
    sets: tuple[str, ...],
    event_type: str,
) -> pd.DataFrame:
    """Concatenate every choice-training chunk for the given (set, event) pairs.

    Order matters here: the booster was trained on a `pd.concat` over
    the same `sorted(...)` chunk order, so reproducing that order is
    what lets the seeded grouped-split pick the same test draft_ids.
    """
    parts = []
    for set_code in sets:
        src = cache_dir / set_code / event_type
        paths = sorted(src.glob("chunk_*.parquet"))
        if not paths:
            raise SystemExit(f"no chunks under {src}")
        for p in paths:
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True)


def load_booster(model_dir: Path) -> tuple[xgb.Booster, list[str]]:
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    return booster, list(meta["feature_names"])


def report_deciles(df: pd.DataFrame, log: logging.Logger) -> None:
    """Bin test rows into deciles of predicted P(keep) and report calibration."""
    log.info(
        "\n%-7s  %-22s  %7s  %10s  %10s  %9s",
        "decile",
        "P(keep) range",
        "n",
        "mean pred",
        "obs keep",
        "gap (pp)",
    )

    # Use rank-based deciles so each bin has the same n. Quantile-based
    # bin edges can produce empty bins when the predicted distribution
    # is heavily skewed (which it is — see ECL calibration).
    df = df.sort_values("p_keep").reset_index(drop=True)
    n = len(df)
    edges = np.linspace(0, n, 11).astype(int)
    for i in range(10):
        lo, hi = edges[i], edges[i + 1]
        sub = df.iloc[lo:hi]
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        obs = float(sub["was_kept"].mean())
        gap_pp = (obs - mean_p) * 100.0
        log.info(
            "  D%-2d   [%6.4f, %6.4f]   %7d  %10.4f  %10.4f  %+9.2f",
            i + 1,
            float(sub["p_keep"].iloc[0]),
            float(sub["p_keep"].iloc[-1]),
            len(sub),
            mean_p,
            obs,
            gap_pp,
        )


def report_calibration_metrics(df: pd.DataFrame, log: logging.Logger) -> None:
    """Aggregate ECE / MCE (binned) — single-number calibration summary."""
    df = df.sort_values("p_keep").reset_index(drop=True)
    n = len(df)
    n_bins = 20
    edges = np.linspace(0, n, n_bins + 1).astype(int)
    weights = []
    abs_gaps = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        sub = df.iloc[lo:hi]
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        obs = float(sub["was_kept"].mean())
        weights.append(len(sub) / n)
        abs_gaps.append(abs(obs - mean_p))
    ece = float(sum(w * g for w, g in zip(weights, abs_gaps, strict=True)))
    mce = float(max(abs_gaps))
    log.info(
        "\nExpected calibration error (ECE, 20 equal-pop bins): %.4f  (=%.2f pp)",
        ece,
        ece * 100,
    )
    log.info("Max calibration error  (MCE, 20 equal-pop bins): %.4f  (=%.2f pp)", mce, mce * 100)


def report_by_context(df: pd.DataFrame, log: logging.Logger) -> None:
    """Calibration sliced by on_play and mulligan_number context."""
    log.info("\n==== Calibration by on_play ====")
    log.info("  %-8s  %7s  %10s  %10s  %9s", "split", "n", "mean pred", "obs keep", "gap (pp)")
    for on_play_flag in (True, False):
        sub = df[df["on_the_play"].astype(bool) == on_play_flag]
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        obs = float(sub["was_kept"].mean())
        log.info(
            "  %-8s  %7d  %10.4f  %10.4f  %+9.2f",
            "on play" if on_play_flag else "on draw",
            len(sub),
            mean_p,
            obs,
            (obs - mean_p) * 100.0,
        )

    log.info("\n==== Calibration by mulligan_number ====")
    log.info("  %-3s  %7s  %10s  %10s  %9s", "mn", "n", "mean pred", "obs keep", "gap (pp)")
    for mn, sub in df.groupby("mulligan_number"):
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        obs = float(sub["was_kept"].mean())
        log.info(
            "  %-3d  %7d  %10.4f  %10.4f  %+9.2f",
            int(mn),
            len(sub),
            mean_p,
            obs,
            (obs - mean_p) * 100.0,
        )

    log.info("\n==== Calibration by expansion ====")
    log.info("  %-5s  %7s  %10s  %10s  %9s", "set", "n", "mean pred", "obs keep", "gap (pp)")
    for set_code, sub in df.groupby("expansion"):
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        obs = float(sub["was_kept"].mean())
        log.info(
            "  %-5s  %7d  %10.4f  %10.4f  %+9.2f",
            str(set_code),
            len(sub),
            mean_p,
            obs,
            (obs - mean_p) * 100.0,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    ap.add_argument("--sets", nargs="+", default=list(DEFAULT_SETS))
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--log-name",
        default="choice_test_calibration.log",
        help="Log filename (saved into --choice-model-dir).",
    )
    args = ap.parse_args()

    log_path = args.choice_model_dir / args.log_name
    log = setup_logger(log_path)
    log.info("==== Choice-model test-set decile calibration ====")
    log.info("Choice model:  %s", args.choice_model_dir)
    log.info("Cache dir:     %s", args.cache_dir)
    log.info("Sets:          %s", args.sets)
    log.info("Event type:    %s", args.event_type)
    log.info(
        "Split:         val_frac=%.2f  test_frac=%.2f  seed=%d  (must match training)",
        args.val_frac,
        args.test_frac,
        args.seed,
    )

    booster, feature_names = load_booster(args.choice_model_dir)
    log.info("Loaded booster with %d features", len(feature_names))

    df = load_choice_training(args.cache_dir, tuple(args.sets), args.event_type)
    log.info("Loaded %d rows total (across %s)", len(df), args.sets)

    splits = _grouped_split(
        df["draft_id"],
        val_frac=args.val_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    n_train = int(splits.train.sum())
    n_val = int(splits.val.sum())
    n_test = int(splits.test.sum())
    log.info("Reproduced split rows: train=%d  val=%d  test=%d", n_train, n_val, n_test)

    # Sanity-check vs the stored metadata so we know we're truly looking at
    # the same test rows the trainer saw.
    meta = json.loads((args.choice_model_dir / "metadata.json").read_text())
    stored = {
        "train": int(meta["train"]["n_rows"]),
        "val": int(meta["val"]["n_rows"]),
        "test": int(meta["test"]["n_rows"]),
    }
    if (n_train, n_val, n_test) != (stored["train"], stored["val"], stored["test"]):
        log.warning(
            "Split row counts don't match metadata. Reproduced=%s  Stored=%s. "
            "Predictions will still bin sensibly, but the test set is not the "
            "exact set the trainer saw — check the cache contents / version.",
            (n_train, n_val, n_test),
            stored,
        )
    else:
        log.info("Reproduced split row counts match metadata exactly.")

    test_df = df.loc[splits.test].reset_index(drop=True)
    log.info(
        "Test rows: %d  observed keep_rate=%.4f",
        len(test_df),
        float(test_df["was_kept"].astype(int).mean()),
    )

    X = test_df[feature_names].astype(float).to_numpy()
    dmat = xgb.DMatrix(X, feature_names=feature_names)
    iter_range = (0, int(meta["best_iteration"]) + 1)
    test_df["p_keep"] = booster.predict(dmat, iteration_range=iter_range)

    # ---- Distribution of P(keep) on test ----
    log.info("\n==== P(keep) distribution on test set ====")
    for q in [0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95, 0.99]:
        log.info("  q%02d: %.4f", int(q * 100), float(test_df["p_keep"].quantile(q)))
    log.info("  mean: %.4f", float(test_df["p_keep"].mean()))

    log.info("\n==== Test-set decile calibration ====")
    report_deciles(test_df, log)
    report_calibration_metrics(test_df, log)
    report_by_context(test_df, log)

    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
