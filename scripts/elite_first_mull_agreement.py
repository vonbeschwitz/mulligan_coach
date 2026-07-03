"""Elite first-mulligan agreement check for the production choice model.

Loads the production choice-model bundle (``models/choice_v6`` by
default) and asks how often its 4-level verdict
(clear/marginal x keep/mull) agrees with the actual first-mulligan
decisions made by elite players on TLA, separately for PremierDraft
and TradDraft.

"Elite" cohort definitions are pinned in the root ``CLAUDE.md``:

* PremierDraft elite — ``rank in {diamond, mythic}`` AND
  ``user_n_games_bucket >= 500`` AND
  ``user_game_win_rate_bucket >= 0.62``.
* TradDraft elite — ``user_n_games_bucket >= 500`` AND
  ``user_game_win_rate_bucket >= 0.68`` (rank filter dropped — Trad
  is smaller and Bo3 already self-selects).

Two summary numbers per event type:

1. **Agreement** — the model's keep-or-mull side (any clear/marginal
   on the same side) matches the player's actual choice.
2. **Not-complete-disagreement** — same minus the two confidently-
   wrong cells: model says ``clear_keep`` but player mulled, or
   ``clear_mulligan`` but player kept. Marginal verdicts on the
   "wrong" side are still tolerated, on the grounds that the model
   wasn't sure either way.

Important: this is an **in-sample** test for both passes.
``models/choice_v6`` was trained on TLA *and* TMT, *and* on both
PremierDraft and TradDraft chunks (see ``logs/tune_choice_v6.log``
— it ingests all four ``(set x event_type)`` cache directories,
~766k rows total). Both the Premier and Trad agreement numbers
here are therefore optimistic relative to a true held-out
evaluation.

Why the WR/n_games scope claim in the root CLAUDE.md
----------------------------------------------------
``user_game_win_rate_bucket`` is empirically per-event-type, not a
combined lifetime stat. Within the same parquets, PremierDraft means
WR mean 0.546 (median 0.54) vs TradDraft mean 0.601 (median 0.62) —
a 5.5pp gap that is not explainable by player self-selection of a
single combined number. We treat the bucket as scoped to the row's
event_type, which is why the Premier and Trad cohorts use different
cutoffs.

Run with:

    .venv/Scripts/python.exe scripts/elite_first_mull_agreement.py

Flags: ``--n-sims`` (default 300 — enough for verdict-bucket stability
without paying the website's full 1000), ``--n-workers`` (default 6).
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

# Pinned in CLAUDE.md. If these change, also edit the "Elite player
# cohorts" section there so the docs stay in sync.
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

VERDICTS = ("clear_keep", "marginal_keep", "marginal_mulligan", "clear_mulligan")


def setup_logger() -> logging.Logger:
    log = logging.getLogger("elite_first_mull_agreement")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


# ---------------------------------------------------------------------------
# Cohort loading
# ---------------------------------------------------------------------------


def load_elite_decisions(
    event_type: str,
    set_code: str,
    log: logging.Logger,
) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# Hand / deck parsing — same convention as the replay benchmark
# ---------------------------------------------------------------------------


def _parse_deck_string(s: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    cards: list[ParsedCard] = []
    for part in s.split(" | "):
        name, sep, count_str = part.rpartition(" x")
        if not sep or not count_str.isdigit():
            return None
        card = lookup.get(name)
        if card is None:
            return None
        cards.extend([card] * int(count_str))
    return cards


def _parse_hand_string(s: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    out: list[ParsedCard] = []
    for name in s.split("|"):
        card = lookup.get(name)
        if card is None:
            return None
        out.append(card)
    return out


def _deck_is_simulator_safe(deck: list[ParsedCard]) -> bool:
    for c in deck:
        if c.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
            return False
        if c.mana_cost is not None and not c.modes:
            return False
    return True


# ---------------------------------------------------------------------------
# Worker setup — one RecommendationService per child process
# ---------------------------------------------------------------------------


_WORKER_SERVICE: RecommendationService | None = None


def _init_worker(choice_model_dir_str: str, set_code: str) -> None:
    global _WORKER_SERVICE
    cb = ChoiceModelBundle.load(Path(choice_model_dir_str))
    stats = FormatStats.build(load_premier_draft_stats(set_code).by_name.values())
    # bundle=None — we only call recommend_choice, which needs the choice bundle.
    _WORKER_SERVICE = RecommendationService(
        bundle=None,
        choice_bundle=cb,
        stats_by_set={set_code: stats},
    )


# Per-row payload. Tuple keeps cross-process pickle cheap.
_WorkItem = tuple[int, list[ParsedCard], list[ParsedCard], bool, int | None, int]


def _do_one(item: _WorkItem) -> tuple[int, str]:
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
    return idx, rec.verdict


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate(
    *,
    event_type: str,
    set_code: str,
    choice_model_dir: Path,
    n_sims: int,
    n_workers: int,
    log: logging.Logger,
    min_deck_size: int = 40,
    max_deck_size: int = 42,
    allowed_draft_ids: set[str] | None = None,
) -> None:
    log.info("\n=== %s / %s ===", event_type, set_code)
    log.info("  Elite filter: %s", ELITE_DEFS[event_type])

    df = load_elite_decisions(event_type, set_code, log)
    if allowed_draft_ids is not None:
        before = len(df)
        df = df.loc[df["draft_id"].astype(str).isin(allowed_draft_ids)].reset_index(drop=True)
        log.info(
            "  draft-id allowlist applied: %d -> %d rows (allowlist has %d ids)",
            before,
            len(df),
            len(allowed_draft_ids),
        )
    if df.empty:
        log.info("  no elite rows; skipping")
        return

    # Parse decks (one per draft_id) and hands.
    lookup = build_name_lookup(set_code)
    decks_by_draft: dict[str, list[ParsedCard]] = {}
    dropped = {"resolve": 0, "size": 0, "unsafe": 0}
    for draft_id, sub in df.groupby("draft_id"):
        row = sub.iloc[0]
        deck = _parse_deck_string(str(row["deck"]), lookup)
        if deck is None:
            dropped["resolve"] += 1
            continue
        # recommend_choice() accepts 40-42-card decks (matching the
        # training pipeline's MAX_DECK_SIZE). The min/max CLI flags
        # let callers narrow further (e.g. to just-the-additions when
        # re-running after a deck-size policy change).
        if not (min_deck_size <= len(deck) <= max_deck_size):
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
                n_sims,
            )
        )

    verdicts: list[str | None] = [None] * len(df)
    t0 = time.time()
    if n_workers <= 1:
        _init_worker(str(choice_model_dir), set_code)
        for done, item in enumerate(items, 1):
            idx, v = _do_one(item)
            verdicts[idx] = v
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
            processes=n_workers,
            initializer=_init_worker,
            initargs=(str(choice_model_dir), set_code),
        ) as pool:
            for done, (idx, v) in enumerate(pool.imap_unordered(_do_one, items, chunksize=4), 1):
                verdicts[idx] = v
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
    log.info("  inference done in %.0fs", time.time() - t0)

    df["verdict"] = verdicts
    df["pred_mull"] = df["verdict"].isin(["clear_mulligan", "marginal_mulligan"])
    df["actual_mull"] = ~df["was_kept"]

    n = len(df)
    n_agree = int((df["pred_mull"] == df["actual_mull"]).sum())
    complete_wrong = ((df["verdict"] == "clear_keep") & df["actual_mull"]) | (
        (df["verdict"] == "clear_mulligan") & ~df["actual_mull"]
    )
    n_not_complete_wrong = n - int(complete_wrong.sum())

    log.info("\n  --- Summary (%s / %s) ---", event_type, set_code)
    log.info("  n decisions:          %d", n)
    log.info(
        "  actual mull rate:     %.2f%%  (%d / %d)",
        100.0 * float(df["actual_mull"].mean()),
        int(df["actual_mull"].sum()),
        n,
    )
    log.info(
        "  predicted mull rate:  %.2f%%  (%d / %d)",
        100.0 * float(df["pred_mull"].mean()),
        int(df["pred_mull"].sum()),
        n,
    )
    log.info("  verdict distribution:")
    for v in VERDICTS:
        c = int((df["verdict"] == v).sum())
        log.info("    %-18s  %5d  (%.2f%%)", v, c, 100.0 * c / n)

    log.info("")
    log.info(
        "  AGREEMENT (model keep/mull == player keep/mull): %d / %d  (%.2f%%)",
        n_agree,
        n,
        100.0 * n_agree / n,
    )
    log.info(
        "  NOT COMPLETE DISAGREEMENT (excludes clear_keep+mulled, "
        "clear_mulligan+kept):  %d / %d  (%.2f%%)",
        n_not_complete_wrong,
        n,
        100.0 * n_not_complete_wrong / n,
    )

    log.info("\n  Confusion matrix (rows=verdict, cols=actual):")
    actual_label = df["was_kept"].map({True: "player_kept", False: "player_mulled"})
    matrix = (
        pd.crosstab(
            df["verdict"],
            actual_label,
            margins=True,
            margins_name="total",
        )
        .reindex(index=[*VERDICTS, "total"])
        .fillna(0)
        .astype(int)
    )
    log.info("\n%s", matrix.to_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set", default="TLA", help="Set code to evaluate. Default: TLA.")
    ap.add_argument(
        "--choice-model-dir",
        type=Path,
        default=DEFAULT_CHOICE_MODEL_DIR,
        help="Choice-model bundle dir. Default: models/choice_v6.",
    )
    ap.add_argument(
        "--n-sims",
        type=int,
        default=300,
        help="Simulator runs per decision. 300 is plenty for verdict-bucket stability.",
    )
    ap.add_argument("--n-workers", type=int, default=6)
    ap.add_argument(
        "--event-types",
        nargs="+",
        default=["PremierDraft", "TradDraft"],
        choices=list(ELITE_DEFS.keys()),
    )
    ap.add_argument(
        "--min-deck-size",
        type=int,
        default=40,
        help="Lower bound (inclusive) on deck size. Defaults to 40.",
    )
    ap.add_argument(
        "--max-deck-size",
        type=int,
        default=42,
        help="Upper bound (inclusive) on deck size. Defaults to 42.",
    )
    ap.add_argument(
        "--draft-ids-file",
        type=Path,
        default=None,
        help=(
            "Optional newline-delimited file of draft_ids. When given, only "
            "elite decisions whose draft_id is in the file are evaluated. Use "
            "to restrict to a model's held-out (test-split) drafts so an "
            "in-sample set can be scored fairly."
        ),
    )
    args = ap.parse_args()

    allowed_draft_ids: set[str] | None = None
    if args.draft_ids_file is not None:
        allowed_draft_ids = {
            line.strip() for line in args.draft_ids_file.read_text().splitlines() if line.strip()
        }

    log = setup_logger()
    log.info("==== Elite first-mulligan agreement check ====")
    log.info("Set: %s   n_sims: %d   n_workers: %d", args.set, args.n_sims, args.n_workers)
    log.info("Choice model: %s", args.choice_model_dir)
    if not args.choice_model_dir.exists():
        log.error("Choice-model dir does not exist; aborting.")
        sys.exit(1)

    # Numpy seeding for any stochastic numpy-side work in load_premier_draft_stats
    # (defensive; the function is deterministic).
    np.random.seed(0)

    for event_type in args.event_types:
        evaluate(
            event_type=event_type,
            set_code=args.set,
            choice_model_dir=args.choice_model_dir,
            n_sims=args.n_sims,
            n_workers=args.n_workers,
            log=log,
            min_deck_size=args.min_deck_size,
            max_deck_size=args.max_deck_size,
            allowed_draft_ids=allowed_draft_ids,
        )


if __name__ == "__main__":
    main()
