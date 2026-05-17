"""Compare post-hoc calibration methods on a fitted booster.

The booster + baseline are reused — only the calibration step changes.
We fit each method on the calibration split and score the held-out
test split, so the comparison is honest.

Methods:

* identity      — no calibration. Take the booster's raw probability
                  (with ``base_margin`` already baked in) as the final
                  prediction. XGBoost with ``binary:logistic`` tends to
                  be reasonably calibrated by default, so this is the
                  natural baseline to beat.
* platt         — two-parameter logistic on the logit of the raw prob:
                  ``p_cal = sigmoid(A * logit(p_raw) + B)``. Smooth,
                  never produces exact 0/1. Restricted to monotone
                  linear-in-logit shifts.
* isotonic_raw  — current production fit, non-parametric monotone
                  step function. Most flexible, but the PAV pooling
                  algorithm can pin tail bins to {0, 1} when the
                  calibration split happens to be all-loss/all-win
                  in that band.
* isotonic_002  — isotonic with ``y_min=0.02`` / ``y_max=0.98``.
* isotonic_005  — isotonic with ``y_min=0.05`` / ``y_max=0.95``.

Metrics on test split:

* log_loss (lower is better)
* brier (lower is better)
* accuracy
* ECE / MCE on 10 deciles of predicted probability
* prediction range + count of saturated predictions (== 0 / == 1)
* tail reliability: 5-bin reliability table on the bottom and top
  quintiles of predicted probability, so we can see tail behavior
  directly.

Output: ``<model-dir>/calibration_methods.log``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.feature_matrix import feature_parquet_paths
from mulligan_coach_model.train import _grouped_split, _per_row_base_margin
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

REPO_ROOT = Path(__file__).resolve().parents[3]

EPS = 1e-7


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("compare_calib")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def raw_probabilities(bundle: ModelBundle, df: pd.DataFrame) -> np.ndarray:
    """Booster output with base_margin baked in (no calibrator applied)."""
    feature_cols = list(bundle.feature_names)
    X = df[feature_cols].astype(np.float32).to_numpy()
    margins = _per_row_base_margin(df, bundle.baseline)
    dm = xgb.DMatrix(X, base_margin=margins, feature_names=feature_cols)
    return bundle.booster.predict(dm, iteration_range=(0, bundle.best_iteration + 1))


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def fit_platt(p_calib: np.ndarray, y_calib: np.ndarray) -> LogisticRegression:
    """Two-parameter Platt scaling on logit(raw)."""
    lr = LogisticRegression(C=1e10, solver="lbfgs")
    lr.fit(_logit(p_calib).reshape(-1, 1), y_calib.astype(int))
    return lr


def apply_platt(lr: LogisticRegression, p: np.ndarray) -> np.ndarray:
    return lr.predict_proba(_logit(p).reshape(-1, 1))[:, 1]


def fit_isotonic(
    p_calib: np.ndarray,
    y_calib: np.ndarray,
    y_min: float | None = None,
    y_max: float | None = None,
) -> IsotonicRegression:
    kw: dict = {"out_of_bounds": "clip", "increasing": True}
    if y_min is not None:
        kw["y_min"] = y_min
    if y_max is not None:
        kw["y_max"] = y_max
    iso = IsotonicRegression(**kw)
    iso.fit(p_calib, y_calib.astype(float))
    return iso


def metrics(y: np.ndarray, p: np.ndarray) -> dict:
    pc = np.clip(p, EPS, 1 - EPS)
    return {
        "n": len(y),
        "log_loss": float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()),
        "brier": float(((p - y) ** 2).mean()),
        "accuracy": float(((p >= 0.5).astype(int) == y).mean()),
        "min_p": float(p.min()),
        "max_p": float(p.max()),
        "n_exact_0": int((p == 0.0).sum()),
        "n_exact_1": int((p == 1.0).sum()),
        "n_below_002": int((p < 0.02).sum()),
        "n_above_098": int((p > 0.98).sum()),
        "mean_p": float(p.mean()),
        "actual": float(y.mean()),
    }


def ece_mce(p: np.ndarray, y: np.ndarray, n_bins: int = 10) -> tuple[float, float]:
    bins = pd.qcut(p, n_bins, labels=False, duplicates="drop")
    n = len(p)
    ece_v = 0.0
    mce_v = 0.0
    for b in np.unique(bins):
        m = bins == b
        nb = int(m.sum())
        if nb == 0:
            continue
        gap = abs(p[m].mean() - y[m].mean())
        ece_v += (nb / n) * gap
        if nb / n >= 0.01:
            mce_v = max(mce_v, gap)
    return float(ece_v), float(mce_v)


def reliability_rows(p: np.ndarray, y: np.ndarray, n_bins: int) -> list[dict]:
    """Per-bin reliability for the lowest and highest quintiles, n_bins each.

    Returns rows with ``bin``, ``n``, ``p_lo``, ``p_hi``, ``mean_p``,
    ``actual``, ``diff`` keys.
    """
    return _reliability(p, y, n_bins)


def _reliability(p: np.ndarray, y: np.ndarray, n_bins: int) -> list[dict]:
    bins = pd.qcut(p, n_bins, labels=False, duplicates="drop")
    out = []
    for b in sorted(np.unique(bins)):
        m = bins == b
        sp = p[m]
        sy = y[m]
        out.append(
            dict(
                bin=int(b) + 1,
                n=len(sp),
                p_lo=float(sp.min()),
                p_hi=float(sp.max()),
                mean_p=float(sp.mean()),
                actual=float(sy.mean()),
                diff=float(sp.mean() - sy.mean()),
            )
        )
    return out


def tail_reliability(p: np.ndarray, y: np.ndarray, n_bins_overall: int = 20) -> list[dict]:
    """Bin into 20 quantiles. Caller inspects top/bottom rows."""
    return _reliability(p, y, n_bins_overall)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--sets", nargs="+", required=True)
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument(
        "--model-training-dir",
        type=Path,
        default=REPO_ROOT / "data" / "processed" / "model_training",
    )
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--calib-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    log = setup_logger(args.model_dir / "calibration_methods.log")
    log.info(f"==== Comparing calibration methods on {args.model_dir} ====")

    # 1) Load same parquets in same order as training.
    paths = []
    for s in args.sets:
        d = args.model_training_dir / s / args.event_type
        paths.extend(feature_parquet_paths(d))
    log.info(f"Loading {len(paths)} parquet shards across {args.sets} ...")
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    log.info(f"  {len(df):,} total rows")

    splits = _grouped_split(
        df["draft_id"],
        val_frac=args.val_frac,
        calib_frac=args.calib_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    calib = df.loc[splits.calib].reset_index(drop=True)
    test = df.loc[splits.test].reset_index(drop=True)
    log.info(f"Calibration split: {len(calib):,} rows")
    log.info(f"Test split:        {len(test):,} rows")

    bundle = ModelBundle.load(args.model_dir)
    log.info(f"Bundle: {len(bundle.feature_names)} features  best_iter={bundle.best_iteration}")

    # 2) Raw probabilities (booster + base_margin) — calibrator NOT applied.
    log.info("Computing raw uncalibrated probabilities on calib + test ...")
    p_calib_raw = raw_probabilities(bundle, calib)
    p_test_raw = raw_probabilities(bundle, test)
    y_calib = calib["won"].astype(int).to_numpy()
    y_test = test["won"].astype(int).to_numpy()
    log.info(
        f"  raw calib range [{p_calib_raw.min():.4f}, {p_calib_raw.max():.4f}]  "
        f"raw test range [{p_test_raw.min():.4f}, {p_test_raw.max():.4f}]"
    )

    # 3) Fit each calibration method on the calibration split.
    log.info("\nFitting calibrators on calibration split ...")
    platt = fit_platt(p_calib_raw, y_calib)
    iso_raw = fit_isotonic(p_calib_raw, y_calib)
    iso_002 = fit_isotonic(p_calib_raw, y_calib, y_min=0.02, y_max=0.98)
    iso_005 = fit_isotonic(p_calib_raw, y_calib, y_min=0.05, y_max=0.95)
    a, b = float(platt.coef_[0][0]), float(platt.intercept_[0])
    log.info(f"  Platt: A={a:.4f}  B={b:.4f}  (p_cal = sigmoid(A * logit(p_raw) + B))")

    # 4) Apply each to test split.
    methods: dict[str, np.ndarray] = {
        "identity      (no cal)": p_test_raw,
        "platt         (2-param)": apply_platt(platt, p_test_raw).clip(0, 1),
        "isotonic_raw  (current)": iso_raw.predict(p_test_raw).clip(0, 1),
        "isotonic_002  (y in [0.02,0.98])": iso_002.predict(p_test_raw).clip(0, 1),
        "isotonic_005  (y in [0.05,0.95])": iso_005.predict(p_test_raw).clip(0, 1),
    }

    # 5) Summary metrics on test.
    log.info(f"\n========== TEST METRICS (n={len(y_test):,}) ==========")
    log.info(
        f"{'method':<34} {'log_loss':>9} {'brier':>8} {'acc':>6}  "
        f"{'ECE':>7} {'MCE':>7}  {'min_p':>7} {'max_p':>7}  {'==0':>4} {'==1':>4}  "
        f"{'<.02':>5} {'>.98':>5}"
    )
    for name, p in methods.items():
        m = metrics(y_test, p)
        e, mce = ece_mce(p, y_test)
        log.info(
            f"{name:<34} {m['log_loss']:>9.4f} {m['brier']:>8.4f} {m['accuracy']:>6.4f}  "
            f"{e:>7.4f} {mce:>7.4f}  {m['min_p']:>7.4f} {m['max_p']:>7.4f}  "
            f"{m['n_exact_0']:>4} {m['n_exact_1']:>4}  "
            f"{m['n_below_002']:>5} {m['n_above_098']:>5}"
        )

    # 6) Tail-focused reliability: 20-bin, show bottom 4 and top 4.
    for name, p in methods.items():
        log.info(f"\n--- 20-bin reliability for {name.strip()} (test) ---")
        rows = tail_reliability(p, y_test, n_bins_overall=20)
        log.info(
            f"  {'bin':>3} {'n':>7} {'p_lo':>7} {'p_hi':>7} {'mean_p':>9} {'actual':>9} {'diff':>9}"
        )
        # Show bottom 4 + top 4 bins to keep output focused on tails.
        focus = [*rows[:4], {"bin": -1}, *rows[-4:]]
        for r in focus:
            if r.get("bin") == -1:
                log.info("  ...")
                continue
            log.info(
                f"  {r['bin']:>3} {r['n']:>7} {r['p_lo']:>7.4f} {r['p_hi']:>7.4f} "
                f"{r['mean_p']:>9.4f} {r['actual']:>9.4f} {r['diff']:>+9.4f}"
            )

    log.info("\nDone.")
    log.info(f"Report saved to {args.model_dir / 'calibration_methods.log'}")


if __name__ == "__main__":
    main()
