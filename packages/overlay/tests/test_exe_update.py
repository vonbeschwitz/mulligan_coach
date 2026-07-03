"""Tests for the notify-only EXE update channel.

Covers the three pieces that carry logic (the Qt tray/controller
wiring in ``gui.py`` / ``tray.py`` needs a live ``QApplication`` and,
like the rest of the overlay suite, is left to manual/integration
testing — no test here constructs a Qt object):

* ``parse_exe_version`` — sidecar JSON → :class:`ExeVersionInfo`,
  including the real published shape and every rejection path.
* ``is_newer_bundle_version`` — the timestamp-prefix comparison and
  its unparseable-stamp fallback.
* ``ExeUpdateChecker.check`` — the end-to-end flow against a local
  HTTP server, plus the offline / disabled / unknown-running-version
  branches.
* the balloon-copy helpers.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from mulligan_coach_overlay.auto_update.exe_update import (
    ExeUpdateChecker,
    ExeVersionInfo,
    ExeVersionParseError,
    is_newer_bundle_version,
    manual_check_message,
    parse_exe_version,
    update_available_message,
)

# A realistic sidecar body, matching what publish_exe_release.py writes
# (see logs/last_published_exe_version.json for the real published one).
_SIDECAR = {
    "schema_version": 1,
    "generated_at": "2026-05-25T23:59:06Z",
    "bundle_version": "20260525T235826Z+177d370",
    "download_url": (
        "https://github.com/vonbeschwitz/mulligan_coach_data/"
        "releases/download/exe-latest/MulliganCoach.zip"
    ),
    "sha256": "81ee80dd674ea396db1bda5c035e47e83b851166e5af9e7d6a2c2b80a910ddbf",
    "size_bytes": 127355710,
    "release_page": ("https://github.com/vonbeschwitz/mulligan_coach_data/releases/tag/exe-latest"),
}


@contextmanager
def _serve(serve_root: Path, status_overrides: dict[str, int] | None = None) -> Iterator[str]:
    """Serve *serve_root* over HTTP on an ephemeral port; yield the base URL."""
    overrides = status_overrides or {}

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

        def log_message(self, format: str, *args: object) -> None:
            pass  # silence access log

        def do_GET(self) -> None:
            code = overrides.get(self.path)
            if code is not None:
                self.send_error(code, "test-injected")
                return
            super().do_GET()

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


def _write_sidecar(root: Path, payload: dict[str, object]) -> None:
    (root / "exe_version.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_exe_version
# ---------------------------------------------------------------------------


def test_parse_valid_sidecar() -> None:
    info = parse_exe_version(json.dumps(_SIDECAR))
    assert info.bundle_version == "20260525T235826Z+177d370"
    assert info.download_url.endswith("MulliganCoach.zip")
    assert info.release_page.endswith("tag/exe-latest")
    assert info.sha256 == _SIDECAR["sha256"]
    assert info.size_bytes == 127355710
    assert info.schema_version == 1


def test_parse_tolerates_unknown_keys_and_optional_fields() -> None:
    payload = {
        "schema_version": 1,
        "bundle_version": "20260101T000000Z+abc1234",
        "download_url": "https://example.com/x.zip",
        "release_page": "https://example.com/rel",
        "future_field": {"nested": True},
    }
    info = parse_exe_version(json.dumps(payload))
    assert info.sha256 is None
    assert info.size_bytes is None


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("bundle_version"), id="missing-bundle-version"),
        pytest.param(lambda d: d.update(bundle_version="  "), id="blank-bundle-version"),
        pytest.param(lambda d: d.pop("download_url"), id="missing-download-url"),
        pytest.param(lambda d: d.pop("release_page"), id="missing-release-page"),
        pytest.param(lambda d: d.pop("schema_version"), id="missing-schema"),
        pytest.param(lambda d: d.update(schema_version=999), id="too-new-schema"),
        pytest.param(lambda d: d.update(schema_version="1"), id="non-int-schema"),
        pytest.param(lambda d: d.update(download_url="file:///etc/passwd"), id="disallowed-scheme"),
        pytest.param(lambda d: d.update(size_bytes=-5), id="negative-size"),
        pytest.param(lambda d: d.update(sha256=1234), id="non-string-sha"),
    ],
)
def test_parse_rejects_bad_sidecars(mutate: object) -> None:
    payload = dict(_SIDECAR)
    mutate(payload)  # type: ignore[operator]
    with pytest.raises(ExeVersionParseError):
        parse_exe_version(json.dumps(payload))


def test_parse_rejects_non_json_and_non_object() -> None:
    with pytest.raises(ExeVersionParseError):
        parse_exe_version("not json{")
    with pytest.raises(ExeVersionParseError):
        parse_exe_version(json.dumps([1, 2, 3]))


# ---------------------------------------------------------------------------
# is_newer_bundle_version
# ---------------------------------------------------------------------------


def test_newer_when_published_timestamp_greater() -> None:
    assert is_newer_bundle_version("20260601T120000Z+aaa", "20260525T235826Z+bbb") is True


def test_not_newer_when_published_older_or_equal() -> None:
    assert is_newer_bundle_version("20260101T000000Z+aaa", "20260525T235826Z+bbb") is False
    assert is_newer_bundle_version("20260525T235826Z+aaa", "20260525T235826Z+aaa") is False


def test_same_timestamp_different_hash_is_not_newer() -> None:
    # Two builds minted in the same second but from different commits:
    # the timestamps tie, so it's not "strictly newer".
    assert is_newer_bundle_version("20260525T235826Z+aaa", "20260525T235826Z+bbb") is False


def test_unparseable_stamps_fall_back_to_string_inequality() -> None:
    # Timestamp-only "nogit" fallback stamps can't be ordered; any
    # difference is treated as an available update, equality is not.
    assert is_newer_bundle_version("weird-a", "weird-b") is True
    assert is_newer_bundle_version("weird-a", "weird-a") is False
    # One side parseable, the other not → still "different ⇒ update".
    assert is_newer_bundle_version("20260601T120000Z+aaa", "nogit-build") is True


# ---------------------------------------------------------------------------
# ExeUpdateChecker.check
# ---------------------------------------------------------------------------


def test_check_reports_update_available(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, _SIDECAR)
    with _serve(tmp_path) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version="20260101T000000Z+old1234",
            timeout_seconds=5.0,
        )
        result = checker.check()
    assert result.status == "update_available"
    assert result.latest is not None
    assert result.latest.bundle_version == "20260525T235826Z+177d370"


def test_check_reports_up_to_date_when_running_matches(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, _SIDECAR)
    with _serve(tmp_path) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version="20260525T235826Z+177d370",
            timeout_seconds=5.0,
        )
        result = checker.check()
    assert result.status == "up_to_date"


def test_check_up_to_date_when_running_is_newer(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, _SIDECAR)
    with _serve(tmp_path) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version="20270101T000000Z+dev0000",
            timeout_seconds=5.0,
        )
        result = checker.check()
    assert result.status == "up_to_date"


def test_check_unknown_when_running_version_missing(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, _SIDECAR)
    with _serve(tmp_path) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version=None,
            timeout_seconds=5.0,
        )
        result = checker.check()
    # We still learned the latest published build, just can't compare.
    assert result.status == "unknown"
    assert result.latest is not None
    assert result.latest.bundle_version == "20260525T235826Z+177d370"


def test_check_disabled_when_url_empty() -> None:
    checker = ExeUpdateChecker(version_url="", running_version="20260101T000000Z+x")
    result = checker.check()
    assert result.status == "unknown"
    assert result.latest is None
    assert "disabled" in result.message.lower()


def test_check_unknown_on_http_error(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, _SIDECAR)
    with _serve(tmp_path, status_overrides={"/exe_version.json": 404}) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version="20260101T000000Z+old",
            timeout_seconds=5.0,
        )
        result = checker.check()
    assert result.status == "unknown"
    assert result.latest is None


def test_check_unknown_on_malformed_json(tmp_path: Path) -> None:
    (tmp_path / "exe_version.json").write_text("{ not json", encoding="utf-8")
    with _serve(tmp_path) as base:
        checker = ExeUpdateChecker(
            version_url=f"{base}/exe_version.json",
            running_version="20260101T000000Z+old",
            timeout_seconds=5.0,
        )
        result = checker.check()
    assert result.status == "unknown"


def test_check_never_raises_on_bad_scheme() -> None:
    checker = ExeUpdateChecker(
        version_url="file:///etc/passwd", running_version="20260101T000000Z+x"
    )
    # Must fold the scheme rejection into an unknown result, not raise.
    result = checker.check()
    assert result.status == "unknown"


# ---------------------------------------------------------------------------
# Balloon copy helpers
# ---------------------------------------------------------------------------


def _result(status: str, latest: ExeVersionInfo | None) -> object:
    from mulligan_coach_overlay.auto_update.exe_update import ExeUpdateResult

    return ExeUpdateResult(
        status=status,  # type: ignore[arg-type]
        running_version="20260101T000000Z+x",
        latest=latest,
        message="",
    )


def test_update_available_message_includes_version() -> None:
    info = parse_exe_version(json.dumps(_SIDECAR))
    title, body = update_available_message(_result("update_available", info))  # type: ignore[arg-type]
    assert title == "Update available"
    assert "20260525T235826Z+177d370" in body


def test_manual_check_messages_per_status() -> None:
    info = parse_exe_version(json.dumps(_SIDECAR))
    up_title, _ = manual_check_message(_result("update_available", info))  # type: ignore[arg-type]
    assert up_title == "Update available"
    ok_title, _ = manual_check_message(_result("up_to_date", info))  # type: ignore[arg-type]
    assert "up to date" in ok_title.lower()
    unk_title, unk_body = manual_check_message(_result("unknown", None))  # type: ignore[arg-type]
    assert "couldn't check" in unk_title.lower()
    assert unk_body  # non-empty
