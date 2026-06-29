"""Apples-to-apples comparison of choice_v6 vs choice_v7 on held-out rows.

choice_v7 = choice_v6's methodology + SOS folded into training. The two
models' own ``metadata.json`` test metrics are NOT directly comparable
because their test populations differ: v7's test split now contains SOS
rows, which are intrinsically harder (lower keep rate -> more mulligan
decisions -> higher log-loss). A larger aggregate log-loss for v7 can
therefore reflect a harder test set, not a worse model.

This script removes that confound by scoring BOTH models on the SAME
rows: v7's held-out (test-split) rows, reproduced bit-for-bit by
re-running the exact ``_grouped_split`` (seed=0) over the exact data
concatenation that ``tune_choice_v7.py`` used.

Cleanliness of each per-set comparison
--------------------------------------
* **SOS** — fully clean. v7 never trained on these specific test-split
  drafts; v6 never trained on *any* SOS. This is the headline number:
  does folding SOS into training help on unseen SOS hands?
* **TLA / TMT** — confounded in v6's favour. v6 used a different split
  (no SOS draft_ids in the permutation), so ~80% of v7's test-split
  TLA/TMT drafts were in v6's *training* set. v6 therefore has a
  leakage advantage on these rows. Read TLA/TMT as: "does v7 hold up
  even against a leak-advantaged v6?" — a tie or win is strong; a
  small loss is expected from the confound, not necessarily regression.

No simulation is run here — we score the cached 200-feature rows
directly with each booster, so it's fast.

Run:
    .venv/Scripts/python.exe packages/model/scripts/compare_choice_v6_v7_heldout.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from mulligan_coach_model.choice_train import (
    SplitMetrics,
    _compute_metrics,
    _grouped_split,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_ROOT = REPO_ROOT / "data" / "processed" / "choice_training"
V6_DIR = REPO_ROOT / "models" / "choice_v6"
V7_DIR = REPO_ROOT / "models" / "choice_v7"
LOG_PATH = V7_DIR / "compare_v6_v7_heldout.log"

# MUST match tune_choice_v7.py exactly so the reproduced split is identical.
SETS = ("TLA", "TMT", "SOS")
EVENT_TYPES = ("PremierDraft", "TradDraft")
VAL_FRAC = 0.10
TEST_FRAC = 0.10
SEED = 0


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("compare_v6_v7")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def _load_model(model_dir: Path) -> tuple[xgb.Booster, list[str], int]:
    """Return (booster, feature_names_in_training_order, best_iteration)."""
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    return booster, list(meta["feature_names"]), int(meta["best_iteration"])


def _predict(
    booster: xgb.Booster,
    feature_names: list[str],
    best_iteration: int,
    df_rows: pd.DataFrame,
) -> np.ndarray:
    """Predict P(keep) for df_rows, aligning columns to the booster's order."""
    X = df_rows[feature_names].astype(float).to_numpy()
    dm = xgb.DMatrix(X, feature_names=feature_names)
    return booster.predict(dm, iteration_range=(0, best_iteration + 1))


def _fmt(m: SplitMetrics) -> str:
    return f"ll={m.log_loss:.4f} brier={m.brier:.4f} acc={m.accuracy:.4f}"


def main() -> None:
    log = setup_logger()
    log.info("==== choice_v6 vs choice_v7 — held-out (v7 test-split) comparison ====")
    log.info("v6: %s", V6_DIR)
    log.info("v7: %s", V7_DIR)

    # Reproduce tune_choice_v7.py's data load order exactly.
    parts: list[pd.DataFrame] = []
    for set_code in SETS:
        for event_type in EVENT_TYPES:
            chunks = sorted((CACHE_ROOT / set_code / event_type).glob("chunk_*.parquet"))
            if not chunks:
                raise SystemExit(f"No chunks under {CACHE_ROOT / set_code / event_type}")
            for p in chunks:
                parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True)
    log.info("Loaded %d rows", len(df))

    splits = _grouped_split(df["draft_id"], val_frac=VAL_FRAC, test_frac=TEST_FRAC, seed=SEED)
    test_df = df.loc[splits.test].reset_index(drop=True)
    log.info("Held-out test rows: %d", len(test_df))

    v6_booster, v6_feats, v6_best = _load_model(V6_DIR)
    v7_booster, v7_feats, v7_best = _load_model(V7_DIR)
    log.info("v6 best_iteration=%d  v7 best_iteration=%d", v6_best, v7_best)
    if set(v6_feats) != set(v7_feats):
        log.warning(
            "Feature-name sets differ between v6 and v7 (v6-only=%s, v7-only=%s); "
            "predictions still align each model to its own order.",
            set(v6_feats) - set(v7_feats),
            set(v7_feats) - set(v6_feats),
        )

    y_all = test_df["was_kept"].astype(int).to_numpy()
    p6_all = _predict(v6_booster, v6_feats, v6_best, test_df)
    p7_all = _predict(v7_booster, v7_feats, v7_best, test_df)

    log.info(
        "\n%-14s %-7s %-9s | %-30s | %-30s | %-9s",
        "subset",
        "n",
        "keep_rt",
        "choice_v6 (no SOS in train)",
        "choice_v7 (+SOS in train)",
        "d_logloss",
    )
    log.info("-" * 116)

    def report(label: str, mask: np.ndarray) -> None:
        y = y_all[mask]
        if len(y) == 0:
            log.info("%-14s %-7s skipped (no rows)", label, 0)
            return
        m6 = _compute_metrics(y, p6_all[mask])
        m7 = _compute_metrics(y, p7_all[mask])
        d = m7.log_loss - m6.log_loss  # negative => v7 better
        flag = "  v7 better" if d < 0 else ("  v6 better" if d > 0 else "")
        log.info(
            "%-14s %-7d %-9.4f | %-30s | %-30s | %+8.4f%s",
            label,
            len(y),
            float(y.mean()),
            _fmt(m6),
            _fmt(m7),
            d,
            flag,
        )

    exp = test_df["expansion"].to_numpy()
    ev = test_df["event_type"].to_numpy()

    # Headline: SOS (clean for both models).
    log.info("# CLEAN comparison (neither model has a leakage edge):")
    report("SOS (all)", exp == "SOS")
    report("SOS Premier", (exp == "SOS") & (ev == "PremierDraft"))
    report("SOS Trad", (exp == "SOS") & (ev == "TradDraft"))

    # Confounded: v6 has a leakage advantage on TLA/TMT (different split).
    log.info("\n# CONFOUNDED (v6 leak-advantaged on these — see docstring):")
    for s in ("TLA", "TMT"):
        report(f"{s} (all)", exp == s)
    report("TLA+TMT (all)", (exp == "TLA") | (exp == "TMT"))

    # Whole held-out set, for completeness (mixes clean + confounded).
    log.info("\n# Whole held-out set (mixed clean/confounded):")
    report("ALL", np.ones(len(test_df), dtype=bool))

    log.info("\nWrote log to %s", LOG_PATH)


if __name__ == "__main__":
    main()
