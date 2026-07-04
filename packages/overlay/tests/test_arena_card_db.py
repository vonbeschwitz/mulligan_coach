"""Unit tests for :mod:`mulligan_coach_overlay.arena_card_db`.

We construct a tiny SQLite file mimicking the shape of Arena's
``Raw_CardDatabase_*.mtga`` and verify the reader extracts only the
fields we care about (set/number/grpId triples, tokens excluded,
blank set codes excluded).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest
from mulligan_coach_overlay.arena_card_db import (
    arena_data_dir_from_log,
    candidate_raw_dirs,
    default_card_database_path,
    find_card_database_in,
    find_card_database_under,
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


def _touch_db(raw_dir: Path, name: str, *, mtime: float | None = None) -> Path:
    """Create a Raw_CardDatabase file inside *raw_dir* (mkdir as needed)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    db = raw_dir / name
    db.write_bytes(b"")
    if mtime is not None:
        os.utime(db, (mtime, mtime))
    return db


class TestArenaDataDirFromLog:
    """The install dir Arena records in its own log's Mono-path line."""

    def _write_log(self, path: Path, managed_dir: str) -> None:
        path.write_text(
            f"Mono path[0] = '{managed_dir}/Managed'\n"
            "Mono config path[0] = '...'\n"
            "Initialize engine version: 2022.3.42f1\n",
            encoding="utf-8",
        )

    def test_extracts_existing_data_dir(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "Epic Games" / "MagicTheGathering" / "MTGA" / "MTGA_Data"
        data_dir.mkdir(parents=True)
        log = tmp_path / "Player.log"
        self._write_log(log, data_dir.as_posix())
        assert arena_data_dir_from_log(log) == data_dir

    def test_backslash_separator(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "MTGA_Data"
        data_dir.mkdir()
        log = tmp_path / "Player.log"
        # Some installs log a backslash path; the regex accepts both.
        win_style = str(data_dir).replace("/", "\\")
        log.write_text(f"Mono path[0] = '{win_style}\\Managed'\n", encoding="utf-8")
        assert arena_data_dir_from_log(log) == data_dir

    def test_missing_dir_returns_none(self, tmp_path: Path) -> None:
        log = tmp_path / "Player.log"
        self._write_log(log, (tmp_path / "not_there" / "MTGA_Data").as_posix())
        assert arena_data_dir_from_log(log) is None

    def test_no_mono_path_line_returns_none(self, tmp_path: Path) -> None:
        log = tmp_path / "Player.log"
        log.write_text("nothing useful here\n", encoding="utf-8")
        assert arena_data_dir_from_log(log) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert arena_data_dir_from_log(tmp_path / "nope.log") is None


class TestFindCardDatabaseIn:
    def test_newest_wins(self, tmp_path: Path) -> None:
        raw = tmp_path / "Raw"
        _touch_db(raw, "Raw_CardDatabase_old.mtga", mtime=1000)
        newest = _touch_db(raw, "Raw_CardDatabase_new.mtga", mtime=2000)
        assert find_card_database_in(raw) == newest

    def test_empty_dir_returns_none(self, tmp_path: Path) -> None:
        raw = tmp_path / "Raw"
        raw.mkdir()
        assert find_card_database_in(raw) is None

    def test_missing_dir_returns_none(self, tmp_path: Path) -> None:
        assert find_card_database_in(tmp_path / "nope") is None


class TestCandidateRawDirs:
    def test_log_derived_dir_comes_first(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "MTGA_Data"
        data_dir.mkdir()
        log = tmp_path / "Player.log"
        log.write_text(f"Mono path[0] = '{data_dir.as_posix()}/Managed'\n", encoding="utf-8")
        dirs = candidate_raw_dirs(log)
        assert dirs[0] == data_dir / "Downloads" / "Raw"
        # The well-known Wizards + Epic fallbacks follow.
        assert any("Epic Games" in str(d) for d in dirs)
        assert any("Wizards of the Coast" in str(d) for d in dirs)

    def test_no_log_still_lists_defaults(self) -> None:
        dirs = candidate_raw_dirs(None)
        assert dirs  # non-empty
        # No duplicates.
        assert len(dirs) == len(set(dirs))


class TestFindCardDatabaseUnder:
    def test_picks_up_nested_install_root(self, tmp_path: Path) -> None:
        raw = tmp_path / "MTGA" / "MTGA_Data" / "Downloads" / "Raw"
        db = _touch_db(raw, "Raw_CardDatabase_x.mtga")
        # User picks the top-level install folder.
        assert find_card_database_under(tmp_path) == db

    def test_picks_raw_dir_directly(self, tmp_path: Path) -> None:
        raw = tmp_path / "Raw"
        db = _touch_db(raw, "Raw_CardDatabase_x.mtga")
        assert find_card_database_under(raw) == db

    def test_recursive_fallback(self, tmp_path: Path) -> None:
        # An unusual layout the direct-probe list doesn't cover.
        weird = tmp_path / "weird" / "nested" / "place"
        db = _touch_db(weird, "Raw_CardDatabase_x.mtga")
        assert find_card_database_under(tmp_path) == db

    def test_nothing_found_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()
        assert find_card_database_under(tmp_path) is None
