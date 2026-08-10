"""Tests for the Scryfall bulk-data download.

Scryfall serves ``oracle_cards`` as a **gzipped JSONL** file; this package
expands it into the JSON array that the cards package reads. The tests cover
both halves: URL discovery off the bulk-data index, and the gz→array
conversion.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from mulligan_coach_data_download import paths, scryfall
from mulligan_coach_data_download.config import SCRYFALL_BULK_DATA_URL
from mulligan_coach_data_download.http import make_client
from mulligan_coach_data_download.manifest import Manifest
from pytest_httpx import HTTPXMock

ORACLE_DOWNLOAD_URL = "https://example.com/oracle-cards-20260504070000.jsonl.gz"


def _bulk_data_index(updated_at: str = "2026-05-04T07:00:00.000+00:00") -> dict[str, object]:
    return {
        "object": "list",
        "has_more": False,
        "data": [
            {
                "type": "default_cards",
                "jsonl_download_uri": "https://example.com/default_cards.jsonl.gz",
                "updated_at": updated_at,
            },
            {
                "type": "oracle_cards",
                "jsonl_download_uri": ORACLE_DOWNLOAD_URL,
                "updated_at": updated_at,
            },
        ],
    }


def _jsonl_gz(cards: list[dict[str, object]]) -> bytes:
    body = "\n".join(json.dumps(c) for c in cards) + "\n"
    return gzip.compress(body.encode("utf-8"))


def test_refresh_oracle_cards_end_to_end(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=SCRYFALL_BULK_DATA_URL, json=_bulk_data_index())
    cards_payload: list[dict[str, object]] = [
        {"name": "Lightning Bolt"},
        {"name": "Counterspell"},
    ]
    httpx_mock.add_response(
        url=ORACLE_DOWNLOAD_URL,
        content=_jsonl_gz(cards_payload),
        headers={"ETag": '"o1"'},
    )

    paths.ensure_layout(tmp_path)
    manifest = Manifest()
    with make_client() as client:
        entry = scryfall.refresh_oracle_cards(
            client=client,
            manifest=manifest,
            root=tmp_path,
            show_progress=False,
        )

    assert entry.row_count == 2
    assert entry.url == ORACLE_DOWNLOAD_URL

    raw_dir = paths.scryfall_raw_dir(tmp_path)
    # The compressed artifact is kept (it's what conditional GET matches on)...
    assert (raw_dir / "oracle_cards.2026-05-04.jsonl.gz").exists()
    # ...and the canonical JSON array the cards package reads is written too.
    expanded = raw_dir / "oracle_cards.2026-05-04.json"
    assert expanded.exists()
    assert json.loads(expanded.read_text(encoding="utf-8")) == cards_payload


def test_refresh_oracle_cards_raises_when_type_missing(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=SCRYFALL_BULK_DATA_URL,
        json={"data": [{"type": "default_cards", "jsonl_download_uri": "https://x"}]},
    )

    paths.ensure_layout(tmp_path)
    with make_client() as client, pytest.raises(RuntimeError, match="oracle_cards"):
        scryfall.refresh_oracle_cards(
            client=client,
            manifest=Manifest(),
            root=tmp_path,
            show_progress=False,
        )


def test_refresh_oracle_cards_raises_when_jsonl_field_missing(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """A future Scryfall rename should fail loudly, not download nothing."""
    httpx_mock.add_response(
        url=SCRYFALL_BULK_DATA_URL,
        json={
            "data": [
                {
                    "type": "oracle_cards",
                    "some_new_field": "https://x",
                    "updated_at": "2026-05-04T07:00:00.000+00:00",
                }
            ]
        },
    )

    paths.ensure_layout(tmp_path)
    with make_client() as client, pytest.raises(RuntimeError, match="jsonl_download_uri"):
        scryfall.refresh_oracle_cards(
            client=client,
            manifest=Manifest(),
            root=tmp_path,
            show_progress=False,
        )


def test_refresh_oracle_cards_rejects_non_object_lines(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=SCRYFALL_BULK_DATA_URL, json=_bulk_data_index())
    httpx_mock.add_response(
        url=ORACLE_DOWNLOAD_URL,
        content=gzip.compress(b'["oops", "scryfall changed shape"]\n'),
    )

    paths.ensure_layout(tmp_path)
    with make_client() as client, pytest.raises(RuntimeError, match="expected a JSON object"):
        scryfall.refresh_oracle_cards(
            client=client,
            manifest=Manifest(),
            root=tmp_path,
            show_progress=False,
        )


def test_convert_jsonl_gz_rejects_malformed_line(tmp_path: Path) -> None:
    gz_path = tmp_path / "oracle.jsonl.gz"
    gz_path.write_bytes(gzip.compress(b'{"name": "ok"}\nnot json at all\n'))

    with pytest.raises(RuntimeError, match="line 2 is not valid JSON"):
        scryfall.convert_jsonl_gz_to_json_array(gz_path, tmp_path / "out.json")

    # Failed conversions must not leave a partial file at the canonical path.
    assert not (tmp_path / "out.json").exists()
