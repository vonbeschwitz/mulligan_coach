"""Per-deck mulligan benchmark for the TLA v2 model.

Replaces the unconditional ``0.4295`` "to-6 mull WR" used in
``mulligan_analysis.log`` with a per-row, per-deck benchmark
computed by simulation:

For each kept-7 (``mulligan_number=0``) row in the test split, we
estimate ``P(win | mull this deck to 6)`` by sampling
``N_MULLIGAN_SAMPLES`` smoother-aware 7-card draws from the deck,
predicting at ``mulligan_number=1`` for each, and averaging. The
smoother is the Arena BO1 hand-smoothing reverse-engineered against
700k+ FIN games — same code used by
:func:`simulate_mulligan_from_deck`.

We deliberately keep the hand size at 7 cards (and bump
``mulligan_number`` to 1) to match training distribution: the model
was trained on 7-card pre-bottom hands plus a context feature. The
bottoming heuristic isn't needed here — the model treats it as
implicit.

The analysis answers two questions:

* How many kept-7 hands does the model flag as "should have mulled"
  when the threshold is *per-deck* rather than the unconditional
  ``0.4295``? Does the actual win-rate on those hands beat the
  per-deck benchmark?
* Does the per-deck benchmark vary meaningfully across decks (i.e.,
  is the unconditional flat number really losing information)?

Performance note: the full test set has ~45k kept-7 rows; running
``N_MULLIGAN_SAMPLES`` sims per row is ~3 hours at ``n_sims=100``.
We sub-sample ``N_HANDS_TO_EVALUATE`` rows for tractability.
"""

from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from mulligan_coach_cards import load_parsed_cards
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats
from mulligan_coach_features import (
    build_feature_row,
    compute_format_priors,
    compute_format_wr_distribution,
    shrink_stats,
    zscore_stats,
)
from mulligan_coach_model import ModelBundle
from mulligan_coach_model.feature_matrix import _library_from_deck
from mulligan_coach_model.inference import _predict_proba
from mulligan_coach_model.training_rows import (
    TrainingRow,
    TrainingRowStats,
    build_name_lookup,
    iter_training_rows,
)
from mulligan_coach_simulation import simulate
from mulligan_coach_simulation.runtime import Card
from mulligan_coach_simulation.smoother import draw_smoothed_hand

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = REPO_ROOT / "models" / "tla_v2"
DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
PARQUET_DIR = REPO_ROOT / "data" / "processed" / "model_training" / "TLA" / "PremierDraft"
LOG_PATH = MODEL_DIR / "mulligan_analysis_per_deck.log"

# Sampling parameters.
N_HANDS_TO_EVALUATE = 200
N_MULLIGAN_SAMPLES = 10  # smoother-aware mull samples per row
N_SIMS_PER_PREDICT = 100
SEED = 20260512

# Re-derive the test split with the same parameters train.py used.
TRAIN_SEED = 0
VAL_FRAC = 0.10
CALIB_FRAC = 0.10
TEST_FRAC = 0.10


def setup_logger() -> logging.Logger:
    log = logging.getLogger("mulligan_per_deck")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_test_keys(log: logging.Logger) -> set[tuple[str, int, int]]:
    """Re-derive the test split's (draft_id, match_number, game_number)
    keys by scanning the chunk parquets. Mirrors the seed=0 grouped
    split used by train.py."""
    log.info("Re-deriving test split from chunk parquets...")
    t0 = time.time()
    rows_meta: list[pd.DataFrame] = []
    chunks = sorted(PARQUET_DIR.glob("chunk_*.parquet"))
    for chunk in chunks:
        df = pq.read_table(  # type: ignore[no-untyped-call]
            chunk, columns=["draft_id", "match_number", "game_number"]
        ).to_pandas()
        rows_meta.append(df)
    meta = pd.concat(rows_meta, ignore_index=True)
    log.info(f"  loaded {len(meta):,} rows across {len(chunks)} chunks")

    # Replicate _grouped_split's assignment.
    unique = meta["draft_id"].unique()
    rng = np.random.default_rng(TRAIN_SEED)
    shuffled = rng.permutation(unique)
    n = len(shuffled)
    n_val = round(n * VAL_FRAC)
    n_calib = round(n * CALIB_FRAC)
    n_test = round(n * TEST_FRAC)
    n_train = n - n_val - n_calib - n_test
    test_ids = set(shuffled[n_train + n_val + n_calib :].tolist())
    test_mask = meta["draft_id"].isin(test_ids)
    keys = {
        (str(d), int(m), int(g))
        for d, m, g in zip(
            meta.loc[test_mask, "draft_id"],
            meta.loc[test_mask, "match_number"],
            meta.loc[test_mask, "game_number"],
            strict=True,
        )
    }
    log.info(f"  test split: {len(keys):,} keys; wall {time.time() - t0:.1f}s")
    return keys


def main() -> None:
    log = setup_logger()
    log.info("Loading TLA card / stats data...")
    t0 = time.time()
    cards = list(load_parsed_cards("TLA"))
    stats_lookup = load_premier_draft_stats("TLA")
    stats_list = list(stats_lookup.by_name.values())
    priors = compute_format_priors(stats_list)
    shrunk_dict = shrink_stats(stats_list, priors=priors)
    shrunk_list = list(shrunk_dict.values())
    wr_dist = compute_format_wr_distribution(shrunk_list)
    zscores_dict = zscore_stats(shrunk_list, distribution=wr_dist)
    bundle = ModelBundle.load(MODEL_DIR)
    log.info(
        f"  loaded {len(cards)} cards, {len(shrunk_dict)} stats, "
        f"model best_iter={bundle.best_iteration}; "
        f"setup wall {time.time() - t0:.1f}s"
    )

    test_keys = load_test_keys(log)

    log.info(f"\nSampling {N_HANDS_TO_EVALUATE} kept-7 hands from the test split...")
    t0 = time.time()
    name_lookup = build_name_lookup("TLA")
    rng = random.Random(SEED)
    sample: list[TrainingRow] = []
    seen = 0
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        tr_stats = TrainingRowStats()
        for tr in iter_training_rows(
            connection=con,
            set_code="TLA",
            name_lookup=name_lookup,
            stats=tr_stats,
        ):
            if tr.mulligan_number != 0:
                continue
            if (tr.draft_id, tr.match_number, tr.game_number) not in test_keys:
                continue
            seen += 1
            if len(sample) < N_HANDS_TO_EVALUATE:
                sample.append(tr)
            else:
                j = rng.randint(0, seen - 1)
                if j < N_HANDS_TO_EVALUATE:
                    sample[j] = tr
            if seen >= 20_000:
                break
    finally:
        con.close()
    log.info(
        f"  streamed {seen} test kept-7 rows, sampled {len(sample)}; wall {time.time() - t0:.1f}s"
    )

    # ---- Compute per-row keep + per-deck mull predictions ----
    log.info(
        f"\nComputing per-row keep + per-deck mull predictions "
        f"(N_mull_samples={N_MULLIGAN_SAMPLES}, "
        f"n_sims={N_SIMS_PER_PREDICT})..."
    )
    t0 = time.time()
    rows_out: list[dict[str, object]] = []
    for i, tr in enumerate(sample):
        if i % 25 == 0 and i > 0:
            elapsed = time.time() - t0
            log.info(
                f"  progress {i}/{len(sample)}  elapsed={elapsed:.0f}s  "
                f"eta={elapsed * (len(sample) - i) / i:.0f}s"
            )
        hand_parsed = list(tr.hand)
        deck_parsed = list(tr.deck)

        # ---- Keep arm: predict P(win | keep this hand) ----
        library_parsed = list(_library_from_deck(tuple(hand_parsed), tuple(deck_parsed)))
        agg = simulate(
            hand_parsed,
            library_parsed,
            on_the_play=tr.on_the_play,
            n_runs=N_SIMS_PER_PREDICT,
            seed=hash((tr.draft_id, tr.match_number, tr.game_number, "keep")) & 0xFFFFFFFF,
        )
        row = build_feature_row(
            hand=hand_parsed,
            deck=deck_parsed,
            aggregate_stats=agg,
            shrunk=shrunk_dict,
            zscores=zscores_dict,
            on_the_play=tr.on_the_play,
            mulligan_number=0,
            event_type="PremierDraft",
            set_code="TLA",
        )
        row["opp_mulligan_count_if_known"] = (
            float("nan") if tr.on_the_play else float(tr.opp_mulligan_number)
        )
        base_margin = bundle.baseline.margin(
            user_wr_bucket=None,
            user_n_games_bucket=None,
            on_the_play=tr.on_the_play,
            opp_mulligan_number=tr.opp_mulligan_number,
        )
        p_keep = _predict_proba(bundle, row, base_margin)

        # ---- Mull arm: per-deck benchmark via smoother-aware sampling ----
        sub_rng = random.Random(
            hash((tr.draft_id, tr.match_number, tr.game_number, "mull")) & 0xFFFFFFFF
        )
        mull_probs: list[float] = []
        for _ in range(N_MULLIGAN_SAMPLES):
            # Smoother needs Card wrappers (for is_land checks).
            deck_cards = [Card(instance_id=idx, parsed=p) for idx, p in enumerate(deck_parsed)]
            mull_hand_cards, mull_library_cards = draw_smoothed_hand(deck_cards, sub_rng)
            mull_hand_parsed = [c.parsed for c in mull_hand_cards]
            mull_library_parsed = [c.parsed for c in mull_library_cards]
            agg2 = simulate(
                mull_hand_parsed,
                mull_library_parsed,
                on_the_play=tr.on_the_play,
                n_runs=N_SIMS_PER_PREDICT,
                seed=sub_rng.randint(0, 2**31 - 1),
            )
            row2 = build_feature_row(
                hand=mull_hand_parsed,
                deck=deck_parsed,
                aggregate_stats=agg2,
                shrunk=shrunk_dict,
                zscores=zscores_dict,
                on_the_play=tr.on_the_play,
                mulligan_number=1,
                event_type="PremierDraft",
                set_code="TLA",
            )
            row2["opp_mulligan_count_if_known"] = (
                float("nan") if tr.on_the_play else float(tr.opp_mulligan_number)
            )
            p_mull = _predict_proba(bundle, row2, base_margin)
            mull_probs.append(p_mull)
        p_mull_avg = float(np.mean(mull_probs))
        p_mull_std = float(np.std(mull_probs))

        rows_out.append(
            {
                "won": int(tr.won),
                "on_the_play": tr.on_the_play,
                "opp_mulligan_number": tr.opp_mulligan_number,
                "p_keep": p_keep,
                "p_mull_per_deck": p_mull_avg,
                "p_mull_std": p_mull_std,
                "should_mull_per_deck": p_keep < p_mull_avg,
                "should_mull_uncond": p_keep < 0.4295,
            }
        )
    log.info(f"  done. wall {time.time() - t0:.0f}s")
    df = pd.DataFrame(rows_out)

    # ---- Report ----
    log.info(f"\n==== Per-deck mulligan analysis (n={len(df)} test kept-7 hands) ====")
    log.info(f"\nActual win rate of sampled hands: {df['won'].mean():.4f}")
    log.info(f"Mean p_keep: {df['p_keep'].mean():.4f}")
    log.info(f"Mean per-deck p_mull: {df['p_mull_per_deck'].mean():.4f}")
    log.info(f"Std of per-deck p_mull across hands: {df['p_mull_per_deck'].std():.4f}")
    log.info("  (if this is 0, the per-deck benchmark is degenerate)")
    log.info(
        f"Mean within-row p_mull std (across {N_MULLIGAN_SAMPLES} samples): "
        f"{df['p_mull_std'].mean():.4f}"
    )
    log.info("\nPer-deck p_mull distribution:")
    log.info(f"  p10: {df['p_mull_per_deck'].quantile(0.10):.4f}")
    log.info(f"  p25: {df['p_mull_per_deck'].quantile(0.25):.4f}")
    log.info(f"  p50: {df['p_mull_per_deck'].quantile(0.50):.4f}")
    log.info(f"  p75: {df['p_mull_per_deck'].quantile(0.75):.4f}")
    log.info(f"  p90: {df['p_mull_per_deck'].quantile(0.90):.4f}")

    n_flag_unc = int(df["should_mull_uncond"].sum())
    n_flag_pd = int(df["should_mull_per_deck"].sum())
    log.info("\n---- 'Should have mulled' detection ----")
    log.info(
        f"Unconditional (p_keep < 0.4295):  {n_flag_unc} / {len(df)} "
        f"({n_flag_unc / len(df) * 100:.1f}%)"
    )
    log.info(
        f"Per-deck     (p_keep < p_mull):   {n_flag_pd} / {len(df)} "
        f"({n_flag_pd / len(df) * 100:.1f}%)"
    )

    log.info("\nActual WR among 'should mull' flagged hands:")
    for col, label in (
        ("should_mull_uncond", "unconditional"),
        ("should_mull_per_deck", "per-deck"),
    ):
        flagged = df[df[col]]
        if len(flagged) > 0:
            log.info(
                f"  {label}: WR={flagged['won'].mean():.4f}  "
                f"(n={len(flagged)}; predicted p_keep mean="
                f"{flagged['p_keep'].mean():.4f}; per-deck mull mean="
                f"{flagged['p_mull_per_deck'].mean():.4f})"
            )

    # Agreement between the two flags.
    both = df["should_mull_uncond"] & df["should_mull_per_deck"]
    only_unc = df["should_mull_uncond"] & ~df["should_mull_per_deck"]
    only_pd = ~df["should_mull_uncond"] & df["should_mull_per_deck"]
    neither = ~df["should_mull_uncond"] & ~df["should_mull_per_deck"]
    log.info("\n---- Agreement matrix ----")
    log.info(f"  both flag mull:       {int(both.sum())}")
    log.info(
        f"  only uncond flags:    {int(only_unc.sum())}  "
        f"(actual WR: {df.loc[only_unc, 'won'].mean():.4f} "
        f"if n>0 else N/A)"
    )
    log.info(
        f"  only per-deck flags:  {int(only_pd.sum())}  "
        f"(actual WR: {df.loc[only_pd, 'won'].mean():.4f} "
        f"if n>0 else N/A)"
    )
    log.info(f"  neither flags mull:   {int(neither.sum())}")


if __name__ == "__main__":
    main()
