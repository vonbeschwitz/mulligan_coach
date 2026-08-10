"""Dump the individual hands where the model HARD-disagrees with a cohort.

"Hard disagreement" = the model returned a **clear_** verdict (clear_keep or
clear_mulligan) and the player did the opposite. Soft disagreements
(marginal_* verdicts) and borderline verdicts are excluded — those are cases
the model itself flagged as close.

Reuses ``cohort_first_mull_agreement``'s loading / cohort / verdict logic
verbatim so the rows here are exactly the ones counted in that script's
``hard disagreement`` line, then joins the actual 7-card hand and decklist
back from the mulligan-decisions parquet for human inspection.

Run:
    .venv/Scripts/python.exe scripts/dump_hard_disagreements.py \\
        --sets MSH --cohort elite --event-type PremierDraft \\
        2>&1 | tee logs/msh_oos/5_elite_hard_disagreements.log
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from cohort_first_mull_agreement import (  # noqa: E402
    DECISIONS_DIR,
    DEFAULT_CHOICE_MODEL_DIR,
    MIN_N_GAMES,
    RANK_JOIN_KEY,
    assign_cohorts,
    join_premier_ranks,
    load_cohort_rows,
    predict_verdicts,
    setup_logger,
)
from mulligan_coach_model import ChoiceModelBundle  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sets", nargs="+", default=["MSH"])
    ap.add_argument("--cohort", default="elite")
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument("--min-n-games", type=int, default=MIN_N_GAMES)
    ap.add_argument("--choice-model-dir", type=Path, default=DEFAULT_CHOICE_MODEL_DIR)
    ap.add_argument("--out-csv", type=Path, default=None)
    args = ap.parse_args()

    log = setup_logger()
    bundle = ChoiceModelBundle.load(args.choice_model_dir)

    df = load_cohort_rows(
        args.sets, args.event_type, bundle.feature_names, log, min_n_games=args.min_n_games
    )
    if df.empty:
        log.info("no rows")
        return
    if args.event_type == "PremierDraft":
        df = join_premier_ranks(df, args.sets, log)
    df = assign_cohorts(df, args.event_type, log)
    df = predict_verdicts(df, bundle, log)

    sub = df.loc[df["cohort"] == args.cohort].copy()
    sub["actual_mull"] = ~sub["was_kept"].astype(bool)
    sub["pred_mull"] = sub["verdict"].isin(["clear_mulligan", "marginal_mulligan"])
    judged = sub.loc[sub["verdict"] != "borderline"]
    hard = judged.loc[
        (judged["pred_mull"] != judged["actual_mull"])
        & judged["verdict"].isin(["clear_keep", "clear_mulligan"])
    ].copy()

    log.info(
        "\n%s / %s: %d judged decisions, %d HARD disagreements (%.2f%%)",
        ",".join(args.sets),
        args.cohort,
        len(judged),
        len(hard),
        100.0 * len(hard) / max(len(judged), 1),
    )
    if hard.empty:
        return

    # Join the human-readable hand + deck back from the decisions parquet.
    frames = [
        pd.read_parquet(
            DECISIONS_DIR / f"{s}.{args.event_type}.parquet",
            columns=[*RANK_JOIN_KEY, "mulligan_number", "hand", "deck", "won", "on_play"],
        )
        for s in args.sets
    ]
    dec = pd.concat(frames, ignore_index=True)
    dec = dec.loc[dec["mulligan_number"] == 0].drop_duplicates(subset=RANK_JOIN_KEY)
    for frame in (hard, dec):
        frame["draft_id"] = frame["draft_id"].astype(str)
        for col in ("build_index", "match_number", "game_number"):
            frame[col] = frame[col].astype("int64")
    hard = hard.merge(dec, on=RANK_JOIN_KEY, how="left")

    hard = hard.sort_values("p_keep")
    for i, (_, r) in enumerate(hard.iterrows(), start=1):
        cards = str(r.get("hand", "")).split("|")
        n_lands = sum(1 for c in cards if c in _BASICS)
        log.info(
            "\n%2d. verdict=%-15s p_keep=%.3f   player=%s   on_play=%s   won=%s",
            i,
            r["verdict"],
            r["p_keep"],
            "MULLIGANED" if r["actual_mull"] else "KEPT",
            bool(r.get("on_play")),
            bool(r.get("won")),
        )
        log.info("    hand (%d basics): %s", n_lands, " | ".join(cards))

    if args.out_csv:
        cols = [
            "draft_id",
            "build_index",
            "match_number",
            "game_number",
            "verdict",
            "p_keep",
            "actual_mull",
            "on_play",
            "won",
            "hand",
            "deck",
        ]
        hard[[c for c in cols if c in hard.columns]].to_csv(args.out_csv, index=False)
        log.info("\nwrote %s", args.out_csv)


_BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes"}


if __name__ == "__main__":
    main()
