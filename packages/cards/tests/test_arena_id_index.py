"""Tests for the MTGJSON-backed arena_id index loader.

Hand-written AllIdentifiers fixtures only — no real download.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mulligan_coach_cards.loader import load_arena_id_index


def _write_all_identifiers(data_root: Path, records: list[dict[str, Any]]) -> Path:
    """Write a minimal AllIdentifiers.json with the given card records.

    The real MTGJSON file is shaped ``{"meta": {...}, "data": {uuid: rec}}``.
    We mirror that shape so the loader exercises the real top-level path.
    """
    path = data_root / "raw" / "mtgjson" / "AllIdentifiers.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"date": "2026-05-08", "version": "test"},
        "data": {f"uuid-{i}": rec for i, rec in enumerate(records)},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _record(
    *,
    name: str = "Test Card",
    set_code: str = "TST",
    oracle_id: str | None = "oracle-a",
    mtga_id: str | None = "1234",
    extra_idents: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a stripped-down MTGJSON record. None values omit the field."""
    idents: dict[str, Any] = {}
    if oracle_id is not None:
        idents["scryfallOracleId"] = oracle_id
    if mtga_id is not None:
        idents["mtgArenaId"] = mtga_id
    if extra_idents:
        idents.update(extra_idents)
    rec: dict[str, Any] = {"name": name, "identifiers": idents}
    if set_code is not None:
        rec["setCode"] = set_code
    return rec


def test_happy_path_indexes_oracle_set_to_mtga_id(tmp_path: Path) -> None:
    _write_all_identifiers(
        tmp_path,
        [_record(oracle_id="oracle-a", set_code="DSK", mtga_id="92352")],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-a", "DSK"): 92352}


def test_skips_records_missing_oracle_id(tmp_path: Path) -> None:
    _write_all_identifiers(
        tmp_path,
        [
            _record(oracle_id=None, set_code="DSK", mtga_id="1"),
            _record(oracle_id="oracle-b", set_code="DSK", mtga_id="2"),
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-b", "DSK"): 2}


def test_skips_records_missing_mtga_id(tmp_path: Path) -> None:
    """Most MTGJSON records have no mtgArenaId — paper-only printings."""
    _write_all_identifiers(
        tmp_path,
        [
            _record(oracle_id="oracle-a", set_code="DSK", mtga_id=None),
            _record(oracle_id="oracle-b", set_code="DSK", mtga_id="42"),
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-b", "DSK"): 42}


def test_skips_records_missing_set_code(tmp_path: Path) -> None:
    _write_all_identifiers(
        tmp_path,
        [
            {"identifiers": {"scryfallOracleId": "oracle-a", "mtgArenaId": "1"}},
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {}


def test_same_oracle_id_different_sets_keeps_both(tmp_path: Path) -> None:
    """Reprints across sets get distinct mtga_ids — both should be indexed."""
    _write_all_identifiers(
        tmp_path,
        [
            _record(oracle_id="oracle-a", set_code="DSK", mtga_id="1"),
            _record(oracle_id="oracle-a", set_code="MOM", mtga_id="2"),
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-a", "DSK"): 1, ("oracle-a", "MOM"): 2}


def test_oracle_set_collision_keeps_first(tmp_path: Path) -> None:
    """Alt-art / showcase reprints share (oracle_id, set_code); first wins."""
    _write_all_identifiers(
        tmp_path,
        [
            _record(oracle_id="oracle-a", set_code="DSK", mtga_id="100"),
            _record(oracle_id="oracle-a", set_code="DSK", mtga_id="200"),
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-a", "DSK"): 100}


def test_mtga_id_string_coerced_to_int(tmp_path: Path) -> None:
    """MTGJSON stores mtgArenaId as a string; we expose it as int."""
    _write_all_identifiers(
        tmp_path,
        [_record(oracle_id="oracle-a", set_code="DSK", mtga_id="92352")],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx[("oracle-a", "DSK")] == 92352
    assert isinstance(idx[("oracle-a", "DSK")], int)


def test_set_code_is_uppercased(tmp_path: Path) -> None:
    """Set codes from MTGJSON are sometimes lowercase in older snapshots."""
    _write_all_identifiers(
        tmp_path,
        [_record(oracle_id="oracle-a", set_code="dsk", mtga_id="1")],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert ("oracle-a", "DSK") in idx
    assert ("oracle-a", "dsk") not in idx


def test_invalid_mtga_id_skipped(tmp_path: Path) -> None:
    """Malformed mtgArenaId (non-numeric) should be skipped, not raise."""
    _write_all_identifiers(
        tmp_path,
        [
            _record(oracle_id="oracle-a", set_code="DSK", mtga_id="not-a-number"),
            _record(oracle_id="oracle-b", set_code="DSK", mtga_id="42"),
        ],
    )
    idx = load_arena_id_index(data_root=tmp_path)
    assert idx == {("oracle-b", "DSK"): 42}


def test_missing_file_raises_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        load_arena_id_index(data_root=tmp_path)
    msg = str(excinfo.value)
    assert "AllIdentifiers" in msg
    assert "mulligan-coach-data refresh-mtgjson" in msg
