"""Detailed calibration / fit analysis of a trained mulligan model.

Loads the same parquets used in training (in the same set order), reconstructs
the test split via the seeded grouped-by-draft-id shuffle, runs predictions
through the ModelBundle, and reports calibration / metric tables across many
slices: overall, per set, per mulligan number, per on-the-play / opp-mull,
per deck colour count, per hand role presence, per hand colour stress,
per simulator-feature bucket, per hand-quality quintile.

Output: <model-dir>/calibration_analysis.log

Why use the test split (not all rows)
-------------------------------------
The booster has seen the train rows during fitting and the calibrator was
fit on the calibration split, so calibration on those splits is not honest.
The test split is the held-out portion, and matches the numbers reported
in metadata.json.

Conventions
-----------
* "gap" = mean_predicted - actual_winrate. Positive => model is over-
  predicting in that slice (P(win) too high vs reality), negative =>
  under-predicting.
* ECE = sum_i (n_i / N) |mean_p_i - actual_i| across deciles of predicted
  probability. MCE = max over bins with at least 1% of data. Lower is
  better; ~0.01 or below is excellent on a binary classifier at this
  data scale.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.feature_matrix import feature_parquet_paths
from mulligan_coach_model.train import _grouped_split, _per_row_base_margin

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("calibration")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


# ---------------------------------------------------------------------------
# Prediction + metric helpers
# ---------------------------------------------------------------------------


def predict_through_bundle(
    bundle: ModelBundle, df: pd.DataFrame, batch_size: int = 50_000
) -> np.ndarray:
    """Run feature columns through baseline + booster."""
    feature_cols = list(bundle.feature_names)
    n = len(df)
    out = np.empty(n, dtype=float)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        sub = df.iloc[start:end]
        X = sub[feature_cols].astype(np.float32).to_numpy()
        margins = _per_row_base_margin(sub, bundle.baseline)
        dm = xgb.DMatrix(X, base_margin=margins, feature_names=feature_cols)
        out[start:end] = bundle.booster.predict(dm, iteration_range=(0, bundle.best_iteration + 1))
    return out


def slice_metrics(y_true: np.ndarray, p_pred: np.ndarray) -> dict[str, float]:
    """Compute one row of summary metrics for a slice.

    Returns NaNs for empty slices; never raises.
    """
    n = len(y_true)
    if n == 0:
        return {
            "n": 0,
            "mean_p": float("nan"),
            "actual": float("nan"),
            "gap": float("nan"),
            "log_loss": float("nan"),
            "brier": float("nan"),
            "accuracy": float("nan"),
        }
    eps = 1e-7
    p_clipped = np.clip(p_pred, eps, 1.0 - eps)
    return {
        "n": n,
        "mean_p": float(p_pred.mean()),
        "actual": float(y_true.mean()),
        "gap": float(p_pred.mean() - y_true.mean()),
        "log_loss": float(
            -(y_true * np.log(p_clipped) + (1 - y_true) * np.log(1 - p_clipped)).mean()
        ),
        "brier": float(((p_pred - y_true) ** 2).mean()),
        "accuracy": float(((p_pred >= 0.5).astype(int) == y_true).mean()),
    }


def reliability_table(p_pred: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> pd.DataFrame:
    """Tabular reliability diagram by predicted-probability quantile."""
    bins = pd.qcut(p_pred, n_bins, labels=False, duplicates="drop")
    rows = []
    for b in sorted(np.unique(bins)):
        mask = bins == b
        sp = p_pred[mask]
        sy = y_true[mask]
        rows.append(
            {
                "bin": int(b) + 1,
                "n": len(sp),
                "p_lo": float(sp.min()),
                "p_hi": float(sp.max()),
                "mean_p": float(sp.mean()),
                "actual": float(sy.mean()),
                "diff": float(sp.mean() - sy.mean()),
            }
        )
    return pd.DataFrame(rows)


def ece_mce(p_pred: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> tuple[float, float]:
    """Expected and maximum calibration error.

    MCE is restricted to bins that hold at least 1% of the data so a tiny
    tail bin doesn't dominate the number.
    """
    bins = pd.qcut(p_pred, n_bins, labels=False, duplicates="drop")
    n = len(p_pred)
    ece_v = 0.0
    mce_v = 0.0
    for b in np.unique(bins):
        mask = bins == b
        n_b = int(mask.sum())
        if n_b == 0:
            continue
        gap = abs(p_pred[mask].mean() - y_true[mask].mean())
        ece_v += (n_b / n) * gap
        if n_b / n >= 0.01:
            mce_v = max(mce_v, gap)
    return float(ece_v), float(mce_v)


def log_reliability(log: logging.Logger, p_pred: np.ndarray, y_true: np.ndarray) -> None:
    """Pretty-print a 10-decile reliability table + ECE/MCE."""
    df = reliability_table(p_pred, y_true, n_bins=10)
    log.info(
        f"  {'dec':>3} {'n':>7} {'p_lo':>7} {'p_hi':>7} {'mean_p':>9} {'actual':>9} {'diff':>9}"
    )
    for _, r in df.iterrows():
        log.info(
            f"  {int(r['bin']):>3} {int(r['n']):>7} {r['p_lo']:>7.4f} {r['p_hi']:>7.4f} "
            f"{r['mean_p']:>9.4f} {r['actual']:>9.4f} {r['diff']:>+9.4f}"
        )
    e, m = ece_mce(p_pred, y_true)
    log.info(f"  ECE = {e:.4f}    MCE = {m:.4f}")


def log_section(
    log: logging.Logger,
    title: str,
    p_pred: np.ndarray,
    y_true: np.ndarray,
    slices: Iterator[tuple[str, np.ndarray]],
    label_width: int = 30,
) -> None:
    """Print one slice section as a labeled metric table."""
    log.info(f"\n========== {title} ==========")
    log.info(
        f"{'slice':<{label_width}} {'n':>8} {'mean_p':>8} {'actual':>8} "
        f"{'gap':>8} {'log_loss':>10} {'brier':>8} {'acc':>6}"
    )
    for label, mask in slices:
        sy = y_true[mask]
        sp = p_pred[mask]
        m = slice_metrics(sy, sp)
        if m["n"] == 0:
            log.info(f"{label:<{label_width}} {0:>8} (empty)")
            continue
        log.info(
            f"{label:<{label_width}} {m['n']:>8} {m['mean_p']:>8.4f} "
            f"{m['actual']:>8.4f} {m['gap']:>+8.4f} {m['log_loss']:>10.4f} "
            f"{m['brier']:>8.4f} {m['accuracy']:>6.4f}"
        )


# ---------------------------------------------------------------------------
# Slice builders
# ---------------------------------------------------------------------------


def has_any(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    """Return a boolean mask: True iff sum of cols (treated as int) > 0."""
    a = np.zeros(len(df), dtype=int)
    for c in cols:
        if c in df.columns:
            a += df[c].fillna(0).astype(int).to_numpy()
    return a > 0


def quantile_bins(s: pd.Series, n_bins: int, label_prefix: str) -> Iterator[tuple[str, np.ndarray]]:
    """Yield (label, mask) for each quantile bin (skips NaN values)."""
    q_bins = pd.qcut(s, n_bins, labels=False, duplicates="drop")
    for b in sorted(q_bins.dropna().unique()):
        mask = (q_bins == b).to_numpy()
        sub = s[mask]
        yield (
            f"{label_prefix} Q{int(b) + 1} [{sub.min():.3f}, {sub.max():.3f}]",
            mask,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument(
        "--sets",
        nargs="+",
        required=True,
        help="Set codes that the model was trained on, in the same order "
        "the training driver used so the seeded split is reproducible.",
    )
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

    log = setup_logger(args.model_dir / "calibration_analysis.log")
    log.info(f"==== Calibration analysis: {args.model_dir} ====")
    log.info(f"Sets: {args.sets}  event_type={args.event_type}  seed={args.seed}")

    # ---- 1. Load all parquets in the same order training used. ----
    paths: list[Path] = []
    for s in args.sets:
        d = args.model_training_dir / s / args.event_type
        chunks = feature_parquet_paths(d)
        if not chunks:
            raise SystemExit(f"No chunks for {s} under {d}")
        log.info(f"  {s}: {len(chunks)} chunks")
        paths.extend(chunks)
    log.info(f"Loading {len(paths)} parquet shards ...")
    df = pd.concat([pd.read_parquet(p) for p in paths], ignore_index=True)
    log.info(f"  {len(df):,} total rows")

    # ---- 2. Reconstruct the test split. ----
    splits = _grouped_split(
        df["draft_id"],
        val_frac=args.val_frac,
        calib_frac=args.calib_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    test = df.loc[splits.test].reset_index(drop=True)
    log.info(f"Test split: {len(test):,} rows ({len(test) / len(df):.1%} of all rows)")

    # ---- 3. Load bundle and predict. ----
    bundle = ModelBundle.load(args.model_dir)
    log.info(f"Bundle: {len(bundle.feature_names)} features  best_iter={bundle.best_iteration}")
    log.info("Predicting on test split ...")
    p_pred = predict_through_bundle(bundle, test)
    test["p_pred"] = p_pred

    y = test["won"].astype(int).to_numpy()
    p = test["p_pred"].to_numpy()

    # ---- 4. Overall metrics + reliability. ----
    log.info("\n========== OVERALL ==========")
    overall = slice_metrics(y, p)
    log.info(f"n = {overall['n']:,}")
    log.info(
        f"base WR = {overall['actual']:.4f}    mean predicted = {overall['mean_p']:.4f}    "
        f"gap = {overall['gap']:+.4f}"
    )
    log.info(
        f"log_loss = {overall['log_loss']:.4f}    brier = {overall['brier']:.4f}    "
        f"accuracy = {overall['accuracy']:.4f}"
    )
    log.info("\nOverall reliability (deciles of predicted probability):")
    log_reliability(log, p, y)

    # ---- 5. By set. ----
    def set_slices() -> Iterator[tuple[str, np.ndarray]]:
        for s in args.sets:
            col = f"set_code_{s}"
            if col in test.columns:
                yield s, (test[col].astype(int).to_numpy() == 1)

    log_section(log, "BY SET", p, y, set_slices())

    log.info("\n--- Reliability per set ---")
    for s in args.sets:
        col = f"set_code_{s}"
        if col not in test.columns:
            continue
        mask = test[col].astype(int).to_numpy() == 1
        if mask.sum() < 500:
            continue
        log.info(f"\n  set = {s}  (n = {mask.sum():,}):")
        log_reliability(log, p[mask], y[mask])

    # ---- 6. By mulligan number. ----
    mn = test["mulligan_number"].astype(int).to_numpy()

    def mull_slices() -> Iterator[tuple[str, np.ndarray]]:
        for n in [0, 1, 2]:
            yield f"mulligan = {n}", mn == n
        yield "mulligan >= 3", mn >= 3

    log_section(log, "BY MULLIGAN NUMBER", p, y, mull_slices())

    log.info("\n--- Reliability per mulligan_number ---")
    for n in [0, 1, 2]:
        mask = mn == n
        if mask.sum() < 200:
            log.info(f"\n  mulligan_number = {n}: n={int(mask.sum())} too small for reliability")
            continue
        log.info(f"\n  mulligan_number = {n}  (n = {mask.sum():,}):")
        log_reliability(log, p[mask], y[mask])

    # ---- 7. By on_the_play. ----
    op = test["on_the_play"].astype(bool).to_numpy()

    def play_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "on_the_play = True", op
        yield "on_the_play = False", ~op

    log_section(log, "BY ON_THE_PLAY", p, y, play_slices())

    # ---- 8. By opp_mulligan_number. ----
    opp = test["opp_mulligan_number"].astype(int).to_numpy()

    def opp_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "opp_mull = 0", opp == 0
        yield "opp_mull = 1", opp == 1
        yield "opp_mull >= 2", opp >= 2

    log_section(log, "BY OPP_MULLIGAN_NUMBER", p, y, opp_slices())

    # ---- 9. Cross: on_the_play x mulligan. ----
    def cross_slices() -> Iterator[tuple[str, np.ndarray]]:
        for play_mask, play_label in [(op, "play"), (~op, "draw")]:
            for k in [0, 1, 2]:
                yield f"{play_label}, mull={k}", play_mask & (mn == k)
            yield f"{play_label}, mull>=3", play_mask & (mn >= 3)

    log_section(log, "BY ON_PLAY x MULLIGAN", p, y, cross_slices())

    # ---- 10. By deck colour count. ----
    def main_color_slices() -> Iterator[tuple[str, np.ndarray]]:
        c = test["n_main_colors_in_deck"].fillna(0).astype(int).to_numpy()
        yield "1 main color", c == 1
        yield "2 main colors", c == 2
        yield "3 main colors", c == 3
        yield "4+ main colors", c >= 4

    log_section(log, "BY DECK MAIN COLORS", p, y, main_color_slices())

    def total_color_slices() -> Iterator[tuple[str, np.ndarray]]:
        c = test["n_total_colors_in_deck"].fillna(0).astype(int).to_numpy()
        yield "1 total color", c == 1
        yield "2 total colors", c == 2
        yield "3 total colors", c == 3
        yield "4+ total colors", c >= 4

    log_section(log, "BY DECK TOTAL COLORS (incl. splash)", p, y, total_color_slices())

    # ---- 11. By lands in hand. ----
    def land_slices() -> Iterator[tuple[str, np.ndarray]]:
        nl = test["n_lands_in_hand"].fillna(0).astype(int).to_numpy()
        for k in [0, 1, 2, 3, 4]:
            yield f"{k} lands in hand", nl == k
        yield "5+ lands in hand", nl >= 5

    log_section(log, "BY LANDS IN HAND", p, y, land_slices())

    # ---- 12. Hand role presence. ----
    roles: list[tuple[str, list[str]]] = [
        ("has ramp spell", ["n_ramp_spells_in_hand"]),
        (
            "has card draw / manip",
            [
                "n_card_draw_or_manipulation_mv_0_2_in_hand",
                "n_card_draw_or_manipulation_mv_3_in_hand",
                "n_card_draw_or_manipulation_mv_4_5_in_hand",
            ],
        ),
        (
            "has destroy/exile removal",
            [
                "n_removal_destroy_or_exile_mv_0_2_in_hand",
                "n_removal_destroy_or_exile_mv_3_in_hand",
                "n_removal_destroy_or_exile_mv_4_5_in_hand",
            ],
        ),
        (
            "has burn",
            [
                "n_burn_mv_0_2_in_hand",
                "n_burn_mv_3_in_hand",
                "n_burn_mv_4_5_in_hand",
            ],
        ),
        (
            "has bounce",
            [
                "n_bounce_mv_0_2_in_hand",
                "n_bounce_mv_3_in_hand",
                "n_bounce_mv_4_5_in_hand",
            ],
        ),
        (
            "has aura removal",
            [
                "n_removal_aura_mv_0_2_in_hand",
                "n_removal_aura_mv_3_in_hand",
                "n_removal_aura_mv_4_5_in_hand",
            ],
        ),
        (
            "has fight/punch",
            [
                "n_punch_fight_mv_0_2_in_hand",
                "n_punch_fight_mv_3_in_hand",
                "n_punch_fight_mv_4_5_in_hand",
            ],
        ),
        (
            "has combat trick",
            [
                "n_combat_trick_mv_0_2_in_hand",
                "n_combat_trick_mv_3_in_hand",
                "n_combat_trick_mv_4_5_in_hand",
            ],
        ),
        (
            "has pump aura",
            [
                "n_pump_aura_mv_0_2_in_hand",
                "n_pump_aura_mv_3_in_hand",
                "n_pump_aura_mv_4_5_in_hand",
            ],
        ),
        (
            "has equipment",
            [
                "n_equipment_mv_0_2_in_hand",
                "n_equipment_mv_3_in_hand",
                "n_equipment_mv_4_5_in_hand",
            ],
        ),
        (
            "has vehicle",
            [
                "n_vehicle_mv_0_2_in_hand",
                "n_vehicle_mv_3_in_hand",
                "n_vehicle_mv_4_5_in_hand",
            ],
        ),
        (
            "has saga",
            [
                "n_saga_mv_0_2_in_hand",
                "n_saga_mv_3_in_hand",
                "n_saga_mv_4_5_in_hand",
            ],
        ),
        (
            "has class card",
            [
                "n_class_card_mv_0_2_in_hand",
                "n_class_card_mv_3_in_hand",
                "n_class_card_mv_4_5_in_hand",
            ],
        ),
        (
            "has planeswalker",
            [
                "n_planeswalker_mv_0_2_in_hand",
                "n_planeswalker_mv_3_in_hand",
                "n_planeswalker_mv_4_5_in_hand",
            ],
        ),
        ("has 2-drop creature", ["n_creatures_mv_le_2_in_hand"]),
        ("has 3-drop creature", ["n_creatures_mv_3_in_hand"]),
        ("has 4-drop creature", ["n_creatures_mv_4_in_hand"]),
        ("has 5-drop creature", ["n_creatures_mv_5_in_hand"]),
        ("has 6+ creature", ["n_creatures_mv_ge_6_in_hand"]),
        (
            "has multi-modal spell",
            ["n_multi_modal_spells_in_hand"],
        ),
    ]

    def role_slices() -> Iterator[tuple[str, np.ndarray]]:
        for label, cols in roles:
            mask = has_any(test, cols)
            yield f"{label} = yes", mask
            yield f"{label} = no", ~mask

    log_section(log, "BY HAND ROLE PRESENCE", p, y, role_slices())

    # ---- 13. Hand colour stress. ----
    npip = test["n_double_or_triple_pip_cards_in_hand"].fillna(0).astype(int).to_numpy()
    nc = test["n_distinct_colors_required_by_hand"].fillna(0).astype(int).to_numpy()

    def pip_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "0 double/triple-pip", npip == 0
        yield "1 double/triple-pip", npip == 1
        yield "2 double/triple-pip", npip == 2
        yield "3+ double/triple-pip", npip >= 3

    log_section(log, "BY HAND COLOUR PIP COUNT", p, y, pip_slices())

    def colors_needed_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "0 distinct colours needed", nc == 0
        yield "1 distinct colour needed", nc == 1
        yield "2 distinct colours needed", nc == 2
        yield "3+ distinct colours needed", nc >= 3

    log_section(log, "BY DISTINCT COLOURS REQUIRED BY HAND", p, y, colors_needed_slices())

    # ---- 14. Simulator playability buckets. ----
    ld3 = test["p_land_drop_by_turn_3"].astype(float).to_numpy()

    def ld3_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "p_LD_t3 < 0.5", ld3 < 0.5
        yield "0.5 <= p_LD_t3 < 0.7", (ld3 >= 0.5) & (ld3 < 0.7)
        yield "0.7 <= p_LD_t3 < 0.85", (ld3 >= 0.7) & (ld3 < 0.85)
        yield "0.85 <= p_LD_t3 < 0.95", (ld3 >= 0.85) & (ld3 < 0.95)
        yield "p_LD_t3 >= 0.95", ld3 >= 0.95

    log_section(log, "BY P(LAND DROP BY T3)", p, y, ld3_slices())

    em4 = test["expected_mana_count_turn_4"].astype(float).to_numpy()

    def em4_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "EM_t4 < 3.0", em4 < 3.0
        yield "3.0 <= EM_t4 < 3.5", (em4 >= 3.0) & (em4 < 3.5)
        yield "3.5 <= EM_t4 < 4.0", (em4 >= 3.5) & (em4 < 4.0)
        yield "EM_t4 >= 4.0", em4 >= 4.0

    log_section(log, "BY EXPECTED MANA TURN 4", p, y, em4_slices())

    # ---- 15. Hand quality (max & avg GIH WR of in-hand spells). ----
    max_gih = test["max_gih_wr_of_hand_spells"].astype(float).to_numpy()

    def max_gih_slices() -> Iterator[tuple[str, np.ndarray]]:
        # Hands with no spells get NaN here.
        yield "no spells in hand (NaN)", np.isnan(max_gih)
        yield "max GIH WR < 0.48", max_gih < 0.48
        yield "0.48 <= max GIH WR < 0.52", (max_gih >= 0.48) & (max_gih < 0.52)
        yield "0.52 <= max GIH WR < 0.56", (max_gih >= 0.52) & (max_gih < 0.56)
        yield "0.56 <= max GIH WR < 0.60", (max_gih >= 0.56) & (max_gih < 0.60)
        yield "max GIH WR >= 0.60", max_gih >= 0.60

    log_section(log, "BY MAX GIH WR IN HAND (bomb-in-hand)", p, y, max_gih_slices(), label_width=34)

    log_section(
        log,
        "BY AVG GIH WR OF HAND (quintiles)",
        p,
        y,
        quantile_bins(test["avg_gih_wr_of_hand_spells"], 5, "avg GIH"),
        label_width=42,
    )

    log_section(
        log,
        "BY DECK %LANDS (quintiles)",
        p,
        y,
        quantile_bins(test["pct_lands_in_deck"], 5, "deck %lands"),
        label_width=42,
    )

    log_section(
        log,
        "BY DECK AVG GIH WR (quintiles)",
        p,
        y,
        quantile_bins(test["avg_gih_wr_of_spells"], 5, "deck avg GIH"),
        label_width=42,
    )

    log.info("\nDone.")
    log.info(f"Report saved to {args.model_dir / 'calibration_analysis.log'}")


if __name__ == "__main__":
    main()
