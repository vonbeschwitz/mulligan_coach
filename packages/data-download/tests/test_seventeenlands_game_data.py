"""Tests for 17Lands game-data download + CSV → Parquet conversion.

Heavy use of small synthetic gz CSVs — we never download real data here.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import duckdb
import pytest
from mulligan_coach_data_download import paths
from mulligan_coach_data_download.config import SEVENTEENLANDS_GAME_URL
from mulligan_coach_data_download.http import make_client
from mulligan_coach_data_download.manifest import Manifest
from mulligan_coach_data_download.seventeenlands import game_data as gd
from pytest_httpx import HTTPXMock


def _make_csv(rows: list[dict[str, str]], columns: list[str]) -> bytes:
    """Build a minimal gz-compressed CSV from a list of row dicts."""
    out = io.StringIO()
    out.write(",".join(columns) + "\n")
    for r in rows:
        out.write(",".join(str(r.get(c, "")) for c in columns) + "\n")
    return gzip.compress(out.getvalue().encode("utf-8"))


# ---------------------------------------------------------------------------
# convert_csv_to_parquet
# ---------------------------------------------------------------------------


def test_convert_happy_path(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[
                {"expansion": "FIN", "event_type": "PremierDraft", "won": "True"},
                {"expansion": "FIN", "event_type": "PremierDraft", "won": "False"},
                {"expansion": "FIN", "event_type": "PremierDraft", "won": "True"},
            ],
            columns=["expansion", "event_type", "won"],
        )
    )
    parquet = tmp_path / "out.parquet"

    n = gd.convert_csv_to_parquet(
        csv,
        parquet,
        expected_set="FIN",
        expected_event_type="PremierDraft",
    )
    assert n == 3
    assert parquet.exists()

    # Read back and verify the format-marking columns survived.
    con = duckdb.connect(":memory:")
    try:
        cols = [
            r[0]
            for r in con.execute(
                f"SELECT * FROM read_parquet('{parquet.as_posix()}') LIMIT 0"
            ).description
        ]
        assert "expansion" in cols
        assert "event_type" in cols
        groups = con.execute(
            f"SELECT expansion, event_type, COUNT(*) "
            f"FROM read_parquet('{parquet.as_posix()}') GROUP BY 1, 2"
        ).fetchall()
        assert groups == [("FIN", "PremierDraft", 3)]
    finally:
        con.close()


def test_convert_rejects_missing_expansion_column(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[{"event_type": "PremierDraft", "won": "True"}],
            columns=["event_type", "won"],
        )
    )
    with pytest.raises(ValueError, match="missing 'expansion'"):
        gd.convert_csv_to_parquet(
            csv,
            tmp_path / "out.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_convert_rejects_missing_event_type_column(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[{"expansion": "FIN", "won": "True"}],
            columns=["expansion", "won"],
        )
    )
    with pytest.raises(ValueError, match="missing 'event_type'"):
        gd.convert_csv_to_parquet(
            csv,
            tmp_path / "out.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_convert_rejects_mismatched_set(tmp_path: Path) -> None:
    """Catches the case where someone (or 17Lands) puts the wrong file at
    the wrong filename. This is the format-marking guarantee in action."""
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[
                {"expansion": "TDM", "event_type": "PremierDraft", "won": "True"},
            ],
            columns=["expansion", "event_type", "won"],
        )
    )
    with pytest.raises(ValueError, match="expansion != 'FIN'"):
        gd.convert_csv_to_parquet(
            csv,
            tmp_path / "out.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_convert_rejects_mismatched_event_type(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[
                {"expansion": "FIN", "event_type": "TradDraft", "won": "True"},
            ],
            columns=["expansion", "event_type", "won"],
        )
    )
    with pytest.raises(ValueError, match="event_type != 'PremierDraft'"):
        gd.convert_csv_to_parquet(
            csv,
            tmp_path / "out.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_convert_rejects_zero_rows(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(_make_csv(rows=[], columns=["expansion", "event_type", "won"]))
    with pytest.raises(ValueError, match="zero rows"):
        gd.convert_csv_to_parquet(
            csv,
            tmp_path / "out.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_convert_writes_atomically(tmp_path: Path) -> None:
    csv = tmp_path / "in.csv.gz"
    csv.write_bytes(
        _make_csv(
            rows=[{"expansion": "FIN", "event_type": "PremierDraft", "won": "True"}],
            columns=["expansion", "event_type", "won"],
        )
    )
    out = tmp_path / "out.parquet"
    gd.convert_csv_to_parquet(csv, out, expected_set="FIN", expected_event_type="PremierDraft")
    assert out.exists()
    leftovers = list(tmp_path.glob(".*tmp*"))
    assert leftovers == [], f"unexpected temp files: {leftovers}"


# ---------------------------------------------------------------------------
# refresh_game_data (download + convert + manifest)
# ---------------------------------------------------------------------------


def test_refresh_game_data_end_to_end(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = _make_csv(
        rows=[
            {"expansion": "FIN", "event_type": "PremierDraft", "won": "True"},
            {"expansion": "FIN", "event_type": "PremierDraft", "won": "False"},
        ],
        columns=["expansion", "event_type", "won"],
    )
    url = SEVENTEENLANDS_GAME_URL.format(set_code="FIN", event_type="PremierDraft")
    httpx_mock.add_response(
        url=url, content=body, headers={"ETag": '"v1"', "Content-Length": str(len(body))}
    )

    manifest = Manifest()
    paths.ensure_layout(tmp_path)
    with make_client() as client:
        entry = gd.refresh_game_data(
            "FIN",
            "PremierDraft",
            client=client,
            manifest=manifest,
            root=tmp_path,
            show_progress=False,
        )

    assert entry.row_count == 2
    assert entry.etag == '"v1"'
    csv = paths.seventeenlands_game_csv("FIN", "PremierDraft", root=tmp_path)
    parquet = paths.seventeenlands_games_parquet("FIN", "PremierDraft", root=tmp_path)
    assert csv.exists()
    assert parquet.exists()
    assert manifest.get(url) is entry


def test_refresh_game_data_skips_when_304(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    """Second run with a 304 from the server must reuse the existing parquet
    and not raise."""
    paths.ensure_layout(tmp_path)
    url = SEVENTEENLANDS_GAME_URL.format(set_code="FIN", event_type="PremierDraft")

    # Pre-populate manifest as if a previous run had completed successfully.
    manifest = Manifest()
    csv = paths.seventeenlands_game_csv("FIN", "PremierDraft", root=tmp_path)
    parquet = paths.seventeenlands_games_parquet("FIN", "PremierDraft", root=tmp_path)
    csv.write_bytes(b"placeholder")
    parquet.parent.mkdir(parents=True, exist_ok=True)
    # Build a real (single-row) parquet so it exists on disk.
    body = _make_csv(
        rows=[{"expansion": "FIN", "event_type": "PremierDraft", "won": "True"}],
        columns=["expansion", "event_type", "won"],
    )
    real_csv = tmp_path / "_seed.csv.gz"
    real_csv.write_bytes(body)
    gd.convert_csv_to_parquet(
        real_csv, parquet, expected_set="FIN", expected_event_type="PremierDraft"
    )

    from mulligan_coach_data_download.manifest import SourceEntry

    manifest.upsert(
        SourceEntry(
            url=url,
            local_path=str(csv),
            etag='"v1"',
            row_count=1,
        )
    )

    httpx_mock.add_response(url=url, status_code=304)

    with make_client() as client:
        entry = gd.refresh_game_data(
            "FIN",
            "PremierDraft",
            client=client,
            manifest=manifest,
            root=tmp_path,
            show_progress=False,
        )

    # 304 path: row_count carries over from the previous entry.
    assert entry.row_count == 1
    assert entry.etag == '"v1"'
