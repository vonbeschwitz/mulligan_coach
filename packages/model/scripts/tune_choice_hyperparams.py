"""Hyperparameter sweep for the combined Premier+Trad choice model.

Goal: find a config that matches the Premier-only choice_v3's
in-domain log-loss while preserving the TradDraft gains from
choice_v4. The selection metric is **Premier-subset validation
log-loss** (not overall), so the chosen hyperparameters don't get
pulled toward the easier Trad slice.

Reproduces v4's grouped (train/val/test) split with seed=0 so test
rows stay held out across the sweep. Trains each config on train,
picks ``best_iteration`` against val, evaluates on test only for
the final reporting line. Final model (best config) is re-fit
exactly the same way and saved to ``models/choice_v5/`` plus a
sweep log written to ``logs/tune_choice_v5.log``.

Run:
    .venv/Scripts/python.exe packages/model/scripts/tune_choice_hyperparams.py
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
import time
from pathlib import Path
from typing import cast

import pandas as pd
import xgboost as xgb
from mulligan_coach_model.choice_train import (
    ChoiceTrainingMetadata,
    ChoiceTrainResult,
    _compute_metrics,
    _feature_columns,
    _grouped_split,
    save_choice_train_result,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CACHE_ROOT = REPO_ROOT / "data" / "processed" / "choice_training"
LOG_PATH = REPO_ROOT / "logs" / "tune_choice_v5.log"
OUTPUT_DIR = REPO_ROOT / "models" / "choice_v5"

SETS = ("TLA", "TMT")
EVENT_TYPES = ("PremierDraft", "TradDraft")

# Fixed split + early-stopping params for the sweep — same as v4 training.
VAL_FRAC = 0.10
TEST_FRAC = 0.10
SEED = 0
N_ESTIMATORS = 2000
EARLY_STOPPING_ROUNDS = 30

# Grid. Kept modest: 3*3*2*2 = 36 configs at ~30s each ~ 18 min.
# Explicit ``list[float]`` per slot so mypy can resolve
# ``itertools.product(*grid.values())`` against its variadic overloads
# — otherwise GRID is inferred as ``dict[str, list[int] | list[float]]``
# and the per-key values come out typed as ``object``.
GRID: dict[str, list[float]] = {
    "max_depth": [4.0, 6.0, 8.0],
    "learning_rate": [0.03, 0.05, 0.08],
    "min_child_weight": [1.0, 5.0],
    "subsample": [1.0, 0.8],
}


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("tune_choice")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_data(log: logging.Logger) -> pd.DataFrame:
    """Concatenate every choice-feature chunk across (set, event_type).

    Order matters: must match how train_choice_model.py loads chunks so
    the grouped-split assigns the same draft_ids to test as v4 did.
    train_choice_model iterates outer=set, inner=event_type.
    """
    parts: list[pd.DataFrame] = []
    for set_code in SETS:
        for event_type in EVENT_TYPES:
            chunks = sorted((CACHE_ROOT / set_code / event_type).glob("chunk_*.parquet"))
            if not chunks:
                raise SystemExit(f"No chunks under {CACHE_ROOT / set_code / event_type}")
            log.info("  loading %s/%s: %d chunks", set_code, event_type, len(chunks))
            for p in chunks:
                parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True)
    log.info("  total rows: %d", len(df))
    return df


def main() -> None:
    log = setup_logger()
    log.info("==== Choice-model hyperparameter sweep ====")
    log.info("Output dir (final model): %s", OUTPUT_DIR)
    log.info("Log: %s", LOG_PATH)
    log.info("Sets: %s, Events: %s", SETS, EVENT_TYPES)
    log.info("Grid: %s", GRID)
    log.info(
        "Fixed: n_estimators=%d, early_stop=%d, seed=%d", N_ESTIMATORS, EARLY_STOPPING_ROUNDS, SEED
    )

    log.info("\nLoading data...")
    df = load_data(log)
    feature_names = _feature_columns(df.columns)
    log.info("  feature columns: %d", len(feature_names))

    splits = _grouped_split(df["draft_id"], val_frac=VAL_FRAC, test_frac=TEST_FRAC, seed=SEED)
    n_train = int(splits.train.sum())
    n_val = int(splits.val.sum())
    n_test = int(splits.test.sum())
    log.info("Split rows: train=%d val=%d test=%d", n_train, n_val, n_test)

    # Pre-build numpy arrays once (X is the heaviest piece).
    log.info("Building feature arrays...")
    X = df[feature_names].astype(float).to_numpy()
    y = df["was_kept"].astype(int).to_numpy()
    ev = df["event_type"].to_numpy()

    dtrain = xgb.DMatrix(X[splits.train], label=y[splits.train], feature_names=feature_names)
    dval = xgb.DMatrix(X[splits.val], label=y[splits.val], feature_names=feature_names)
    dtest = xgb.DMatrix(X[splits.test], label=y[splits.test], feature_names=feature_names)

    y_val = y[splits.val]
    y_test = y[splits.test]
    ev_val = ev[splits.val]
    ev_test = ev[splits.test]
    mask_val_premier = ev_val == "PremierDraft"
    mask_val_trad = ev_val == "TradDraft"
    mask_test_premier = ev_test == "PremierDraft"
    mask_test_trad = ev_test == "TradDraft"

    keys = list(GRID.keys())
    configs = list(itertools.product(*[GRID[k] for k in keys]))
    log.info("\nSweeping %d configs...", len(configs))
    log.info(
        "%-3s | %-7s %-5s %-3s %-6s | %5s | %9s %9s %9s | %9s %9s %9s | best_it",
        "id",
        "lr",
        "depth",
        "mcw",
        "subs",
        "secs",
        "val_ll",
        "val_p_ll",
        "val_t_ll",
        "test_ll",
        "test_p_ll",
        "test_t_ll",
    )

    results: list[dict[str, object]] = []
    best_premier_val_ll = float("inf")
    best_config: dict[str, float] | None = None
    best_booster: xgb.Booster | None = None
    best_best_iter: int = -1
    best_idx: int = -1

    t_start = time.time()
    for i, combo in enumerate(configs):
        cfg = dict(zip(keys, combo, strict=True))
        params = {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "tree_method": "hist",
            "max_depth": int(cfg["max_depth"]),
            "eta": float(cfg["learning_rate"]),
            "min_child_weight": float(cfg["min_child_weight"]),
            "subsample": float(cfg["subsample"]),
            "seed": SEED,
        }
        t0 = time.time()
        booster = xgb.train(
            params,
            dtrain,
            num_boost_round=N_ESTIMATORS,
            evals=[(dtrain, "train"), (dval, "val")],
            early_stopping_rounds=EARLY_STOPPING_ROUNDS,
            verbose_eval=False,
        )
        best_iter = int(booster.best_iteration)
        ir = (0, best_iter + 1)
        p_val = booster.predict(dval, iteration_range=ir)
        p_test = booster.predict(dtest, iteration_range=ir)

        val_all = _compute_metrics(y_val, p_val)
        val_p = _compute_metrics(y_val[mask_val_premier], p_val[mask_val_premier])
        val_t = _compute_metrics(y_val[mask_val_trad], p_val[mask_val_trad])
        test_all = _compute_metrics(y_test, p_test)
        test_p = _compute_metrics(y_test[mask_test_premier], p_test[mask_test_premier])
        test_t = _compute_metrics(y_test[mask_test_trad], p_test[mask_test_trad])

        secs = time.time() - t0
        log.info(
            "%-3d | %-7.3f %-5d %-3d %-6.2f | %5.1f | %9.4f %9.4f %9.4f | %9.4f %9.4f %9.4f | %d",
            i,
            cfg["learning_rate"],
            int(cfg["max_depth"]),
            int(cfg["min_child_weight"]),
            cfg["subsample"],
            secs,
            val_all.log_loss,
            val_p.log_loss,
            val_t.log_loss,
            test_all.log_loss,
            test_p.log_loss,
            test_t.log_loss,
            best_iter,
        )

        results.append(
            {
                "id": i,
                "config": cfg,
                "best_iteration": best_iter,
                "val_log_loss": val_all.log_loss,
                "val_premier_log_loss": val_p.log_loss,
                "val_trad_log_loss": val_t.log_loss,
                "test_log_loss": test_all.log_loss,
                "test_premier_log_loss": test_p.log_loss,
                "test_trad_log_loss": test_t.log_loss,
                "secs": secs,
            }
        )

        # Selection by Premier-val log_loss — matches the user's stated goal.
        if val_p.log_loss < best_premier_val_ll:
            best_premier_val_ll = val_p.log_loss
            best_config = cfg
            best_booster = booster
            best_best_iter = best_iter
            best_idx = i

    total_secs = time.time() - t_start
    log.info("\nSweep finished in %.1f min", total_secs / 60.0)

    assert best_booster is not None and best_config is not None
    log.info("\n==== Best config (lowest Premier-val log_loss) ====")
    log.info("  id=%d  config=%s", best_idx, best_config)
    log.info("  best_iteration=%d  premier_val_log_loss=%.4f", best_best_iter, best_premier_val_ll)

    # Sort by Premier val ll to show the leaderboard.
    log.info("\n==== Top 10 by Premier-val log_loss ====")
    log.info(
        "%-3s %-7s %-5s %-3s %-6s %9s %9s %9s %9s",
        "id",
        "lr",
        "depth",
        "mcw",
        "subs",
        "v_p_ll",
        "v_t_ll",
        "t_p_ll",
        "t_t_ll",
    )
    for r in sorted(results, key=lambda r: float(r["val_premier_log_loss"]))[:10]:  # type: ignore[arg-type]
        c = cast("dict[str, float]", r["config"])
        log.info(
            "%-3d %-7.3f %-5d %-3d %-6.2f %9.4f %9.4f %9.4f %9.4f",
            r["id"],
            c["learning_rate"],
            int(c["max_depth"]),
            int(c["min_child_weight"]),
            c["subsample"],
            r["val_premier_log_loss"],
            r["val_trad_log_loss"],
            r["test_premier_log_loss"],
            r["test_trad_log_loss"],
        )

    # Final eval on test split using the best booster
    log.info("\n==== Final test metrics for best config ====")
    ir = (0, best_best_iter + 1)
    p_test = best_booster.predict(dtest, iteration_range=ir)
    test_all = _compute_metrics(y_test, p_test)
    test_p = _compute_metrics(y_test[mask_test_premier], p_test[mask_test_premier])
    test_t = _compute_metrics(y_test[mask_test_trad], p_test[mask_test_trad])
    log.info(
        "  ALL     n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f  keep_rate=%.4f",
        test_all.n_rows,
        test_all.log_loss,
        test_all.brier,
        test_all.accuracy,
        test_all.keep_rate,
    )
    log.info(
        "  Premier n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f  keep_rate=%.4f",
        test_p.n_rows,
        test_p.log_loss,
        test_p.brier,
        test_p.accuracy,
        test_p.keep_rate,
    )
    log.info(
        "  Trad    n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f  keep_rate=%.4f",
        test_t.n_rows,
        test_t.log_loss,
        test_t.brier,
        test_t.accuracy,
        test_t.keep_rate,
    )

    # Persist the best booster as choice_v5.
    p_train = best_booster.predict(dtrain, iteration_range=ir)
    train_all = _compute_metrics(y[splits.train], p_train)
    val_all_best = _compute_metrics(y_val, best_booster.predict(dval, iteration_range=ir))

    metadata = ChoiceTrainingMetadata(
        feature_names=tuple(feature_names),
        train=train_all,
        val=val_all_best,
        test=test_all,
        best_iteration=best_best_iter,
        seed=SEED,
    )
    result = ChoiceTrainResult(booster=best_booster, metadata=metadata)
    save_choice_train_result(result, OUTPUT_DIR)

    # Save sweep results alongside the model for provenance.
    sweep_dump = {
        "grid": GRID,
        "fixed": {
            "n_estimators": N_ESTIMATORS,
            "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
            "val_frac": VAL_FRAC,
            "test_frac": TEST_FRAC,
            "seed": SEED,
        },
        "best": {"id": best_idx, "config": best_config, "best_iteration": best_best_iter},
        "results": results,
    }
    (OUTPUT_DIR / "sweep_results.json").write_text(json.dumps(sweep_dump, indent=2))
    log.info("\nWrote model to %s", OUTPUT_DIR)
    log.info("Wrote sweep_results.json + xgboost.json + metadata.json")


if __name__ == "__main__":
    main()
