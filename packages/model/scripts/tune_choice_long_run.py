"""Follow-up sweep: re-run the top v5 configs with a much higher boost cap.

The v5 sweep (``tune_choice_hyperparams.py``) capped ``n_estimators``
at 2000. The best depth=6 / lr=0.03 configs all hit ``best_iteration``
in the 1989-1999 range with ``early_stopping_rounds=30`` never firing,
which means training ran out before validation log-loss plateaued.
This script re-trains those configs (plus two even-slower-lr variants)
with ``n_estimators=8000`` and ``early_stopping_rounds=50`` so early
stopping has room to actually trigger.

Uses the same seed=0 grouped split as v4 / v5 so test rows are held
fixed across the sweep. Selection metric: Premier-val log-loss
(same as ``tune_choice_hyperparams.py``). Best model is saved as
``models/choice_v6/``.

Run:
    .venv/Scripts/python.exe packages/model/scripts/tune_choice_long_run.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

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
LOG_PATH = REPO_ROOT / "logs" / "tune_choice_v6.log"
OUTPUT_DIR = REPO_ROOT / "models" / "choice_v6"

SETS = ("TLA", "TMT")
EVENT_TYPES = ("PremierDraft", "TradDraft")

VAL_FRAC = 0.10
TEST_FRAC = 0.10
SEED = 0
N_ESTIMATORS = 8000
EARLY_STOPPING_ROUNDS = 50

# Focused set: configs that capped on v5 + slower-lr variants to check
# whether even more, even smaller steps help.
CONFIGS = [
    # The v5 winner.
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 1, "subsample": 0.8},
    # Tied-for-second on v5.
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 5, "subsample": 0.8},
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 1, "subsample": 1.0},
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 5, "subsample": 1.0},
    # Slower lr — if 0.03 was helping, 0.02 might help more.
    {"max_depth": 6, "learning_rate": 0.02, "min_child_weight": 1, "subsample": 0.8},
    {"max_depth": 6, "learning_rate": 0.02, "min_child_weight": 5, "subsample": 0.8},
]


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("tune_choice_long")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def main() -> None:
    log = setup_logger()
    log.info("==== Choice-model long-run follow-up sweep ====")
    log.info("Output dir: %s", OUTPUT_DIR)
    log.info("Configs to run: %d", len(CONFIGS))
    log.info(
        "n_estimators=%d  early_stopping=%d  seed=%d", N_ESTIMATORS, EARLY_STOPPING_ROUNDS, SEED
    )

    log.info("\nLoading data...")
    parts: list[pd.DataFrame] = []
    for set_code in SETS:
        for event_type in EVENT_TYPES:
            chunks = sorted((CACHE_ROOT / set_code / event_type).glob("chunk_*.parquet"))
            log.info("  %s/%s: %d chunks", set_code, event_type, len(chunks))
            for p in chunks:
                parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True)
    feature_names = _feature_columns(df.columns)
    log.info("Total rows=%d  features=%d", len(df), len(feature_names))

    splits = _grouped_split(df["draft_id"], val_frac=VAL_FRAC, test_frac=TEST_FRAC, seed=SEED)
    log.info(
        "Split rows: train=%d val=%d test=%d",
        int(splits.train.sum()),
        int(splits.val.sum()),
        int(splits.test.sum()),
    )

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
    mv_p = ev_val == "PremierDraft"
    mv_t = ev_val == "TradDraft"
    mt_p = ev_test == "PremierDraft"
    mt_t = ev_test == "TradDraft"

    log.info(
        "\n%-3s | %-7s %-5s %-3s %-6s | %5s | %9s %9s %9s | %9s %9s %9s | best_it",
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

    best_premier_val_ll = float("inf")
    best_idx = -1
    best_booster: xgb.Booster | None = None
    best_best_iter = -1
    best_config: dict[str, float] | None = None
    results: list[dict[str, object]] = []

    t_all = time.time()
    for i, cfg in enumerate(CONFIGS):
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

        v_a = _compute_metrics(y_val, p_val)
        v_p = _compute_metrics(y_val[mv_p], p_val[mv_p])
        v_t = _compute_metrics(y_val[mv_t], p_val[mv_t])
        t_a = _compute_metrics(y_test, p_test)
        t_p = _compute_metrics(y_test[mt_p], p_test[mt_p])
        t_t = _compute_metrics(y_test[mt_t], p_test[mt_t])
        secs = time.time() - t0

        log.info(
            "%-3d | %-7.3f %-5d %-3d %-6.2f | %5.1f | %9.4f %9.4f %9.4f | %9.4f %9.4f %9.4f | %d",
            i,
            cfg["learning_rate"],
            int(cfg["max_depth"]),
            int(cfg["min_child_weight"]),
            cfg["subsample"],
            secs,
            v_a.log_loss,
            v_p.log_loss,
            v_t.log_loss,
            t_a.log_loss,
            t_p.log_loss,
            t_t.log_loss,
            best_iter,
        )
        results.append(
            {
                "id": i,
                "config": cfg,
                "best_iteration": best_iter,
                "val_log_loss": v_a.log_loss,
                "val_premier_log_loss": v_p.log_loss,
                "val_trad_log_loss": v_t.log_loss,
                "test_log_loss": t_a.log_loss,
                "test_premier_log_loss": t_p.log_loss,
                "test_trad_log_loss": t_t.log_loss,
                "secs": secs,
            }
        )

        if v_p.log_loss < best_premier_val_ll:
            best_premier_val_ll = v_p.log_loss
            best_booster = booster
            best_best_iter = best_iter
            best_config = cfg
            best_idx = i

    log.info("\nLong-run sweep finished in %.1f min", (time.time() - t_all) / 60.0)

    assert best_booster is not None and best_config is not None
    log.info("\n==== Best config (lowest Premier-val log_loss) ====")
    log.info("  id=%d  config=%s", best_idx, best_config)
    log.info("  best_iteration=%d  premier_val_log_loss=%.4f", best_best_iter, best_premier_val_ll)

    # Final test metrics for best.
    ir = (0, best_best_iter + 1)
    p_test = best_booster.predict(dtest, iteration_range=ir)
    p_train = best_booster.predict(dtrain, iteration_range=ir)
    p_val = best_booster.predict(dval, iteration_range=ir)
    t_a = _compute_metrics(y_test, p_test)
    t_p = _compute_metrics(y_test[mt_p], p_test[mt_p])
    t_t = _compute_metrics(y_test[mt_t], p_test[mt_t])
    log.info("\nTest metrics for best model:")
    log.info(
        "  ALL     n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f",
        t_a.n_rows,
        t_a.log_loss,
        t_a.brier,
        t_a.accuracy,
    )
    log.info(
        "  Premier n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f",
        t_p.n_rows,
        t_p.log_loss,
        t_p.brier,
        t_p.accuracy,
    )
    log.info(
        "  Trad    n=%d  log_loss=%.4f  brier=%.4f  acc=%.4f",
        t_t.n_rows,
        t_t.log_loss,
        t_t.brier,
        t_t.accuracy,
    )

    metadata = ChoiceTrainingMetadata(
        feature_names=tuple(feature_names),
        train=_compute_metrics(y[splits.train], p_train),
        val=_compute_metrics(y_val, p_val),
        test=t_a,
        best_iteration=best_best_iter,
        seed=SEED,
    )
    save_choice_train_result(ChoiceTrainResult(booster=best_booster, metadata=metadata), OUTPUT_DIR)
    (OUTPUT_DIR / "sweep_results.json").write_text(
        json.dumps(
            {
                "configs": CONFIGS,
                "fixed": {
                    "n_estimators": N_ESTIMATORS,
                    "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
                    "val_frac": VAL_FRAC,
                    "test_frac": TEST_FRAC,
                    "seed": SEED,
                },
                "best": {"id": best_idx, "config": best_config, "best_iteration": best_best_iter},
                "results": results,
            },
            indent=2,
        )
    )
    log.info("\nWrote model to %s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
