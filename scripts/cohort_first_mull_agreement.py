"""Skill-cohort first-mulligan agreement for the production choice model.

Answers: which player-skill band does the production choice model's
keep/mull verdict agree with most?

Unlike ``elite_first_mull_agreement.py`` (which re-simulates every
hand through ``RecommendationService.recommend_choice``), this script
scores **already-simulated** feature rows straight from the
choice-training cache at
``data/processed/choice_training/{SET}/{EVENT}/chunk_*.parquet`` —
no simulator runs, so it finishes in minutes. Two accepted caveats:

* Cached rows were materialised at ``n_sims_per_row=200`` (the elite
  script uses 300 fresh sims), so verdicts near a band edge can flip
  relative to that script's published numbers.
* The score is **in-sample for TLA/TMT/SOS** — choice_prod trained on
  those rows, so cohorts are compared against each other under
  identical conditions rather than measuring absolute held-out
  performance. ``--sets MSH`` is the exception: choice_prod never saw
  MSH (its ``set_code_MSH`` one-hot has split weight 0), so that run
  IS a genuine out-of-sample measurement.

Cohorts (first-mull decisions only, ``user_n_games_raw >=
--min-n-games``, default 500; WR values are 17Lands bucket centers,
0.02 apart):

* PremierDraft — mid_low 0.50-0.52, mid 0.54-0.56, mid_high
  0.58-0.60, elite >= 0.62 AND rank in {diamond, mythic}. Rank is
  joined from the mulligan_decisions parquet (the cache doesn't
  carry it). Players with WR >= 0.62 but below diamond fall in NO
  cohort — they'd contaminate either neighbour.
* TradDraft — mid_low 0.50-0.58, mid 0.60-0.62, mid_high 0.64-0.66,
  elite >= 0.68 (no rank filter, matching the pinned elite def).

The "filtered-out" bottom group (WR < 0.50 with >= 50 games) is NOT
scoreable from the cache: the choice materialiser drops those rows
before simulating, so they would need fresh simulation.

Per cohort, over judged (non-borderline) decisions, we report:

* agreement — verdict side matches the player's actual choice;
* soft disagreement — marginal_* verdict on the wrong side;
* hard disagreement — clear_* verdict on the wrong side.

A note on ``--min-n-games``: it gates every cohort, never one band
alone. The 17Lands games ladder is coarse (1/5/10/50/100/500/1000), so
``>=200`` selects exactly the same players as ``>=500``; the only real
alternative is 100, which grows the cohorts ~10-24x at the cost of a
noisier WR-based skill proxy (at bucket 100, 15.7% of players clear
WR 0.62 vs 5.96% at bucket 500 — much of that gap is short-run luck).
Runs at different gates are not comparable to each other.

Run with (tee so the results survive the terminal):

    .venv/Scripts/python.exe scripts/cohort_first_mull_agreement.py \
        2>&1 | tee logs/cohort_first_mull_agreement.log
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import xgboost as xgb
from mulligan_coach_model import ChoiceModelBundle
from mulligan_coach_recommend.service import (
    CHOICE_BORDERLINE_THRESHOLD,
    CHOICE_CLEAR_KEEP_THRESHOLD,
    CHOICE_MARGINAL_KEEP_THRESHOLD,
    CHOICE_MARGINAL_MULL_THRESHOLD,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = REPO_ROOT / "data" / "processed" / "choice_training"
DECISIONS_DIR = REPO_ROOT / "data" / "processed" / "seventeenlands" / "mulligan_decisions"
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_prod"

MIN_N_GAMES = 500

# WR bands per cohort, as (low, high) INCLUSIVE bounds on the 0.02-wide
# 17Lands bucket centers. Chosen with the owner 2026-07-11 (see the
# root CLAUDE.md for the pinned elite defs the endpoints match).
COHORT_BANDS: dict[str, dict[str, tuple[float, float]]] = {
    "PremierDraft": {
        "mid_low": (0.50, 0.52),
        "mid": (0.54, 0.56),
        "mid_high": (0.58, 0.60),
        "elite": (0.62, 1.00),  # + rank filter, applied separately
    },
    "TradDraft": {
        "mid_low": (0.50, 0.58),
        "mid": (0.60, 0.62),
        "mid_high": (0.64, 0.66),
        "elite": (0.68, 1.00),
    },
}
ELITE_PREMIER_RANKS = {"diamond", "mythic"}
COHORT_ORDER = ("mid_low", "mid", "mid_high", "elite")

VERDICTS = ("clear_keep", "marginal_keep", "borderline", "marginal_mulligan", "clear_mulligan")

# Context columns needed beyond the model's feature names.
CONTEXT_COLS = [
    "was_kept",
    "user_wr_raw",
    "user_n_games_raw",
    "draft_id",
    "build_index",
    "match_number",
    "game_number",
    "expansion",
    "mulligan_number",
]

RANK_JOIN_KEY = ["draft_id", "build_index", "match_number", "game_number"]


def setup_logger() -> logging.Logger:
    log = logging.getLogger("cohort_first_mull_agreement")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


# ---------------------------------------------------------------------------
# Cache loading
# ---------------------------------------------------------------------------


def load_cohort_rows(
    sets: list[str],
    event_type: str,
    feature_names: tuple[str, ...],
    log: logging.Logger,
    min_n_games: int = MIN_N_GAMES,
) -> pd.DataFrame:
    """Load first-mull rows above ``min_n_games`` from the choice-training cache.

    Filter pushdown happens at the parquet layer so only the matching
    rows are materialised. ``_meta.json`` sidecars share the shard
    directory, so we glob ``chunk_*.parquet`` explicitly rather than
    handing pyarrow the directory.

    ``min_n_games`` gates ALL cohorts, not just elite — lowering it for
    one band only would confound skill with WR-estimate reliability. The
    17Lands games ladder is coarse (1/5/10/50/100/500/1000), so the only
    meaningful settings are 500 (the pinned default) and 100.
    """
    columns = list(dict.fromkeys([*feature_names, *CONTEXT_COLS]))
    row_filter = (ds.field("mulligan_number") == 0) & (ds.field("user_n_games_raw") >= min_n_games)
    frames: list[pd.DataFrame] = []
    for set_code in sets:
        shard_dir = CACHE_ROOT / set_code / event_type
        chunks = sorted(shard_dir.glob("chunk_*.parquet"))
        if not chunks:
            log.info("  %s/%s: no cache chunks, skipping", set_code, event_type)
            continue
        dataset = ds.dataset([str(p) for p in chunks], format="parquet")
        table = dataset.to_table(columns=columns, filter=row_filter)
        frame = table.to_pandas()
        log.info(
            "  %s/%s: %s first-mull rows with >=%d games (from %d chunks)",
            set_code,
            event_type,
            f"{len(frame):,}",
            min_n_games,
            len(chunks),
        )
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def join_premier_ranks(df: pd.DataFrame, sets: list[str], log: logging.Logger) -> pd.DataFrame:
    """Attach the 17Lands ``rank`` column from the decisions parquets.

    The choice cache doesn't carry rank, but the Premier elite cohort
    is defined with a diamond/mythic filter, so we join it back by
    the decision identity key.

    Reads the **per-set** ``{SET}.PremierDraft.parquet`` files for the
    sets under analysis. The older ``combined.PremierDraft.parquet``
    covers only the sets that happened to be built together (TLA/TMT/SOS),
    so using it silently gave every row of any other set a null rank —
    which the elite filter then reads as "not diamond/mythic", emptying
    the elite cohort without an error.
    """
    frames: list[pd.DataFrame] = []
    for set_code in sets:
        path = DECISIONS_DIR / f"{set_code}.PremierDraft.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"{path} missing — the Premier elite cohort needs per-set ranks. "
                f"Build it with scripts/mulligan_decisions/build_dataset.py --sets {set_code}."
            )
        frames.append(pd.read_parquet(path, columns=[*RANK_JOIN_KEY, "mulligan_number", "rank"]))
    ranks = pd.concat(frames, ignore_index=True)
    ranks = ranks.loc[ranks["mulligan_number"] == 0, [*RANK_JOIN_KEY, "rank"]]
    ranks = ranks.drop_duplicates(subset=RANK_JOIN_KEY)
    # Normalise key dtypes on both sides before merging.
    for frame in (df, ranks):
        frame["draft_id"] = frame["draft_id"].astype(str)
        for col in ("build_index", "match_number", "game_number"):
            frame[col] = frame[col].astype("int64")
    out = df.merge(ranks, on=RANK_JOIN_KEY, how="left")
    n_missing = int(out["rank"].isna().sum())
    if n_missing:
        missing_pct = 100.0 * n_missing / len(out)
        log.info(
            "  rank join: %d rows without a rank (%.2f%%, treated as non-elite)",
            n_missing,
            missing_pct,
        )
        # A wholesale join failure would silently zero out the elite cohort.
        if missing_pct > 5.0:
            raise RuntimeError(
                f"rank join failed for {missing_pct:.1f}% of rows — the decisions "
                f"parquets for {sets} do not cover the cached rows. Refusing to "
                f"report an elite cohort built from a broken join."
            )
    return out


# ---------------------------------------------------------------------------
# Cohort assignment + prediction
# ---------------------------------------------------------------------------


def assign_cohorts(df: pd.DataFrame, event_type: str, log: logging.Logger) -> pd.DataFrame:
    """Label each row with its cohort; drop rows outside every cohort."""
    wr = df["user_wr_raw"].round(2)
    cohort = pd.Series(pd.NA, index=df.index, dtype="object")
    for name, (lo, hi) in COHORT_BANDS[event_type].items():
        in_band = (wr >= lo) & (wr <= hi)
        if name == "elite" and event_type == "PremierDraft":
            excluded = in_band & ~df["rank"].isin(ELITE_PREMIER_RANKS)
            log.info(
                "  elite band rank filter: %d in WR band, %d excluded for rank below diamond",
                int(in_band.sum()),
                int(excluded.sum()),
            )
            in_band &= df["rank"].isin(ELITE_PREMIER_RANKS)
        cohort.loc[in_band] = name
    df = df.assign(cohort=cohort)
    kept = df.loc[df["cohort"].notna()].reset_index(drop=True)
    log.info(
        "  cohort assignment: %s of %s rows fall in a cohort",
        f"{len(kept):,}",
        f"{len(df):,}",
    )
    return kept


def predict_verdicts(
    df: pd.DataFrame,
    bundle: ChoiceModelBundle,
    log: logging.Logger,
) -> pd.DataFrame:
    """Batch-score cached feature rows and map p_keep to the 5 verdicts."""
    t0 = time.time()
    feature_cols = list(bundle.feature_names)
    matrix = df[feature_cols].astype(np.float64).to_numpy()
    dmatrix = xgb.DMatrix(matrix, feature_names=feature_cols)
    p_keep = bundle.booster.predict(dmatrix, iteration_range=(0, bundle.best_iteration + 1))
    # Same strict-> semantics as service._classify_choice_verdict:
    # p_keep of exactly 0.85 is marginal_keep, not clear_keep.
    verdict = np.select(
        [
            p_keep > CHOICE_CLEAR_KEEP_THRESHOLD,
            p_keep > CHOICE_MARGINAL_KEEP_THRESHOLD,
            p_keep > CHOICE_BORDERLINE_THRESHOLD,
            p_keep > CHOICE_MARGINAL_MULL_THRESHOLD,
        ],
        ["clear_keep", "marginal_keep", "borderline", "marginal_mulligan"],
        default="clear_mulligan",
    )
    log.info("  scored %s rows in %.0fs", f"{len(df):,}", time.time() - t0)
    return df.assign(p_keep=p_keep, verdict=verdict)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report_event(df: pd.DataFrame, event_type: str, log: logging.Logger) -> None:
    df = df.assign(
        actual_mull=~df["was_kept"].astype(bool),
        pred_mull=df["verdict"].isin(["clear_mulligan", "marginal_mulligan"]),
        is_clear=df["verdict"].isin(["clear_keep", "clear_mulligan"]),
        is_marginal=df["verdict"].isin(["marginal_keep", "marginal_mulligan"]),
    )

    summary_rows = []
    for name in COHORT_ORDER:
        sub = df.loc[df["cohort"] == name]
        n = len(sub)
        if n == 0:
            log.info("\n  --- %s / %s: no rows ---", event_type, name)
            continue
        judged = sub.loc[sub["verdict"] != "borderline"]
        n_judged = len(judged)
        n_borderline = n - n_judged
        wrong_side = judged["pred_mull"] != judged["actual_mull"]
        n_agree = n_judged - int(wrong_side.sum())
        n_soft = int((wrong_side & judged["is_marginal"]).sum())
        n_hard = int((wrong_side & judged["is_clear"]).sum())

        log.info("\n  --- %s / %s ---", event_type, name)
        log.info(
            "  n=%s decisions (%s drafts)   actual mull rate: %.2f%%",
            f"{n:,}",
            f"{sub['draft_id'].nunique():,}",
            100.0 * float(sub["actual_mull"].mean()),
        )
        log.info("  verdict distribution:")
        for v in VERDICTS:
            c = int((sub["verdict"] == v).sum())
            log.info("    %-18s  %6d  (%.2f%%)", v, c, 100.0 * c / n)
        log.info(
            "  judged: %s   borderline set aside: %s (%.2f%% of all)",
            f"{n_judged:,}",
            f"{n_borderline:,}",
            100.0 * n_borderline / n,
        )
        log.info(
            "  agreement:          %6d / %d  (%.2f%%)",
            n_agree,
            n_judged,
            100.0 * n_agree / n_judged,
        )
        log.info(
            "  soft disagreement:  %6d / %d  (%.2f%%)   (marginal verdict, wrong side)",
            n_soft,
            n_judged,
            100.0 * n_soft / n_judged,
        )
        log.info(
            "  hard disagreement:  %6d / %d  (%.2f%%)   (clear verdict, wrong side)",
            n_hard,
            n_judged,
            100.0 * n_hard / n_judged,
        )
        summary_rows.append(
            {
                "cohort": name,
                "n": n,
                "borderline_pct": 100.0 * n_borderline / n,
                "agree_pct": 100.0 * n_agree / n_judged,
                "soft_dis_pct": 100.0 * n_soft / n_judged,
                "hard_dis_pct": 100.0 * n_hard / n_judged,
                "actual_mull_pct": 100.0 * float(sub["actual_mull"].mean()),
            }
        )

    log.info("\n  ==== %s cohort summary (judged decisions only) ====", event_type)
    summary = pd.DataFrame(summary_rows).set_index("cohort")
    log.info("\n%s", summary.round(2).to_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--choice-model-dir",
        type=Path,
        default=DEFAULT_CHOICE_MODEL_DIR,
        help="Choice-model bundle dir. Default: models/choice_prod.",
    )
    ap.add_argument(
        "--sets",
        nargs="+",
        default=["TLA", "TMT", "SOS"],
        help="Set codes whose choice caches to pool. Default: TLA TMT SOS.",
    )
    ap.add_argument(
        "--event-types",
        nargs="+",
        default=["PremierDraft", "TradDraft"],
        choices=list(COHORT_BANDS.keys()),
    )
    ap.add_argument(
        "--min-n-games",
        type=int,
        default=MIN_N_GAMES,
        help=(
            "Minimum 17Lands games-played bucket, applied to EVERY cohort. "
            "The ladder is coarse (1/5/10/50/100/500/1000), so only 500 "
            "(default, the pinned cohort definition) and 100 (~20x the rows, "
            "but a noisier skill proxy) are meaningful."
        ),
    )
    args = ap.parse_args()

    log = setup_logger()
    log.info("==== Skill-cohort first-mulligan agreement (cache-based) ====")
    log.info("Choice model: %s", args.choice_model_dir)
    log.info("Sets pooled: %s   min n_games bucket: %d", args.sets, args.min_n_games)
    if args.min_n_games != MIN_N_GAMES:
        log.info(
            "NOTE: non-default games gate (%d, pinned default is %d) — cohorts are NOT "
            "comparable to previously published runs.",
            args.min_n_games,
            MIN_N_GAMES,
        )
    log.info("NOTE: in-sample scoring on 200-sim cached feature rows (see docstring).")

    bundle = ChoiceModelBundle.load(args.choice_model_dir)
    if bundle.version_warning:
        log.info("version warning: %s", bundle.version_warning)

    for event_type in args.event_types:
        log.info("\n=== %s ===", event_type)
        df = load_cohort_rows(
            args.sets, event_type, bundle.feature_names, log, min_n_games=args.min_n_games
        )
        if df.empty:
            log.info("  no rows; skipping")
            continue
        if event_type == "PremierDraft":
            df = join_premier_ranks(df, args.sets, log)
        df = assign_cohorts(df, event_type, log)
        if df.empty:
            log.info("  no cohort rows; skipping")
            continue
        df = predict_verdicts(df, bundle, log)
        report_event(df, event_type, log)


if __name__ == "__main__":
    main()
