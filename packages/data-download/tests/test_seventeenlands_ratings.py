"""Tests for 17Lands card-ratings download + JSON → Parquet conversion."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
from mulligan_coach_data_download import paths
from mulligan_coach_data_download.config import SEVENTEENLANDS_RATINGS_URL
from mulligan_coach_data_download.http import make_client
from mulligan_coach_data_download.manifest import Manifest
from mulligan_coach_data_download.seventeenlands import ratings as r17
from pytest_httpx import HTTPXMock


def _sample_ratings() -> list[dict[str, object]]:
    return [
        {
            "name": "Lightning Bolt",
            "color": "R",
            "rarity": "common",
            "ever_drawn_win_rate": 0.55,
            "opening_hand_win_rate": 0.52,
        },
        {
            "name": "Counterspell",
            "color": "U",
            "rarity": "common",
            "ever_drawn_win_rate": 0.50,
            "opening_hand_win_rate": 0.49,
        },
    ]


def test_convert_injects_format_marking(tmp_path: Path) -> None:
    src = tmp_path / "ratings.json"
    src.write_text(json.dumps(_sample_ratings()), encoding="utf-8")
    out = tmp_path / "ratings.parquet"

    n = r17.convert_ratings_json_to_parquet(
        src, out, expected_set="FIN", expected_event_type="PremierDraft"
    )
    assert n == 2
    assert out.exists()

    con = duckdb.connect(":memory:")
    try:
        rows = con.execute(
            f"SELECT name, expansion, event_type FROM read_parquet('{out.as_posix()}') ORDER BY name"
        ).fetchall()
        assert rows == [
            ("Counterspell", "FIN", "PremierDraft"),
            ("Lightning Bolt", "FIN", "PremierDraft"),
        ]
    finally:
        con.close()


def test_convert_handles_empty_array(tmp_path: Path) -> None:
    src = tmp_path / "ratings.json"
    src.write_text("[]", encoding="utf-8")
    out = tmp_path / "ratings.parquet"

    n = r17.convert_ratings_json_to_parquet(
        src, out, expected_set="FIN", expected_event_type="PremierDraft"
    )
    assert n == 0
    assert out.exists()


def test_convert_rejects_non_array(tmp_path: Path) -> None:
    src = tmp_path / "ratings.json"
    src.write_text('{"oops": "not an array"}', encoding="utf-8")
    with pytest.raises(ValueError, match="expected a JSON array"):
        r17.convert_ratings_json_to_parquet(
            src,
            tmp_path / "ratings.parquet",
            expected_set="FIN",
            expected_event_type="PremierDraft",
        )


def test_refresh_ratings_end_to_end(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = json.dumps(_sample_ratings()).encode("utf-8")
    url = SEVENTEENLANDS_RATINGS_URL.format(set_code="FIN", event_type="PremierDraft")
    httpx_mock.add_response(url=url, content=body, headers={"ETag": '"r1"'})

    paths.ensure_layout(tmp_path)
    manifest = Manifest()
    with make_client() as client:
        entry = r17.refresh_ratings(
            "FIN",
            "PremierDraft",
            client=client,
            manifest=manifest,
            root=tmp_path,
            show_progress=False,
        )

    assert entry.row_count == 2
    assert entry.etag == '"r1"'
    parquet = paths.seventeenlands_ratings_parquet("FIN", "PremierDraft", root=tmp_path)
    assert parquet.exists()
    json_path = paths.seventeenlands_ratings_json("FIN", "PremierDraft", root=tmp_path)
    assert json_path.exists()
