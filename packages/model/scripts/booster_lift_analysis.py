"""Quantify how much per-hand signal the booster adds beyond the baseline.

The baseline model already absorbs the strong context signals
``(user_wr_bucket x user_n_games_bucket x on_the_play x opp_mulligan)``.
On the deploy-time test, the full model's apparent calibration / log-
loss is mostly the baseline doing its job; the booster's marginal value
is harder to see from aggregate metrics alone.

This script residualizes on the baseline. For every test row we compute:

* ``p_baseline_i = sigmoid(base_margin_i)`` — what context predicts
  on its own, no booster contribution.
* ``p_full_i = sigmoid(base_margin_i + booster_logit_i)`` — what the
  full model predicts.
* ``delta_i = p_full_i - p_baseline_i`` — the booster's claimed
  per-hand correction (positive = hand looks better than its context
  suggests).
* ``actual_residual_i = won_i - p_baseline_i`` — the row's actual
  surprise vs the baseline.

The booster was trained to predict ``actual_residual``. So the question
"does the booster add real per-hand signal" reduces to "does ``delta``
track ``actual_residual`` across the test set."

Two views in one report:

* **View A (broad)**: full test set. The booster's lift here includes
  mulligan-number signal + hand features + deck features. This is the
  honest deploy-time picture (the deploy pipeline cares about all three).
* **View B (conservative)**: restricted to ``mulligan_number == 0``
  (kept-7). The booster doesn't get credit for the obvious "this is a
  mulligan'd hand, push the prediction down" signal — only hand-/deck-
  level information. The cleanest "are the per-hand features doing
  anything" question.

Headline metrics, both views:

* log_loss / brier / AUC for ``p_baseline`` vs ``p_full``. The gap is
  the booster's marginal information (in nats / Brier units / AUC pts).
* ``corr(delta, actual_residual)`` — Pearson and Spearman. Tells us
  whether the booster's claimed lift moves in the same direction as
  the actual lift.
* Residual reliability table: 10 deciles of ``delta``, showing mean
  predicted ``delta`` vs mean actual ``residual`` per decile. A
  diagonal means the booster is well-calibrated as a residual
  predictor.
* Top-minus-bottom decile actual-residual spread — within-context
  lift in percentage points.

Output: ``<model-dir>/booster_lift_analysis.log``.
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
from sklearn.metrics import roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[3]

EPS = 1e-7


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("booster_lift")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    pc = np.clip(p, EPS, 1 - EPS)
    return float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean())


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(((p - y) ** 2).mean())


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    """AUC, returning None if the slice has only one outcome class."""
    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, p))


def slice_residual_metrics(
    mask: np.ndarray,
    p_full: np.ndarray,
    p_baseline: np.ndarray,
    y: np.ndarray,
) -> dict[str, float | int | None]:
    """Compute residualized metrics for one slice.

    Returns mean delta (booster's claimed lift), mean actual residual,
    gap, plus log_loss / AUC comparison between baseline-only and full
    model restricted to the slice.
    """
    n = int(mask.sum())
    if n == 0:
        return {"n": 0}
    p_full_s = p_full[mask]
    p_base_s = p_baseline[mask]
    y_s = y[mask].astype(float)
    delta = p_full_s - p_base_s
    resid = y_s - p_base_s
    return {
        "n": n,
        "mean_delta": float(delta.mean()),
        "mean_resid": float(resid.mean()),
        "gap": float(delta.mean() - resid.mean()),
        "ll_gap": float(log_loss(y_s, p_base_s) - log_loss(y_s, p_full_s)),
        "auc_base": _safe_auc(y_s, p_base_s),
        "auc_full": _safe_auc(y_s, p_full_s),
    }


def log_slice_table(
    log: logging.Logger,
    title: str,
    slices: Iterator[tuple[str, np.ndarray]],
    p_full: np.ndarray,
    p_baseline: np.ndarray,
    y: np.ndarray,
    label_width: int = 32,
) -> None:
    """Print a residual-metrics table for a category of slices.

    Columns:
    * ``Δ_pred``    — booster's claimed lift for the slice (mean delta).
    * ``Δ_actual``  — actual lift over baseline (mean residual).
    * ``gap``       — Δ_pred minus Δ_actual; near 0 means the booster
                      correctly identifies the slice's bias.
    * ``LL_gap``    — log_loss(baseline) - log_loss(full) within slice;
                      positive means the booster helps prediction here.
    * ``AUC_gap``   — AUC(full) - AUC(baseline) within slice;
                      positive means the booster discriminates better
                      among rows that share this feature.
    """
    log.info(f"\n========== {title} ==========")
    log.info(
        f"{'slice':<{label_width}} {'n':>7} {'Δ_pred':>8} {'Δ_actual':>9} "
        f"{'gap':>8} {'LL_gap':>8} {'AUC_gap':>8}"
    )
    for label, mask in slices:
        m = slice_residual_metrics(mask, p_full, p_baseline, y)
        if m["n"] == 0:
            log.info(f"{label:<{label_width}} {0:>7} (empty)")
            continue
        auc_gap_str = (
            f"{m['auc_full'] - m['auc_base']:>+8.4f}"
            if m["auc_full"] is not None and m["auc_base"] is not None
            else f"{'n/a':>8}"
        )
        log.info(
            f"{label:<{label_width}} {m['n']:>7} {m['mean_delta']:>+8.4f} "
            f"{m['mean_resid']:>+9.4f} {m['gap']:>+8.4f} {m['ll_gap']:>+8.4f} "
            f"{auc_gap_str}"
        )


def _has_any(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    a = np.zeros(len(df), dtype=int)
    for c in cols:
        if c in df.columns:
            a += df[c].fillna(0).astype(int).to_numpy()
    return a > 0


def report_view(
    log: logging.Logger,
    name: str,
    mask: np.ndarray,
    p_full_all: np.ndarray,
    p_baseline_all: np.ndarray,
    y_all: np.ndarray,
) -> None:
    p_full = p_full_all[mask]
    p_baseline = p_baseline_all[mask]
    y = y_all[mask].astype(float)
    delta = p_full - p_baseline
    residual = y - p_baseline

    log.info(f"\n{'=' * 70}")
    log.info(f"  {name}")
    log.info(f"{'=' * 70}")
    log.info(f"n = {len(p_full):,}    actual WR = {y.mean():.4f}")
    log.info(
        f"  p_baseline: mean={p_baseline.mean():.4f}  std={p_baseline.std():.4f}  "
        f"range=[{p_baseline.min():.4f}, {p_baseline.max():.4f}]"
    )
    log.info(
        f"  p_full    : mean={p_full.mean():.4f}  std={p_full.std():.4f}  "
        f"range=[{p_full.min():.4f}, {p_full.max():.4f}]"
    )
    log.info(
        f"  delta     : mean={delta.mean():+.4f}  std={delta.std():.4f}  "
        f"range=[{delta.min():+.4f}, {delta.max():+.4f}]"
    )

    # Aggregate metric comparison.
    ll_b = log_loss(y, p_baseline)
    ll_f = log_loss(y, p_full)
    br_b = brier(y, p_baseline)
    br_f = brier(y, p_full)
    auc_b = roc_auc_score(y, p_baseline)
    auc_f = roc_auc_score(y, p_full)

    log.info("")
    log.info(f"  {'metric':<10} {'baseline':>10} {'full':>10} {'gap':>10}    interpretation")
    log.info(
        f"  {'log_loss':<10} {ll_b:>10.4f} {ll_f:>10.4f} {ll_b - ll_f:>+10.4f}    "
        f"positive => booster reduces log-loss"
    )
    log.info(
        f"  {'brier':<10} {br_b:>10.4f} {br_f:>10.4f} {br_b - br_f:>+10.4f}    "
        f"positive => booster reduces Brier"
    )
    log.info(
        f"  {'AUC':<10} {auc_b:>10.4f} {auc_f:>10.4f} {auc_f - auc_b:>+10.4f}    "
        f"positive => booster sharpens ranking"
    )

    # Correlation between predicted and actual residual.
    pearson = float(np.corrcoef(delta, residual)[0, 1])
    spearman = float(pd.Series(delta).corr(pd.Series(residual), method="spearman"))
    log.info("")
    log.info(f"  corr(delta, actual_residual)  Pearson  = {pearson:+.4f}")
    log.info(f"  corr(delta, actual_residual)  Spearman = {spearman:+.4f}")
    log.info("    Positive => the booster's per-row claim moves with the actual surprise.")

    # Residual reliability: deciles of delta vs mean actual residual.
    bins = pd.qcut(delta, 10, labels=False, duplicates="drop")
    log.info("")
    log.info("  Residual reliability (10 deciles of model's predicted delta):")
    log.info(
        f"    {'dec':>3} {'n':>7} {'delta_lo':>9} {'delta_hi':>9} "
        f"{'mean_delta':>11} {'mean_resid':>11} {'gap':>9}"
    )
    decile_rows: list[tuple[int, int, float, float]] = []
    for b in sorted(np.unique(bins)):
        m = bins == b
        d_lo = float(delta[m].min())
        d_hi = float(delta[m].max())
        mean_d = float(delta[m].mean())
        mean_r = float(residual[m].mean())
        log.info(
            f"    {int(b) + 1:>3} {int(m.sum()):>7} {d_lo:>+9.4f} {d_hi:>+9.4f} "
            f"{mean_d:>+11.4f} {mean_r:>+11.4f} {mean_d - mean_r:>+9.4f}"
        )
        decile_rows.append((int(b), int(m.sum()), mean_d, mean_r))

    # Top-vs-bottom decile spread.
    log.info("")
    log.info(
        f"  Bottom-decile actual_residual mean: {decile_rows[0][3]:+.4f}  (n={decile_rows[0][1]:,})"
    )
    log.info(
        f"  Top-decile    actual_residual mean: {decile_rows[-1][3]:+.4f}  "
        f"(n={decile_rows[-1][1]:,})"
    )
    spread = decile_rows[-1][3] - decile_rows[0][3]
    log.info(f"  Top-minus-bottom spread (within-context lift in pp): {spread:+.4f}")


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

    log = setup_logger(args.model_dir / "booster_lift_analysis.log")
    log.info(f"==== Booster lift analysis: {args.model_dir} ====")
    log.info(f"Sets: {args.sets}  event_type={args.event_type}  seed={args.seed}")

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

    splits = _grouped_split(
        df["draft_id"],
        val_frac=args.val_frac,
        calib_frac=args.calib_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    test = df.loc[splits.test].reset_index(drop=True)
    log.info(f"Test split: {len(test):,} rows ({len(test) / len(df):.1%})")

    bundle = ModelBundle.load(args.model_dir)
    log.info(f"Bundle: {len(bundle.feature_names)} features  best_iter={bundle.best_iteration}")

    # Compute base_margin once, then both prediction sets in one pass.
    feature_cols = list(bundle.feature_names)
    X = test[feature_cols].astype(np.float32).to_numpy()
    base_margin = _per_row_base_margin(test, bundle.baseline)
    dm = xgb.DMatrix(X, base_margin=base_margin, feature_names=feature_cols)
    p_full = bundle.booster.predict(dm, iteration_range=(0, bundle.best_iteration + 1))
    # sigmoid(base_margin) — the baseline's standalone prediction.
    p_baseline = 1.0 / (1.0 + np.exp(-base_margin))
    y = test["won"].astype(int).to_numpy()

    # --- View A: whole test set ---
    report_view(
        log,
        "VIEW A: full test set (baseline absorbs context + user skill)",
        np.ones(len(test), dtype=bool),
        p_full,
        p_baseline,
        y,
    )

    # --- View B: kept-7 only ---
    mask_k7 = (test["mulligan_number"] == 0).to_numpy()
    report_view(
        log,
        "VIEW B: restricted to mulligan_number == 0 (kept-7 hands only)",
        mask_k7,
        p_full,
        p_baseline,
        y,
    )

    # --- View C: per-slice residual analysis on kept-7 ---
    log.info(f"\n\n{'#' * 70}")
    log.info("# VIEW C: Per-slice residual analysis on kept-7 hands")
    log.info("#")
    log.info("# For each slice we report:")
    log.info("#   Δ_pred    = mean delta within slice (booster's claimed lift)")
    log.info("#   Δ_actual  = mean actual residual within slice (actual lift vs baseline)")
    log.info("#   gap       = Δ_pred - Δ_actual (slice-level miscalibration)")
    log.info("#   LL_gap    = log_loss(baseline) - log_loss(full) within slice")
    log.info("#   AUC_gap   = AUC(full) - AUC(baseline) within slice")
    log.info(f"{'#' * 70}")

    # Restrict everything below to kept-7 hands.
    k7 = test.loc[mask_k7].reset_index(drop=True)
    p_full_k7 = p_full[mask_k7]
    p_base_k7 = p_baseline[mask_k7]
    y_k7 = y[mask_k7]

    # ---- By set ----
    def set_slices() -> Iterator[tuple[str, np.ndarray]]:
        for s in args.sets:
            col = f"set_code_{s}"
            if col in k7.columns:
                yield s, (k7[col].astype(int).to_numpy() == 1)

    log_slice_table(log, "BY SET", set_slices(), p_full_k7, p_base_k7, y_k7)

    # ---- On the play / draw (baseline absorbs these; sanity check) ----
    op = k7["on_the_play"].astype(bool).to_numpy()

    def play_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "on_the_play = True", op
        yield "on_the_play = False", ~op

    log_slice_table(
        log,
        "BY ON_THE_PLAY (sanity check — baseline absorbs this)",
        play_slices(),
        p_full_k7,
        p_base_k7,
        y_k7,
    )

    # ---- Opp mulligan (baseline absorbs this too) ----
    opp = k7["opp_mulligan_number"].astype(int).to_numpy()

    def opp_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "opp_mull = 0", opp == 0
        yield "opp_mull = 1", opp == 1
        yield "opp_mull >= 2", opp >= 2

    log_slice_table(
        log, "BY OPP_MULLIGAN_NUMBER (sanity check)", opp_slices(), p_full_k7, p_base_k7, y_k7
    )

    # ---- Deck colors (main / total) ----
    def main_color_slices() -> Iterator[tuple[str, np.ndarray]]:
        c = k7["n_main_colors_in_deck"].fillna(0).astype(int).to_numpy()
        yield "1 main color", c == 1
        yield "2 main colors", c == 2
        yield "3 main colors", c == 3
        yield "4+ main colors", c >= 4

    log_slice_table(log, "BY DECK MAIN COLORS", main_color_slices(), p_full_k7, p_base_k7, y_k7)

    def total_color_slices() -> Iterator[tuple[str, np.ndarray]]:
        c = k7["n_total_colors_in_deck"].fillna(0).astype(int).to_numpy()
        yield "1 total color", c == 1
        yield "2 total colors", c == 2
        yield "3 total colors", c == 3
        yield "4+ total colors", c >= 4

    log_slice_table(
        log, "BY DECK TOTAL COLORS (incl. splash)", total_color_slices(), p_full_k7, p_base_k7, y_k7
    )

    # ---- Lands in hand ----
    def land_slices() -> Iterator[tuple[str, np.ndarray]]:
        nl = k7["n_lands_in_hand"].fillna(0).astype(int).to_numpy()
        for kk in [0, 1, 2, 3, 4]:
            yield f"{kk} lands in hand", nl == kk
        yield "5+ lands in hand", nl >= 5

    log_slice_table(log, "BY LANDS IN HAND", land_slices(), p_full_k7, p_base_k7, y_k7)

    # ---- Hand role presence ----
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
        ("has multi-modal spell", ["n_multi_modal_spells_in_hand"]),
    ]

    def role_slices() -> Iterator[tuple[str, np.ndarray]]:
        for label, cols in roles:
            mask = _has_any(k7, cols)
            yield f"{label} = yes", mask
            yield f"{label} = no", ~mask

    log_slice_table(log, "BY HAND ROLE PRESENCE", role_slices(), p_full_k7, p_base_k7, y_k7)

    # ---- Color stress ----
    npip = k7["n_double_or_triple_pip_cards_in_hand"].fillna(0).astype(int).to_numpy()
    nc = k7["n_distinct_colors_required_by_hand"].fillna(0).astype(int).to_numpy()

    def pip_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "0 double/triple-pip", npip == 0
        yield "1 double/triple-pip", npip == 1
        yield "2 double/triple-pip", npip == 2
        yield "3+ double/triple-pip", npip >= 3

    log_slice_table(log, "BY HAND COLOUR PIP COUNT", pip_slices(), p_full_k7, p_base_k7, y_k7)

    def colors_needed_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "0 distinct colours needed", nc == 0
        yield "1 distinct colour needed", nc == 1
        yield "2 distinct colours needed", nc == 2
        yield "3+ distinct colours needed", nc >= 3

    log_slice_table(
        log,
        "BY DISTINCT COLOURS REQUIRED BY HAND",
        colors_needed_slices(),
        p_full_k7,
        p_base_k7,
        y_k7,
    )

    # ---- Simulator playability buckets ----
    ld3 = k7["p_land_drop_by_turn_3"].astype(float).to_numpy()

    def ld3_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "p_LD_t3 < 0.5", ld3 < 0.5
        yield "0.5 <= p_LD_t3 < 0.7", (ld3 >= 0.5) & (ld3 < 0.7)
        yield "0.7 <= p_LD_t3 < 0.85", (ld3 >= 0.7) & (ld3 < 0.85)
        yield "0.85 <= p_LD_t3 < 0.95", (ld3 >= 0.85) & (ld3 < 0.95)
        yield "p_LD_t3 >= 0.95", ld3 >= 0.95

    log_slice_table(log, "BY P(LAND DROP BY T3)", ld3_slices(), p_full_k7, p_base_k7, y_k7)

    em4 = k7["expected_mana_count_turn_4"].astype(float).to_numpy()

    def em4_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "EM_t4 < 3.0", em4 < 3.0
        yield "3.0 <= EM_t4 < 3.5", (em4 >= 3.0) & (em4 < 3.5)
        yield "3.5 <= EM_t4 < 4.0", (em4 >= 3.5) & (em4 < 4.0)
        yield "EM_t4 >= 4.0", em4 >= 4.0

    log_slice_table(log, "BY EXPECTED MANA TURN 4", em4_slices(), p_full_k7, p_base_k7, y_k7)

    # ---- Hand quality (max GIH WR) ----
    max_gih = k7["max_gih_wr_of_hand_spells"].astype(float).to_numpy()

    def max_gih_slices() -> Iterator[tuple[str, np.ndarray]]:
        yield "no spells in hand (NaN)", np.isnan(max_gih)
        yield "max GIH WR < 0.48", max_gih < 0.48
        yield "0.48 <= max GIH WR < 0.52", (max_gih >= 0.48) & (max_gih < 0.52)
        yield "0.52 <= max GIH WR < 0.56", (max_gih >= 0.52) & (max_gih < 0.56)
        yield "0.56 <= max GIH WR < 0.60", (max_gih >= 0.56) & (max_gih < 0.60)
        yield "max GIH WR >= 0.60", max_gih >= 0.60

    log_slice_table(
        log,
        "BY MAX GIH WR IN HAND (mostly degenerate — arena_id lag)",
        max_gih_slices(),
        p_full_k7,
        p_base_k7,
        y_k7,
        label_width=34,
    )

    # ---- Deck %lands (quintiles) ----
    def deck_lands_q_slices() -> Iterator[tuple[str, np.ndarray]]:
        q = k7["pct_lands_in_deck"]
        q_bins = pd.qcut(q, 5, labels=False, duplicates="drop")
        for b in sorted(q_bins.dropna().unique()):
            mask = (q_bins == b).to_numpy()
            sub = q[mask]
            yield (
                f"Q{int(b) + 1} [{sub.min():.3f}, {sub.max():.3f}]",
                mask,
            )

    log_slice_table(
        log,
        "BY DECK %LANDS (quintiles)",
        deck_lands_q_slices(),
        p_full_k7,
        p_base_k7,
        y_k7,
        label_width=34,
    )

    log.info("")
    log.info(f"Report saved to {args.model_dir / 'booster_lift_analysis.log'}")


if __name__ == "__main__":
    main()
