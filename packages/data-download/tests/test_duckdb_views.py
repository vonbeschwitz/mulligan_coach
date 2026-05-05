"""Tests for the unified DuckDB views over the Parquet files."""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import duckdb
import pytest
from mulligan_coach_data_download import paths
from mulligan_coach_data_download.seventeenlands.duckdb_views import build_views
from mulligan_coach_data_download.seventeenlands.game_data import (
    convert_csv_to_parquet,
)
from mulligan_coach_data_download.seventeenlands.ratings import (
    convert_ratings_json_to_parquet,
)


def _gz_csv(rows: list[dict[str, str]], columns: list[str]) -> bytes:
    out = io.StringIO()
    out.write(",".join(columns) + "\n")
    for r in rows:
        out.write(",".join(r.get(c, "") for c in columns) + "\n")
    return gzip.compress(out.getvalue().encode("utf-8"))


def _seed_games(tmp_path: Path, set_code: str, event_type: str, n: int) -> None:
    """Create a parquet at the canonical processed location for (set, event_type)."""
    csv = tmp_path / f"_seed.{set_code}.{event_type}.csv.gz"
    csv.write_bytes(
        _gz_csv(
            rows=[
                {"expansion": set_code, "event_type": event_type, "won": "True"} for _ in range(n)
            ],
            columns=["expansion", "event_type", "won"],
        )
    )
    parquet = paths.seventeenlands_games_parquet(set_code, event_type, root=tmp_path)
    convert_csv_to_parquet(csv, parquet, expected_set=set_code, expected_event_type=event_type)


def _seed_ratings(tmp_path: Path, set_code: str, event_type: str, n: int) -> None:
    import json

    src = tmp_path / f"_seed.{set_code}.{event_type}.ratings.json"
    src.write_text(
        json.dumps([{"name": f"Card {i}", "ever_drawn_win_rate": 0.5} for i in range(n)]),
        encoding="utf-8",
    )
    parquet = paths.seventeenlands_ratings_parquet(set_code, event_type, root=tmp_path)
    convert_ratings_json_to_parquet(
        src, parquet, expected_set=set_code, expected_event_type=event_type
    )


def test_build_views_unifies_across_sets_and_event_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_DATA_ROOT", str(tmp_path))
    paths.ensure_layout(tmp_path)

    # Two games datasets with different sets/event types.
    _seed_games(tmp_path, "FIN", "PremierDraft", n=3)
    _seed_games(tmp_path, "TDM", "Sealed", n=5)
    _seed_ratings(tmp_path, "FIN", "PremierDraft", n=2)

    counts = build_views(root=tmp_path)
    assert counts == {"games": 8, "ratings": 2}

    # Open the actual duckdb file and confirm the format-marking columns are
    # queryable through the view (the user's stated end-to-end check).
    db = paths.games_duckdb(root=tmp_path)
    assert db.exists()
    con = duckdb.connect(str(db), read_only=True)
    try:
        per_format = con.execute(
            "SELECT expansion, event_type, COUNT(*) FROM games GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        assert per_format == [
            ("FIN", "PremierDraft", 3),
            ("TDM", "Sealed", 5),
        ]

        # No null format markers — that's the invariant.
        nulls = con.execute(
            "SELECT COUNT(*) FROM games WHERE expansion IS NULL OR event_type IS NULL"
        ).fetchone()
        assert nulls is not None and nulls[0] == 0
    finally:
        con.close()


def test_build_views_with_no_data_drops_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_DATA_ROOT", str(tmp_path))
    paths.ensure_layout(tmp_path)

    counts = build_views(root=tmp_path)
    assert counts == {"games": 0, "ratings": 0}

    db = paths.games_duckdb(root=tmp_path)
    assert db.exists()
    con = duckdb.connect(str(db), read_only=True)
    try:
        # Querying a nonexistent view should error — confirms we dropped it
        # cleanly rather than leaving a broken one behind.
        with pytest.raises(duckdb.CatalogException):
            con.execute("SELECT * FROM games").fetchall()
    finally:
        con.close()
