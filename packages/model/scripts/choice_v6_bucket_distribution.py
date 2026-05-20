"""Quick distribution of P(keep) predictions from choice_v6 across the proposed
overlay decision buckets.

Buckets (in the overlay, the displayed number is the **mulligan** score = 1 - p_keep):
    clear keep      : p_keep > 0.75   (mull score < 25)
    marginal keep   : 0.50 < p_keep <= 0.75
    marginal mull   : 0.25 < p_keep <= 0.50
    clear mull      : p_keep <= 0.25

Reports the bucket distribution overall and split by (event_type, mulligan_number)
so we can sanity-check the spread before wiring the overlay.

Run:
    .venv/Scripts/python.exe packages/model/scripts/choice_v6_bucket_distribution.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import xgboost as xgb
from mulligan_coach_model.choice_train import _grouped_split

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = REPO_ROOT / "models" / "choice_v6"
CACHE_ROOT = REPO_ROOT / "data" / "processed" / "choice_training"
SETS = ("TLA", "TMT")
EVENT_TYPES = ("PremierDraft", "TradDraft")


def bucket(p_keep: float) -> str:
    if p_keep > 0.75:
        return "clear_keep"
    if p_keep > 0.50:
        return "marginal_keep"
    if p_keep > 0.25:
        return "marginal_mull"
    return "clear_mull"


def main() -> None:
    booster = xgb.Booster()
    booster.load_model(str(MODEL_DIR / "xgboost.json"))
    meta = json.loads((MODEL_DIR / "metadata.json").read_text())
    feature_names = list(meta["feature_names"])
    best_iter = int(meta["best_iteration"])

    parts = []
    for set_code in SETS:
        for ev in EVENT_TYPES:
            for p in sorted((CACHE_ROOT / set_code / ev).glob("chunk_*.parquet")):
                parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True)
    print(f"Loaded {len(df):,} rows")

    splits = _grouped_split(df["draft_id"], val_frac=0.10, test_frac=0.10, seed=0)
    test_df = df.loc[splits.test].reset_index(drop=True)
    print(f"Test rows: {len(test_df):,}")

    X = test_df[feature_names].astype(float).to_numpy()
    dmat = xgb.DMatrix(X, feature_names=feature_names)
    test_df["p_keep"] = booster.predict(dmat, iteration_range=(0, best_iter + 1))
    test_df["mull_score_pct"] = (1.0 - test_df["p_keep"]) * 100.0
    test_df["bucket"] = test_df["p_keep"].map(bucket)

    print("\n==== Overall (test set) ====")
    print(f"mean p_keep = {test_df['p_keep'].mean():.4f}")
    n = len(test_df)
    counts = test_df["bucket"].value_counts()
    for b in ("clear_keep", "marginal_keep", "marginal_mull", "clear_mull"):
        c = int(counts.get(b, 0))
        print(f"  {b:<14s}  n={c:>6d}  {c / n * 100:5.2f}%")

    print("\n==== By event_type x mulligan_number ====")
    print(
        f"  {'event':<13s} {'mn':>3s}  {'n':>7s}  {'CK%':>6s} {'MK%':>6s} {'MM%':>6s} {'CM%':>6s}  {'kept%':>6s}"
    )
    for (ev, mn), sub in test_df.groupby(["event_type", "mulligan_number"], observed=True):
        n = len(sub)
        counts = sub["bucket"].value_counts()
        ck = counts.get("clear_keep", 0) / n * 100
        mk = counts.get("marginal_keep", 0) / n * 100
        mm = counts.get("marginal_mull", 0) / n * 100
        cm = counts.get("clear_mull", 0) / n * 100
        kept = float(sub["was_kept"].astype(int).mean()) * 100
        print(
            f"  {ev:<13s} {int(mn):>3d}  {n:>7d}  {ck:>5.1f}% {mk:>5.1f}% {mm:>5.1f}% {cm:>5.1f}%  {kept:>5.1f}%"
        )

    print("\n==== Bucket vs observed keep behaviour ====")
    print(
        f"  {'bucket':<14s}  {'n':>7s}  {'mean p_keep':>11s}  {'obs kept%':>9s}  {'agreement%':>10s}"
    )
    for b in ("clear_keep", "marginal_keep", "marginal_mull", "clear_mull"):
        sub = test_df[test_df["bucket"] == b]
        if sub.empty:
            continue
        mean_p = float(sub["p_keep"].mean())
        kept = float(sub["was_kept"].astype(int).mean())
        agree = kept if b.endswith("keep") else (1 - kept)
        print(
            f"  {b:<14s}  {len(sub):>7d}  {mean_p:>11.4f}  {kept * 100:>8.2f}%  {agree * 100:>9.2f}%"
        )

    print("\n==== Mull-score percentiles (mulligan-display value, 0-100) ====")
    pct = test_df["mull_score_pct"]
    for q in (1, 5, 10, 25, 50, 75, 90, 95, 99):
        print(f"  q{q:>2d}: {float(pct.quantile(q / 100)):>6.2f}")
    print(f"  mean: {float(pct.mean()):>6.2f}")


if __name__ == "__main__":
    main()
