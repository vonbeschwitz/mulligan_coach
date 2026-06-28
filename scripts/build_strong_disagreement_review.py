"""Materialize the *strong* model-vs-elite-player disagreements for review.

"Strong disagreement" = the two confidently-wrong cells of the choice
model's confusion matrix against elite players' first-mulligan decisions
(the same elite cohort + methodology as
``scripts/elite_first_mull_agreement.py``):

* model verdict ``clear_keep`` (p_keep > 0.75) BUT the player **mulled**, and
* model verdict ``clear_mulligan`` (p_keep <= 0.25) BUT the player **kept**.

(``marginal_*`` verdicts on the "wrong" side are excluded — the model
wasn't confident there, so those aren't *strong* disagreements.)

This generalizes ``scripts/build_clear_keep_mulled_review.py``, which only
covers the first direction. Because a kept hand can only produce a
``clear_mulligan`` disagreement and a mulled hand only a ``clear_keep``
one, we have to run the choice model on the *whole* elite cohort (both
kept and mulled) to get every row's verdict — there's no cheap pre-filter
like the single-direction script had.

Output is a reviewer-compatible parquet for ``scripts/mulligan_reviewer/app.py``:

* ``p_keep_choice`` is the universal-required column.
* ``p_keep_win`` / ``p_mull_win`` are left NaN (we don't run the win
  model), so review with ``--preset all`` (the win-arm presets would
  filter every row out via NaN comparisons).
* ``verdict`` and ``disagreement`` columns are added for downstream
  triage even though the reviewer template doesn't render them.

Run (defaults: SOS PremierDraft):
    .venv/Scripts/python.exe scripts/build_strong_disagreement_review.py
    .venv/Scripts/python.exe scripts/mulligan_reviewer/app.py \\
        --parquet data/processed/mulligan_review/strong_disagreement.SOS.PremierDraft.parquet \\
        --preset all
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from mulligan_coach_cards import ParsedCard, ParseStatus
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_model import ChoiceModelBundle, build_name_lookup
from mulligan_coach_recommend import FormatStats, RecommendationService

REPO_ROOT = Path(__file__).resolve().parents[1]
DECISIONS_DIR = REPO_ROOT / "data" / "processed" / "seventeenlands" / "mulligan_decisions"
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v6"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "processed" / "mulligan_review"

# Elite cohort definitions — pinned to match CLAUDE.md and
# scripts/elite_first_mull_agreement.py. Keep these in sync.
ELITE_DEFS: dict[str, dict[str, Any]] = {
    "PremierDraft": {
        "min_n_games_bucket": 500,
        "min_wr_bucket": 0.62,
        "ranks": {"diamond", "mythic"},
        "parquet": "combined.PremierDraft.parquet",
    },
    "TradDraft": {
        "min_n_games_bucket": 500,
        "min_wr_bucket": 0.68,
        "ranks": None,
        "parquet": "combined.TradDraft.parquet",
    },
}


def setup_logger() -> logging.Logger:
    log = logging.getLogger("build_strong_disagreement")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_elite_decisions(log: logging.Logger, set_code: str, event_type: str) -> pd.DataFrame:
    spec = ELITE_DEFS[event_type]
    parquet = DECISIONS_DIR / spec["parquet"]
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
        & (df["user_n_games_bucket"] >= spec["min_n_games_bucket"])
        & (df["user_game_win_rate_bucket"] >= spec["min_wr_bucket"])
    )
    if spec["ranks"] is not None:
        mask &= df["rank"].isin(spec["ranks"])
    df = df.loc[mask].reset_index(drop=True)
    log.info("  elite mn=0 subset for %s/%s: %s rows", event_type, set_code, f"{len(df):,}")
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


def _do_one(item: _WorkItem) -> tuple[int, float, str]:
    """Return (row_idx, p_keep_choice, verdict)."""
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
    return idx, float(rec.p_keep), str(rec.verdict)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="SOS")
    ap.add_argument(
        "--event-type",
        default="PremierDraft",
        choices=sorted(ELITE_DEFS.keys()),
    )
    ap.add_argument("--n-sims", type=int, default=300)
    ap.add_argument("--n-workers", type=int, default=6)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument(
        "--out-parquet",
        type=Path,
        default=None,
        help="Output parquet path. Defaults to "
        "data/processed/mulligan_review/strong_disagreement.<SET>.<EVENT>.parquet.",
    )
    args = ap.parse_args()

    out_parquet = args.out_parquet or (
        DEFAULT_OUT_DIR / f"strong_disagreement.{args.set}.{args.event_type}.parquet"
    )

    log = setup_logger()
    log.info("==== Build strong-disagreement review parquet ====")
    log.info(
        "Set: %s   Event: %s   n_sims: %d   n_workers: %d",
        args.set,
        args.event_type,
        args.n_sims,
        args.n_workers,
    )
    log.info("Choice model: %s", args.choice_model_dir)
    log.info("Output: %s", out_parquet)
    if not args.choice_model_dir.exists():
        log.error("Choice-model dir does not exist; aborting.")
        sys.exit(1)

    np.random.seed(0)

    df = load_elite_decisions(log, args.set, args.event_type)
    if df.empty:
        log.info("No elite rows; nothing to do.")
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
    if df.empty:
        return

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
    verdicts: list[str | None] = [None] * len(df)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(
        processes=args.n_workers,
        initializer=_init_worker,
        initargs=(str(args.choice_model_dir), args.set),
    ) as pool:
        for done, (idx, pk, verdict) in enumerate(
            pool.imap_unordered(_do_one, items, chunksize=4), 1
        ):
            p_keep_choice[idx] = pk
            verdicts[idx] = verdict
            if done % 100 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  progress %d/%d  elapsed=%.0fs  eta=%.0fs", done, len(items), elapsed, eta
                )
    log.info("  inference done in %.0fs", time.time() - t0)

    df["p_keep_choice"] = p_keep_choice
    df["verdict"] = verdicts
    # Win-model columns expected by the reviewer template — left NaN
    # since we didn't run the win model (review with --preset all).
    df["p_keep_win"] = np.nan
    df["p_mull_win"] = np.nan

    # Strong disagreement = confidently-wrong cells only.
    clear_keep_mulled = (df["verdict"] == "clear_keep") & (~df["was_kept"])
    clear_mull_kept = (df["verdict"] == "clear_mulligan") & (df["was_kept"])
    df["disagreement"] = np.select(
        [clear_keep_mulled, clear_mull_kept],
        ["clear_keep_player_mulled", "clear_mulligan_player_kept"],
        default="",
    )
    strong = df.loc[clear_keep_mulled | clear_mull_kept].reset_index(drop=True)

    log.info("")
    log.info("  strong disagreements: %d / %d total elite decisions", len(strong), len(df))
    log.info(
        "    clear_keep  + player mulled: %d",
        int((strong["disagreement"] == "clear_keep_player_mulled").sum()),
    )
    log.info(
        "    clear_mull  + player kept:   %d",
        int((strong["disagreement"] == "clear_mulligan_player_kept").sum()),
    )

    if strong.empty:
        log.info("No strong disagreements found; not writing a parquet.")
        return

    # Drop the ParsedCard-list column before serialization (pyarrow can't
    # pickle it cleanly and the reviewer reads the string ``hand`` column).
    strong = strong.drop(columns=["hand_cards"])

    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    strong.to_parquet(out_parquet, compression="zstd", index=False)
    log.info(
        "Wrote %s (%d rows, %.1f KB)", out_parquet, len(strong), out_parquet.stat().st_size / 1024
    )
    log.info("")
    log.info("To review:")
    log.info(
        "  .venv/Scripts/python.exe scripts/mulligan_reviewer/app.py --parquet %s --preset all",
        out_parquet,
    )


if __name__ == "__main__":
    main()
