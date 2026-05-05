"""Tests for the MTGJSON AllIdentifiers download."""

from __future__ import annotations

from pathlib import Path

from mulligan_coach_data_download import mtgjson, paths
from mulligan_coach_data_download.config import MTGJSON_ALL_IDENTIFIERS_URL
from mulligan_coach_data_download.http import make_client
from mulligan_coach_data_download.manifest import Manifest
from pytest_httpx import HTTPXMock


def test_refresh_all_identifiers(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = b'{"meta": {"version": "5.x"}, "data": {}}'
    httpx_mock.add_response(
        url=MTGJSON_ALL_IDENTIFIERS_URL,
        content=body,
        headers={"ETag": '"m1"'},
    )

    paths.ensure_layout(tmp_path)
    manifest = Manifest()
    with make_client() as client:
        entry = mtgjson.refresh_all_identifiers(
            client=client,
            manifest=manifest,
            root=tmp_path,
            show_progress=False,
        )

    dest = paths.mtgjson_raw_dir(tmp_path) / "AllIdentifiers.json"
    assert dest.exists()
    assert dest.read_bytes() == body
    assert entry.etag == '"m1"'
    assert manifest.get(MTGJSON_ALL_IDENTIFIERS_URL) is not None
