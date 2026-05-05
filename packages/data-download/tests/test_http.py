"""Tests for the streamed-download helper.

All HTTP traffic is mocked via ``pytest-httpx`` — the live network is never
touched. ``httpx_mock`` is the fixture provided by that plugin.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
from mulligan_coach_data_download.http import download_to, make_client
from mulligan_coach_data_download.manifest import SourceEntry
from pytest_httpx import HTTPXMock

URL = "https://example.com/data.csv.gz"


def test_download_writes_file_and_records_metadata(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    body = b"hello, world\n" * 1000
    httpx_mock.add_response(
        url=URL,
        content=body,
        headers={
            "ETag": '"abc123"',
            "Last-Modified": "Wed, 21 Oct 2025 07:28:00 GMT",
            "Content-Length": str(len(body)),
        },
    )

    dest = tmp_path / "data.csv.gz"
    with make_client() as client:
        result = download_to(URL, dest, client=client, show_progress=False)

    assert not result.not_modified
    assert dest.read_bytes() == body
    assert result.entry.etag == '"abc123"'
    assert result.entry.last_modified == "Wed, 21 Oct 2025 07:28:00 GMT"
    assert result.entry.size == len(body)
    assert result.entry.sha256 == hashlib.sha256(body).hexdigest()


def test_download_handles_304_not_modified(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    dest = tmp_path / "data.csv.gz"
    dest.write_bytes(b"old content")
    previous = SourceEntry(
        url=URL,
        local_path=str(dest),
        etag='"abc"',
        last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
    )

    httpx_mock.add_response(url=URL, status_code=304)

    with make_client() as client:
        result = download_to(
            URL,
            dest,
            client=client,
            previous=previous,
            show_progress=False,
        )

    assert result.not_modified
    assert result.entry is previous
    assert dest.read_bytes() == b"old content"  # untouched


def test_download_sends_conditional_headers_when_file_exists(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    dest = tmp_path / "data.csv.gz"
    dest.write_bytes(b"old content")
    previous = SourceEntry(
        url=URL,
        local_path=str(dest),
        etag='"abc"',
        last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
    )

    # Match only when the conditional headers are present.
    httpx_mock.add_response(
        url=URL,
        match_headers={
            "If-None-Match": '"abc"',
            "If-Modified-Since": "Wed, 21 Oct 2025 07:28:00 GMT",
        },
        status_code=304,
    )

    with make_client() as client:
        result = download_to(
            URL,
            dest,
            client=client,
            previous=previous,
            show_progress=False,
        )

    assert result.not_modified


def test_download_skips_conditional_headers_when_local_file_missing(
    tmp_path: Path, httpx_mock: HTTPXMock
) -> None:
    """If we have manifest metadata but the file on disk is gone, we must
    re-download regardless — sending ETag would risk a 304 that misleads the
    caller into thinking the (now nonexistent) file is current."""
    dest = tmp_path / "data.csv.gz"
    previous = SourceEntry(
        url=URL,
        local_path=str(dest),
        etag='"abc"',
        last_modified="Wed, 21 Oct 2025 07:28:00 GMT",
    )

    body = b"fresh content"
    httpx_mock.add_response(url=URL, content=body)

    with make_client() as client:
        result = download_to(
            URL,
            dest,
            client=client,
            previous=previous,
            show_progress=False,
        )

    assert not result.not_modified
    assert dest.read_bytes() == body
    sent_request = httpx_mock.get_request(url=URL)
    assert sent_request is not None
    assert "If-None-Match" not in sent_request.headers
    assert "If-Modified-Since" not in sent_request.headers


def test_download_raises_on_error_status(tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=URL, status_code=500)
    dest = tmp_path / "data.csv.gz"

    with make_client() as client, pytest.raises(httpx.HTTPStatusError):
        download_to(URL, dest, client=client, show_progress=False)

    # No file should be left behind on failure.
    assert not dest.exists()
    assert not list(tmp_path.glob(".*tmp*"))
