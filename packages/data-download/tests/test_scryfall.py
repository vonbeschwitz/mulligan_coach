"""Tests for the Scryfall bulk-data download."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mulligan_coach_data_download import paths, scryfall
from mulligan_coach_data_download.config import SCRYFALL_BULK_DATA_URL
from mulligan_coach_data_download.http import make_client
from mulligan_coach_data_download.manifest import Manifest
from pytest_httpx import HTTPXMock

ORACLE_DOWNLOAD_URL = "https://example.com/oracle_cards-2026-05-04.json"


def _bulk_data_index(updated_at: str = "2026-05-04T07:00:00.000+00:00") -> dict[str, object]:
    return {
        "object": "list",
        "has_more": False,
        "data": [
            {
                "type": "default_cards",
                "download_uri": "https://example.com/default_cards.json",
                "updated_at": updated_at,
            },
            {
                "type": "oracle_cards",
                "download_uri": ORACLE_DOWNLOAD_URL,
                "updated_at": updated_at,
            },
        ],
    }


def test_refresh_oracle_cards_end_to_end(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SCRYFALL_BULK_DATA_URL,
        json=_bulk_data_index(),
    )
    cards_payload = [{"name": "Lightning Bolt"}, {"name": "Counterspell"}]
    httpx_mock.add_response(
        url=ORACLE_DOWNLOAD_URL,
        content=json.dumps(cards_payload).encode("utf-8"),
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
    expected_path = paths.scryfall_raw_dir(tmp_path) / "oracle_cards.2026-05-04.json"
    assert expected_path.exists()


def test_refresh_oracle_cards_raises_when_type_missing(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=SCRYFALL_BULK_DATA_URL,
        json={"data": [{"type": "default_cards", "download_uri": "https://x"}]},
    )

    paths.ensure_layout(tmp_path)
    with make_client() as client, pytest.raises(RuntimeError, match="oracle_cards"):
        scryfall.refresh_oracle_cards(
            client=client,
            manifest=Manifest(),
            root=tmp_path,
            show_progress=False,
        )


def test_refresh_oracle_cards_rejects_non_array(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=SCRYFALL_BULK_DATA_URL,
        json=_bulk_data_index(),
    )
    httpx_mock.add_response(
        url=ORACLE_DOWNLOAD_URL,
        content=b'{"oops": "scryfall changed shape"}',
    )

    paths.ensure_layout(tmp_path)
    with make_client() as client, pytest.raises(RuntimeError, match="not an array"):
        scryfall.refresh_oracle_cards(
            client=client,
            manifest=Manifest(),
            root=tmp_path,
            show_progress=False,
        )
