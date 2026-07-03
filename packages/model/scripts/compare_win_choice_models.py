"""Compare the win and choice models against real skilled-player decisions.

We have two parallel models now:

* **Win model** (``models/all3_v2``) — predicts ``P(win | hand)``. The
  production verdict is ``p_keep < p_mull + MULLIGAN_BIAS`` -> mull.
* **Choice model** (``models/choice_v3``) — predicts
  ``P(skilled player keeps | hand)`` directly. The natural threshold
  is ``p_keep_choice < 0.5`` -> mull.

This script runs *both* models on the same skilled-player replay-data
decision sample (the same one
:mod:`replay_mulligan_benchmark` uses) and asks:

1. How often does each model agree with what the player actually did?
2. How often do the two models agree with each other?
3. On which hands do they diverge most — i.e. one model says keep
   confidently while the other says mull confidently?

The keep-arm prediction is shared between the two models: we simulate
once, build the feature row once, then call both XGBoost boosters on
that row. The mulligan arm (a Monte Carlo over fresh hands) is only
needed by the win model; the choice model's "mull" signal is just
``1 - p_keep_choice``.

Output
------
* ``<win-model-dir>/compare_win_choice.log`` — full report.
* ``<win-model-dir>/compare_win_choice.parquet`` — per-row predictions
  for downstream inspection.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from mulligan_coach_cards import ParsedCard
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_features import build_feature_row
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.choice_inference import (
    ChoiceModelBundle,
    predict_keep_probability_from_feature_row,
)
from mulligan_coach_model.feature_matrix import _library_from_deck
from mulligan_coach_model.inference import _predict_proba as _win_predict_proba
from mulligan_coach_recommend import (
    DEFAULT_N_MULLIGAN_SAMPLES,
    DEFAULT_N_SIMS_KEEP,
    DEFAULT_N_SIMS_PER_MULLIGAN,
    MULLIGAN_BIAS,
    FormatStats,
)
from mulligan_coach_simulation import simulate

REPO_ROOT = Path(__file__).resolve().parents[3]

# Reuse the existing benchmark's loading + mull-arm machinery — same
# data source, same skill filter, same deck-grouped sampling. Adding
# ``packages/model/scripts`` to sys.path so the import works even when
# this file is run as a bare script (no package context).
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from replay_mulligan_benchmark import (  # type: ignore[import-not-found]  # noqa: E402
    DECISIONS_PATH,
    EVENT_TYPE,
    _ArmKey,
    compute_mull_arms,
    load_skilled_decisions,
    resolve_decisions,
)

DEFAULT_WIN_MODEL_DIR = REPO_ROOT / "models" / "all3_v2"
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v3"
SAMPLE_SEED = 20260519


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("compare_win_choice")
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
# Worker setup — both bundles loaded once per process
# ---------------------------------------------------------------------------


_WORKER_WIN: ModelBundle | None = None
_WORKER_CHOICE: ChoiceModelBundle | None = None
_WORKER_STATS: dict[str, FormatStats] = {}


def _init_worker(win_model_dir_str: str, choice_model_dir_str: str, sets: tuple[str, ...]) -> None:
    """Per-process: load BOTH model bundles + per-set stats once.

    Workers are spawn-based on Windows so each child pays the load
    cost (~0.1s each). The two boosters live side by side and one
    simulate/feature-build call serves both.
    """
    global _WORKER_WIN, _WORKER_CHOICE, _WORKER_STATS
    _WORKER_WIN = ModelBundle.load(Path(win_model_dir_str))
    _WORKER_CHOICE = ChoiceModelBundle.load(Path(choice_model_dir_str))
    _WORKER_STATS = {
        s: FormatStats.build(load_premier_draft_stats(s).by_name.values()) for s in sets
    }


# ---------------------------------------------------------------------------
# Combined keep-arm worker: one sim, two boosters
# ---------------------------------------------------------------------------


_KeepWorkItem = tuple[
    int,  # row_idx
    list[ParsedCard],  # hand
    list[ParsedCard],  # deck
    str,  # set_code
    bool,  # on_play
    int | None,  # opp_mull
    int,  # n_sims
    int,  # seed
]


def _do_keep_both(item: _KeepWorkItem) -> tuple[int, float, float]:
    """Run simulate + build_feature_row ONCE, then call both models."""
    assert _WORKER_WIN is not None
    assert _WORKER_CHOICE is not None
    row_idx, hand, deck, set_code, on_play, opp_mull, n_sims, seed = item

    stats = _WORKER_STATS.get(set_code)
    shrunk = stats.shrunk if stats is not None else {}
    zscores = stats.zscores if stats is not None else {}

    library = _library_from_deck(tuple(hand), tuple(deck))
    aggregate = simulate(
        list(hand),
        list(library),
        on_the_play=on_play,
        n_runs=n_sims,
        seed=seed,
    )
    row = build_feature_row(
        hand=list(hand),
        deck=list(deck),
        aggregate_stats=aggregate,
        shrunk=shrunk,
        zscores=zscores,
        on_the_play=on_play,
        mulligan_number=0,
        event_type=EVENT_TYPE,
        set_code=set_code,
    )
    # Conditional opp_mull feature — matches what both training caches
    # filled in: NaN on the play, value on the draw.
    if on_play or opp_mull is None:
        row["opp_mulligan_count_if_known"] = float("nan")
    else:
        row["opp_mulligan_count_if_known"] = float(opp_mull)

    # Win model needs baseline residualisation; choice model doesn't.
    base_margin = _WORKER_WIN.baseline.margin(
        user_wr_bucket=None,
        user_n_games_bucket=None,
        on_the_play=on_play,
        opp_mulligan_number=opp_mull,
    )
    p_win = _win_predict_proba(_WORKER_WIN, row, base_margin)
    p_choice = predict_keep_probability_from_feature_row(_WORKER_CHOICE, row)
    return row_idx, float(p_win), float(p_choice)


def _seed_for_row(draft_id: str, game_number: int, build_index: int, match_number: int) -> int:
    """Stable per-row seed (mirrors the replay-benchmark hash)."""
    import hashlib

    digest = hashlib.sha256(
        repr((draft_id, build_index, match_number, game_number)).encode()
    ).digest()
    return int.from_bytes(digest[:4], "big")


def compute_keep_arms_both(
    *,
    df: pd.DataFrame,
    decks_by_draft: dict[str, tuple[list[ParsedCard], str]],
    win_model_dir: Path,
    choice_model_dir: Path,
    n_sims: int,
    n_workers: int,
    log: logging.Logger,
) -> tuple[np.ndarray, np.ndarray]:
    """Run both models' keep-side prediction in parallel.

    Returns ``(p_keep_win, p_keep_choice)`` as numpy arrays aligned
    with ``df`` row indices.
    """
    items: list[_KeepWorkItem] = []
    for i, row in enumerate(df.itertuples(index=False)):
        deck, set_code = decks_by_draft[str(row.draft_id)]
        opp_mull = (
            None
            if bool(row.on_play) or pd.isna(row.opp_num_mulligans)
            else int(row.opp_num_mulligans)
        )
        seed = _seed_for_row(
            str(row.draft_id),
            int(row.game_number),
            int(row.build_index),
            int(row.match_number),
        )
        items.append(
            (
                i,
                list(row.hand_cards),
                deck,
                set_code,
                bool(row.on_play),
                opp_mull,
                n_sims,
                seed,
            )
        )

    p_win = np.full(len(df), np.nan, dtype=float)
    p_choice = np.full(len(df), np.nan, dtype=float)

    log.info(
        "  keep-arm work items: %d  (n_sims=%d, n_workers=%d, two models per row)",
        len(items),
        n_sims,
        n_workers,
    )
    t0 = time.time()
    sets = tuple(sorted({s for _, s in decks_by_draft.values()}))

    if n_workers <= 1:
        _init_worker(str(win_model_dir), str(choice_model_dir), sets)
        for done, item in enumerate(items, start=1):
            idx, pw, pc = _do_keep_both(item)
            p_win[idx] = pw
            p_choice[idx] = pc
            if done % 100 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  keep progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                    done,
                    len(items),
                    elapsed,
                    eta,
                )
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(
            processes=n_workers,
            initializer=_init_worker,
            initargs=(str(win_model_dir), str(choice_model_dir), sets),
        ) as pool:
            for done, (idx, pw, pc) in enumerate(
                pool.imap_unordered(_do_keep_both, items, chunksize=4), start=1
            ):
                p_win[idx] = pw
                p_choice[idx] = pc
                if done % 100 == 0 or done == len(items):
                    elapsed = time.time() - t0
                    eta = elapsed * (len(items) - done) / done if done else 0.0
                    log.info(
                        "  keep progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                        done,
                        len(items),
                        elapsed,
                        eta,
                    )
    log.info("  keep arms done in %.0fs", time.time() - t0)
    return p_win, p_choice


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _confusion(df: pd.DataFrame, pred_col: str, actual_col: str, log: logging.Logger) -> None:
    """Print a 2x2 confusion matrix for one model's flag vs the player."""
    matrix = pd.crosstab(
        df[actual_col].map({True: "player mulled", False: "player kept"}),
        df[pred_col].map({True: "model says MULL", False: "model says keep"}),
        margins=True,
        margins_name="total",
    )
    log.info("\n%s", matrix.to_string())
    agree = int((df[pred_col] == df[actual_col]).sum())
    n = len(df)
    log.info("Agreement: %d / %d  (%.2f%%)", agree, n, 100.0 * agree / n if n else 0.0)


def _print_hand_examples(
    df: pd.DataFrame,
    *,
    label: str,
    n: int,
    log: logging.Logger,
) -> None:
    """Print top-N rows from ``df`` (already sorted) in a compact form."""
    if df.empty:
        log.info("  (none)")
        return
    for _, row in df.head(n).iterrows():
        play_draw = "play" if row["on_play"] else "draw"
        player_did = "MULLED" if row["actual_mull"] else "kept"
        win_did = "MULL" if row["pred_mull_win"] else "keep"
        choice_did = "MULL" if row["pred_mull_choice"] else "keep"
        log.info(
            "  [%s] %s mn0 %s  player=%s  win=%s (p_keep=%.3f p_mull=%.3f delta=%+.3f)  "
            "choice=%s (p_keep=%.3f)",
            row["expansion"],
            label,
            play_draw,
            player_did,
            win_did,
            row["p_keep_win"],
            row["p_mull_win"],
            row["p_keep_win"] - row["p_mull_win"],
            choice_did,
            row["p_keep_choice"],
        )
        log.info("      hand: %s", row["hand"])


def report(
    *,
    df: pd.DataFrame,
    n_sims_keep: int,
    n_sims_per_mulligan: int,
    n_mulligan_samples: int,
    min_n_games_bucket: int,
    min_wr_bucket: float,
    n_examples: int,
    log: logging.Logger,
) -> None:
    df = df.dropna(subset=["p_keep_win", "p_keep_choice", "p_mull_win"]).copy()

    # Two flag rules for the win model:
    # * unbiased: matches the older replay benchmark on all3_v1
    # * biased: matches production (overlay & website) using MULLIGAN_BIAS
    df["pred_mull_win"] = df["p_keep_win"] < (df["p_mull_win"] + MULLIGAN_BIAS)
    df["pred_mull_win_unbiased"] = df["p_keep_win"] < df["p_mull_win"]
    # Choice model: natural binary-classifier threshold.
    df["pred_mull_choice"] = df["p_keep_choice"] < 0.5
    df["actual_mull"] = ~df["was_kept"]

    # Continuous keep-direction scores. Sign tells you which way each
    # model leans; magnitude is its confidence. On different scales —
    # win uses arm-vs-arm deltas (~±0.05 typical); choice is a
    # probability around the 0.5 threshold (~±0.4 typical).
    df["score_win"] = df["p_keep_win"] - (df["p_mull_win"] + MULLIGAN_BIAS)
    df["score_choice"] = df["p_keep_choice"] - 0.5

    n = len(df)
    actual_mull_rate = float(df["actual_mull"].mean())
    log.info("\n==== Replay-data mull benchmark: win vs choice model ====")
    log.info(
        "Sample: %d decisions  (skilled: min_n_games_bucket>=%d, min_wr_bucket>=%.2f)",
        n,
        min_n_games_bucket,
        min_wr_bucket,
    )
    log.info("Across %d unique drafts", df["draft_id"].nunique())
    log.info(
        "Settings: keep n_sims=%d  mull %d samples x %d sims (deeper-mulligan floor on)",
        n_sims_keep,
        n_mulligan_samples,
        n_sims_per_mulligan,
    )
    log.info(
        "MULLIGAN_BIAS = %.3f  (win-model production verdict adds this to p_mull)", MULLIGAN_BIAS
    )

    log.info("\n---- Headline rates ----")
    log.info(
        "Actual mull rate (skilled players):           %.2f%% (%d / %d)",
        100.0 * actual_mull_rate,
        int(df["actual_mull"].sum()),
        n,
    )
    log.info(
        "Win model mull rate (production, +%.2f bias):  %.2f%% (%d / %d)",
        MULLIGAN_BIAS,
        100.0 * float(df["pred_mull_win"].mean()),
        int(df["pred_mull_win"].sum()),
        n,
    )
    log.info(
        "Win model mull rate (unbiased, p_keep<p_mull): %.2f%% (%d / %d)",
        100.0 * float(df["pred_mull_win_unbiased"].mean()),
        int(df["pred_mull_win_unbiased"].sum()),
        n,
    )
    log.info(
        "Choice model mull rate (p_keep<0.5):           %.2f%% (%d / %d)",
        100.0 * float(df["pred_mull_choice"].mean()),
        int(df["pred_mull_choice"].sum()),
        n,
    )
    log.info("Mean p_keep_win:        %.4f", float(df["p_keep_win"].mean()))
    log.info("Mean p_mull_win:        %.4f", float(df["p_mull_win"].mean()))
    log.info("Mean p_keep_choice:     %.4f", float(df["p_keep_choice"].mean()))

    log.info("\n---- Threshold that calibrates choice model to player rate ----")
    # What threshold on p_keep_choice reproduces the actual mull rate?
    # Sort p_keep_choice ascending; the k-th smallest defines the
    # threshold where exactly k decisions are flagged mull.
    sorted_choice = np.sort(df["p_keep_choice"].to_numpy())
    target_n = round(actual_mull_rate * n)
    if target_n == 0:
        log.info("  actual mull rate is zero — no calibration needed")
    elif target_n >= n:
        log.info("  actual mull rate is 100%% — no useful calibration")
    else:
        calibrated_t = float(sorted_choice[target_n])
        log.info(
            "  threshold %.4f reproduces the %.2f%% player mull rate exactly",
            calibrated_t,
            100.0 * actual_mull_rate,
        )
        calibrated_flag = df["p_keep_choice"] < calibrated_t
        agree = int((calibrated_flag == df["actual_mull"]).sum())
        log.info(
            "  agreement at this threshold: %d / %d (%.2f%%)",
            agree,
            n,
            100.0 * agree / n if n else 0.0,
        )

    log.info("\n---- Confusion matrix: win model (production +bias) vs player ----")
    _confusion(df, pred_col="pred_mull_win", actual_col="actual_mull", log=log)

    log.info("\n---- Confusion matrix: win model (unbiased) vs player ----")
    _confusion(df, pred_col="pred_mull_win_unbiased", actual_col="actual_mull", log=log)

    log.info("\n---- Confusion matrix: choice model (p_keep<0.5) vs player ----")
    _confusion(df, pred_col="pred_mull_choice", actual_col="actual_mull", log=log)

    log.info("\n---- Confusion matrix: win model (+bias) vs choice model ----")
    matrix = pd.crosstab(
        df["pred_mull_win"].map({True: "win says MULL", False: "win says keep"}),
        df["pred_mull_choice"].map({True: "choice says MULL", False: "choice says keep"}),
        margins=True,
        margins_name="total",
    )
    log.info("\n%s", matrix.to_string())
    cross_agree = int((df["pred_mull_win"] == df["pred_mull_choice"]).sum())
    log.info(
        "Cross-model agreement: %d / %d  (%.2f%%)",
        cross_agree,
        n,
        100.0 * cross_agree / n if n else 0.0,
    )

    # Quadrant outcomes — which model is "right" in each disagreement bucket?
    log.info("\n---- Game outcomes when win and choice disagree ----")
    log.info("Outcome shown is the actual game outcome of the player's chosen action.")

    def _quad(label: str, sub: pd.DataFrame) -> None:
        if len(sub) == 0:
            log.info("  %s: n=0", label)
            return
        log.info(
            "  %s: n=%5d  player kept=%.1f%%  actual WR=%.4f  "
            "mean p_keep_win=%.3f  mean p_mull_win=%.3f  mean p_keep_choice=%.3f",
            label,
            len(sub),
            100.0 * float(sub["was_kept"].mean()),
            float(sub["won"].mean()),
            float(sub["p_keep_win"].mean()),
            float(sub["p_mull_win"].mean()),
            float(sub["p_keep_choice"].mean()),
        )

    both_keep = df[(~df["pred_mull_win"]) & (~df["pred_mull_choice"])]
    both_mull = df[df["pred_mull_win"] & df["pred_mull_choice"]]
    win_mull_choice_keep = df[df["pred_mull_win"] & (~df["pred_mull_choice"])]
    win_keep_choice_mull = df[(~df["pred_mull_win"]) & df["pred_mull_choice"]]
    _quad("both KEEP", both_keep)
    _quad("both MULL", both_mull)
    _quad("WIN says MULL, CHOICE says keep", win_mull_choice_keep)
    _quad("WIN says keep, CHOICE says MULL", win_keep_choice_mull)

    log.info("\n---- Per-set breakdown ----")
    for set_code, sub in df.groupby("expansion"):
        log.info(
            "  %s: n=%5d  player mull=%.2f%%  win(+b) mull=%.2f%%  choice mull=%.2f%%  "
            "win-vs-player agree=%.2f%%  choice-vs-player agree=%.2f%%  "
            "win-vs-choice agree=%.2f%%",
            set_code,
            len(sub),
            100.0 * float(sub["actual_mull"].mean()),
            100.0 * float(sub["pred_mull_win"].mean()),
            100.0 * float(sub["pred_mull_choice"].mean()),
            100.0 * float((sub["pred_mull_win"] == sub["actual_mull"]).mean()),
            100.0 * float((sub["pred_mull_choice"] == sub["actual_mull"]).mean()),
            100.0 * float((sub["pred_mull_win"] == sub["pred_mull_choice"]).mean()),
        )

    log.info("\n---- On-play / on-draw breakdown ----")
    for on_play, sub in df.groupby("on_play"):
        label = "on play" if on_play else "on draw"
        log.info(
            "  %s: n=%5d  player mull=%.2f%%  win(+b) mull=%.2f%%  choice mull=%.2f%%  "
            "win-vs-player agree=%.2f%%  choice-vs-player agree=%.2f%%",
            label,
            len(sub),
            100.0 * float(sub["actual_mull"].mean()),
            100.0 * float(sub["pred_mull_win"].mean()),
            100.0 * float(sub["pred_mull_choice"].mean()),
            100.0 * float((sub["pred_mull_win"] == sub["actual_mull"]).mean()),
            100.0 * float((sub["pred_mull_choice"] == sub["actual_mull"]).mean()),
        )

    # ---- Divergence examples ----
    log.info("\n---- Most divergent hands (models disagree direction-wise) ----")
    diverge = df[df["pred_mull_win"] != df["pred_mull_choice"]].copy()
    # Confidence-weighted divergence: large |score_win| AND large |score_choice|
    # means both models are confident, just in opposite directions.
    diverge["divergence_score"] = diverge["score_win"].abs() + diverge["score_choice"].abs()

    log.info(
        "\nTop %d 'WIN says MULL, CHOICE says keep' (sorted by joint confidence):",
        n_examples,
    )
    a = diverge[diverge["pred_mull_win"] & (~diverge["pred_mull_choice"])].sort_values(
        "divergence_score", ascending=False
    )
    _print_hand_examples(a, label="WIN-mull/CHOICE-keep", n=n_examples, log=log)

    log.info(
        "\nTop %d 'WIN says keep, CHOICE says MULL' (sorted by joint confidence):",
        n_examples,
    )
    b = diverge[(~diverge["pred_mull_win"]) & diverge["pred_mull_choice"]].sort_values(
        "divergence_score", ascending=False
    )
    _print_hand_examples(b, label="WIN-keep/CHOICE-mull", n=n_examples, log=log)

    # Calibration spot-check: when both models say MULL the strongest,
    # how often was the player's kept hand actually a loss?
    log.info("\n---- Calibration sanity: low-keep tail ----")
    bottom_win = df.nsmallest(max(50, n // 50), "p_keep_win")
    log.info(
        "Bottom 2%% by p_keep_win (n=%d):  player mull=%.1f%%  actual WR (kept only)=%.4f  "
        "mean p_keep_choice=%.3f",
        len(bottom_win),
        100.0 * float(bottom_win["actual_mull"].mean()),
        float(bottom_win.loc[bottom_win["was_kept"], "won"].mean())
        if bottom_win["was_kept"].any()
        else float("nan"),
        float(bottom_win["p_keep_choice"].mean()),
    )
    bottom_choice = df.nsmallest(max(50, n // 50), "p_keep_choice")
    log.info(
        "Bottom 2%% by p_keep_choice (n=%d):  player mull=%.1f%%  actual WR (kept only)=%.4f  "
        "mean p_keep_win=%.3f  mean p_mull_win=%.3f",
        len(bottom_choice),
        100.0 * float(bottom_choice["actual_mull"].mean()),
        float(bottom_choice.loc[bottom_choice["was_kept"], "won"].mean())
        if bottom_choice["was_kept"].any()
        else float("nan"),
        float(bottom_choice["p_keep_win"].mean()),
        float(bottom_choice["p_mull_win"].mean()),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--win-model-dir", type=Path, default=DEFAULT_WIN_MODEL_DIR)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument("--decisions-path", type=Path, default=DECISIONS_PATH)
    ap.add_argument("--n-drafts", type=int, default=600)
    ap.add_argument("--min-n-games-bucket", type=int, default=100)
    ap.add_argument("--min-wr-bucket", type=float, default=0.58)
    ap.add_argument("--n-sims-keep", type=int, default=DEFAULT_N_SIMS_KEEP)
    ap.add_argument("--n-sims-per-mulligan", type=int, default=DEFAULT_N_SIMS_PER_MULLIGAN)
    ap.add_argument("--n-mulligan-samples", type=int, default=DEFAULT_N_MULLIGAN_SAMPLES)
    ap.add_argument("--n-workers", type=int, default=6)
    ap.add_argument("--n-examples", type=int, default=10, help="Divergence examples to surface")
    ap.add_argument("--out-stem", type=str, default="compare_win_choice")
    ap.add_argument("--seed", type=int, default=SAMPLE_SEED)
    args = ap.parse_args()

    log_path = args.win_model_dir / f"{args.out_stem}.log"
    log = setup_logger(log_path)
    log.info("==== %s ====", __doc__.splitlines()[0])
    log.info("Win model dir:    %s", args.win_model_dir)
    log.info("Choice model dir: %s", args.choice_model_dir)
    log.info("Decisions:        %s", args.decisions_path)
    log.info(
        "Settings: n_drafts=%d  skill>=(%d games, WR %.2f)  "
        "keep n_sims=%d  mull %d samples x %d sims  n_workers=%d",
        args.n_drafts,
        args.min_n_games_bucket,
        args.min_wr_bucket,
        args.n_sims_keep,
        args.n_mulligan_samples,
        args.n_sims_per_mulligan,
        args.n_workers,
    )

    log.info("\nLoading skilled decisions...")
    df_sample = load_skilled_decisions(
        parquet_path=args.decisions_path,
        min_n_games_bucket=args.min_n_games_bucket,
        min_wr_bucket=args.min_wr_bucket,
        n_drafts=args.n_drafts,
        log=log,
        exclude_drafts_from=None,
        seed=args.seed,
    )

    log.info("\nResolving decks and hands...")
    df, decks_by_draft = resolve_decisions(df_sample=df_sample, log=log)

    log.info("\nComputing keep arms (both models, one sim per row)...")
    p_keep_win, p_keep_choice = compute_keep_arms_both(
        df=df,
        decks_by_draft=decks_by_draft,
        win_model_dir=args.win_model_dir,
        choice_model_dir=args.choice_model_dir,
        n_sims=args.n_sims_keep,
        n_workers=args.n_workers,
        log=log,
    )
    df["p_keep_win"] = p_keep_win
    df["p_keep_choice"] = p_keep_choice

    log.info("\nComputing mulligan arms for the win model (deck-grouped, parallel)...")
    arms = compute_mull_arms(
        df=df,
        decks_by_draft=decks_by_draft,
        model_dir=args.win_model_dir,
        n_sims=args.n_sims_per_mulligan,
        n_samples=args.n_mulligan_samples,
        n_workers=args.n_workers,
        log=log,
    )

    # Stitch the per-group mull arm onto each row.
    p_mull = np.full(len(df), np.nan, dtype=float)
    p_mull_raw = np.full(len(df), np.nan, dtype=float)
    for i, row in enumerate(df.itertuples(index=False)):
        k = _ArmKey(
            draft_id=str(row.draft_id),
            on_play=bool(row.on_play),
            opp_mulligan_number=(
                None
                if bool(row.on_play) or pd.isna(row.opp_num_mulligans)
                else int(row.opp_num_mulligans)
            ),
        )
        arm = arms.get(k)
        if arm is not None:
            p_mull[i] = arm.floored_mean
            p_mull_raw[i] = arm.raw_mean
    df["p_mull_win"] = p_mull
    df["p_mull_win_raw"] = p_mull_raw

    out_parquet = args.win_model_dir / f"{args.out_stem}.parquet"
    out_df = df.drop(columns=["hand_cards"])
    out_df.to_parquet(out_parquet, index=False)
    log.info("\nWrote per-row results to %s", out_parquet)

    report(
        df=df,
        n_sims_keep=args.n_sims_keep,
        n_sims_per_mulligan=args.n_sims_per_mulligan,
        n_mulligan_samples=args.n_mulligan_samples,
        min_n_games_bucket=args.min_n_games_bucket,
        min_wr_bucket=args.min_wr_bucket,
        n_examples=args.n_examples,
        log=log,
    )
    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
