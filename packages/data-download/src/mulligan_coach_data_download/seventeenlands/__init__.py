"""17Lands data downloads.

Two distinct pieces of data:

* :mod:`.game_data` — large gz-compressed CSVs (one row per game).
* :mod:`.ratings`   — small JSON files of per-card aggregate stats.

Both are normalized into Parquet files under ``data/processed/`` so that the
:mod:`.duckdb_views` module can expose unified ``games`` / ``ratings``
SQL views to the rest of the project.
"""

from __future__ import annotations
