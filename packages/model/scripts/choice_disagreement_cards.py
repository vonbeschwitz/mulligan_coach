"""Card-level over-representation analysis on choice-model disagreements.

Hypothesis: if a particular card is mis-encoded in our ParsedCard
pipeline (wrong cost, wrong role tags, wrong mana shape, etc.), the
simulator's playability features for hands containing that card will
be systematically wrong, and the choice model will mis-rank those
hands. Such cards should show up disproportionately in the test rows
where the model and the player disagreed.

Method:

1. Reproduce the held-out test split (seed=0, 80/10/10 grouped by
   draft_id — same as the trainer).
2. Predict P(keep) on the test rows; threshold at 0.5 for a binary
   model verdict. ``was_kept`` is the player's actual decision.
3. Join the test rows back to the ``mulligan_decisions`` parquet on
   ``(draft_id, build_index, match_number, game_number,
   mulligan_number)`` to recover the hand string.
4. For each set in the test set, compute per-card "lift" — the ratio
   of a card's per-hand prevalence in the disagree group divided by
   its per-hand prevalence in the agree group.
5. Report top-N cards by lift in three slices:
   * **Overall disagree** — any disagreement, either direction.
   * **Among kept hands**: model said MULL, player kept (suggests
     the card was *under-encoded* — model thinks the hand is worse
     than it really is).
   * **Among mulled hands**: model said keep, player mulled
     (suggests the card was *over-encoded* — model thinks the hand
     is better than it really is).

Cards with lift much greater than 1 in any slice are candidates for
re-auditing in their per-set ParsedCard encoding (see
``scripts/audit/`` for the patch-fix tooling).

Caveats:

* Lift is a noisy estimator on small samples — we filter to cards
  with at least ``--min-total-appearances`` combined occurrences
  (default 100) and report ``n_disagree`` so the reader can spot
  weak evidence.
* The kept-bias of the test set (keep_rate ~ 0.90) means
  ``model_keep_player_mull`` has many disagreement rows but the
  ``model_mull_player_keep`` slice may be sparser depending on the
  model's predicted-mull rate.

Run:
    .venv/Scripts/python.exe packages/model/scripts/choice_disagreement_cards.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import xgboost as xgb

# Reuse the exact split logic the training pipeline used so we get
# byte-identical row assignment to "test".
from mulligan_coach_model.choice_train import _grouped_split

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CHOICE_MODEL_DIR = REPO_ROOT / "models" / "choice_v3"
DEFAULT_CHOICE_CACHE_DIR = REPO_ROOT / "data" / "processed" / "choice_training"
DEFAULT_DECISIONS_PATH = (
    REPO_ROOT
    / "data"
    / "processed"
    / "seventeenlands"
    / "mulligan_decisions"
    / "combined.PremierDraft.parquet"
)
DEFAULT_SETS = ("TLA", "TMT")


def setup_logger(log_path: Path) -> logging.Logger:
    log = logging.getLogger("choice_disagreement_cards")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(sh)
    return log


def load_choice_training(
    cache_dir: Path,
    sets: tuple[str, ...],
    event_type: str,
) -> pd.DataFrame:
    parts = []
    for set_code in sets:
        src = cache_dir / set_code / event_type
        paths = sorted(src.glob("chunk_*.parquet"))
        if not paths:
            raise SystemExit(f"no chunks under {src}")
        for p in paths:
            parts.append(pd.read_parquet(p))
    return pd.concat(parts, ignore_index=True)


def load_booster(model_dir: Path) -> tuple[xgb.Booster, list[str], int]:
    booster = xgb.Booster()
    booster.load_model(str(model_dir / "xgboost.json"))
    meta = json.loads((model_dir / "metadata.json").read_text())
    return booster, list(meta["feature_names"]), int(meta["best_iteration"])


def _count_per_hand_unique_cards(hand_series: pd.Series) -> Counter[str]:
    """For each hand, count each distinct card once.

    Multiple copies in the same hand (e.g. 2 Swamps) don't double-count
    for the per-hand prevalence metric, which is what 'lift' uses.
    """
    counter: Counter[str] = Counter()
    for h in hand_series:
        if not isinstance(h, str):
            continue
        for name in set(part.strip() for part in h.split("|") if part.strip()):
            counter[name] += 1
    return counter


def _lift_table(
    *,
    agree_hands: pd.Series,
    disagree_hands: pd.Series,
    min_total_appearances: int,
) -> pd.DataFrame:
    """Return per-card lift table.

    Each row: {card, n_disagree, n_agree, p_disagree, p_agree, lift}.
    Filtered to cards with at least ``min_total_appearances`` total
    hand-level occurrences across both groups.
    """
    n_a = len(agree_hands)
    n_d = len(disagree_hands)
    a_counts = _count_per_hand_unique_cards(agree_hands)
    d_counts = _count_per_hand_unique_cards(disagree_hands)

    rows = []
    for card in set(a_counts) | set(d_counts):
        a = a_counts.get(card, 0)
        d = d_counts.get(card, 0)
        if a + d < min_total_appearances:
            continue
        p_a = a / n_a if n_a else 0.0
        p_d = d / n_d if n_d else 0.0
        lift = (p_d / p_a) if p_a > 0 else float("inf")
        rows.append(
            {
                "card": card,
                "n_disagree": d,
                "n_agree": a,
                "p_disagree": p_d,
                "p_agree": p_a,
                "lift": lift,
            }
        )
    return pd.DataFrame(rows)


def _report_top(
    df: pd.DataFrame,
    *,
    label: str,
    n_disagree: int,
    n_agree: int,
    top_n: int,
    log: logging.Logger,
) -> None:
    log.info("\n%s", label)
    log.info("  n_disagree=%d  n_agree=%d", n_disagree, n_agree)
    if n_disagree < 50 or n_agree < 50:
        log.info("  (too few rows in one of the groups — skipping)")
        return
    if df.empty:
        log.info("  (no cards meet min-total-appearances filter)")
        return
    df_sorted = df.sort_values("lift", ascending=False).head(top_n)
    log.info(
        "  %-40s  %7s  %7s  %9s  %9s  %7s",
        "card",
        "n_dis",
        "n_agr",
        "p_dis",
        "p_agr",
        "lift",
    )
    for _, r in df_sorted.iterrows():
        log.info(
            "  %-40s  %7d  %7d  %9.4f  %9.4f  %7.2f",
            str(r["card"])[:40],
            int(r["n_disagree"]),
            int(r["n_agree"]),
            float(r["p_disagree"]),
            float(r["p_agree"]),
            float(r["lift"]),
        )


def analyse_set(
    df: pd.DataFrame,
    *,
    set_code: str,
    min_total_appearances: int,
    top_n: int,
    log: logging.Logger,
) -> dict[str, pd.DataFrame]:
    """Run the three disagreement-slice lifts for one set and return them.

    The returned dict has keys ``overall``, ``among_kept``,
    ``among_mulled`` — each value is the full per-card lift table for
    that slice (so callers can persist them).
    """
    sub = df[df["expansion"] == set_code].copy()
    log.info("\n==== Set: %s — n_test_rows=%d ====", set_code, len(sub))
    if len(sub) < 200:
        log.info("  (too few rows for this set — skipping)")
        return {}

    sub["model_keep"] = sub["p_keep"] >= 0.5
    sub["player_keep"] = sub["was_kept"].astype(bool)
    sub["disagree"] = sub["model_keep"] != sub["player_keep"]
    log.info(
        "  Overall disagree rate: %.2f%%  (n_disagree=%d)",
        100.0 * float(sub["disagree"].mean()),
        int(sub["disagree"].sum()),
    )

    results: dict[str, pd.DataFrame] = {}

    # ---- Overall disagree (either direction) ----
    overall = _lift_table(
        agree_hands=sub.loc[~sub["disagree"], "hand"],
        disagree_hands=sub.loc[sub["disagree"], "hand"],
        min_total_appearances=min_total_appearances,
    )
    _report_top(
        overall,
        label=f"---- {set_code} — overall disagree (any direction) ----",
        n_disagree=int(sub["disagree"].sum()),
        n_agree=int((~sub["disagree"]).sum()),
        top_n=top_n,
        log=log,
    )
    results["overall"] = overall

    # ---- Among kept hands: model said MULL, player kept ----
    # If a card is *under-encoded* (looks worse than it actually is),
    # the model will lean MULL on hands containing it but the player
    # — who knows the card's real value — will keep. So we'd expect
    # under-encoded cards to be over-represented here.
    kept = sub[sub["player_keep"]]
    mk_disagree = kept["p_keep"] < 0.5
    among_kept = _lift_table(
        agree_hands=kept.loc[~mk_disagree, "hand"],
        disagree_hands=kept.loc[mk_disagree, "hand"],
        min_total_appearances=min_total_appearances,
    )
    _report_top(
        among_kept,
        label=(
            f"---- {set_code} — among KEPT hands: model said MULL, player kept "
            "(possible UNDER-encoded cards) ----"
        ),
        n_disagree=int(mk_disagree.sum()),
        n_agree=int((~mk_disagree).sum()),
        top_n=top_n,
        log=log,
    )
    results["among_kept"] = among_kept

    # ---- Among mulled hands: model said keep, player mulled ----
    # If a card is *over-encoded* (looks better than it actually is),
    # the model will lean KEEP on hands containing it but the player
    # mulls anyway. So over-encoded cards should over-index here.
    mulled = sub[~sub["player_keep"]]
    km_disagree = mulled["p_keep"] >= 0.5
    among_mulled = _lift_table(
        agree_hands=mulled.loc[~km_disagree, "hand"],
        disagree_hands=mulled.loc[km_disagree, "hand"],
        min_total_appearances=min_total_appearances,
    )
    _report_top(
        among_mulled,
        label=(
            f"---- {set_code} — among MULLED hands: model said keep, player mulled "
            "(possible OVER-encoded cards) ----"
        ),
        n_disagree=int(km_disagree.sum()),
        n_agree=int((~km_disagree).sum()),
        top_n=top_n,
        log=log,
    )
    results["among_mulled"] = among_mulled

    return results


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument("--choice-cache-dir", type=Path, default=DEFAULT_CHOICE_CACHE_DIR)
    ap.add_argument("--decisions-path", type=Path, default=DEFAULT_DECISIONS_PATH)
    ap.add_argument("--sets", nargs="+", default=list(DEFAULT_SETS))
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--val-frac", type=float, default=0.10)
    ap.add_argument("--test-frac", type=float, default=0.10)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument(
        "--min-total-appearances",
        type=int,
        default=100,
        help="Only report cards with this many or more total appearances "
        "across agree+disagree groups (filters out rare-card noise).",
    )
    ap.add_argument("--top-n", type=int, default=20)
    ap.add_argument(
        "--log-name",
        default="choice_disagreement_cards.log",
        help="Log filename (saved into --choice-model-dir).",
    )
    args = ap.parse_args()

    log_path = args.choice_model_dir / args.log_name
    log = setup_logger(log_path)
    log.info("==== Card overrepresentation on choice-model disagreements ====")
    log.info("Choice model:  %s", args.choice_model_dir)
    log.info("Cache dir:     %s", args.choice_cache_dir)
    log.info("Decisions:     %s", args.decisions_path)
    log.info("Sets:          %s", args.sets)
    log.info(
        "Split:         val_frac=%.2f  test_frac=%.2f  seed=%d  (must match training)",
        args.val_frac,
        args.test_frac,
        args.seed,
    )
    log.info("Min total appearances: %d  Top N: %d", args.min_total_appearances, args.top_n)

    booster, feature_names, best_iter = load_booster(args.choice_model_dir)
    log.info("Loaded booster with %d features  best_iter=%d", len(feature_names), best_iter)

    df_all = load_choice_training(args.choice_cache_dir, tuple(args.sets), args.event_type)
    log.info("Loaded %d rows total across %s", len(df_all), args.sets)

    splits = _grouped_split(
        df_all["draft_id"],
        val_frac=args.val_frac,
        test_frac=args.test_frac,
        seed=args.seed,
    )
    test_df = df_all.loc[splits.test].reset_index(drop=True)
    log.info(
        "Test rows: %d  observed keep_rate=%.4f", len(test_df), float(test_df["was_kept"].mean())
    )

    # Predict P(keep) on test rows.
    X = test_df[feature_names].astype(float).to_numpy()
    dmat = xgb.DMatrix(X, feature_names=feature_names)
    test_df["p_keep"] = booster.predict(dmat, iteration_range=(0, best_iter + 1))

    # Join hands back. mulligan_decisions is keyed by (draft_id,
    # build_index, match_number, game_number, mulligan_number); the
    # choice cache carries the same keys so the merge is exact.
    log.info("Loading mulligan_decisions hand strings from %s", args.decisions_path)
    md = pd.read_parquet(
        args.decisions_path,
        columns=[
            "draft_id",
            "build_index",
            "match_number",
            "game_number",
            "mulligan_number",
            "hand",
        ],
    )
    test_df = test_df.merge(
        md,
        on=["draft_id", "build_index", "match_number", "game_number", "mulligan_number"],
        how="left",
        validate="one_to_one",
    )
    n_missing = int(test_df["hand"].isna().sum())
    if n_missing:
        log.warning("  %d test rows have no matching hand string — they'll be skipped", n_missing)
        test_df = test_df.dropna(subset=["hand"]).reset_index(drop=True)
    log.info("Joined test rows with hand strings: %d", len(test_df))

    # Per-set analysis.
    for set_code in args.sets:
        analyse_set(
            test_df,
            set_code=set_code,
            min_total_appearances=args.min_total_appearances,
            top_n=args.top_n,
            log=log,
        )

    log.info("\nFull log written to %s", log_path)


if __name__ == "__main__":
    main()
