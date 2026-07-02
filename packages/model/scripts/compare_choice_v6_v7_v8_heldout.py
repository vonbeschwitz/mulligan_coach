"""Three-way score of choice_v6 / v7 / v8 on choice_v8's held-out rows.

Extends ``compare_choice_v6_v7_heldout.py`` to add v8. It reproduces
choice_v8's test split by re-running the exact ``_grouped_split``
(seed=0) over the current TLA+TMT+SOS / Premier+Trad choice_training
caches, then scores all three boosters on those rows.

READ THIS BEFORE TRUSTING THE CROSS-MODEL NUMBERS
-------------------------------------------------
This comparison is **confounded by train/test leakage for v6 and v7**,
and the confound is not fixable after the fact. ``_grouped_split`` is
permutation-index based (``rng.permutation(unique_draft_ids)`` then
slice by position), so a draft's split assignment depends on the number
and first-appearance order of unique draft_ids in the concatenated
frame. Both changed when TLA/TMT were re-materialised for v8 (row
counts moved, and the materialiser writes via ``imap_unordered`` so row
order isn't stable). Therefore:

* v8's test split != v7's split != v6's split.
* Much of what is now v8's *test* set was in v7's / v6's *training*
  set, so v7 and v6 score artificially low (leaked) here.
* The old TLA/TMT caches that defined v7's/v6's splits were overwritten,
  so those splits can't be reconstructed — there is no set of rows that
  is genuinely held-out for all three models anymore.

The tell is printed at the top of the run: v7 scores LOWER log-loss on
v8's test set than on v7's own honest held-out test (metadata.json). A
model can only beat its own held-out when it trained on the rows.

What you CAN conclude
---------------------
* Only the **v8** column is a genuine held-out estimate on these rows.
* For an honest v7-vs-v8 comparison, use each model's own
  ``metadata.json`` test metrics (printed below). They are on different
  (each model's own) held-out populations, but each is leakage-free.
* SOS features are byte-identical to v7's (only TLA/TMT were
  re-materialised), so a *future* clean comparison would require a
  materialisation-invariant split (hash draft_id -> bucket) and a
  retrain of both models under it.

No simulation is run here — cached 200-feature rows are scored directly.

Run:
    .venv/Scripts/python.exe packages/model/scripts/compare_choice_v6_v7_v8_heldout.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

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
V8_DIR = REPO_ROOT / "models" / "choice_v8"
LOG_PATH = V8_DIR / "compare_v6_v7_v8_heldout.log"

# MUST match tune_choice_v8.py exactly so the reproduced split is v8's.
SETS = ("TLA", "TMT", "SOS")
EVENT_TYPES = ("PremierDraft", "TradDraft")
VAL_FRAC = 0.10
TEST_FRAC = 0.10
SEED = 0


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("compare_v6_v7_v8")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def _load_model(model_dir: Path) -> tuple[xgb.Booster, list[str], int, dict[str, Any]]:
    """Return (booster, feature_names_in_training_order, best_iteration, metadata)."""
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    return booster, list(meta["feature_names"]), int(meta["best_iteration"]), meta


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
    log.info("==== choice_v6 vs v7 vs v8 — scored on v8's held-out test split ====")
    log.info("v6: %s", V6_DIR)
    log.info("v7: %s", V7_DIR)
    log.info("v8: %s", V8_DIR)

    # Reproduce tune_choice_v8.py's data load order exactly.
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
    log.info("Held-out test rows (v8's split): %d", len(test_df))

    v6_booster, v6_feats, v6_best, v6_meta = _load_model(V6_DIR)
    v7_booster, v7_feats, v7_best, v7_meta = _load_model(V7_DIR)
    v8_booster, v8_feats, v8_best, v8_meta = _load_model(V8_DIR)
    log.info("best_iteration: v6=%d  v7=%d  v8=%d", v6_best, v7_best, v8_best)

    y_all = test_df["was_kept"].astype(int).to_numpy()
    p6_all = _predict(v6_booster, v6_feats, v6_best, test_df)
    p7_all = _predict(v7_booster, v7_feats, v7_best, test_df)
    p8_all = _predict(v8_booster, v8_feats, v8_best, test_df)

    # --- Leakage tell + the only honest v7-vs-v8 comparison -------------
    log.info("\n#### HONEST per-model held-out test (each on its OWN split; leakage-free) ####")
    for name, meta in (("v6", v6_meta), ("v7", v7_meta), ("v8", v8_meta)):
        t = meta["test"]
        log.info(
            "  %s: ll=%.4f brier=%.4f acc=%.4f  n=%d",
            name,
            t["log_loss"],
            t["brier"],
            t["accuracy"],
            t["n_rows"],
        )
    v7_here = _compute_metrics(y_all, p7_all)
    log.info(
        "\n  LEAKAGE CHECK: v7 on v8's-test ll=%.4f vs v7 own-heldout ll=%.4f "
        "(v7 lower here => it trained on these rows; the cross-model table below "
        "is leakage-confounded for v6 & v7 — only the v8 column is honest).",
        v7_here.log_loss,
        v7_meta["test"]["log_loss"],
    )

    log.info(
        "\n%-14s %-7s %-8s | %-22s | %-22s | %-22s",
        "subset",
        "n",
        "keep_rt",
        "choice_v6 (LEAKED)",
        "choice_v7 (LEAKED)",
        "choice_v8 (honest)",
    )
    log.info("-" * 100)

    def report(label: str, mask: np.ndarray) -> None:
        y = y_all[mask]
        if len(y) == 0:
            log.info("%-14s %-7s skipped (no rows)", label, 0)
            return
        m6 = _compute_metrics(y, p6_all[mask])
        m7 = _compute_metrics(y, p7_all[mask])
        m8 = _compute_metrics(y, p8_all[mask])
        log.info(
            "%-14s %-7d %-8.4f | %-22s | %-22s | %-22s",
            label,
            len(y),
            float(y.mean()),
            _fmt(m6),
            _fmt(m7),
            _fmt(m8),
        )

    exp = test_df["expansion"].to_numpy()
    ev = test_df["event_type"].to_numpy()

    log.info("# By set (v6/v7 columns leakage-inflated; see header):")
    report("SOS (all)", exp == "SOS")
    report("SOS Premier", (exp == "SOS") & (ev == "PremierDraft"))
    report("SOS Trad", (exp == "SOS") & (ev == "TradDraft"))
    for s in ("TLA", "TMT"):
        report(f"{s} (all)", exp == s)
        report(f"{s} Premier", (exp == s) & (ev == "PremierDraft"))
        report(f"{s} Trad", (exp == s) & (ev == "TradDraft"))

    log.info("\n# Whole held-out set:")
    report("ALL", np.ones(len(test_df), dtype=bool))
    report("ALL Premier", ev == "PremierDraft")
    report("ALL Trad", ev == "TradDraft")

    log.info("\nWrote log to %s", LOG_PATH)


if __name__ == "__main__":
    main()
