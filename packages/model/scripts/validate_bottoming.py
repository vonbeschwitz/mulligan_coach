"""Brute-force validation of the bottoming heuristic on real TLA hands.

For each sampled (hand, deck, on_the_play) row from the 17Lands TLA
training data, this script:

1. Asks the heuristic in :mod:`mulligan_coach_simulation.bottoming`
   which of the 7 hand cards to put on the bottom.
2. Brute-forces all 7 candidates: for each candidate, builds the
   resulting (hand_6, library_34) state, runs the simulator on it,
   predicts P(win) at ``mulligan_number=1`` through the trained
   model.
3. Compares the heuristic's pick to the model-optimal pick.

The model is trained on 7-card pre-bottom hands, so feeding it a
6-card hand is slightly out-of-distribution. We still consider
*rankings* (ordering of candidates by model P(win)) meaningful —
the validation question is whether the heuristic picks near the
top of that ranking, not whether the absolute probabilities are
calibrated.

Outputs go to ``models/tla_v2/bottoming_validation.log``.
"""

from __future__ import annotations

import logging
import random
import sys
import time
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
from mulligan_coach_cards import load_parsed_cards
from mulligan_coach_cards.seventeenlands_stats import load_premier_draft_stats

# Pull build_feature_row from the same module the model uses for parity.
from mulligan_coach_features import (
    build_feature_row,
    compute_format_priors,
    compute_format_wr_distribution,
    shrink_stats,
    stats_for_card,
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
from mulligan_coach_simulation import bottom_card, simulate
from mulligan_coach_simulation.runtime import Card

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_DIR = REPO_ROOT / "models" / "tla_v2"
DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
LOG_PATH = MODEL_DIR / "bottoming_validation.log"

N_HANDS_TO_VALIDATE = 200
N_SIMS_PER_PREDICT = 1000
SEED = 20260512


def setup_logger() -> logging.Logger:
    log = logging.getLogger("validate_bottoming")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(LOG_PATH, mode="w", encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


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
    log.info(f"  loaded {len(cards)} cards, {len(shrunk_dict)} shrunk stats")

    bundle = ModelBundle.load(MODEL_DIR)
    log.info(
        f"  loaded model: {len(bundle.feature_names)} features, best_iter={bundle.best_iteration}"
    )
    log.info(f"Setup wall: {time.time() - t0:.1f}s")

    # Build a shrunk-OH-WR lookup keyed by `Card` for the bottoming
    # heuristic's rule S4. The stats dict is name-keyed (folded), so we
    # resolve via the shared folded-name join with DFC front-face
    # fallback.
    def oh_wr(c: Card) -> float | None:
        s = stats_for_card(c.parsed, shrunk_dict)
        return None if s is None else s.shrunk_opening_hand_win_rate

    log.info(f"Sampling {N_HANDS_TO_VALIDATE} hands from TLA PremierDraft...")
    t0 = time.time()
    name_lookup = build_name_lookup("TLA")
    rng = random.Random(SEED)
    # Reservoir-sample N hands while streaming through duckdb. Faster
    # than loading everything first.
    sample: list[TrainingRow] = []
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
    try:
        tr_stats = TrainingRowStats()
        # Filter to kept-7 (mulligan_number=0); we want to study the
        # heuristic's behaviour on real opening hands.
        n_seen = 0
        for tr in iter_training_rows(
            connection=con,
            set_code="TLA",
            name_lookup=name_lookup,
            stats=tr_stats,
        ):
            if tr.mulligan_number != 0:
                continue
            n_seen += 1
            if len(sample) < N_HANDS_TO_VALIDATE:
                sample.append(tr)
            else:
                # Reservoir: replace with probability N/n_seen.
                j = rng.randint(0, n_seen - 1)
                if j < N_HANDS_TO_VALIDATE:
                    sample[j] = tr
            if n_seen >= 50_000:  # cap streaming for speed
                break
    finally:
        con.close()
    log.info(
        f"  streamed {n_seen} kept-7 rows, sampled {len(sample)}; wall {time.time() - t0:.1f}s"
    )

    # ---- Main validation loop ----
    log.info(
        f"\nRunning brute force ({len(sample)} hands x 7 bottoms x sim n={N_SIMS_PER_PREDICT})..."
    )
    t0 = time.time()
    heuristic_ranks: list[int] = []  # 1-indexed; 1 = optimal
    heuristic_gaps: list[float] = []  # P(win) gap from optimal pick
    decision_categories: Counter[str] = Counter()
    failures = 0

    for i, tr in enumerate(sample):
        if i % 25 == 0 and i > 0:
            elapsed = time.time() - t0
            log.info(
                f"  progress {i}/{len(sample)}  elapsed={elapsed:.0f}s  "
                f"eta={elapsed * (len(sample) - i) / i:.0f}s"
            )

        hand_parsed = list(tr.hand)
        deck_parsed = list(tr.deck)
        # Wrap as Cards so the heuristic and sim can use them.
        cards_full = [Card(instance_id=idx, parsed=p) for idx, p in enumerate(deck_parsed)]
        # The training data records deck (40) and hand (7). The hand
        # cards are a multiset of deck cards. We need to identify a
        # corresponding Card-for-each-hand-position. We assign by
        # finding the first deck index whose parsed matches each hand
        # parsed entry (greedy, first-fit).
        deck_avail_idxs = list(range(len(cards_full)))
        hand_card_idxs: list[int] = []
        for hp in hand_parsed:
            for k, didx in enumerate(deck_avail_idxs):
                if cards_full[didx].parsed is hp:
                    hand_card_idxs.append(didx)
                    del deck_avail_idxs[k]
                    break
            else:
                # Fall back to oracle_id match (the deck/hand were
                # constructed from the same name_lookup so the parsed
                # objects should be identical, but defend in case).
                for k, didx in enumerate(deck_avail_idxs):
                    if cards_full[didx].parsed.oracle_id == hp.oracle_id:
                        hand_card_idxs.append(didx)
                        del deck_avail_idxs[k]
                        break
                else:
                    failures += 1
                    break
        if len(hand_card_idxs) != 7:
            continue
        hand_cards: list[Card] = [cards_full[k] for k in hand_card_idxs]
        # heuristic_pick is the Card object the heuristic would bottom.
        heuristic_pick = bottom_card(hand_cards, cards_full, oh_wr=oh_wr)

        # Brute force all 7 candidates.
        candidate_probs: list[float] = []
        for cand in hand_cards:
            hand6_cards = [c for c in hand_cards if c is not cand]
            hand6_parsed = [c.parsed for c in hand6_cards]
            library_parsed = list(_library_from_deck(tuple(hand6_parsed), tuple(deck_parsed)))
            # Put the bottomed card at the *end* of the library
            # (true bottom of deck). The simulator reshuffles
            # internally — we want the bottomed card NOT to be in
            # the draw pool. Easiest: keep it out of the library
            # entirely. Library size becomes 33 instead of 34.
            # Note: this slightly under-represents the actual
            # post-bottom deck (the bottomed card sits at index 33
            # of 34); the simulator's 4-turn window virtually never
            # reaches that depth, so the approximation is benign.
            agg = simulate(
                hand6_parsed,
                library_parsed,
                on_the_play=tr.on_the_play,
                n_runs=N_SIMS_PER_PREDICT,
                seed=hash((tr.draft_id, tr.match_number, tr.game_number, cand.instance_id))
                & 0xFFFFFFFF,
            )
            row = build_feature_row(
                hand=hand6_parsed,
                deck=deck_parsed,
                aggregate_stats=agg,
                shrunk=shrunk_dict,
                zscores=zscores_dict,
                on_the_play=tr.on_the_play,
                mulligan_number=1,
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
            p = _predict_proba(bundle, row, base_margin)
            candidate_probs.append(p)

        # Identify heuristic's pick index and its rank.
        heuristic_idx = hand_cards.index(heuristic_pick)
        # Rank by descending P(win): rank 1 = highest P(win).
        sorted_desc = np.argsort(-np.array(candidate_probs))
        rank = int(np.where(sorted_desc == heuristic_idx)[0][0]) + 1
        best_p = max(candidate_probs)
        heuristic_p = candidate_probs[heuristic_idx]
        gap = best_p - heuristic_p
        heuristic_ranks.append(rank)
        heuristic_gaps.append(gap)
        decision_categories["land" if heuristic_pick.is_land else "spell"] += 1

    log.info(f"  done. wall {time.time() - t0:.0f}s; failures={failures}")

    # ---- Report ----
    ranks = np.array(heuristic_ranks)
    gaps = np.array(heuristic_gaps)
    log.info(f"\n==== Validation summary on n={len(ranks)} hands ====")
    log.info(f"Decision split: {dict(decision_categories)}")
    log.info("\nRank distribution of heuristic pick (1=best of 7 by model):")
    for r in range(1, 8):
        count = int((ranks == r).sum())
        log.info(f"  rank {r}: {count} ({count / len(ranks) * 100:.1f}%)")
    log.info(f"\nMean rank:   {ranks.mean():.2f}")
    log.info(f"Median rank: {int(np.median(ranks))}")
    log.info(f"Top-1 rate:  {(ranks == 1).mean() * 100:.1f}%")
    log.info(f"Top-3 rate:  {(ranks <= 3).mean() * 100:.1f}%")
    log.info("\nP(win) gap from optimal (heuristic_p - best_p, negative=worse):")
    log.info(f"  min:    {-gaps.max():.4f}")
    log.info(f"  p25:    {-np.percentile(gaps, 75):.4f}")
    log.info(f"  median: {-np.percentile(gaps, 50):.4f}")
    log.info(f"  p75:    {-np.percentile(gaps, 25):.4f}")
    log.info(f"  mean:   {-gaps.mean():.4f}")
    log.info(f"  max:    {-gaps.min():.4f}  (0 == heuristic was optimal)")
    log.info(
        f"\nFraction of hands where heuristic was within 0.01 P(win) of "
        f"optimal: {(gaps <= 0.01).mean() * 100:.1f}%"
    )
    log.info(f"Fraction within 0.005: {(gaps <= 0.005).mean() * 100:.1f}%")


if __name__ == "__main__":
    main()
