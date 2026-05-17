"""Compare website recommendations to real player mulligan decisions.

The sister script :mod:`website_mulligan_benchmark` measures how often
the website *would* flag a kept-7 hand as a mulligan, but every row it
sees was kept by some player — so we only ever learn the model's
flag rate, not whether it agrees with humans.

This script asks the inverse question: on the 17Lands replay-data
*candidate-hand* dataset (one row per mulligan DECISION, including the
ones the player threw away), how does the website's recommendation
agree with what skilled players actually did?

For each sampled decision row we compute:

* ``p_keep`` — :func:`predict_win_probability` on this hand + deck at
  ``mulligan_number=0``, with the website's default 1000 sims.
* ``p_mull`` — the website's mulligan arm at ``mulligan_number_to=1``
  (50 fresh smoother-aware draws x 40 sims, with the deeper-mulligan
  floor at level 2).

A decision is **flagged mull** by the website iff ``p_keep < p_mull``.
We then compare against ``was_kept`` to get a 2x2 agreement matrix.

Why focus on skilled players
----------------------------
The 17Lands data spans every account that opted in, including very
new players whose mulligan judgement is unreliable. To get a clean
"is the model in the same ball park as people who know what they're
doing" signal we filter to ``user_n_games_bucket >= 100`` AND
``user_game_win_rate_bucket >= 0.58`` by default — roughly the top
~30% of accounts by ladder WR with at least a half-season of games.

Why mn=0 only
-------------
Mulligan levels 1+ have known bottoming complications (the model
treats the recorded hand as 7 cards even when the player bottomed
some). mn=0 carries the vast majority of decisions anyway (~90% of
the dataset). Confine the test to where the training distribution
exactly matches and the signal is cleanest.

Output
------
A summary log at ``<model_dir>/replay_mulligan_benchmark.log``:

* Predicted vs actual mull rate (the headline calibration check).
* Confusion matrix (kept/mulled, predicted keep/mull).
* WR-by-quadrant: did the model spot the bad keeps?
* Delta distribution and per-set / on-play breakdowns.
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
from mulligan_coach_cards import ParsedCard, ParseStatus
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_model import ModelBundle, build_name_lookup
from mulligan_coach_model.inference import predict_win_probability
from mulligan_coach_recommend import (
    DEFAULT_N_MULLIGAN_SAMPLES,
    DEFAULT_N_SIMS_KEEP,
    DEFAULT_N_SIMS_PER_MULLIGAN,
    FormatStats,
    MulliganArmResult,
    RecommendationService,
    _deck_signature,
    _MulliganCacheKey,
    _stable_seed,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_DIR = REPO_ROOT / "models" / "all3_v1"
DECISIONS_PATH = (
    REPO_ROOT
    / "data"
    / "processed"
    / "seventeenlands"
    / "mulligan_decisions"
    / "combined.PremierDraft.parquet"
)
EVENT_TYPE = "PremierDraft"

# Sampling seed (separate from the train/test split seed used by the
# sister benchmark — these rows come from a totally different source).
SAMPLE_SEED = 20260516


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("replay_mull_benchmark")
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
# Decision loading + sampling
# ---------------------------------------------------------------------------


def load_skilled_decisions(
    *,
    parquet_path: Path,
    min_n_games_bucket: int,
    min_wr_bucket: float,
    n_drafts: int,
    log: logging.Logger,
    exclude_drafts_from: Path | None = None,
    seed: int = SAMPLE_SEED,
) -> pd.DataFrame:
    """Filter to skilled-player mn=0 decisions and draft-sample down.

    Draft-first sampling makes the mulligan arm reusable across every
    decision sharing a deck — every game in a draft uses the same
    40-card maindeck (Bo1), so one mull-arm compute per
    ``(draft, on_play, opp_mull)`` group covers all decisions in
    that group.

    ``exclude_drafts_from`` is an optional path to a prior
    per-row parquet output; any draft_id present there is dropped
    from the candidate pool before sampling. Used to extend a sample
    without duplicating work.
    """
    cols = [
        "expansion",
        "draft_id",
        "build_index",
        "match_number",
        "game_number",
        "on_play",
        "user_n_games_bucket",
        "user_game_win_rate_bucket",
        "opp_num_mulligans",
        "num_mulligans_in_game",
        "won",
        "mulligan_number",
        "was_kept",
        "hand_size",
        "hand",
        "deck",
    ]
    df = pd.read_parquet(parquet_path, columns=cols)
    log.info("  loaded %s rows from %s", f"{len(df):,}", parquet_path.name)

    mask = (
        (df["mulligan_number"] == 0)
        & (df["hand_size"] == 7)
        & (df["user_n_games_bucket"] >= min_n_games_bucket)
        & (df["user_game_win_rate_bucket"] >= min_wr_bucket)
    )
    df = df.loc[mask].reset_index(drop=True)
    log.info(
        "  skilled mn=0 subset: %s rows  (min_n_games_bucket>=%d, min_wr_bucket>=%.2f)",
        f"{len(df):,}",
        min_n_games_bucket,
        min_wr_bucket,
    )

    excluded: set[str] = set()
    if exclude_drafts_from is not None:
        prev = pd.read_parquet(exclude_drafts_from, columns=["draft_id"])
        excluded = {str(d) for d in prev["draft_id"].unique()}
        before = len(df)
        df = df.loc[~df["draft_id"].isin(excluded)].reset_index(drop=True)
        log.info(
            "  excluded %d draft_ids from %s -> %s rows remain  (dropped %d)",
            len(excluded),
            exclude_drafts_from.name,
            f"{len(df):,}",
            before - len(df),
        )

    # Draft-first uniform sample. Sort the unique draft ids so the
    # numpy permutation is deterministic given ``seed``.
    unique_drafts = np.array(sorted(df["draft_id"].unique()))
    rng = np.random.default_rng(seed)
    take = min(n_drafts, len(unique_drafts))
    chosen = rng.choice(unique_drafts, size=take, replace=False)
    sample = df.loc[df["draft_id"].isin(chosen)].reset_index(drop=True)
    log.info(
        "  sampled %d drafts -> %s decisions  (avg %.2f decisions/draft)",
        take,
        f"{len(sample):,}",
        len(sample) / take if take else 0.0,
    )
    log.info(
        "  actual mull rate in sample: %.2f%% (%d of %d)",
        100.0 * float((~sample["was_kept"]).mean()),
        int((~sample["was_kept"]).sum()),
        len(sample),
    )
    return sample


# ---------------------------------------------------------------------------
# Deck / hand parsing
# ---------------------------------------------------------------------------


def _parse_deck_string(deck_str: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    """Parse ``"Name xN | Name xM | ..."`` -> list of 40 ParsedCards.

    Returns None if any card name doesn't resolve in the lookup
    (cross-set bonus-sheet card the parsed-card JSON doesn't have, or
    a typo/corruption). The caller drops the row when this happens.
    """
    cards: list[ParsedCard] = []
    for part in deck_str.split(" | "):
        name, _sep, count_str = part.rpartition(" x")
        if not _sep or not count_str.isdigit():
            return None
        card = lookup.get(name)
        if card is None:
            return None
        cards.extend([card] * int(count_str))
    return cards


def _parse_hand_string(hand_str: str, lookup: dict[str, ParsedCard]) -> list[ParsedCard] | None:
    """Parse ``"Name|Name|...|Name"`` -> list of 7 ParsedCards (or None)."""
    out: list[ParsedCard] = []
    for name in hand_str.split("|"):
        card = lookup.get(name)
        if card is None:
            return None
        out.append(card)
    return out


def _deck_is_simulator_safe(deck: list[ParsedCard]) -> bool:
    """Same predicate the sister benchmark uses — reject decks with
    cards the feature builder / simulator would refuse."""
    for c in deck:
        if c.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
            return False
        if c.mana_cost is not None and not c.modes:
            return False
    return True


def resolve_decisions(
    *,
    df_sample: pd.DataFrame,
    log: logging.Logger,
) -> tuple[pd.DataFrame, dict[str, tuple[list[ParsedCard], str]]]:
    """Parse hands + decks into ParsedCard lists.

    Returns ``(df_resolved, decks_by_draft)``. The dataframe is
    augmented with an ``object``-typed ``hand_cards`` column and rows
    that fail to resolve are dropped. The per-draft deck dict is what
    the mulligan-arm and keep-arm workers consume — every row from a
    surviving draft uses that single deck (Bo1).
    """
    sets_in_sample = sorted(df_sample["expansion"].unique())
    log.info("  building name lookups for: %s", ", ".join(sets_in_sample))
    lookups: dict[str, dict[str, ParsedCard]] = {s: build_name_lookup(s) for s in sets_in_sample}

    # Parse one deck per draft (the deck string is identical for all
    # decisions in a draft; we look at the first row per draft).
    decks_by_draft: dict[str, tuple[list[ParsedCard], str]] = {}
    dropped_deck_resolve = 0
    dropped_deck_unsafe = 0
    dropped_deck_wrong_size = 0
    for draft_id, sub in df_sample.groupby("draft_id"):
        row = sub.iloc[0]
        deck = _parse_deck_string(str(row["deck"]), lookups[str(row["expansion"])])
        if deck is None:
            dropped_deck_resolve += 1
            continue
        if not (40 <= len(deck) <= 42):
            dropped_deck_wrong_size += 1
            continue
        if not _deck_is_simulator_safe(deck):
            dropped_deck_unsafe += 1
            continue
        decks_by_draft[str(draft_id)] = (deck, str(row["expansion"]))
    log.info(
        "  parsed decks: kept %d  (dropped %d unresolved name, %d wrong size, %d unsafe)",
        len(decks_by_draft),
        dropped_deck_resolve,
        dropped_deck_wrong_size,
        dropped_deck_unsafe,
    )

    # Only keep decision rows that belong to a successfully-parsed deck.
    df = df_sample.loc[df_sample["draft_id"].isin(decks_by_draft.keys())].reset_index(drop=True)

    # Parse hand strings; drop any row whose hand we can't resolve.
    parsed_hands: list[list[ParsedCard] | None] = []
    for _, row in df.iterrows():
        parsed_hands.append(_parse_hand_string(str(row["hand"]), lookups[str(row["expansion"])]))
    df["hand_cards"] = parsed_hands
    missing_hand = df["hand_cards"].isna()
    if missing_hand.any():
        log.info("  dropped %d rows with unresolvable hand card(s)", int(missing_hand.sum()))
        df = df.loc[~missing_hand].reset_index(drop=True)

    log.info("  decisions remaining after parse: %d", len(df))
    return df, decks_by_draft


# ---------------------------------------------------------------------------
# Worker setup (shared by keep- and mull-arm phases)
# ---------------------------------------------------------------------------


_WORKER_SERVICE: RecommendationService | None = None


def _init_worker(model_dir_str: str, sets: tuple[str, ...]) -> None:
    """Per-process: load the model bundle and per-set 17Lands stats once.

    spawn-based workers don't inherit parent state, so this is where
    each child pays the model-load cost (~0.1s). Reused across every
    task that worker handles.
    """
    global _WORKER_SERVICE
    bundle = ModelBundle.load(Path(model_dir_str))
    stats_by_set = {
        s: FormatStats.build(load_premier_draft_stats(s).by_arena_id.values()) for s in sets
    }
    _WORKER_SERVICE = RecommendationService(bundle=bundle, stats_by_set=stats_by_set)


# ---------------------------------------------------------------------------
# Keep arm — one simulate + predict per decision row
# ---------------------------------------------------------------------------


# Payload kept as a plain tuple so cross-process pickling is cheap.
# Order: (row_idx, hand, deck, set_code, on_play, opp_mull, n_sims, seed).
_KeepWorkItem = tuple[
    int,
    list[ParsedCard],
    list[ParsedCard],
    str,
    bool,
    int | None,
    int,
    int,
]


def _do_keep(item: _KeepWorkItem) -> tuple[int, float]:
    """Run :func:`predict_win_probability` for one decision row."""
    assert _WORKER_SERVICE is not None
    row_idx, hand, deck, set_code, on_play, opp_mull, n_sims, seed = item
    service = _WORKER_SERVICE
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
    return row_idx, float(p_keep)


def _keep_seed_for_row(draft_id: str, game_number: int, build_index: int, match_number: int) -> int:
    """Stable 32-bit seed for one decision row.

    Re-runs of the script on the same row are bit-identical. Uses the
    same hash style as the website's mulligan cache: SHA-256 first 4
    bytes of the repr.
    """
    import hashlib

    key = (draft_id, build_index, match_number, game_number)
    digest = hashlib.sha256(repr(key).encode()).digest()
    return int.from_bytes(digest[:4], "big")


def compute_keep_arms(
    *,
    df: pd.DataFrame,
    decks_by_draft: dict[str, tuple[list[ParsedCard], str]],
    model_dir: Path,
    n_sims: int,
    n_workers: int,
    log: logging.Logger,
) -> np.ndarray:
    """Compute ``p_keep`` for every decision row in parallel.

    Each row needs its own simulate() call (different 7-card hand),
    so no deck-grouping shortcut here. Parallelism is the only lever.
    """
    items: list[_KeepWorkItem] = []
    for i, row in enumerate(df.itertuples(index=False)):
        deck, set_code = decks_by_draft[str(row.draft_id)]
        opp_mull = (
            None
            if bool(row.on_play) or pd.isna(row.opp_num_mulligans)
            else int(row.opp_num_mulligans)
        )
        seed = _keep_seed_for_row(
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

    out = np.empty(len(df), dtype=float)
    out[:] = np.nan

    log.info("  keep-arm work items: %d  (n_sims=%d, n_workers=%d)", len(items), n_sims, n_workers)
    t0 = time.time()
    sets = tuple(sorted({s for _, s in decks_by_draft.values()}))

    if n_workers <= 1:
        _init_worker(str(model_dir), sets)
        for done, item in enumerate(items, start=1):
            idx, p = _do_keep(item)
            out[idx] = p
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
            initargs=(str(model_dir), sets),
        ) as pool:
            for done, (idx, p) in enumerate(
                pool.imap_unordered(_do_keep, items, chunksize=4), start=1
            ):
                out[idx] = p
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
    log.info("  keep arm done in %.0fs", time.time() - t0)
    return out


# ---------------------------------------------------------------------------
# Mulligan arm — one compute per (draft_id, on_play, opp_mull) group
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ArmKey:
    draft_id: str
    on_play: bool
    opp_mulligan_number: int | None


_MullWorkItem = tuple[
    _ArmKey,
    list[ParsedCard],
    str,
    int,
    int,
]


def _do_mull(item: _MullWorkItem) -> tuple[_ArmKey, MulliganArmResult]:
    """One call to the website's exact :meth:`_compute_mulligan_arm`."""
    key, deck, set_code, n_sims, n_samples = item
    assert _WORKER_SERVICE is not None
    cache_key = _MulliganCacheKey(
        deck_signature=_deck_signature(deck),
        on_the_play=key.on_play,
        mulligan_number_to=1,
        opp_mulligan_number=key.opp_mulligan_number,
        n_sims_per_mulligan=n_sims,
        n_mulligan_samples=n_samples,
    )
    seed = _stable_seed(cache_key)
    res = _WORKER_SERVICE._compute_mulligan_arm(
        deck=deck,
        on_the_play=key.on_play,
        mulligan_number_to=1,
        opp_mulligan_number=key.opp_mulligan_number,
        set_code=set_code,
        n_sims_per_mulligan=n_sims,
        n_mulligan_samples=n_samples,
        seed=seed,
    )
    return key, res


def compute_mull_arms(
    *,
    df: pd.DataFrame,
    decks_by_draft: dict[str, tuple[list[ParsedCard], str]],
    model_dir: Path,
    n_sims: int,
    n_samples: int,
    n_workers: int,
    log: logging.Logger,
) -> dict[_ArmKey, MulliganArmResult]:
    """Compute the website-style mulligan arm per unique group in the
    sample. Deck-grouped so each unique ``(draft, on_play, opp_mull)``
    is paid for once even if many decisions share it.
    """
    keys: set[_ArmKey] = set()
    for row in df.itertuples(index=False):
        keys.add(
            _ArmKey(
                draft_id=str(row.draft_id),
                on_play=bool(row.on_play),
                opp_mulligan_number=(
                    None
                    if bool(row.on_play) or pd.isna(row.opp_num_mulligans)
                    else int(row.opp_num_mulligans)
                ),
            )
        )
    log.info(
        "  mull-arm groups: %d  across %d decisions",
        len(keys),
        len(df),
    )

    items: list[_MullWorkItem] = []
    for k in keys:
        deck, set_code = decks_by_draft[k.draft_id]
        items.append((k, deck, set_code, n_sims, n_samples))

    arms: dict[_ArmKey, MulliganArmResult] = {}
    t0 = time.time()
    sets = tuple(sorted({s for _, s in decks_by_draft.values()}))

    if n_workers <= 1:
        _init_worker(str(model_dir), sets)
        for done, item in enumerate(items, start=1):
            k, res = _do_mull(item)
            arms[k] = res
            if done % 50 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  mull progress %d/%d  elapsed=%.0fs  eta=%.0fs",
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
            initargs=(str(model_dir), sets),
        ) as pool:
            for done, (k, res) in enumerate(
                pool.imap_unordered(_do_mull, items, chunksize=2), start=1
            ):
                arms[k] = res
                if done % 50 == 0 or done == len(items):
                    elapsed = time.time() - t0
                    eta = elapsed * (len(items) - done) / done if done else 0.0
                    log.info(
                        "  mull progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                        done,
                        len(items),
                        elapsed,
                        eta,
                    )
    log.info("  mull arm done in %.0fs", time.time() - t0)
    return arms


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(
    *,
    df: pd.DataFrame,
    n_sims_keep: int,
    n_sims_per_mulligan: int,
    n_mulligan_samples: int,
    min_n_games_bucket: int,
    min_wr_bucket: float,
    log: logging.Logger,
) -> None:
    df = df.dropna(subset=["p_keep", "p_mull"]).copy()
    df["delta"] = df["p_keep"] - df["p_mull"]
    df["pred_mull"] = df["p_keep"] < df["p_mull"]
    df["actual_mull"] = ~df["was_kept"]

    n = len(df)
    actual_mull_rate = float(df["actual_mull"].mean())
    pred_mull_rate = float(df["pred_mull"].mean())

    log.info("\n==== Replay-data mull benchmark ====")
    log.info(
        "Sample: %d decisions  (skilled subset: min_n_games_bucket>=%d, min_wr_bucket>=%.2f)",
        n,
        min_n_games_bucket,
        min_wr_bucket,
    )
    log.info("Across %d unique drafts", df["draft_id"].nunique())
    log.info(
        "Settings: keep n_sims=%d  mull %d samples x %d sims (with deeper-mulligan floor)",
        n_sims_keep,
        n_mulligan_samples,
        n_sims_per_mulligan,
    )

    log.info("\n---- Headline ----")
    log.info(
        "Actual mull rate (skilled players):    %.2f%% (%d / %d)",
        100.0 * actual_mull_rate,
        int(df["actual_mull"].sum()),
        n,
    )
    log.info(
        "Predicted mull rate (website):         %.2f%% (%d / %d)",
        100.0 * pred_mull_rate,
        int(df["pred_mull"].sum()),
        n,
    )
    log.info(
        "Ratio actual / predicted:              %.2fx",
        actual_mull_rate / pred_mull_rate if pred_mull_rate > 0 else float("inf"),
    )
    log.info("Mean p_keep: %.4f", float(df["p_keep"].mean()))
    log.info("Mean p_mull: %.4f", float(df["p_mull"].mean()))
    log.info(
        "Actual WR (overall, kept decisions only): %.4f",
        float(df.loc[df["was_kept"], "won"].mean()),
    )

    # ---- Confusion matrix ----
    log.info("\n---- Agreement matrix (rows=player, cols=website) ----")
    matrix = pd.crosstab(
        df["actual_mull"].map({True: "player mulled", False: "player kept"}),
        df["pred_mull"].map({True: "website mull", False: "website keep"}),
        margins=True,
        margins_name="total",
    )
    log.info("\n%s", matrix.to_string())

    both_keep = df[(~df["actual_mull"]) & (~df["pred_mull"])]
    both_mull = df[df["actual_mull"] & df["pred_mull"]]
    player_kept_we_mull = df[(~df["actual_mull"]) & df["pred_mull"]]
    player_mulled_we_keep = df[df["actual_mull"] & (~df["pred_mull"])]
    log.info(
        "\nAgreement: %d / %d  (%.2f%%)",
        len(both_keep) + len(both_mull),
        n,
        100.0 * (len(both_keep) + len(both_mull)) / n if n else 0.0,
    )

    # ---- WR-by-quadrant ----
    log.info("\n---- Game outcomes by quadrant ----")
    log.info("Quadrants reflect what happened in the game the decision was made for.")

    def _quad(label: str, sub: pd.DataFrame) -> None:
        if len(sub) == 0:
            log.info("  %s: n=0", label)
            return
        log.info(
            "  %s: n=%5d  actual WR=%.4f  mean p_keep=%.4f  mean p_mull=%.4f  mean delta=%+.4f",
            label,
            len(sub),
            float(sub["won"].mean()),
            float(sub["p_keep"].mean()),
            float(sub["p_mull"].mean()),
            float(sub["delta"].mean()),
        )

    _quad("both KEEP (player kept, website keep)", both_keep)
    _quad("both MULL (player mulled, website mull)", both_mull)
    _quad("disagreement: player kept, website MULL", player_kept_we_mull)
    _quad("disagreement: player MULLED, website keep", player_mulled_we_keep)

    # ---- Calibration sanity: kept hands ----
    log.info("\n---- Among hands the player KEPT ----")
    kept = df[~df["actual_mull"]]
    log.info(
        "  n=%d  actual WR=%.4f  mean p_keep=%.4f  mean p_mull=%.4f",
        len(kept),
        float(kept["won"].mean()),
        float(kept["p_keep"].mean()),
        float(kept["p_mull"].mean()),
    )

    log.info("\n---- Among hands the player MULLIGANED ----")
    mulled = df[df["actual_mull"]]
    log.info(
        "  n=%d  mean p_keep=%.4f  mean p_mull=%.4f  mean delta=%+.4f",
        len(mulled),
        float(mulled["p_keep"].mean()) if len(mulled) else float("nan"),
        float(mulled["p_mull"].mean()) if len(mulled) else float("nan"),
        float(mulled["delta"].mean()) if len(mulled) else float("nan"),
    )
    if len(mulled):
        log.info(
            "  fraction website would ALSO mull: %.2f%% (%d / %d)",
            100.0 * float(mulled["pred_mull"].mean()),
            int(mulled["pred_mull"].sum()),
            len(mulled),
        )

    # ---- Delta distribution ----
    log.info("\n---- Delta (p_keep - p_mull) distribution, all rows ----")
    for q, label in ((0.01, "p01"), (0.10, "p10"), (0.50, "p50"), (0.90, "p90"), (0.99, "p99")):
        log.info("  %s: %+.4f", label, float(df["delta"].quantile(q)))
    log.info("  min: %+.4f", float(df["delta"].min()))
    log.info("  max: %+.4f", float(df["delta"].max()))

    # ---- Per-set ----
    log.info("\n---- By set ----")
    for set_code, sub in df.groupby("expansion"):
        log.info(
            "  %s: n=%5d  actual mull=%.2f%%  pred mull=%.2f%%  agree=%.2f%%  "
            "mean p_keep=%.4f  mean p_mull=%.4f",
            set_code,
            len(sub),
            100.0 * float(sub["actual_mull"].mean()),
            100.0 * float(sub["pred_mull"].mean()),
            100.0 * float((sub["actual_mull"] == sub["pred_mull"]).mean()),
            float(sub["p_keep"].mean()),
            float(sub["p_mull"].mean()),
        )

    # ---- Per on-play / on-draw ----
    log.info("\n---- By on-play / on-draw ----")
    for on_play, sub in df.groupby("on_play"):
        label = "on play" if on_play else "on draw"
        log.info(
            "  %s: n=%5d  actual mull=%.2f%%  pred mull=%.2f%%  agree=%.2f%%  "
            "mean p_keep=%.4f  mean p_mull=%.4f",
            label,
            len(sub),
            100.0 * float(sub["actual_mull"].mean()),
            100.0 * float(sub["pred_mull"].mean()),
            100.0 * float((sub["actual_mull"] == sub["pred_mull"]).mean()),
            float(sub["p_keep"].mean()),
            float(sub["p_mull"].mean()),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    ap.add_argument("--decisions-path", type=Path, default=DECISIONS_PATH)
    ap.add_argument(
        "--n-drafts",
        type=int,
        default=1000,
        help=(
            "Number of distinct drafts to sample (Bo1, so each draft "
            "contributes ~5-6 decisions). 1000 drafts ~= ~6k decisions."
        ),
    )
    ap.add_argument(
        "--min-n-games-bucket",
        type=int,
        default=100,
        help="17Lands skill filter: minimum games-played bucket (5/10/50/100/500/1000).",
    )
    ap.add_argument(
        "--min-wr-bucket",
        type=float,
        default=0.58,
        help="17Lands skill filter: minimum win-rate bucket (2pp granularity).",
    )
    ap.add_argument("--n-sims-keep", type=int, default=DEFAULT_N_SIMS_KEEP)
    ap.add_argument("--n-sims-per-mulligan", type=int, default=DEFAULT_N_SIMS_PER_MULLIGAN)
    ap.add_argument("--n-mulligan-samples", type=int, default=DEFAULT_N_MULLIGAN_SAMPLES)
    ap.add_argument("--n-workers", type=int, default=6)
    ap.add_argument(
        "--exclude-drafts-from",
        type=Path,
        default=None,
        help=(
            "Optional path to a prior per-row parquet output; drafts "
            "already covered there are dropped from the sampling pool. "
            "Use to extend a sample without re-doing work."
        ),
    )
    ap.add_argument(
        "--out-stem",
        type=str,
        default="replay_mulligan_benchmark",
        help=(
            "Filename stem (under <model_dir>) for the .log and "
            ".parquet outputs. Override when extending a sample so the "
            "first run's files aren't clobbered."
        ),
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=SAMPLE_SEED,
        help="Draft-sampling seed. Bump to draw a fresh disjoint sample.",
    )
    args = ap.parse_args()

    log_path = args.model_dir / f"{args.out_stem}.log"
    log = setup_logger(log_path)
    log.info("==== %s ====", __doc__.splitlines()[0])
    log.info("Model dir: %s", args.model_dir)
    log.info("Decisions: %s", args.decisions_path)
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

    log.info("\nLoading model bundle...")
    t0 = time.time()
    bundle = ModelBundle.load(args.model_dir)
    log.info(
        "  loaded model (best_iter=%d, %d features) in %.1fs",
        bundle.best_iteration,
        len(bundle.feature_names),
        time.time() - t0,
    )

    log.info("\nLoading & sampling skilled decisions...")
    df_sample = load_skilled_decisions(
        parquet_path=args.decisions_path,
        min_n_games_bucket=args.min_n_games_bucket,
        min_wr_bucket=args.min_wr_bucket,
        n_drafts=args.n_drafts,
        log=log,
        exclude_drafts_from=args.exclude_drafts_from,
        seed=args.seed,
    )

    log.info("\nResolving decks and hands...")
    df, decks_by_draft = resolve_decisions(df_sample=df_sample, log=log)

    log.info("\nComputing keep arms (parallel)...")
    df["p_keep"] = compute_keep_arms(
        df=df,
        decks_by_draft=decks_by_draft,
        model_dir=args.model_dir,
        n_sims=args.n_sims_keep,
        n_workers=args.n_workers,
        log=log,
    )

    log.info("\nComputing mulligan arms (deck-grouped, parallel)...")
    arms = compute_mull_arms(
        df=df,
        decks_by_draft=decks_by_draft,
        model_dir=args.model_dir,
        n_sims=args.n_sims_per_mulligan,
        n_samples=args.n_mulligan_samples,
        n_workers=args.n_workers,
        log=log,
    )

    # Stitch the mull arm back onto each row by lookup.
    p_mull = np.empty(len(df), dtype=float)
    p_mull_raw = np.empty(len(df), dtype=float)
    n_below = np.empty(len(df), dtype=int)
    floor_value = np.empty(len(df), dtype=float)
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
        if arm is None:
            p_mull[i] = float("nan")
            p_mull_raw[i] = float("nan")
            n_below[i] = 0
            floor_value[i] = float("nan")
        else:
            p_mull[i] = arm.floored_mean
            p_mull_raw[i] = arm.raw_mean
            n_below[i] = arm.n_samples_below_floor
            floor_value[i] = arm.floor_value if arm.floor_value is not None else float("nan")
    df["p_mull"] = p_mull
    df["p_mull_raw"] = p_mull_raw
    df["floor_clamps"] = n_below
    df["floor_value"] = floor_value

    # Drop the hand_cards object column before writing — pyarrow can't
    # serialise a list-of-ParsedCard cleanly and we don't need it on
    # disk (the hand string is already there).
    out_df = df.drop(columns=["hand_cards"])
    out_parquet = args.model_dir / f"{args.out_stem}.parquet"
    out_df.to_parquet(out_parquet, index=False)
    log.info("\nWrote per-row results to %s", out_parquet)

    report(
        df=df,
        n_sims_keep=args.n_sims_keep,
        n_sims_per_mulligan=args.n_sims_per_mulligan,
        n_mulligan_samples=args.n_mulligan_samples,
        min_n_games_bucket=args.min_n_games_bucket,
        min_wr_bucket=args.min_wr_bucket,
        log=log,
    )
    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
