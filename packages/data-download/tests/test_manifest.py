"""Tests for the manifest module."""

from __future__ import annotations

from pathlib import Path

import pytest
from mulligan_coach_data_download.manifest import Manifest, SourceEntry


def test_load_returns_empty_when_missing(tmp_path: Path) -> None:
    manifest = Manifest.load(tmp_path / "missing.json")
    assert manifest.sources == {}
    assert manifest.version == 1


def test_round_trip_save_load(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    m = Manifest()
    m.upsert(
        SourceEntry(
            url="https://example.com/x.csv.gz",
            local_path=str(tmp_path / "x.csv.gz"),
            etag='"abc"',
            size=1234,
            sha256="deadbeef",
        )
    )
    m.save(path)

    reloaded = Manifest.load(path)
    entry = reloaded.get("https://example.com/x.csv.gz")
    assert entry is not None
    assert entry.etag == '"abc"'
    assert entry.size == 1234
    assert entry.downloaded_at is not None  # auto-populated by upsert


def test_upsert_overwrites_in_place(tmp_path: Path) -> None:
    m = Manifest()
    url = "https://example.com/x.csv.gz"
    m.upsert(SourceEntry(url=url, local_path="/tmp/a", size=1))
    m.upsert(SourceEntry(url=url, local_path="/tmp/a", size=2))
    assert len(m.sources) == 1
    entry = m.get(url)
    assert entry is not None
    assert entry.size == 2


def test_save_is_atomic_no_partial_files(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    m = Manifest()
    m.save(path)

    # No leftover temp files should remain in the directory.
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".manifest-")]
    assert leftovers == [], f"unexpected temp files: {leftovers}"


def test_extra_fields_rejected() -> None:
    # Catches typos / drift between code and on-disk schema early.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SourceEntry.model_validate({"url": "u", "local_path": "/x", "unknown_field": "oops"})
