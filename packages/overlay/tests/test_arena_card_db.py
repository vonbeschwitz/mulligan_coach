"""Unit tests for :mod:`mulligan_coach_overlay.arena_card_db`.

We construct a tiny SQLite file mimicking the shape of Arena's
``Raw_CardDatabase_*.mtga`` and verify the reader extracts only the
fields we care about (set/number/grpId triples, tokens excluded,
blank set codes excluded).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from mulligan_coach_overlay.arena_card_db import (
    default_card_database_path,
    load_arena_id_pairs,
)


def _make_fake_db(path: Path) -> None:
    """Write a SQLite file with a minimal Cards table schema.

    Only the columns the reader actually queries — adding the others
    would make the fixture more accurate but doesn't change the test.
    """
    conn = sqlite3.connect(path)
    try:
        # Minimal schema: only the columns + tables we actually query.
        # Cards.TitleId joins to Localizations_enUS.LocId for the name.
        conn.execute(
            "CREATE TABLE Cards ("
            "  GrpId INTEGER PRIMARY KEY, "
            "  ExpansionCode TEXT, "
            "  CollectorNumber TEXT, "
            "  TitleId INTEGER, "
            "  IsToken INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        conn.execute(
            "CREATE TABLE Localizations_enUS (LocId INTEGER PRIMARY KEY, Formatted INTEGER, Loc TEXT)"
        )
        conn.executemany(
            "INSERT INTO Localizations_enUS VALUES (?,?,?)",
            [
                (1, 0, "Some Real Card"),
                (2, 0, "Another Card"),
                (3, 0, "TLA Card"),
                (4, 0, "Plains"),
            ],
        )
        conn.executemany(
            "INSERT INTO Cards (GrpId, ExpansionCode, CollectorNumber, TitleId, IsToken) VALUES (?,?,?,?,?)",
            [
                (100001, "SOS", "1", 1, 0),
                (100002, "SOS", "2", 2, 0),
                (100003, "TLA", "150", 3, 0),
                # Token — must be filtered.
                (100004, "SOS", "T1", 1, 1),
                # Blank set code — must be filtered.
                (100005, "", "5", 1, 0),
                # NULL collector number — must be filtered.
                (100006, "ECL", None, 1, 0),
                # Alt-art duplicate (same set+number, different grpId).
                # The reader returns the raw rows; dedupe is the caller's job.
                (100007, "SOS", "1", 1, 0),
                # Basic-land printing — name "Plains" is what matters
                # for the basic-land fallback in card_index.
                (100008, "FDN", "270", 4, 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


class TestLoadArenaIdPairs:
    def test_returns_all_non_token_rows(self, tmp_path: Path) -> None:
        db = tmp_path / "fake.mtga"
        _make_fake_db(db)
        pairs = load_arena_id_pairs(db)
        assert sorted(pairs) == [
            ("FDN", "270", 100008, "Plains"),
            ("SOS", "1", 100001, "Some Real Card"),
            ("SOS", "1", 100007, "Some Real Card"),
            ("SOS", "2", 100002, "Another Card"),
            ("TLA", "150", 100003, "TLA Card"),
        ]

    def test_skips_tokens_blanks_and_null_numbers(self, tmp_path: Path) -> None:
        db = tmp_path / "fake.mtga"
        _make_fake_db(db)
        pairs = load_arena_id_pairs(db)
        # Token (100004), blank set (100005), null number (100006) excluded.
        grpids = {grp for _set, _num, grp, _name in pairs}
        assert 100004 not in grpids
        assert 100005 not in grpids
        assert 100006 not in grpids

    def test_uppercases_set_codes(self, tmp_path: Path) -> None:
        db = tmp_path / "fake.mtga"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE Cards ("
            "  GrpId INTEGER PRIMARY KEY, "
            "  ExpansionCode TEXT, "
            "  CollectorNumber TEXT, "
            "  TitleId INTEGER, "
            "  IsToken INTEGER NOT NULL DEFAULT 0"
            ")"
        )
        conn.execute(
            "CREATE TABLE Localizations_enUS (LocId INTEGER PRIMARY KEY, Formatted INTEGER, Loc TEXT)"
        )
        conn.execute("INSERT INTO Localizations_enUS VALUES (1, 0, 'Foo')")
        conn.execute("INSERT INTO Cards VALUES (1, 'sos', '1', 1, 0)")
        conn.commit()
        conn.close()
        pairs = load_arena_id_pairs(db)
        assert pairs == [("SOS", "1", 1, "Foo")]


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``MULLIGAN_COACH_MTGA_CARDDB`` always wins over auto-detection."""
    fake = tmp_path / "fake.mtga"
    fake.write_bytes(b"")  # empty file is enough; just needs to exist
    monkeypatch.setenv("MULLIGAN_COACH_MTGA_CARDDB", str(fake))
    assert default_card_database_path() == fake


def test_env_override_missing_path_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_MTGA_CARDDB", str(tmp_path / "does_not_exist.mtga"))
    assert default_card_database_path() is None
