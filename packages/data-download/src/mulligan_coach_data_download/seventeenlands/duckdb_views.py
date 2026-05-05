"""Build the unified DuckDB views over the per-(set, event_type) Parquet files.

We deliberately keep the underlying data in Parquet files on disk and only
use a DuckDB database file to hold **view definitions**. This means:

* Refreshing data is just dropping in a new Parquet — the views adapt
  automatically on next query.
* The DuckDB file is tiny (just SQL text) and easy to re-create.
* Downstream code (the model package, the website) can query
  ``SELECT * FROM games WHERE expansion = 'FIN' AND event_type = 'PremierDraft'``
  without caring how many sets are loaded.

The 17Lands CSVs have a fixed set of "common" columns (expansion, event_type,
won, on_play, num_mulligans, …) plus a *variable* column per card name.
DuckDB's ``read_parquet(..., union_by_name=true)`` handles that schema
variation by null-filling missing columns across the union.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb

from .. import paths

log = logging.getLogger(__name__)

GAMES_VIEW = "games"
RATINGS_VIEW = "ratings"


def _glob_for(subdir: str, root: Path | None) -> str:
    """Glob pattern (forward slashes — DuckDB on Windows handles them) for
    every ``<set>/<event_type>.parquet`` file under one of our processed
    sub-trees."""
    return (paths.seventeenlands_processed_dir(root) / subdir / "*" / "*.parquet").as_posix()


def _has_any_files(glob: str) -> bool:
    # DuckDB raises if a glob matches nothing; do the existence check ourselves.
    parent = Path(glob).parent.parent  # strip "<set>/<event_type>.parquet"
    if not parent.exists():
        return False
    return any(parent.rglob("*.parquet"))


def build_views(root: Path | None = None) -> dict[str, int]:
    """Create or replace the ``games`` / ``ratings`` views in
    ``data/processed/games.duckdb``.

    Returns a small dict reporting the row count visible through each view —
    useful for the ``status`` CLI command and for tests.
    """
    db_path = paths.games_duckdb(root)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    games_glob = _glob_for("games", root)
    ratings_glob = _glob_for("ratings", root)

    counts: dict[str, int] = {}
    con = duckdb.connect(str(db_path))
    try:
        for view, glob in ((GAMES_VIEW, games_glob), (RATINGS_VIEW, ratings_glob)):
            if not _has_any_files(glob):
                # Drop any stale view so a downstream SELECT gets a clear
                # "table not found" instead of "I/O error: no files matched".
                con.execute(f"DROP VIEW IF EXISTS {view}")
                counts[view] = 0
                log.info("No parquet files matched %s — view %s dropped", glob, view)
                continue

            con.execute(
                f"CREATE OR REPLACE VIEW {view} AS "
                f"SELECT * FROM read_parquet('{glob}', union_by_name=true)"
            )
            row = con.execute(f"SELECT COUNT(*) FROM {view}").fetchone()
            counts[view] = int(row[0]) if row else 0
            log.info("View %s: %d rows", view, counts[view])
    finally:
        con.close()

    return counts
