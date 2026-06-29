"""Train choice_v7: choice_v6's sweep re-run with SOS folded into training.

This is a near-verbatim copy of ``tune_choice_long_run.py`` (the script
that produced ``models/choice_v6``) with one substantive change: the
training data now spans **TLA + TMT + SOS** instead of just TLA + TMT.
Everything else — the 6-config hyperparameter sweep, the grouped
``draft_id`` split at ``seed=0``, and the selection metric
(lowest PremierDraft validation log-loss) — is kept identical so the
two models are methodologically comparable.

Why re-run the whole sweep instead of just refitting choice_v6's
winning config (lr=0.02 / mcw=5 / subsample=0.8): adding a third
format shifts the data distribution, and the best config can move with
it. The sweep is cheap (~minutes per config), so we re-select rather
than assume.

Known caveat (intentional, per project owner decision)
------------------------------------------------------
The TLA/TMT choice-feature caches under
``data/processed/choice_training/{TLA,TMT}/`` were materialised on
2026-05-19, *before* simulator/parser changes #57 and #58 landed
(2026-05-21). The SOS caches were materialised fresh on 2026-06-27/28
with the current code. So TLA/TMT rows reflect an older simulator than
the SOS rows — the training set mixes two simulator versions. This is a
deliberate trade-off to avoid a ~17h-per-set re-materialisation of
TLA/TMT; revisit by re-materialising TLA/TMT if evaluation shows the
mismatch is hurting. See the SOS-vs-others distribution check in the
evaluation step.

A second, smaller caveat: the feature builder's one-hot ``set_code``
list (``known_sets``) does not include SOS, so SOS rows carry all-zero
set_code dummies (set_code_TLA/TMT/ECL = 0). That makes SOS the
reference category rather than giving it a named dummy — internally
consistent with inference (which passes set_code="SOS" -> same all-zero
encoding), just worth knowing when reading feature importances.

Run:
    .venv/Scripts/python.exe packages/model/scripts/tune_choice_v7.py
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
LOG_PATH = REPO_ROOT / "logs" / "tune_choice_v7.log"
OUTPUT_DIR = REPO_ROOT / "models" / "choice_v7"

# Only change vs choice_v6: SOS added to the training sets.
SETS = ("TLA", "TMT", "SOS")
EVENT_TYPES = ("PremierDraft", "TradDraft")

VAL_FRAC = 0.10
TEST_FRAC = 0.10
SEED = 0
N_ESTIMATORS = 8000
EARLY_STOPPING_ROUNDS = 50

# Same focused config set choice_v6 swept over.
CONFIGS = [
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 1, "subsample": 0.8},
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 5, "subsample": 0.8},
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 1, "subsample": 1.0},
    {"max_depth": 6, "learning_rate": 0.03, "min_child_weight": 5, "subsample": 1.0},
    {"max_depth": 6, "learning_rate": 0.02, "min_child_weight": 1, "subsample": 0.8},
    {"max_depth": 6, "learning_rate": 0.02, "min_child_weight": 5, "subsample": 0.8},
]


def setup_logger() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("tune_choice_v7")
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
    log.info("==== choice_v7 sweep (choice_v6 methodology + SOS) ====")
    log.info("Output dir: %s", OUTPUT_DIR)
    log.info("Sets: %s  Event types: %s", SETS, EVENT_TYPES)
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
            if not chunks:
                raise SystemExit(
                    f"No chunks under {CACHE_ROOT / set_code / event_type}; "
                    f"run materialize_choice_features.py for {set_code} {event_type} first."
                )
            for p in chunks:
                parts.append(pd.read_parquet(p))
    df = pd.concat(parts, ignore_index=True)
    feature_names = _feature_columns(df.columns)
    log.info("Total rows=%d  features=%d", len(df), len(feature_names))
    log.info(
        "Per-set rows: %s",
        {s: int((df["expansion"] == s).sum()) for s in SETS},
    )

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

    log.info("\nSweep finished in %.1f min", (time.time() - t_all) / 60.0)

    assert best_booster is not None and best_config is not None
    log.info("\n==== Best config (lowest Premier-val log_loss) ====")
    log.info("  id=%d  config=%s", best_idx, best_config)
    log.info("  best_iteration=%d  premier_val_log_loss=%.4f", best_best_iter, best_premier_val_ll)

    # Final metrics for best.
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
                "sets": list(SETS),
                "event_types": list(EVENT_TYPES),
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
