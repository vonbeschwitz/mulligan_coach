"""Measure keep-arm P(win) variance across smaller sim counts.

The overlay currently runs ``n_sims=1000`` for the keep arm (the
mulligan arm is a different beast — 50 fresh draws x 40 sims each,
where between-hand variance dominates so per-sample precision matters
less). This script answers: if we dialed n_sims down to 500 or 200,
would the displayed P(win) still be stable enough to act on?

Method
------

For each of ``--n-hands`` sampled real (hand, deck, on_play)
combinations from the 17Lands replay-data dataset (filtered the same
way ``replay_mulligan_benchmark`` does — skilled players, mn=0, kept
hands), and for each setting ``n_sims in {200, 500, 1000}``, we run
the keep arm ``--n-runs`` times with independent seeds. We then
compute the standard deviation of P(win) across those runs and
average across the sampled hand/deck combos.

Note: each ``(combo, n_sims, seed)`` triple is independent — we don't
share simulator work across n_sims settings, which would correlate
the estimates. The per-hand 1000-sim runs serve as a baseline for the
"inherent" variance we already live with.

Output
------

Printed table + a parquet (``keep_arm_variance.parquet``) under the
model dir, one row per (combo, n_sims) with the std/mean/min/max of
the P(win) draws.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from mulligan_coach_cards import ParsedCard
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.inference import predict_win_probability
from mulligan_coach_recommend import FormatStats, RecommendationService

# Reuse the existing parsing helpers — same code path the replay
# benchmark exercises, so we know the hands resolve cleanly.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_mulligan_benchmark import (
    DECISIONS_PATH,
    EVENT_TYPE,
    load_skilled_decisions,
    resolve_decisions,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = REPO_ROOT / "models" / "all3_v2"

SAMPLE_SEED = 20260519
RUN_SEED_BASE = 0xC0FFEE  # base for the per-run seed stream

DEFAULT_N_SIMS_LIST = (200, 500, 1000)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("keep_arm_variance")
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
# Worker
# ---------------------------------------------------------------------------


_WORKER_SERVICE: RecommendationService | None = None


def _init_worker(model_dir_str: str, sets: tuple[str, ...]) -> None:
    """Per-process: load model bundle + per-set 17Lands stats once."""
    global _WORKER_SERVICE
    bundle = ModelBundle.load(Path(model_dir_str))
    stats_by_set = {
        s: FormatStats.build(load_premier_draft_stats(s).by_arena_id.values()) for s in sets
    }
    _WORKER_SERVICE = RecommendationService(bundle=bundle, stats_by_set=stats_by_set)


# Work item shape:
#   (combo_idx, run_idx, n_sims, seed, hand, deck, set_code, on_play, opp_mull)
_WorkItem = tuple[
    int,
    int,
    int,
    int,
    list[ParsedCard],
    list[ParsedCard],
    str,
    bool,
    int | None,
]


def _do_one(item: _WorkItem) -> tuple[int, int, int, float]:
    """One keep-arm prediction at a specific ``(combo, n_sims, seed)``.

    Returns ``(combo_idx, n_sims, run_idx, p_keep)``.
    """
    assert _WORKER_SERVICE is not None
    combo_idx, run_idx, n_sims, seed, hand, deck, set_code, on_play, opp_mull = item
    service = _WORKER_SERVICE
    assert service.bundle is not None
    stats = service.stats_by_set.get(set_code)
    shrunk = stats.shrunk if stats is not None else {}
    zscores = stats.zscores if stats is not None else {}
    p_keep = predict_win_probability(
        service.bundle,
        hand=hand,
        deck=deck,
        on_the_play=on_play,
        mulligan_number=0,
        opp_mulligan_number=opp_mull,
        event_type=EVENT_TYPE,
        set_code=set_code,
        shrunk=shrunk,
        zscores=zscores,
        n_sims=n_sims,
        seed=seed,
    )
    return combo_idx, n_sims, run_idx, float(p_keep)


# ---------------------------------------------------------------------------
# Combo selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Combo:
    combo_idx: int
    set_code: str
    on_play: bool
    opp_mull: int | None
    hand: list[ParsedCard]
    deck: list[ParsedCard]
    hand_str: str  # for the audit log


def sample_combos(
    *,
    parquet_path: Path,
    n_hands: int,
    log: logging.Logger,
) -> list[Combo]:
    """Sample ``n_hands`` distinct (hand, deck, on_play) combos from
    the skilled-player kept-7 subset."""
    # Pull enough drafts that we'll definitely get ``n_hands`` resolvable
    # rows; the parsing step typically retains ~95%+ of input rows.
    df = load_skilled_decisions(
        parquet_path=parquet_path,
        min_n_games_bucket=100,
        min_wr_bucket=0.58,
        n_drafts=max(50, n_hands),
        log=log,
        seed=SAMPLE_SEED,
    )
    df, decks_by_draft = resolve_decisions(df_sample=df, log=log)

    # Keep only kept-7 rows; we want hands the player actually played.
    df = df.loc[df["was_kept"]].reset_index(drop=True)
    if len(df) == 0:
        raise SystemExit("No was_kept=True rows after resolution; widen the draft sample.")

    # Uniform random sample down to n_hands (without replacement).
    rng = np.random.default_rng(SAMPLE_SEED)
    take = min(n_hands, len(df))
    idx = rng.choice(len(df), size=take, replace=False)
    df = df.iloc[idx].reset_index(drop=True)
    log.info("  sampled %d combos from %d resolvable kept-7 rows", take, take)

    combos: list[Combo] = []
    for i, row in enumerate(df.itertuples(index=False)):
        deck, set_code = decks_by_draft[str(row.draft_id)]
        opp_mull = (
            None
            if bool(row.on_play) or pd.isna(row.opp_num_mulligans)
            else int(row.opp_num_mulligans)
        )
        combos.append(
            Combo(
                combo_idx=i,
                set_code=set_code,
                on_play=bool(row.on_play),
                opp_mull=opp_mull,
                hand=list(row.hand_cards),
                deck=deck,
                hand_str=str(row.hand),
            )
        )
    return combos


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    ap.add_argument("--decisions-path", type=Path, default=DECISIONS_PATH)
    ap.add_argument(
        "--n-hands",
        type=int,
        default=30,
        help="How many distinct (hand, deck, on_play) combinations to test.",
    )
    ap.add_argument(
        "--n-runs",
        type=int,
        default=40,
        help="How many independent re-runs per (combo, n_sims).",
    )
    ap.add_argument(
        "--n-sims-list",
        type=int,
        nargs="+",
        default=list(DEFAULT_N_SIMS_LIST),
        help="n_sims values to evaluate (the 1000 baseline included).",
    )
    ap.add_argument("--n-workers", type=int, default=8)
    args = ap.parse_args()

    log_path = args.model_dir / "keep_arm_variance.log"
    log = setup_logger(log_path)
    log.info("==== Keep-arm P(win) variance benchmark ====")
    log.info("Model dir:    %s", args.model_dir)
    log.info("Decisions:    %s", args.decisions_path)
    log.info("n_hands:      %d", args.n_hands)
    log.info("n_runs:       %d (per combo, per n_sims)", args.n_runs)
    log.info("n_sims_list:  %s", args.n_sims_list)
    log.info("n_workers:    %d", args.n_workers)

    # ---- Sample combos ----------------------------------------------------
    log.info("\nSampling combos...")
    combos = sample_combos(
        parquet_path=args.decisions_path,
        n_hands=args.n_hands,
        log=log,
    )

    # ---- Build work items -------------------------------------------------
    # For each (combo, n_sims) pair we produce ``n_runs`` work items with
    # distinct seeds. Seeds derived from a deterministic stream so the
    # whole benchmark is reproducible.
    items: list[_WorkItem] = []
    rng_seed = np.random.default_rng(RUN_SEED_BASE)
    for c in combos:
        for n_sims in args.n_sims_list:
            for run_idx in range(args.n_runs):
                seed = int(rng_seed.integers(0, 2**31 - 1))
                items.append(
                    (
                        c.combo_idx,
                        run_idx,
                        n_sims,
                        seed,
                        c.hand,
                        c.deck,
                        c.set_code,
                        c.on_play,
                        c.opp_mull,
                    )
                )
    log.info(
        "\nTotal work items: %d  (%d combos x %d n_sims x %d runs)",
        len(items),
        len(combos),
        len(args.n_sims_list),
        args.n_runs,
    )

    # ---- Run --------------------------------------------------------------
    sets = tuple(sorted({c.set_code for c in combos}))
    log.info("  sets needed: %s", sets)

    results: list[tuple[int, int, int, float]] = []
    t0 = time.time()
    if args.n_workers <= 1:
        _init_worker(str(args.model_dir), sets)
        for done, item in enumerate(items, start=1):
            results.append(_do_one(item))
            if done % 200 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                    done,
                    len(items),
                    elapsed,
                    eta,
                )
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(
            processes=args.n_workers,
            initializer=_init_worker,
            initargs=(str(args.model_dir), sets),
        ) as pool:
            for done, res in enumerate(pool.imap_unordered(_do_one, items, chunksize=4), start=1):
                results.append(res)
                if done % 200 == 0 or done == len(items):
                    elapsed = time.time() - t0
                    eta = elapsed * (len(items) - done) / done if done else 0.0
                    log.info(
                        "  progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                        done,
                        len(items),
                        elapsed,
                        eta,
                    )
    log.info("  all runs done in %.0fs", time.time() - t0)

    # ---- Aggregate --------------------------------------------------------
    raw = pd.DataFrame(results, columns=["combo_idx", "n_sims", "run_idx", "p_keep"])

    agg = (
        raw.groupby(["combo_idx", "n_sims"])["p_keep"]
        .agg(["mean", "std", "min", "max", "count"])
        .reset_index()
        .rename(columns={"mean": "mean_p_keep", "std": "std_p_keep"})
    )

    # ---- Report -----------------------------------------------------------
    log.info("\n==== Per-n_sims summary ====")
    log.info(
        "%-8s  %-12s  %-12s  %-12s  %-12s",
        "n_sims",
        "avg_std",
        "median_std",
        "max_std",
        "min_std",
    )
    summary_rows: list[dict[str, float]] = []
    for n_sims in sorted(args.n_sims_list):
        sub = agg.loc[agg["n_sims"] == n_sims, "std_p_keep"]
        row = {
            "n_sims": int(n_sims),
            "avg_std": float(sub.mean()),
            "median_std": float(sub.median()),
            "max_std": float(sub.max()),
            "min_std": float(sub.min()),
        }
        summary_rows.append(row)
        log.info(
            "%-8d  %-12.5f  %-12.5f  %-12.5f  %-12.5f",
            int(n_sims),
            row["avg_std"],
            row["median_std"],
            row["max_std"],
            row["min_std"],
        )

    log.info("\n==== Per-combo std dev by n_sims ====")
    pivot = agg.pivot(index="combo_idx", columns="n_sims", values="std_p_keep")
    pivot = pivot.reindex(columns=sorted(args.n_sims_list))
    pivot["mean_p_keep_1000"] = agg.loc[agg["n_sims"] == max(args.n_sims_list)].set_index(
        "combo_idx"
    )["mean_p_keep"]
    log.info(
        "%-6s  %-10s  %s",
        "combo",
        "p_keep~",
        "  ".join(f"std@{n}".rjust(10) for n in sorted(args.n_sims_list)),
    )
    for idx, row in pivot.iterrows():
        std_cells = "  ".join(
            f"{row[n]:>10.5f}" if pd.notna(row[n]) else " " * 10 for n in sorted(args.n_sims_list)
        )
        log.info(
            "%-6d  %-10.4f  %s",
            int(idx),
            float(row["mean_p_keep_1000"]) if pd.notna(row["mean_p_keep_1000"]) else float("nan"),
            std_cells,
        )

    # ---- Save -------------------------------------------------------------
    out_parquet = args.model_dir / "keep_arm_variance.parquet"
    agg.to_parquet(out_parquet, index=False)
    log.info("\nPer-(combo, n_sims) stats -> %s", out_parquet)
    raw_parquet = args.model_dir / "keep_arm_variance_raw.parquet"
    raw.to_parquet(raw_parquet, index=False)
    log.info("Raw P(win) draws       -> %s", raw_parquet)
    log.info("Full log               -> %s", log_path)


if __name__ == "__main__":
    main()
