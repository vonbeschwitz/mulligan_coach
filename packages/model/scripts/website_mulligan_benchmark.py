"""Empirical check: how often does the website recommend mulligan?

The website's interactive feedback suggested the model almost never
votes "mulligan" even on visibly sketchy hands. This script reproduces
the website's recommendation pipeline on a ~5k sample of real kept-7
hands from the test split and reports the resulting should-mull rate.

How it mirrors the website
--------------------------
* **Mulligan arm**: imports the website's
  :meth:`RecommendationService._compute_mulligan_arm` directly and
  drives it with the website's defaults
  (``50`` fresh hands x ``40`` sims each, uniform sampling, with the
  deeper-mulligan floor at ``mulligan_to=2``). Same seed-derivation
  as the website's cache: ``sha256(repr(cache_key))[:4]``.
* **Keep arm**: predicts directly from the *stored* feature row in
  the materialised parquet. That row was built by the same
  ``build_feature_row`` the website uses at runtime (just at a
  different sim count), so the column vocabulary matches the
  ``all3_v1`` model exactly. Skipping the re-simulation here saves
  ~5000 simulate() calls (~10 minutes) without changing the model's
  in-distribution behaviour for kept-7 rows.

Deck-grouped efficiency
-----------------------
Premier Draft is Bo1 — every game played within a single draft
shares the same 40-card maindeck. The script groups sample rows by
``(draft_id, on_the_play, opp_mulligan_number)`` and runs each mull
arm exactly once per group. With ~5 games per draft, that cuts
mulligan-arm compute by ~3-5x.

Output
------
A summary log written to ``<model_dir>/website_mulligan_benchmark.log``:

* Should-mull rate (the headline number).
* Distribution of ``p_keep - p_mull`` deltas.
* Floor-clamp diagnostics (how often the deeper-mulligan floor
  bumped a sample, and by how much).
* Per-set breakdown.
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xgboost as xgb
from mulligan_coach_cards import ParsedCard, ParseStatus
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_model import (
    ModelBundle,
    TrainingRowStats,
    build_name_lookup,
    iter_training_rows,
)
from mulligan_coach_recommend import (
    DEFAULT_N_MULLIGAN_SAMPLES,
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
DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
TRAINING_DIR = REPO_ROOT / "data" / "processed" / "model_training"

SETS = ("TLA", "ECL", "TMT")
EVENT_TYPE = "PremierDraft"

# Reproduce the seed=0 grouped-by-draft_id split used by train.py /
# train_multi_set.py so we hit the SAME test rows the model never saw.
SPLIT_SEED = 0
VAL_FRAC = 0.10
CALIB_FRAC = 0.10
TEST_FRAC = 0.10

# Sub-sampling seed for picking ~5k rows out of the ~100k test rows.
SAMPLE_SEED = 20260516


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("website_mull_benchmark")
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
# Test-split reconstruction
# ---------------------------------------------------------------------------


def load_test_sample(
    *,
    n_drafts: int,
    bundle_feature_names: list[str],
    log: logging.Logger,
) -> pd.DataFrame:
    """Sample ``n_drafts`` test-split drafts and return all their kept-7 rows.

    Draft-first sampling is the key efficiency win: each draft
    contributes ~4 kept-7 games (Bo1, same maindeck across games),
    so one mulligan-arm compute amortises across multiple keep
    arms. Uniform row sampling would scatter the budget across
    ~5000 nearly-unique decks and undo that savings.

    Concat order matters: ``train_multi_set.py`` passes ``--sets TLA ECL
    TMT`` and ``feature_parquet_paths`` returns sorted chunks. We
    replicate that here so ``pd.unique(df["draft_id"])`` yields the
    same order ``train_model`` saw, and the seed=0 permutation lands
    on the same test draft-ids.
    """
    # ``mulligan_number`` / ``on_the_play`` / ``opp_mulligan_count_if_known``
    # are already in the feature vocabulary; ``opp_mulligan_number`` (the
    # raw context column, not the masked feature) and the join keys we
    # need on top of that. Use a list + de-dupe to keep order stable
    # without dragging the feature column twice (pandas read_table would
    # then yield duplicate-label columns).
    extra_cols = [
        "draft_id",
        "match_number",
        "game_number",
        "expansion",
        "event_type",
        "on_the_play",
        "opp_mulligan_number",
        "mulligan_number",
        "won",
    ]
    seen = set(bundle_feature_names)
    needed_cols = list(bundle_feature_names) + [c for c in extra_cols if c not in seen]

    parts: list[pd.DataFrame] = []
    for set_code in SETS:
        chunks = sorted((TRAINING_DIR / set_code / EVENT_TYPE).glob("chunk_*.parquet"))
        log.info("  %s: %d chunks", set_code, len(chunks))
        for chunk in chunks:
            parts.append(pq.read_table(chunk, columns=needed_cols).to_pandas())
    df = pd.concat(parts, ignore_index=True)
    log.info("  total rows: %s", f"{len(df):,}")

    # _grouped_split (in train.py) uses pd.Series.unique() on draft_id
    # then numpy permutation with seed=0.
    unique_drafts = df["draft_id"].unique()
    rng = np.random.default_rng(SPLIT_SEED)
    shuffled = rng.permutation(unique_drafts)
    n = len(shuffled)
    n_val = round(n * VAL_FRAC)
    n_calib = round(n * CALIB_FRAC)
    n_test = round(n * TEST_FRAC)
    n_train = n - n_val - n_calib - n_test
    test_ids = set(shuffled[n_train + n_val + n_calib :].tolist())

    # Filter to test split + kept-7 mulligans.
    mask = df["draft_id"].isin(test_ids) & (df["mulligan_number"] == 0)
    df_test = df.loc[mask].reset_index(drop=True)
    log.info("  test rows (kept-7): %s", f"{len(df_test):,}")

    # Draft-first sampling: pick n_drafts test draft_ids uniformly,
    # then take every kept-7 row those drafts contributed. Each draft
    # contributes ~4 kept-7 games in the test split, so the resulting
    # row count is roughly ``4 * n_drafts``.
    test_draft_ids = df_test["draft_id"].unique()
    take_drafts = min(n_drafts, len(test_draft_ids))
    sample_rng = np.random.default_rng(SAMPLE_SEED)
    chosen_drafts = sample_rng.choice(test_draft_ids, size=take_drafts, replace=False)
    df_sample = df_test.loc[df_test["draft_id"].isin(chosen_drafts)].reset_index(drop=True)
    log.info(
        "  sampled drafts: %d  -> %s kept-7 rows  (avg %.2f rows/draft)",
        take_drafts,
        f"{len(df_sample):,}",
        len(df_sample) / take_drafts,
    )
    return df_sample


# ---------------------------------------------------------------------------
# Keep arm: predict from stored features (one batch, no re-sim)
# ---------------------------------------------------------------------------


def predict_p_keep(
    *,
    df_sample: pd.DataFrame,
    bundle: ModelBundle,
    log: logging.Logger,
) -> np.ndarray:
    """Run the stored features through baseline + XGBoost in one shot.

    No simulator call — the parquet's feature row was built by the
    same ``build_feature_row`` the website uses. Sim noise at
    materialise time was at the model's training resolution; that's
    the population the booster learned on.
    """
    feature_names = list(bundle.feature_names)
    t0 = time.time()
    X = df_sample[feature_names].to_numpy(dtype=float)

    # Per-row base margin. The baseline's deploy path
    # (user_wr=None, user_n=None) is what the website uses too.
    opp = df_sample["opp_mulligan_number"]
    on_play = df_sample["on_the_play"].to_numpy(dtype=bool)
    base_margins = np.empty(len(df_sample), dtype=float)
    for i, (on_p, opp_v) in enumerate(zip(on_play, opp, strict=True)):
        opp_int: int | None = None if (on_p or pd.isna(opp_v)) else int(opp_v)
        base_margins[i] = bundle.baseline.margin(
            user_wr_bucket=None,
            user_n_games_bucket=None,
            on_the_play=bool(on_p),
            opp_mulligan_number=opp_int,
        )

    dm = xgb.DMatrix(X, base_margin=base_margins, feature_names=feature_names)
    proba = bundle.booster.predict(dm, iteration_range=(0, bundle.best_iteration + 1))
    log.info("  predicted %s rows in %.1fs", f"{len(df_sample):,}", time.time() - t0)
    return np.asarray(proba, dtype=float)


# ---------------------------------------------------------------------------
# Deck reconstruction: one (draft_id -> 40-card deck) entry per draft
# ---------------------------------------------------------------------------


def _deck_is_simulator_safe(deck: list[ParsedCard]) -> bool:
    """Predicate matching :func:`check_deck_encodings`.

    Returns False if any card would make the simulator refuse the
    deck — i.e. a ``NEEDS_LLM`` / ``NEEDS_HUMAN`` status, or a
    castable shape with no encoded modes. Dropping these decks
    upfront avoids paying the worker-spawn / DuckDB cost for rows
    we wouldn't be able to evaluate anyway.
    """
    for c in deck:
        if c.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
            return False
        if c.mana_cost is not None and not c.modes:
            return False
    return True


def load_decks_by_draft(
    *,
    df_sample: pd.DataFrame,
    log: logging.Logger,
) -> tuple[dict[str, tuple[list[ParsedCard], str]], int]:
    """For every draft_id in the sample, pull one game's 40-card deck.

    Premier Draft is Bo1 so all games in a draft share the same
    maindeck. We register a per-set temp view filtered to the
    needed draft_ids, then iterate ``iter_training_rows`` against
    it — collecting at most one ``TrainingRow`` per draft for the
    deck content.

    Decks containing any card the simulator can't handle (re-encoded
    bonus-sheet cards still flagged ``needs_llm`` after a recent
    invalidation pass) are dropped here rather than crashing a worker
    later. The second return value is the count of dropped drafts.
    """
    decks: dict[str, tuple[list[ParsedCard], str]] = {}
    dropped_unsafe = 0
    sample_by_set = df_sample.groupby("expansion")["draft_id"].unique()

    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        for set_code, draft_ids_arr in sample_by_set.items():
            draft_ids = [str(d) for d in draft_ids_arr.tolist()]
            t0 = time.time()
            # Register a small dataframe of needed draft_ids and join the
            # ``games`` view to it in a temp view. ``iter_training_rows``
            # can then run against the temp view via ``view_name=``.
            con.register(
                "_needed_drafts",
                pd.DataFrame({"draft_id": draft_ids}),
            )
            view_name = f"_games_for_{set_code.lower()}"
            con.execute(
                f"CREATE OR REPLACE TEMP VIEW {view_name} AS "
                f"SELECT g.* FROM games g "
                f"WHERE g.draft_id IN (SELECT draft_id FROM _needed_drafts)"
            )

            name_lookup = build_name_lookup(set_code)
            stats = TrainingRowStats()
            found = 0
            unsafe_here = 0
            wanted = set(draft_ids)
            seen_drafts: set[str] = set()
            for tr in iter_training_rows(
                connection=con,
                set_code=set_code,
                event_type=EVENT_TYPE,
                view_name=view_name,
                name_lookup=name_lookup,
                stats=stats,
            ):
                # Iterate at most one row per draft — but we may need
                # to skip a draft if its first encountered deck is
                # simulator-unsafe; that's still "one decision per
                # draft", which is what ``seen_drafts`` tracks.
                if tr.draft_id in seen_drafts:
                    continue
                seen_drafts.add(tr.draft_id)
                deck_list = list(tr.deck)
                if not _deck_is_simulator_safe(deck_list):
                    unsafe_here += 1
                    if found + unsafe_here >= len(wanted):
                        break
                    continue
                decks[tr.draft_id] = (deck_list, set_code)
                found += 1
                if found + unsafe_here >= len(wanted):
                    break
            dropped_unsafe += unsafe_here
            log.info(
                "  %s: needed %d drafts, found %d  (dropped %d unsafe; wall %.1fs)",
                set_code,
                len(wanted),
                found,
                unsafe_here,
                time.time() - t0,
            )
    finally:
        con.close()
    return decks, dropped_unsafe


# ---------------------------------------------------------------------------
# Mulligan-arm phase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ArmKey:
    """Group key for one cached mulligan-arm result.

    Mirrors the website's :class:`_MulliganCacheKey` except we don't
    carry the deck signature in the key — we already have the draft
    id, which uniquely identifies the deck in Premier Draft (Bo1).
    """

    draft_id: str
    on_the_play: bool
    opp_mulligan_number: int | None


def _opp_from_row(row: pd.Series) -> int | None:
    """Mirror the website's `opp_mulligan_number` coercion.

    On the play the opp count is masked to None (the feature is NaN
    by design); on the draw we keep the integer when present.
    """
    if bool(row["on_the_play"]):
        return None
    v = row["opp_mulligan_number"]
    if pd.isna(v):
        return None
    return int(v)


# Worker-process state: each child process loads its own ModelBundle +
# FormatStats once via ``_init_worker`` and reuses them across tasks.
# A thread pool wouldn't scale here — the simulator is Python-heavy
# enough that the GIL serialises calls and 6 threads ran *slower*
# than 1 in earlier benchmarks. multiprocessing.Pool side-steps the
# GIL entirely at the cost of spawn time (one-time, ~5s per worker
# on Windows).
_WORKER_SERVICE: RecommendationService | None = None


def _init_worker(model_dir_str: str, sets: tuple[str, ...]) -> None:
    """Per-worker initialiser: load the bundle and per-set stats once."""
    global _WORKER_SERVICE
    bundle = ModelBundle.load(Path(model_dir_str))
    stats_by_set = {
        s: FormatStats.build(load_premier_draft_stats(s).by_arena_id.values()) for s in sets
    }
    _WORKER_SERVICE = RecommendationService(bundle=bundle, stats_by_set=stats_by_set)


# Pool task payload. Tuple form (instead of a dataclass) so it
# serialises cheaply between processes.
_WorkItem = tuple[
    _ArmKey,
    list[ParsedCard],  # deck (40 ParsedCards)
    str,  # set_code
    int,  # n_sims_per_mulligan
    int,  # n_mulligan_samples
]


def _do_arm(item: _WorkItem) -> tuple[_ArmKey, MulliganArmResult]:
    """Compute one mulligan arm inside a worker process.

    Uses the website's exact ``_compute_mulligan_arm`` so the seed
    derivation, sampling and floor logic all match what a real
    user's click would have produced.
    """
    key, deck, set_code, n_sims, n_samples = item
    assert _WORKER_SERVICE is not None  # initialiser must have run
    cache_key = _MulliganCacheKey(
        deck_signature=_deck_signature(deck),
        on_the_play=key.on_the_play,
        mulligan_number_to=1,
        opp_mulligan_number=key.opp_mulligan_number,
        n_sims_per_mulligan=n_sims,
        n_mulligan_samples=n_samples,
    )
    seed = _stable_seed(cache_key)
    res = _WORKER_SERVICE._compute_mulligan_arm(
        deck=deck,
        on_the_play=key.on_the_play,
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
    df_sample: pd.DataFrame,
    decks_by_draft: dict[str, tuple[list[ParsedCard], str]],
    model_dir: Path,
    n_sims_per_mulligan: int,
    n_mulligan_samples: int,
    n_workers: int,
    log: logging.Logger,
) -> dict[_ArmKey, MulliganArmResult]:
    """Compute the website-style mulligan arm once per
    ``(draft_id, on_play, opp_mull)`` group present in the sample.

    Parallelised via multiprocessing.Pool — see ``_init_worker``.
    """
    # Build the set of unique arm keys present in the sample.
    keys: set[_ArmKey] = set()
    for row in df_sample.itertuples(index=False):
        if row.draft_id not in decks_by_draft:
            continue
        keys.add(
            _ArmKey(
                draft_id=str(row.draft_id),
                on_the_play=bool(row.on_the_play),
                opp_mulligan_number=(
                    None
                    if bool(row.on_the_play) or pd.isna(row.opp_mulligan_number)
                    else int(row.opp_mulligan_number)
                ),
            )
        )
    log.info(
        "  %d unique (draft, on_play, opp_mull) groups across %d sample rows",
        len(keys),
        len(df_sample),
    )

    # Pre-build work items so the parent process is just a feeder.
    items: list[_WorkItem] = []
    for key in keys:
        deck, set_code = decks_by_draft[key.draft_id]
        items.append((key, deck, set_code, n_sims_per_mulligan, n_mulligan_samples))

    arms: dict[_ArmKey, MulliganArmResult] = {}
    t0 = time.time()

    if n_workers <= 1:
        # Single-process path: re-use the parent's import-time bundle.
        # Useful for debugging without paying spawn costs.
        _init_worker(str(model_dir), SETS)
        for i, item in enumerate(items):
            k, res = _do_arm(item)
            arms[k] = res
            if (i + 1) % 50 == 0 or i + 1 == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - i - 1) / (i + 1)
                log.info(
                    "  progress %d/%d  elapsed=%.0fs  eta=%.0fs",
                    i + 1,
                    len(items),
                    elapsed,
                    eta,
                )
    else:
        # Spawn context is the safe default on Windows; explicit so
        # Linux runs behave identically (no fork-time copies of e.g.
        # the parent's already-loaded bundle).
        ctx = mp.get_context("spawn")
        with ctx.Pool(
            processes=n_workers,
            initializer=_init_worker,
            initargs=(str(model_dir), SETS),
        ) as pool:
            for done, (k, res) in enumerate(
                pool.imap_unordered(_do_arm, items, chunksize=2), start=1
            ):
                arms[k] = res
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
    log.info("  done in %.0fs", time.time() - t0)
    return arms


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(
    *,
    df: pd.DataFrame,
    n_sims_per_mulligan: int,
    n_mulligan_samples: int,
    log: logging.Logger,
) -> None:
    df_use = df.dropna(subset=["p_mull"]).copy()
    df_use["delta"] = df_use["p_keep"] - df_use["p_mull"]
    df_use["should_mull"] = df_use["p_keep"] < df_use["p_mull"]
    n = len(df_use)
    n_mull = int(df_use["should_mull"].sum())

    log.info("\n==== Website-style mull benchmark ====")
    log.info("Sample: %d kept-7 hands across %d unique drafts", n, df_use["draft_id"].nunique())
    log.info(
        "Mull settings: %d samples x %d sims, with deeper-mulligan floor",
        n_mulligan_samples,
        n_sims_per_mulligan,
    )

    log.info("\n---- Headline ----")
    log.info(
        "Should-mull rate: %d / %d  (%.2f%%)",
        n_mull,
        n,
        100.0 * n_mull / n if n else 0.0,
    )
    log.info("Actual WR of sampled hands: %.4f", float(df_use["won"].mean()))
    log.info("Mean p_keep: %.4f", float(df_use["p_keep"].mean()))
    log.info("Mean p_mull: %.4f (floored)", float(df_use["p_mull"].mean()))
    log.info("Mean p_mull_raw: %.4f (pre-floor)", float(df_use["p_mull_raw"].mean()))

    log.info("\n---- Delta (p_keep - p_mull) distribution ----")
    for q, label in ((0.01, "p01"), (0.10, "p10"), (0.50, "p50"), (0.90, "p90"), (0.99, "p99")):
        log.info("  %s: %+.4f", label, float(df_use["delta"].quantile(q)))
    log.info("  min: %+.4f", float(df_use["delta"].min()))
    log.info("  max: %+.4f", float(df_use["delta"].max()))

    log.info("\n---- Floor diagnostics ----")
    # n_samples_below_floor is a per-group integer; sum across rows
    # double-counts groups so we use group-level stats instead.
    group_keys = df_use[["draft_id", "on_the_play", "opp_mulligan_number"]].drop_duplicates()
    log.info("  unique mull-arm groups: %d", len(group_keys))
    # Per row, what fraction of the 50 samples were clamped on its arm?
    df_use["clamp_pct"] = df_use["n_samples_below_floor"] / n_mulligan_samples
    log.info(
        "  mean fraction of samples clamped per row: %.2f%%",
        100.0 * float(df_use["clamp_pct"].mean()),
    )
    df_use["floor_bump"] = df_use["p_mull"] - df_use["p_mull_raw"]
    log.info(
        "  mean p_mull bump from floor: %+.4f  (max: %+.4f)",
        float(df_use["floor_bump"].mean()),
        float(df_use["floor_bump"].max()),
    )

    log.info("\n---- By set ----")
    for set_code, sub in df_use.groupby("expansion"):
        sm = int(sub["should_mull"].sum())
        log.info(
            "  %s: n=%5d  should_mull=%5d (%.2f%%)  mean p_keep=%.4f  mean p_mull=%.4f",
            set_code,
            len(sub),
            sm,
            100.0 * sm / len(sub),
            float(sub["p_keep"].mean()),
            float(sub["p_mull"].mean()),
        )

    log.info("\n---- By on-play / on-draw ----")
    for on_play, sub in df_use.groupby("on_the_play"):
        sm = int(sub["should_mull"].sum())
        label = "on play" if on_play else "on draw"
        log.info(
            "  %s: n=%5d  should_mull=%5d (%.2f%%)  mean p_keep=%.4f  mean p_mull=%.4f",
            label,
            len(sub),
            sm,
            100.0 * sm / len(sub),
            float(sub["p_keep"].mean()),
            float(sub["p_mull"].mean()),
        )

    log.info("\n---- Among flagged 'should-mull' hands ----")
    flagged = df_use[df_use["should_mull"]]
    if len(flagged):
        log.info(
            "  actual WR: %.4f  predicted p_keep mean: %.4f  p_mull mean: %.4f",
            float(flagged["won"].mean()),
            float(flagged["p_keep"].mean()),
            float(flagged["p_mull"].mean()),
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    ap.add_argument(
        "--n-drafts",
        type=int,
        default=1200,
        help=(
            "Number of test-split drafts to sample. Each draft contributes "
            "~4 kept-7 games, so 1200 drafts ~= 5000 hands."
        ),
    )
    ap.add_argument("--n-mulligan-samples", type=int, default=DEFAULT_N_MULLIGAN_SAMPLES)
    ap.add_argument("--n-sims-per-mulligan", type=int, default=DEFAULT_N_SIMS_PER_MULLIGAN)
    ap.add_argument(
        "--n-workers",
        type=int,
        default=6,
        help="Worker processes for the mulligan-arm phase. 1 == sequential.",
    )
    args = ap.parse_args()

    log_path = args.model_dir / "website_mulligan_benchmark.log"
    log = setup_logger(log_path)
    log.info("==== %s ====", __doc__.splitlines()[0])
    log.info("Model dir: %s", args.model_dir)
    log.info(
        "Settings: n_drafts=%d  n_mulligan_samples=%d  n_sims_per_mulligan=%d  n_workers=%d",
        args.n_drafts,
        args.n_mulligan_samples,
        args.n_sims_per_mulligan,
        args.n_workers,
    )

    # ---- Bundle + stats ----
    log.info("\nLoading model bundle and per-set 17Lands stats...")
    t0 = time.time()
    bundle = ModelBundle.load(args.model_dir)
    log.info(
        "  loaded model (best_iter=%d, %d features) in %.1fs",
        bundle.best_iteration,
        len(bundle.feature_names),
        time.time() - t0,
    )

    # ---- Sample test drafts ----
    log.info("\nLoading & sampling test drafts...")
    df_sample = load_test_sample(
        n_drafts=args.n_drafts,
        bundle_feature_names=list(bundle.feature_names),
        log=log,
    )

    # ---- Keep arm (vectorised over the whole sample) ----
    log.info("\nPredicting p_keep from stored features...")
    df_sample["p_keep"] = predict_p_keep(df_sample=df_sample, bundle=bundle, log=log)

    # ---- Deck reconstruction ----
    log.info("\nReconstructing decks (one per draft) via DuckDB filtered views...")
    decks_by_draft, dropped_unsafe = load_decks_by_draft(df_sample=df_sample, log=log)
    log.info(
        "  reconstructed %d decks (dropped %d for simulator-unsafe cards)",
        len(decks_by_draft),
        dropped_unsafe,
    )

    # Drop rows whose draft we couldn't simulate (unsafe deck or
    # missing reconstruction). The remaining rows are what the
    # website would have been able to evaluate anyway.
    have_deck = df_sample["draft_id"].isin(decks_by_draft.keys())
    if not have_deck.all():
        log.info(
            "  %d sample rows belong to dropped drafts; excluding from analysis",
            int((~have_deck).sum()),
        )
        df_sample = df_sample.loc[have_deck].reset_index(drop=True)

    # ---- Mulligan arm (deck-grouped, multi-process) ----
    log.info("\nComputing mulligan arm per (draft, on_play, opp_mull)...")
    arms = compute_mull_arms(
        df_sample=df_sample,
        decks_by_draft=decks_by_draft,
        model_dir=args.model_dir,
        n_sims_per_mulligan=args.n_sims_per_mulligan,
        n_mulligan_samples=args.n_mulligan_samples,
        n_workers=args.n_workers,
        log=log,
    )

    # ---- Assemble per-row results ----
    p_mull = np.empty(len(df_sample), dtype=float)
    p_mull_raw = np.empty(len(df_sample), dtype=float)
    n_below = np.empty(len(df_sample), dtype=int)
    floor = np.empty(len(df_sample), dtype=float)
    for i, row in enumerate(df_sample.itertuples(index=False)):
        key = _ArmKey(
            draft_id=str(row.draft_id),
            on_the_play=bool(row.on_the_play),
            opp_mulligan_number=(
                None
                if bool(row.on_the_play) or pd.isna(row.opp_mulligan_number)
                else int(row.opp_mulligan_number)
            ),
        )
        arm = arms.get(key)
        if arm is None:
            p_mull[i] = float("nan")
            p_mull_raw[i] = float("nan")
            n_below[i] = 0
            floor[i] = float("nan")
        else:
            p_mull[i] = arm.floored_mean
            p_mull_raw[i] = arm.raw_mean
            n_below[i] = arm.n_samples_below_floor
            floor[i] = arm.floor_value if arm.floor_value is not None else float("nan")
    df_sample["p_mull"] = p_mull
    df_sample["p_mull_raw"] = p_mull_raw
    df_sample["n_samples_below_floor"] = n_below
    df_sample["floor_value"] = floor

    # ---- Report ----
    report(
        df=df_sample,
        n_sims_per_mulligan=args.n_sims_per_mulligan,
        n_mulligan_samples=args.n_mulligan_samples,
        log=log,
    )
    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
