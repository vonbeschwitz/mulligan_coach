"""17Lands game-data download + CSV → Parquet conversion.

This is the most safety-critical module in the package: a bug here silently
corrupts the model's training signal. See ``CLAUDE.md`` (this package) for
the format-marking invariant.

Why DuckDB instead of pandas? The 17Lands CSVs can be many GB. DuckDB reads
gz CSVs directly, runs validation SQL on them without copying to memory, and
writes Parquet — all in one process. Pandas would need to be chunked
manually and we'd reinvent the same machinery DuckDB already has.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import duckdb
import httpx

from .. import paths
from ..config import SEVENTEENLANDS_GAME_URL
from ..http import download_to
from ..manifest import Manifest, SourceEntry

log = logging.getLogger(__name__)


def game_data_url(set_code: str, event_type: str) -> str:
    return SEVENTEENLANDS_GAME_URL.format(set_code=set_code, event_type=event_type)


# ---------------------------------------------------------------------------
# CSV → Parquet conversion (pure function, decoupled from HTTP for testing).
# ---------------------------------------------------------------------------


def _sql_str(s: str) -> str:
    """Escape a Python string for safe interpolation as a SQL string literal.

    DuckDB's ``COPY ... TO 'path'`` form requires a literal — there is no
    parameter binding for this clause. We use forward-slash paths because
    DuckDB on Windows tolerates them and they avoid backslash-escape headaches.
    """
    return "'" + s.replace("\\", "/").replace("'", "''") + "'"


def convert_csv_to_parquet(
    csv_path: Path,
    parquet_path: Path,
    *,
    expected_set: str,
    expected_event_type: str,
) -> int:
    """Read a 17Lands gz CSV, validate the format-marking columns, write a
    Parquet file atomically, and return the row count.

    Raises ``ValueError`` if:

    * ``expansion`` or ``event_type`` columns are missing.
    * Any row has NULL ``expansion`` or ``event_type``.
    * Any row's ``expansion`` does not match ``expected_set``.
    * Any row's ``event_type`` does not match ``expected_event_type``.
    """
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    # tmp file in the same directory so os.replace is a true atomic rename.
    tmp_parquet = parquet_path.with_name(f".{parquet_path.name}.tmp-{os.getpid()}")

    csv_literal = _sql_str(str(csv_path))

    con = duckdb.connect(":memory:")
    try:
        # We register the CSV as a temporary view and run all validation SQL
        # against the view — DuckDB streams the gz CSV through pipelines
        # without materializing it into RAM.
        con.execute(f"CREATE TEMP VIEW games_src AS SELECT * FROM read_csv_auto({csv_literal})")

        cols = [r[0] for r in con.execute("DESCRIBE games_src").fetchall()]
        if "expansion" not in cols:
            raise ValueError(f"{csv_path}: missing 'expansion' column (got {cols!r})")
        if "event_type" not in cols:
            raise ValueError(f"{csv_path}: missing 'event_type' column (got {cols!r})")

        # One pass over the data computes everything we need to validate.
        row = con.execute(
            """
            SELECT
                COUNT(*)                                               AS total,
                COUNT(*) FILTER (WHERE expansion IS NULL)              AS null_set,
                COUNT(*) FILTER (WHERE event_type IS NULL)             AS null_event,
                COUNT(*) FILTER (WHERE expansion <> ?)                 AS mismatched_set,
                COUNT(*) FILTER (WHERE event_type <> ?)                AS mismatched_event
            FROM games_src
            """,
            [expected_set, expected_event_type],
        ).fetchone()
        assert row is not None  # COUNT() always returns a row
        total, null_set, null_event, mismatched_set, mismatched_event = row

        if total == 0:
            raise ValueError(f"{csv_path}: contains zero rows")
        if null_set or null_event:
            raise ValueError(
                f"{csv_path}: {null_set} null expansion / {null_event} null event_type rows"
            )
        if mismatched_set:
            raise ValueError(
                f"{csv_path}: {mismatched_set} rows have expansion != {expected_set!r}"
            )
        if mismatched_event:
            raise ValueError(
                f"{csv_path}: {mismatched_event} rows have event_type != {expected_event_type!r}"
            )

        # Validation passed — write Parquet (zstd is a good size/speed default).
        tmp_literal = _sql_str(str(tmp_parquet))
        con.execute(
            f"COPY (SELECT * FROM games_src) TO {tmp_literal} (FORMAT PARQUET, COMPRESSION 'ZSTD')"
        )
    finally:
        con.close()

    tmp_parquet.replace(parquet_path)
    log.info(
        "Converted %s → %s (%d rows, %s / %s)",
        csv_path.name,
        parquet_path.name,
        total,
        expected_set,
        expected_event_type,
    )
    return int(total)


# ---------------------------------------------------------------------------
# Top-level orchestrator: download + convert + manifest update.
# ---------------------------------------------------------------------------


def refresh_game_data(
    set_code: str,
    event_type: str,
    *,
    client: httpx.Client,
    manifest: Manifest,
    root: Path | None = None,
    show_progress: bool = True,
) -> SourceEntry:
    """Refresh one (set_code, event_type) dataset end-to-end.

    Steps:
        1. Look up any prior download in ``manifest`` for this URL.
        2. Conditional GET — server returns 304 if unchanged.
        3. If the gz CSV changed (or the parquet is missing), re-build the
           parquet from the gz CSV with full validation.
        4. Update the manifest entry with size / etag / row_count.

    Returns the manifest entry it just upserted.
    """
    url = game_data_url(set_code, event_type)
    csv_path = paths.seventeenlands_game_csv(set_code, event_type, root)
    parquet_path = paths.seventeenlands_games_parquet(set_code, event_type, root)

    previous = manifest.get(url)
    result = download_to(
        url,
        csv_path,
        client=client,
        previous=previous,
        show_progress=show_progress,
    )

    needs_rebuild = (not result.not_modified) or (not parquet_path.exists())
    if needs_rebuild:
        row_count = convert_csv_to_parquet(
            csv_path,
            parquet_path,
            expected_set=set_code,
            expected_event_type=event_type,
        )
    else:
        # Trust the previously-recorded row count if available.
        row_count = previous.row_count if previous and previous.row_count else 0

    entry = result.entry.model_copy(update={"row_count": row_count})
    return manifest.upsert(entry)
