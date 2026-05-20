"""Compare predicted vs realized keep rate, bucketed by lands in hand.

Premise (from card-disagreement analysis): basic lands were the most
robust over-representation signal in 'model said keep, player mulled'
hands, suggesting the model under-penalizes hands with too-many or
too-few lands relative to what players actually do. A direct way to
check that is to bucket every test hand by its land count (0..7) and
overlay:

* mean predicted P(keep) from the choice model
* observed keep rate (what the player actually did)

If at e.g. ``n_lands == 5`` the model says P(keep) = 0.85 but players
keep only 65%, that's a flood-recognition gap. Likewise at
``n_lands == 1``: a sharp player-mull rate that the model misses
points to a screw-recognition gap.

Run:
    .venv/Scripts/python.exe packages/model/scripts/choice_keep_rate_by_lands.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import pandas as pd
import xgboost as xgb
from mulligan_coach_cards import load_parsed_cards
from mulligan_coach_model.choice_train import _grouped_split

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v3"
DEFAULT_CHOICE_CACHE_DIR = REPO_ROOT / "data" / "processed" / "choice_training"
DEFAULT_DECISIONS_PATH = (
    REPO_ROOT
    / "data"
    / "processed"
    / "seventeenlands"
    / "mulligan_decisions"
    / "combined.PremierDraft.parquet"
)
DEFAULT_SETS = ("TLA", "TMT")

# 17Lands columns use card names; the per-set parsed-cards JSON does
# not include basics (they're synthesised in the win/choice model
# training paths). We just hard-code the five MTG basics here.
BASIC_LANDS = frozenset({"Plains", "Island", "Swamp", "Mountain", "Forest"})


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("choice_keep_rate_by_lands")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def build_land_names(set_code: str) -> set[str]:
    """Return every card name in this set that's a land, plus basics."""
    lands = set(BASIC_LANDS)
    for card in load_parsed_cards(set_code):
        if card.role_features.is_land:
            lands.add(card.name)
            # 17Lands uses the front-face name for DFCs; add both forms
            # just in case the parsed-card name uses the joint format.
            if " // " in card.name:
                lands.add(card.name.split(" // ")[0])
    return lands


def count_lands(hand: str, land_set: frozenset[str] | set[str]) -> int:
    """Count cards in the pipe-delimited hand that are in ``land_set``."""
    if not isinstance(hand, str):
        return -1
    return sum(1 for c in hand.split("|") if c.strip() in land_set)


def load_choice_training(
    cache_dir: Path,
    sets: tuple[str, ...],
    event_type: str,
) -> pd.DataFrame:
    parts = []
    for set_code in sets:
        src = cache_dir / set_code / event_type
        paths = sorted(src.glob("chunk_*.parquet"))
        if not paths:
            raise SystemExit(f"no chunks under {src}")
        for p in paths:
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True)


def load_booster(model_dir: Path) -> tuple[xgb.Booster, list[str], int]:
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    return booster, list(meta["feature_names"]), int(meta["best_iteration"])


def report_table(
    df: pd.DataFrame,
    *,
    label: str,
    log: logging.Logger,
) -> None:
    """Bucket by n_lands and print pred vs observed keep rate."""
    log.info("\n%s", label)
    log.info(
        "  %-7s  %8s  %10s  %10s  %10s  %12s",
        "n_lands",
        "n",
        "obs keep",
        "pred keep",
        "gap (pp)",
        "% of hands",
    )
    total = len(df)
    for n_lands in range(0, 8):
        sub = df[df["n_lands"] == n_lands]
        if sub.empty:
            continue
        obs = float(sub["was_kept"].mean())
        pred = float(sub["p_keep"].mean())
        log.info(
            "  %-7d  %8d  %10.4f  %10.4f  %+10.2f  %11.2f%%",
            n_lands,
            len(sub),
            obs,
            pred,
            (pred - obs) * 100.0,
            100.0 * len(sub) / total,
        )

    # Overall aggregate gap (probability-weighted by population).
    obs_all = float(df["was_kept"].mean())
    pred_all = float(df["p_keep"].mean())
    log.info(
        "  %-7s  %8d  %10.4f  %10.4f  %+10.2f  %11.2f%%",
        "ALL",
        total,
        obs_all,
        pred_all,
        (pred_all - obs_all) * 100.0,
        100.0,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument("--choice-cache-dir", type=Path, default=DEFAULT_CHOICE_CACHE_DIR)
    ap.add_argument("--decisions-path", type=Path, default=DEFAULT_DECISIONS_PATH)
    ap.add_argument("--sets", nargs="+", default=list(DEFAULT_SETS))
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--log-name",
        default="choice_keep_rate_by_lands.log",
        help="Log filename (saved into --choice-model-dir).",
    )
    args = ap.parse_args()

    log_path = args.choice_model_dir / args.log_name
    log = setup_logger(log_path)
    log.info("==== Choice-model keep rate vs n_lands ====")
    log.info("Choice model:  %s", args.choice_model_dir)
    log.info("Sets:          %s", args.sets)

    booster, feature_names, best_iter = load_booster(args.choice_model_dir)
    log.info("Loaded booster (best_iter=%d)", best_iter)

    df_all = load_choice_training(args.choice_cache_dir, tuple(args.sets), args.event_type)
    splits = _grouped_split(
        df_all["draft_id"],
        val_frac=args.val_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    test_df = df_all.loc[splits.test].reset_index(drop=True)
    log.info("Test rows: %d", len(test_df))

    X = test_df[feature_names].astype(float).to_numpy()
    dmat = xgb.DMatrix(X, feature_names=feature_names)
    test_df["p_keep"] = booster.predict(dmat, iteration_range=(0, best_iter + 1))

    # Join hand strings.
    md = pd.read_parquet(
        args.decisions_path,
        columns=[
            "draft_id",
            "build_index",
            "match_number",
            "game_number",
            "mulligan_number",
            "hand",
        ],
    )
    test_df = (
        test_df.merge(
            md,
            on=["draft_id", "build_index", "match_number", "game_number", "mulligan_number"],
            how="left",
            validate="one_to_one",
        )
        .dropna(subset=["hand"])
        .reset_index(drop=True)
    )

    # Build per-set land sets and apply.
    log.info("\nBuilding per-set land lookups...")
    land_sets: dict[str, frozenset[str]] = {}
    for set_code in args.sets:
        land_sets[set_code] = frozenset(build_land_names(set_code))
        log.info("  %s: %d land names (basics + non-basic)", set_code, len(land_sets[set_code]))

    test_df["n_lands"] = test_df.apply(
        lambda row: count_lands(row["hand"], land_sets[row["expansion"]]),
        axis=1,
    )

    # ---- Combined report (both sets together) ----
    report_table(
        test_df,
        label="==== ALL sets combined ====",
        log=log,
    )

    # ---- Per-set report ----
    for set_code in args.sets:
        sub = test_df[test_df["expansion"] == set_code]
        if sub.empty:
            continue
        report_table(
            sub,
            label=f"==== Set: {set_code} ====",
            log=log,
        )

    # ---- mn=0 only, all sets combined (the natural keep-7 decision) ----
    mn0 = test_df[test_df["mulligan_number"] == 0]
    report_table(
        mn0,
        label="==== mn=0 only (the original-7 keep decisions), all sets combined ====",
        log=log,
    )

    # ---- On play / on draw split (any miscalibration concentrated here?) ----
    log.info("\n==== mn=0, on play / on draw split ====")
    for on_play_flag in (True, False):
        side = mn0[mn0["on_the_play"].astype(bool) == on_play_flag]
        if side.empty:
            continue
        report_table(
            side,
            label=f"-- mn=0, {'on play' if on_play_flag else 'on draw'} --",
            log=log,
        )

    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
