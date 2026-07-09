"""Tests for the ``_snapshot_download_counts`` helper duplicated in both
publishers (``publish_exe_release.py`` + ``publish_data_release.py``).

The helper preserves GitHub's per-asset ``download_count`` in an
append-only log *before* a ``--clobber`` upload resets it. It must be
read-only, best-effort, and — critically — never block a publish, so the
tests cover the happy path plus every failure mode (release missing, gh
error, gh not installed) and the append-only contract.

Both publishers carry a near-verbatim copy (the same duplication
convention they use for ``_run_gh`` / ``_ensure_release``), so every test
runs against *both* modules to catch the two copies drifting apart.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_PACKAGING = Path(__file__).resolve().parents[1] / "packaging"
_MODULES = ("publish_exe_release", "publish_data_release")


@pytest.fixture(params=_MODULES)
def publisher(request: pytest.FixtureRequest) -> Iterator[object]:
    """Load one publisher script by file path (parametrised over both)."""
    name = request.param
    script = _PACKAGING / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(name, None)


def _ok(assets: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    """A successful ``gh api`` result carrying *assets*."""
    return subprocess.CompletedProcess(
        args=["gh"], returncode=0, stdout=json.dumps({"assets": assets}), stderr=""
    )


def _fail(returncode: int, stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout="", stderr=stderr)


_SAMPLE_ASSETS = [
    {"name": "MulliganCoachSetup.exe", "download_count": 42, "updated_at": "2026-07-01T00:00:00Z"},
    {"name": "manifest.json", "download_count": 7, "updated_at": "2026-07-02T00:00:00Z"},
]


def test_records_each_asset(publisher: object, tmp_path: Path) -> None:
    log = tmp_path / "download_counts.jsonl"
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo",
        "data-current",
        dry_run=False,
        log_path=log,
        runner=lambda _args: _ok(_SAMPLE_ASSETS),
    )
    assert n == 2
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    first = rows[0]
    assert first["repo"] == "owner/repo"
    assert first["tag"] == "data-current"
    assert first["name"] == "MulliganCoachSetup.exe"
    assert first["download_count"] == 42
    assert first["updated_at"] == "2026-07-01T00:00:00Z"
    assert first["snapshot_at"].endswith("Z")  # timestamped


def test_queries_the_right_release(publisher: object, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(args)
        return _ok(_SAMPLE_ASSETS)

    publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo", "exe-latest", dry_run=False, log_path=tmp_path / "c.jsonl", runner=runner
    )
    assert seen == [["api", "repos/owner/repo/releases/tags/exe-latest"]]


def test_append_only_across_publishes(publisher: object, tmp_path: Path) -> None:
    log = tmp_path / "download_counts.jsonl"
    for _ in range(3):
        publisher._snapshot_download_counts(  # type: ignore[attr-defined]
            "owner/repo",
            "data-current",
            dry_run=False,
            log_path=log,
            runner=lambda _args: _ok(_SAMPLE_ASSETS[:1]),
        )
    # One asset x three publishes = three appended lines, nothing truncated.
    assert len(log.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_no_assets_writes_nothing(publisher: object, tmp_path: Path) -> None:
    log = tmp_path / "download_counts.jsonl"
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo", "exe-latest", dry_run=False, log_path=log, runner=lambda _a: _ok([])
    )
    assert n == 0
    assert not log.exists()


def test_release_missing_is_benign(
    publisher: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "download_counts.jsonl"
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo",
        "exe-latest",
        dry_run=False,
        log_path=log,
        runner=lambda _a: _fail(1, "gh: Not Found (HTTP 404)"),
    )
    assert n == 0
    assert not log.exists()
    # Benign phrasing, NOT a loud warning — first publish is normal.
    out = capsys.readouterr()
    assert "WARNING" not in out.err


def test_gh_error_warns_but_continues(
    publisher: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log = tmp_path / "download_counts.jsonl"
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo",
        "data-current",
        dry_run=False,
        log_path=log,
        runner=lambda _a: _fail(1, "error connecting to api.github.com"),
    )
    assert n == 0  # did not block
    assert not log.exists()
    assert "WARNING" in capsys.readouterr().err


def test_gh_not_installed_warns_but_continues(
    publisher: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def runner(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise OSError("No such file or directory: 'gh'")

    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo",
        "data-current",
        dry_run=False,
        log_path=tmp_path / "c.jsonl",
        runner=runner,
    )
    assert n == 0
    assert "WARNING" in capsys.readouterr().err


def test_dry_run_skips_everything(publisher: object, tmp_path: Path) -> None:
    def runner(_args: list[str]) -> subprocess.CompletedProcess[str]:
        raise AssertionError("runner must not be called under dry-run")

    log = tmp_path / "download_counts.jsonl"
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo", "data-current", dry_run=True, log_path=log, runner=runner
    )
    assert n == 0
    assert not log.exists()


def test_malformed_json_warns_but_continues(
    publisher: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="not json{", stderr="")
    n = publisher._snapshot_download_counts(  # type: ignore[attr-defined]
        "owner/repo",
        "data-current",
        dry_run=False,
        log_path=tmp_path / "c.jsonl",
        runner=lambda _a: bad,
    )
    assert n == 0
    assert "WARNING" in capsys.readouterr().err
