"""Tests for :mod:`mulligan_coach_overlay.auto_update.downloader`.

These tests spin up a tiny in-process HTTP server (stdlib
``http.server``) so the downloader exercises real socket /
urllib code paths without depending on the public internet.
The server serves files from a per-test ``tmp_path`` so different
tests can serve different payloads under different URLs.
"""

from __future__ import annotations

import hashlib
import http.server
import socket
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

import pytest
from mulligan_coach_overlay.auto_update.downloader import (
    DownloadError,
    download_to_file,
    sha256_file,
)


def _sha256_of(data: bytes) -> str:
    """SHA256 hex digest of bytes — used to compute the expected hash."""
    return hashlib.sha256(data).hexdigest()


@contextmanager
def _serve(serve_root: Path, route_overrides: dict[str, int] | None = None) -> Iterator[str]:
    """Spin up an HTTP server rooted at *serve_root*.

    Yields the base URL (``http://127.0.0.1:<port>``) the test
    should fetch from. ``route_overrides`` lets a test inject
    server-side errors per path (e.g. ``{"/bad": HTTPStatus 500}``).

    Why an ephemeral port: lets tests run in parallel without
    fighting over a fixed port.
    """
    overrides: dict[str, int] = route_overrides or {}

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

        def log_message(self, format: str, *args: object) -> None:
            """Silence the default access log; tests don't want it on stderr."""

        def do_GET(self) -> None:
            override = overrides.get(self.path)
            if isinstance(override, int):
                self.send_error(override, "test-injected error")
                return
            super().do_GET()

    # ``allow_reuse_address`` short-circuits the TIME_WAIT lockout
    # when a test runs back-to-back on the same port.
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=2.0)


def test_sha256_file_returns_empty_for_missing(tmp_path: Path) -> None:
    """Missing file → empty hash so "needs download" comparisons trigger."""
    assert sha256_file(tmp_path / "absent.bin") == ""


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    """Direct round-trip against stdlib hashlib for one fixture."""
    blob = b"Cassia, the Sea-bender" * 1024
    target = tmp_path / "fixture.bin"
    target.write_bytes(blob)
    assert sha256_file(target) == _sha256_of(blob)


def test_download_writes_target_atomically(tmp_path: Path) -> None:
    """Happy path: file lands at dest, .tmp sidecar cleaned up."""
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    payload = b"ratings parquet bytes" * 200
    (serve_root / "TLA.parquet").write_bytes(payload)

    dest = tmp_path / "dest" / "TLA.parquet"
    with _serve(serve_root) as base:
        download_to_file(
            f"{base}/TLA.parquet",
            dest,
            expected_sha256=_sha256_of(payload),
        )

    assert dest.read_bytes() == payload
    assert not (dest.with_suffix(".parquet.tmp")).exists()


def test_sha_mismatch_aborts_swap(tmp_path: Path) -> None:
    """A SHA mismatch leaves dest untouched and removes the staged tmp."""
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    payload = b"unexpected-bytes"
    (serve_root / "x.bin").write_bytes(payload)

    dest = tmp_path / "x.bin"
    dest.write_bytes(b"the-known-good-prior-content")

    with _serve(serve_root) as base, pytest.raises(DownloadError, match="SHA256 mismatch"):
        download_to_file(
            f"{base}/x.bin",
            dest,
            expected_sha256="0" * 64,
        )

    # Prior content untouched; no stale tmp left behind.
    assert dest.read_bytes() == b"the-known-good-prior-content"
    assert not dest.with_suffix(".bin.tmp").exists()


def test_download_http_404_raises_download_error(tmp_path: Path) -> None:
    """Permanent HTTP errors aren't retried; failure surfaces immediately."""
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    dest = tmp_path / "x.bin"

    with _serve(serve_root) as base, pytest.raises(DownloadError, match=r"HTTPError|404"):
        download_to_file(
            f"{base}/nonexistent.bin",
            dest,
            expected_sha256="0" * 64,
        )

    assert not dest.exists()


def test_download_server_5xx_retried_then_failed(tmp_path: Path) -> None:
    """5xx errors are retried once, then fold into DownloadError on second fail."""
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    dest = tmp_path / "x.bin"

    with (
        _serve(serve_root, route_overrides={"/x.bin": HTTPStatus.INTERNAL_SERVER_ERROR}) as base,
        pytest.raises(DownloadError),
    ):
        download_to_file(
            f"{base}/x.bin",
            dest,
            expected_sha256="0" * 64,
            timeout_seconds=5.0,
        )


def test_oversize_response_aborted(tmp_path: Path) -> None:
    """A response larger than expected_size_bytes aborts mid-stream.

    Protects against a misconfigured URL pointing at, say, a giant
    archive instead of a small parquet.
    """
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    actual = b"x" * 5_000
    (serve_root / "big.bin").write_bytes(actual)

    dest = tmp_path / "big.bin"
    with (
        _serve(serve_root) as base,
        pytest.raises(DownloadError, match="exceeded expected_size_bytes"),
    ):
        download_to_file(
            f"{base}/big.bin",
            dest,
            expected_sha256=_sha256_of(actual),
            expected_size_bytes=1024,
        )

    # The .tmp sidecar shouldn't survive a failed download.
    assert not dest.with_suffix(".bin.tmp").exists()


def test_connection_refused_raises_download_error(tmp_path: Path) -> None:
    """Network unreachable wraps into the public DownloadError type.

    We bind to an ephemeral port, close it, then try to download
    from the same port — guaranteed to be refused.
    """
    # Grab a port that no longer listens.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()

    dest = tmp_path / "x.bin"
    with pytest.raises(DownloadError):
        download_to_file(
            f"http://127.0.0.1:{closed_port}/x.bin",
            dest,
            expected_sha256="0" * 64,
            timeout_seconds=2.0,
        )
