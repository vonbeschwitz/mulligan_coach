"""17Lands card-ratings download + JSON → Parquet conversion.

The card-ratings endpoint returns a JSON array — one object per card in the
format, with aggregate stats (GIH WR, OH WR, drawn WR, ALSA, etc.). Unlike
the game CSVs, the ratings JSON does not include ``expansion`` or
``event_type`` per row, so we **inject** those columns ourselves before
writing Parquet. The format-marking invariant from ``game_data.py`` applies
here too: every output row carries the (set, event_type) it came from.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import duckdb
import httpx

from .. import paths
from ..config import SEVENTEENLANDS_RATINGS_URL
from ..http import download_to
from ..manifest import Manifest, SourceEntry
from .game_data import _sql_str

log = logging.getLogger(__name__)


def ratings_url(set_code: str, event_type: str) -> str:
    return SEVENTEENLANDS_RATINGS_URL.format(set_code=set_code, event_type=event_type)


def convert_ratings_json_to_parquet(
    json_path: Path,
    parquet_path: Path,
    *,
    expected_set: str,
    expected_event_type: str,
) -> int:
    """Read a 17Lands ratings JSON, inject ``expansion`` + ``event_type``
    columns, write Parquet atomically, return row count.

    The endpoint occasionally returns an empty array for very new sets — we
    accept that and write a zero-row parquet rather than failing, since the
    caller may legitimately want the file to exist.
    """
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_parquet = parquet_path.with_name(f".{parquet_path.name}.tmp-{os.getpid()}")

    raw: Any = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(
            f"{json_path}: expected a JSON array of card-rating objects, got {type(raw).__name__}"
        )

    # Inject the format-marking columns. Done in Python (not SQL) because
    # DuckDB's read_json_auto can't cleanly add per-source literal columns
    # alongside the original schema in one pass.
    enriched = [
        {
            **(row if isinstance(row, dict) else {}),
            "expansion": expected_set,
            "event_type": expected_event_type,
        }
        for row in raw
    ]

    # Write to a temporary "enriched" JSON, then have DuckDB convert it to
    # parquet — this gives us automatic schema inference + null handling for
    # the (variable) per-card fields.
    enriched_path = parquet_path.with_name(f".{parquet_path.name}.enriched.tmp-{os.getpid()}")
    enriched_path.write_text(json.dumps(enriched), encoding="utf-8")

    con = duckdb.connect(":memory:")
    try:
        json_literal = _sql_str(str(enriched_path))
        con.execute(
            f"CREATE TEMP VIEW ratings AS SELECT * FROM read_json({json_literal}, format='array')"
        )

        row = con.execute("SELECT COUNT(*) FROM ratings").fetchone()
        assert row is not None
        total = int(row[0])

        # Defensive checks (only meaningful when there's at least one row).
        if total > 0:
            cols = [r[0] for r in con.execute("DESCRIBE ratings").fetchall()]
            if "expansion" not in cols or "event_type" not in cols:
                raise AssertionError(
                    f"{json_path}: format-marking columns missing post-injection (cols: {cols!r})"
                )
            check = con.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE expansion <> ?)  AS bad_set,
                    COUNT(*) FILTER (WHERE event_type <> ?) AS bad_event
                FROM ratings
                """,
                [expected_set, expected_event_type],
            ).fetchone()
            assert check is not None
            bad_set, bad_event = check
            if bad_set or bad_event:
                raise AssertionError(
                    f"{json_path}: format-marking mismatch after injection "
                    f"(bad_set={bad_set}, bad_event={bad_event})"
                )

        tmp_literal = _sql_str(str(tmp_parquet))
        con.execute(
            f"COPY (SELECT * FROM ratings) TO {tmp_literal} (FORMAT PARQUET, COMPRESSION 'ZSTD')"
        )
    finally:
        con.close()
        if enriched_path.exists():
            enriched_path.unlink()

    tmp_parquet.replace(parquet_path)
    log.info(
        "Converted %s → %s (%d rows, %s / %s)",
        json_path.name,
        parquet_path.name,
        total,
        expected_set,
        expected_event_type,
    )
    return total


def refresh_ratings(
    set_code: str,
    event_type: str,
    *,
    client: httpx.Client,
    manifest: Manifest,
    root: Path | None = None,
    show_progress: bool = True,
) -> SourceEntry:
    """Download + convert one (set_code, event_type) ratings file."""
    url = ratings_url(set_code, event_type)
    json_path = paths.seventeenlands_ratings_json(set_code, event_type, root)
    parquet_path = paths.seventeenlands_ratings_parquet(set_code, event_type, root)

    previous = manifest.get(url)
    result = download_to(
        url,
        json_path,
        client=client,
        previous=previous,
        show_progress=show_progress,
    )

    needs_rebuild = (not result.not_modified) or (not parquet_path.exists())
    if needs_rebuild:
        row_count = convert_ratings_json_to_parquet(
            json_path,
            parquet_path,
            expected_set=set_code,
            expected_event_type=event_type,
        )
    else:
        row_count = previous.row_count if previous and previous.row_count else 0

    entry = result.entry.model_copy(update={"row_count": row_count})
    return manifest.upsert(entry)
