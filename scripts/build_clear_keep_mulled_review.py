"""Materialize the "model says clear_keep but player mulled" cohort for review.

Loads the same elite PremierDraft / TLA cohort that
``scripts/elite_first_mull_agreement.py`` uses, but runs the choice
model only on the **mulled** decisions (``was_kept = False``) — that's
the only side that can produce a "clear_keep + player mulled"
disagreement, so we save ~10x the compute vs. running the full cohort.

Then it filters to ``p_keep_choice > 0.75`` (the choice model's
``clear_keep`` band) and writes a parquet shaped for
``scripts/mulligan_reviewer/app.py``:

* The reviewer's universal-required column is ``p_keep_choice``.
* Win-model columns (``p_keep_win``, ``p_mull_win``) are left ``NaN``;
  the reviewer's ``dropna`` only enforces ``p_keep_choice`` and the
  ``all`` preset doesn't reference the win arms. The template will
  render the win-model rows as ``"nan"`` — honest about the fact that
  this view is choice-model-only.

Run:
    .venv/Scripts/python.exe scripts/build_clear_keep_mulled_review.py
    .venv/Scripts/python.exe scripts/mulligan_reviewer/app.py \\
        --parquet models/choice_v6/clear_keep_player_mulled.parquet \\
        --preset all
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
from mulligan_coach_cards import ParsedCard, ParseStatus
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_model import ChoiceModelBundle, build_name_lookup
from mulligan_coach_recommend import FormatStats, RecommendationService

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS_DIR = REPO_ROOT / "data" / "processed" / "seventeenlands" / "mulligan_decisions"
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v6"
DEFAULT_OUT_PARQUET = DEFAULT_CHOICE_MODEL_DIR / "clear_keep_player_mulled.parquet"

# Pinned to match scripts/elite_first_mull_agreement.py.
ELITE_FILTER = {
    "min_n_games_bucket": 500,
    "min_wr_bucket": 0.62,
    "ranks": {"diamond", "mythic"},
}


def setup_logger() -> logging.Logger:
    log = logging.getLogger("build_clear_keep_review")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_elite_mulled(log: logging.Logger, set_code: str) -> pd.DataFrame:
    parquet = DECISIONS_DIR / "combined.PremierDraft.parquet"
    cols = [
        "expansion",
        "rank",
        "user_n_games_bucket",
        "user_game_win_rate_bucket",
        "draft_id",
        "build_index",
        "match_number",
        "game_number",
        "on_play",
        "opp_num_mulligans",
        "num_mulligans_in_game",
        "won",
        "mulligan_number",
        "was_kept",
        "hand_size",
        "hand",
        "deck",
    ]
    df = pd.read_parquet(parquet, columns=cols)
    log.info("  loaded %s rows from %s", f"{len(df):,}", parquet.name)
    mask = (
        (df["expansion"] == set_code)
        & (df["mulligan_number"] == 0)
        & (df["hand_size"] == 7)
        & (~df["was_kept"])
        & (df["user_n_games_bucket"] >= ELITE_FILTER["min_n_games_bucket"])
        & (df["user_game_win_rate_bucket"] >= ELITE_FILTER["min_wr_bucket"])
        & (df["rank"].isin(ELITE_FILTER["ranks"]))
    )
    df = df.loc[mask].reset_index(drop=True)
    log.info("  elite mulled mn=0 subset for %s: %s rows", set_code, f"{len(df):,}")
    return df


# Hand/deck parsing — same conventions as elite_first_mull_agreement.py.
def _parse_deck_string(s: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    cards: list[ParsedCard] = []
    for part in s.split(" | "):
        name, sep, count_str = part.rpartition(" x")
        if not sep or not count_str.isdigit():
            return None
        c = lookup.get(name)
        if c is None:
            return None
        cards.extend([c] * int(count_str))
    return cards


def _parse_hand_string(s: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    out: list[ParsedCard] = []
    for name in s.split("|"):
        c = lookup.get(name)
        if c is None:
            return None
        out.append(c)
    return out


def _deck_is_simulator_safe(deck: list[ParsedCard]) -> bool:
    for c in deck:
        if c.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
            return False
        if c.mana_cost is not None and not c.modes:
            return False
    return True


_WORKER_SERVICE: RecommendationService | None = None


def _init_worker(choice_model_dir_str: str, set_code: str) -> None:
    global _WORKER_SERVICE
    cb = ChoiceModelBundle.load(Path(choice_model_dir_str))
    stats = FormatStats.build(load_premier_draft_stats(set_code).by_arena_id.values())
    _WORKER_SERVICE = RecommendationService(
        bundle=None,
        choice_bundle=cb,
        stats_by_set={set_code: stats},
    )


_WorkItem = tuple[int, list[ParsedCard], list[ParsedCard], bool, int | None, int]


def _do_one(item: _WorkItem) -> tuple[int, float]:
    """Return (row_idx, p_keep_choice)."""
    assert _WORKER_SERVICE is not None
    idx, hand, deck, on_play, opp_mull, n_sims = item
    rec = _WORKER_SERVICE.recommend_choice(
        hand=hand,
        deck=deck,
        on_the_play=on_play,
        mulligan_number=0,
        opp_mulligan_number=opp_mull,
        n_sims=n_sims,
    )
    return idx, float(rec.p_keep)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="TLA")
    ap.add_argument("--n-sims", type=int, default=300)
    ap.add_argument("--n-workers", type=int, default=6)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument(
        "--out-parquet",
        type=Path,
        default=DEFAULT_OUT_PARQUET,
        help="Where to write the reviewer-compatible parquet.",
    )
    ap.add_argument(
        "--p-keep-threshold",
        type=float,
        default=0.75,
        help="Only save rows with p_keep_choice > this (default: 0.75 = clear_keep band).",
    )
    args = ap.parse_args()

    log = setup_logger()
    log.info("==== Build clear-keep-but-mulled review parquet ====")
    log.info(
        "Set: %s   n_sims: %d   n_workers: %d   p_keep > %.2f",
        args.set,
        args.n_sims,
        args.n_workers,
        args.p_keep_threshold,
    )
    log.info("Choice model: %s", args.choice_model_dir)
    log.info("Output: %s", args.out_parquet)
    if not args.choice_model_dir.exists():
        log.error("Choice-model dir does not exist; aborting.")
        sys.exit(1)

    np.random.seed(0)

    df = load_elite_mulled(log, args.set)
    if df.empty:
        log.info("No elite mulled rows; nothing to do.")
        return

    # Parse decks (one per draft) and hands.
    lookup = build_name_lookup(args.set)
    decks_by_draft: dict[str, list[ParsedCard]] = {}
    dropped = {"resolve": 0, "size": 0, "unsafe": 0}
    for draft_id, sub in df.groupby("draft_id"):
        row = sub.iloc[0]
        deck = _parse_deck_string(str(row["deck"]), lookup)
        if deck is None:
            dropped["resolve"] += 1
            continue
        if not (40 <= len(deck) <= 42):
            dropped["size"] += 1
            continue
        if not _deck_is_simulator_safe(deck):
            dropped["unsafe"] += 1
            continue
        decks_by_draft[str(draft_id)] = deck
    log.info(
        "  decks parsed: %d kept  (dropped %d unresolved, %d size, %d unsafe)",
        len(decks_by_draft),
        dropped["resolve"],
        dropped["size"],
        dropped["unsafe"],
    )

    df = df.loc[df["draft_id"].astype(str).isin(decks_by_draft)].reset_index(drop=True)
    hands = [_parse_hand_string(str(r["hand"]), lookup) for _, r in df.iterrows()]
    df["hand_cards"] = hands
    bad = df["hand_cards"].isna()
    if bad.any():
        log.info("  dropping %d rows with unresolvable hand cards", int(bad.sum()))
        df = df.loc[~bad].reset_index(drop=True)
    log.info("  rows after parse: %s", f"{len(df):,}")

    items: list[_WorkItem] = []
    for i, row in enumerate(df.itertuples(index=False)):
        on_play = bool(row.on_play)
        opp_mull = None if on_play or pd.isna(row.opp_num_mulligans) else int(row.opp_num_mulligans)
        items.append(
            (
                i,
                list(row.hand_cards),
                decks_by_draft[str(row.draft_id)],
                on_play,
                opp_mull,
                args.n_sims,
            )
        )

    p_keep_choice = np.full(len(df), np.nan, dtype=float)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(
        processes=args.n_workers,
        initializer=_init_worker,
        initargs=(str(args.choice_model_dir), args.set),
    ) as pool:
        for done, (idx, pk) in enumerate(pool.imap_unordered(_do_one, items, chunksize=4), 1):
            p_keep_choice[idx] = pk
            if done % 50 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                    done,
                    len(items),
                    elapsed,
                    eta,
                )
    log.info("  inference done in %.0fs", time.time() - t0)

    df["p_keep_choice"] = p_keep_choice
    # Win-model columns expected by the reviewer template — left NaN
    # since we didn't run the win model. Reviewer's loosened dropna
    # only requires p_keep_choice.
    df["p_keep_win"] = np.nan
    df["p_mull_win"] = np.nan

    n_total = len(df)
    keep = df[df["p_keep_choice"] > args.p_keep_threshold].reset_index(drop=True)
    log.info(
        "  rows with p_keep_choice > %.2f: %d / %d (%.2f%%)",
        args.p_keep_threshold,
        len(keep),
        n_total,
        100.0 * len(keep) / n_total if n_total else 0.0,
    )

    # Drop the ParsedCard-list column before serialization (pyarrow
    # can't pickle it cleanly and the reviewer reads the string).
    keep = keep.drop(columns=["hand_cards"])

    args.out_parquet.parent.mkdir(parents=True, exist_ok=True)
    keep.to_parquet(args.out_parquet, compression="zstd", index=False)
    log.info(
        "Wrote %s (%d rows, %.1f KB)",
        args.out_parquet,
        len(keep),
        args.out_parquet.stat().st_size / 1024,
    )
    log.info("")
    log.info("To review:")
    log.info(
        "  .venv/Scripts/python.exe scripts/mulligan_reviewer/app.py --parquet %s --preset all",
        args.out_parquet,
    )


if __name__ == "__main__":
    main()
