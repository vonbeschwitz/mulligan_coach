"""Drop feature-parquet rows whose deck contains a sim-affected card.

Given the affected-cards list from :mod:`find_affected_cards`, this
script:

1. Queries DuckDB for the ``(draft_id, match_number, game_number)`` of
   every row in the targeted set whose maindeck contains at least one
   affected card. (Opening-hand inclusion implies deck inclusion, so we
   don't need a separate OH check.)
2. Walks every ``chunk_*.parquet`` in the set's materialised feature
   directory, filters out the identified rows, and rewrites the
   chunk atomically. If every row in a chunk was invalidated, the
   chunk file is deleted.
3. Reports per-set counts of rows / chunks affected.

After this runs, ``materialize_feature_matrix(resume=True)`` refills
the gaps using the post-audit parsed-card data — only the rows that
*actually* needed re-simulation pay the simulator cost.

DFC handling: 17Lands stores the front-face name only, while
``ParsedCard.name`` uses the joint ``Front // Back`` form. We split
each affected card name on `` // `` and probe the front face.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_AFFECTED_JSON = REPO_ROOT / "scripts" / "resim" / "affected_cards.json"
DEFAULT_DUCKDB_PATH = REPO_ROOT / "data" / "processed" / "games.duckdb"
DEFAULT_MODEL_TRAINING_DIR = REPO_ROOT / "data" / "processed" / "model_training"

log = logging.getLogger("invalidate_affected_rows")


def _column_name_for_card(card_name: str) -> str:
    """Return the 17Lands ``deck_<name>`` column for a ParsedCard name.

    DFC names are stored as ``Front // Back``; 17Lands uses the front
    face only. Anything else passes through verbatim.
    """
    front = card_name.split(" // ", 1)[0]
    return f"deck_{front}"


def _filter_existing_columns(
    con: duckdb.DuckDBPyConnection, candidate_cols: list[str]
) -> tuple[list[str], list[str]]:
    """Split *candidate_cols* into (present, missing) by inspecting the
    ``games`` view's schema. Missing columns are silently ignored
    later — they correspond to cards that 17Lands never recorded in
    this dataset (e.g. very new cards waiting for the data lag, or
    rename mismatches we should know about)."""
    all_cols = {row[0] for row in con.execute("DESCRIBE games").fetchall()}
    present = [c for c in candidate_cols if c in all_cols]
    missing = [c for c in candidate_cols if c not in all_cols]
    return present, missing


def _build_invalidation_set(
    con: duckdb.DuckDBPyConnection,
    set_code: str,
    affected_card_names: list[str],
    *,
    event_type: str = "PremierDraft",
) -> set[tuple[str, int, int]]:
    """Return the set of ``(draft_id, match_number, game_number)``
    triples in ``set_code`` whose maindeck contains any of the
    affected cards.

    The query is one big OR over ``"deck_<name>" > 0`` checks. With
    ~30 cards per set DuckDB plans this cheaply (column scan with a
    bitmap union); no need to chunk the predicate list.
    """
    if not affected_card_names:
        return set()

    candidate_cols = [_column_name_for_card(n) for n in affected_card_names]
    present, missing = _filter_existing_columns(con, candidate_cols)
    if missing:
        log.warning(
            "%s: skipping %d card column(s) not present in DuckDB schema: %s",
            set_code,
            len(missing),
            ", ".join(missing[:5]) + (" …" if len(missing) > 5 else ""),
        )
    if not present:
        return set()

    # Identifier-quote column names with embedded special chars by
    # doubling double-quotes (DuckDB SQL spec). Card names contain
    # apostrophes and commas; double quotes inside a name would
    # break the parser, but no card name has those.
    quoted = [f'"{c.replace(chr(34), chr(34) * 2)}"' for c in present]
    deck_predicate = " OR ".join(f"{q} > 0" for q in quoted)

    sql = f"""
        SELECT draft_id, match_number, game_number
        FROM games
        WHERE expansion = ? AND event_type = ?
          AND ({deck_predicate})
    """
    log.info(
        "%s: querying invalidation set across %d affected columns…",
        set_code,
        len(present),
    )
    rows = con.execute(sql, [set_code, event_type]).fetchall()
    return {(str(d), int(m), int(g)) for d, m, g in rows}


def _rewrite_chunks(
    chunk_dir: Path,
    invalidate: set[tuple[str, int, int]],
    *,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Filter out invalidated rows from every chunk in *chunk_dir*.

    Returns ``(chunks_touched, rows_invalidated, chunks_deleted)``.

    Atomic rewrite: each chunk is staged as a ``.tmp-<pid>`` neighbour
    and moved into place via :func:`os.replace`. A chunk that ends
    up empty is deleted instead of leaving a zero-row parquet.
    """
    chunks_touched = 0
    rows_invalidated = 0
    chunks_deleted = 0

    for chunk_path in sorted(chunk_dir.glob("chunk_*.parquet")):
        table = pq.read_table(chunk_path)  # type: ignore[no-untyped-call]
        df = table.to_pandas()
        if df.empty:
            continue

        key_tuples = [
            (str(d), int(m), int(g))
            for d, m, g in zip(df["draft_id"], df["match_number"], df["game_number"], strict=True)
        ]
        mask = [t not in invalidate for t in key_tuples]
        n_kept = sum(mask)
        n_dropped = len(mask) - n_kept
        if n_dropped == 0:
            continue

        chunks_touched += 1
        rows_invalidated += n_dropped
        if dry_run:
            continue

        if n_kept == 0:
            chunk_path.unlink()
            chunks_deleted += 1
            continue

        kept_df = df.loc[mask].reset_index(drop=True)
        new_table = pa.Table.from_pandas(kept_df, preserve_index=False)
        tmp = chunk_path.with_name(f".{chunk_path.name}.tmp-{os.getpid()}")
        pq.write_table(new_table, tmp, compression="zstd")  # type: ignore[no-untyped-call]
        tmp.replace(chunk_path)

    return chunks_touched, rows_invalidated, chunks_deleted


def invalidate_for_set(
    *,
    set_code: str,
    affected_card_names: list[str],
    duckdb_path: Path,
    model_training_dir: Path,
    event_type: str,
    dry_run: bool,
) -> dict[str, int]:
    """Run the invalidation pipeline for one ``(set, event_type)``.

    Returns a stats dict with the per-set counters.
    """
    chunk_dir = model_training_dir / set_code / event_type
    if not chunk_dir.exists():
        log.info("%s: no chunk directory at %s — skipping", set_code, chunk_dir)
        return {
            "affected_cards": len(affected_card_names),
            "rows_to_invalidate": 0,
            "chunks_touched": 0,
            "rows_invalidated": 0,
            "chunks_deleted": 0,
        }

    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        invalidate = _build_invalidation_set(
            con, set_code, affected_card_names, event_type=event_type
        )
    finally:
        con.close()

    log.info(
        "%s: %d affected card(s) -> %d row(s) in invalidation set",
        set_code,
        len(affected_card_names),
        len(invalidate),
    )

    chunks_touched, rows_invalidated, chunks_deleted = _rewrite_chunks(
        chunk_dir, invalidate, dry_run=dry_run
    )
    log.info(
        "%s: rewrote %d chunk(s), dropped %d row(s), deleted %d empty chunk(s)%s",
        set_code,
        chunks_touched,
        rows_invalidated,
        chunks_deleted,
        " [DRY RUN]" if dry_run else "",
    )
    return {
        "affected_cards": len(affected_card_names),
        "rows_to_invalidate": len(invalidate),
        "chunks_touched": chunks_touched,
        "rows_invalidated": rows_invalidated,
        "chunks_deleted": chunks_deleted,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--affected-json",
        type=Path,
        default=DEFAULT_AFFECTED_JSON,
        help="Path to affected_cards.json (output of find_affected_cards.py).",
    )
    ap.add_argument(
        "--duckdb-path",
        type=Path,
        default=DEFAULT_DUCKDB_PATH,
    )
    ap.add_argument(
        "--model-training-dir",
        type=Path,
        default=DEFAULT_MODEL_TRAINING_DIR,
    )
    ap.add_argument("--event-type", default="PremierDraft")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute counts but do not modify any parquet files.",
    )
    args = ap.parse_args()

    data = json.loads(args.affected_json.read_text(encoding="utf-8"))
    per_set: dict[str, list[str]] = data["affected"]

    summary: dict[str, dict[str, int]] = {}
    for set_code, names in per_set.items():
        summary[set_code] = invalidate_for_set(
            set_code=set_code,
            affected_card_names=names,
            duckdb_path=args.duckdb_path,
            model_training_dir=args.model_training_dir,
            event_type=args.event_type,
            dry_run=args.dry_run,
        )

    total_rows = sum(s["rows_invalidated"] for s in summary.values())
    total_chunks = sum(s["chunks_touched"] for s in summary.values())
    log.info("---")
    log.info(
        "TOTAL: %d row(s) invalidated across %d chunk(s)%s",
        total_rows,
        total_chunks,
        " [DRY RUN]" if args.dry_run else "",
    )


if __name__ == "__main__":
    main()
