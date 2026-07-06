"""Per-row P(mulligan) dump for elite first-mull decisions + calibration table.

Companion to ``elite_first_mull_agreement.py``, which only records the
4-level verdict per decision. This script re-scores the same elite mn=0
decisions (same cohort filters, same deck/hand parsing, same
``recommend_choice`` path) but keeps the raw ``p_keep`` per row, so we
can tabulate model-probability calibration at finer granularity than
the four verdict bands — e.g. "of hands the model gives a 30-35%
mulligan chance, what fraction did elite players actually mulligan?".

Outputs:

* ``logs/elite_calibration_rows.parquet`` — one row per elite decision:
  ``set_code, event_type, p_keep, was_kept, on_play``.
* A calibration table (5%-wide buckets of P(mull) = 1 - p_keep vs
  observed elite mull fraction) printed at the end, pooled over all
  sets, plus per-event-type splits.

Reuses the eval script's helpers by importing it as a module, so cohort
definitions / parsing / filters can't drift between the two scripts.

Run:
    .venv/Scripts/python.exe scripts/elite_calibration_dump.py \
        --choice-model-dir models/choice_v9 --n-workers 10 \
        > logs/elite_calibration_dump.log 2>&1
"""

from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import elite_first_mull_agreement as efma  # noqa: E402

SETS = ("TLA", "TMT", "SOS")
EVENT_TYPES = ("PremierDraft", "TradDraft")
OUT_PARQUET = REPO_ROOT / "logs" / "elite_calibration_rows.parquet"


def _do_one_p(item: efma._WorkItem) -> tuple[int, float]:
    """Same as efma._do_one but returns the raw p_keep, not the verdict."""
    assert efma._WORKER_SERVICE is not None
    idx, hand, deck, on_play, opp_mull, n_sims = item
    rec = efma._WORKER_SERVICE.recommend_choice(
        hand=hand,
        deck=deck,
        on_the_play=on_play,
        mulligan_number=0,
        opp_mulligan_number=opp_mull,
        n_sims=n_sims,
    )
    return idx, float(rec.p_keep)


def score_one(
    *,
    event_type: str,
    set_code: str,
    choice_model_dir: Path,
    n_sims: int,
    n_workers: int,
    log: logging.Logger,
) -> pd.DataFrame:
    """Elite mn=0 decisions for one (set, event) scored to p_keep per row.

    Mirrors ``efma.evaluate``'s prep exactly (same drops, same deck-size
    bounds 40-42) but keeps probabilities.
    """
    log.info("=== %s / %s ===", event_type, set_code)
    df = efma.load_elite_decisions(event_type, set_code, log)
    if df.empty:
        return pd.DataFrame()

    lookup = efma.build_name_lookup(set_code)
    decks_by_draft: dict[str, list] = {}
    for draft_id, sub in df.groupby("draft_id"):
        deck = efma._parse_deck_string(str(sub.iloc[0]["deck"]), lookup)
        if deck is None or not (40 <= len(deck) <= 42):
            continue
        if not efma._deck_is_simulator_safe(deck):
            continue
        decks_by_draft[str(draft_id)] = deck

    df = df.loc[df["draft_id"].astype(str).isin(decks_by_draft)].reset_index(drop=True)
    df["hand_cards"] = [efma._parse_hand_string(str(r["hand"]), lookup) for _, r in df.iterrows()]
    df = df.loc[~df["hand_cards"].isna()].reset_index(drop=True)
    log.info("  rows after parse: %d", len(df))
    if df.empty:
        return pd.DataFrame()

    items: list[efma._WorkItem] = []
    for i, row in enumerate(df.itertuples(index=False)):
        on_play = bool(row.on_play)
        opp_mull = None if on_play or pd.isna(row.opp_num_mulligans) else int(row.opp_num_mulligans)
        items.append(
            (i, list(row.hand_cards), decks_by_draft[str(row.draft_id)], on_play, opp_mull, n_sims)
        )

    p_keep: list[float | None] = [None] * len(df)
    t0 = time.time()
    ctx = mp.get_context("spawn")
    with ctx.Pool(
        processes=n_workers,
        initializer=efma._init_worker,
        initargs=(str(choice_model_dir), set_code),
    ) as pool:
        for done, (idx, p) in enumerate(pool.imap_unordered(_do_one_p, items, chunksize=4), 1):
            p_keep[idx] = p
            if done % 500 == 0 or done == len(items):
                elapsed = time.time() - t0
                eta = elapsed * (len(items) - done) / done if done else 0.0
                log.info(
                    "  progress %d/%d  elapsed=%.0fs  eta=%.0fs", done, len(items), elapsed, eta
                )

    return pd.DataFrame(
        {
            "set_code": set_code,
            "event_type": event_type,
            "p_keep": p_keep,
            "was_kept": df["was_kept"].astype(bool).to_numpy(),
            "on_play": df["on_play"].astype(bool).to_numpy(),
        }
    )


def calibration_table(rows: pd.DataFrame, log: logging.Logger, label: str) -> None:
    """Print observed elite mull fraction per 5%-wide P(mull) bucket."""
    d = rows.copy()
    d["p_mull"] = 1.0 - d["p_keep"]
    edges = [i / 20 for i in range(21)]  # 0.00, 0.05, ..., 1.00
    d["bucket"] = pd.cut(d["p_mull"], edges, right=False, include_lowest=True)
    g = d.groupby("bucket", observed=False).agg(
        n=("was_kept", "size"),
        actual_mull_frac=("was_kept", lambda x: float((~x).mean()) if len(x) else float("nan")),
        mean_pred_mull=("p_mull", "mean"),
    )
    log.info("\n== Calibration (%s): P(mull) bucket vs observed elite mull fraction ==", label)
    log.info("%-14s %8s %14s %16s", "bucket", "n", "mean pred", "actual mulled")
    for bucket, row in g.iterrows():
        if row["n"] == 0:
            continue
        log.info(
            "%-14s %8d %13.1f%% %15.1f%%",
            f"[{bucket.left:.2f},{bucket.right:.2f})",
            int(row["n"]),
            100 * row["mean_pred_mull"],
            100 * row["actual_mull_frac"],
        )
    log.info(
        "TOTAL          %8d   overall pred %.2f%%  actual %.2f%%",
        len(d),
        100 * d["p_mull"].mean(),
        100 * (~d["was_kept"]).mean(),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    log = logging.getLogger("elite_calibration_dump")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--choice-model-dir", type=Path, default=REPO_ROOT / "models" / "choice_v9")
    ap.add_argument("--n-sims", type=int, default=300)
    ap.add_argument("--n-workers", type=int, default=10)
    args = ap.parse_args()

    frames = []
    for set_code in SETS:
        for event_type in EVENT_TYPES:
            frames.append(
                score_one(
                    event_type=event_type,
                    set_code=set_code,
                    choice_model_dir=args.choice_model_dir,
                    n_sims=args.n_sims,
                    n_workers=args.n_workers,
                    log=log,
                )
            )
    rows = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    rows.to_parquet(OUT_PARQUET, index=False)
    log.info("\nWrote %d rows to %s", len(rows), OUT_PARQUET)

    calibration_table(rows, log, "ALL sets, Premier+Trad")
    for ev in EVENT_TYPES:
        calibration_table(rows[rows["event_type"] == ev], log, f"ALL sets, {ev}")


if __name__ == "__main__":
    main()
