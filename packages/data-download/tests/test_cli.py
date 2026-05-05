"""Smoke tests for the typer CLI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from mulligan_coach_data_download.cli import app
from mulligan_coach_data_download.manifest import Manifest, SourceEntry
from typer.testing import CliRunner


def test_help_shows_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in (
        "refresh-17lands",
        "refresh-scryfall",
        "refresh-mtgjson",
        "refresh-all",
        "status",
    ):
        assert cmd in result.output, f"missing command in help: {cmd}"


def test_status_with_no_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_DATA_ROOT", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "No manifest yet" in result.output


def test_status_with_seed_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_DATA_ROOT", str(tmp_path))
    from mulligan_coach_data_download import paths

    paths.ensure_layout(tmp_path)
    manifest = Manifest()
    manifest.upsert(
        SourceEntry(
            url="https://example.com/x.csv.gz",
            local_path=str(tmp_path / "x.csv.gz"),
            etag='"abc"',
            size=1024 * 1024 * 5,
            row_count=42,
        )
    )
    manifest.save(paths.manifest_path(tmp_path))

    runner = CliRunner()
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "https://example.com/x.csv.gz" in result.output
    assert "42 rows" in result.output
    assert "5.00 MiB" in result.output
