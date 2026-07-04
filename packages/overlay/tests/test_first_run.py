"""Unit tests for :mod:`mulligan_coach_overlay.first_run`."""

from __future__ import annotations

from pathlib import Path

import pytest
from mulligan_coach_overlay.first_run import (
    FirstRunState,
    SetupStatus,
    assess_setup,
    load_first_run_state,
    reconcile_state,
    save_first_run_state,
    should_auto_show,
)


@pytest.fixture(autouse=True)
def _neutralize_real_card_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Force auto-detection of the CardDatabase to find nothing.

    Otherwise a dev machine with Arena installed would resolve the real
    DB and flip ``card_db_found`` in tests that don't provide one. The
    env override's "missing path ⇒ None" branch short-circuits detection
    deterministically on every platform.
    """
    monkeypatch.setenv("MULLIGAN_COACH_MTGA_CARDDB", str(tmp_path / "no_such.mtga"))


def _write_log(path: Path, *, detailed: str) -> None:
    marker = {"enabled": "DETAILED LOGS: ENABLED", "disabled": "DETAILED LOGS: DISABLED"}.get(
        detailed, ""
    )
    path.write_text(f"[UnityCrossThreadLogger]6/24/2026\n{marker}\n", encoding="utf-8")


def _make_card_db(tmp_path: Path) -> Path:
    raw = tmp_path / "Raw"
    raw.mkdir()
    (raw / "Raw_CardDatabase_x.mtga").write_bytes(b"")
    return raw


# ---------------------------------------------------------------------------
# SetupStatus.all_clear
# ---------------------------------------------------------------------------


def test_all_clear_requires_everything() -> None:
    ok = SetupStatus(arena_log_found=True, detailed_logs="enabled", card_db_found=True)
    assert ok.all_clear is True


@pytest.mark.parametrize(
    "status",
    [
        SetupStatus(arena_log_found=False, detailed_logs="enabled", card_db_found=True),
        SetupStatus(arena_log_found=True, detailed_logs="disabled", card_db_found=True),
        SetupStatus(arena_log_found=True, detailed_logs="unknown", card_db_found=True),
        SetupStatus(arena_log_found=True, detailed_logs="enabled", card_db_found=False),
    ],
)
def test_all_clear_false_when_any_missing(status: SetupStatus) -> None:
    assert status.all_clear is False


# ---------------------------------------------------------------------------
# assess_setup
# ---------------------------------------------------------------------------


def test_assess_all_clear(tmp_path: Path) -> None:
    log = tmp_path / "Player.log"
    _write_log(log, detailed="enabled")
    raw = _make_card_db(tmp_path)
    status = assess_setup(log_path=log, card_db_dir=str(raw))
    assert status.all_clear
    assert status.card_db_path is not None


def test_assess_detailed_logs_disabled(tmp_path: Path) -> None:
    log = tmp_path / "Player.log"
    _write_log(log, detailed="disabled")
    raw = _make_card_db(tmp_path)
    status = assess_setup(log_path=log, card_db_dir=str(raw))
    assert status.arena_log_found
    assert status.detailed_logs == "disabled"
    assert not status.all_clear


def test_assess_arena_missing(tmp_path: Path) -> None:
    status = assess_setup(log_path=tmp_path / "nope.log", card_db_dir=None)
    assert not status.arena_log_found
    assert status.detailed_logs == "unknown"
    assert not status.card_db_found


def test_assess_card_db_missing(tmp_path: Path) -> None:
    log = tmp_path / "Player.log"
    _write_log(log, detailed="enabled")
    status = assess_setup(log_path=log, card_db_dir=None)
    assert status.arena_log_found
    assert status.detailed_logs == "enabled"
    assert not status.card_db_found


# ---------------------------------------------------------------------------
# should_auto_show / reconcile_state
# ---------------------------------------------------------------------------


def test_auto_show_false_when_all_clear() -> None:
    status = SetupStatus(arena_log_found=True, detailed_logs="enabled", card_db_found=True)
    assert should_auto_show(status, FirstRunState()) is False


def test_auto_show_true_when_not_verified_and_broken() -> None:
    status = SetupStatus(arena_log_found=True, detailed_logs="disabled", card_db_found=True)
    assert should_auto_show(status, FirstRunState(detailed_logs_verified=False)) is True


def test_auto_show_false_once_verified_even_if_broken() -> None:
    # The no-nag guarantee: once onboarded, we don't auto-pop again.
    status = SetupStatus(arena_log_found=True, detailed_logs="disabled", card_db_found=True)
    assert should_auto_show(status, FirstRunState(detailed_logs_verified=True)) is False


def test_reconcile_marks_verified_on_first_all_clear() -> None:
    status = SetupStatus(arena_log_found=True, detailed_logs="enabled", card_db_found=True)
    state = FirstRunState(detailed_logs_verified=False, card_db_dir="/some/dir")
    new = reconcile_state(status, state)
    assert new.detailed_logs_verified is True
    assert new.card_db_dir == "/some/dir"  # preserved


def test_reconcile_noop_when_not_all_clear() -> None:
    status = SetupStatus(arena_log_found=True, detailed_logs="disabled", card_db_found=True)
    state = FirstRunState(detailed_logs_verified=False)
    assert reconcile_state(status, state) is state


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "first_run.json"
    state = FirstRunState(detailed_logs_verified=True, card_db_dir="C:/x/Raw")
    save_first_run_state(path, state)
    assert load_first_run_state(path) == state


def test_load_missing_returns_defaults(tmp_path: Path) -> None:
    assert load_first_run_state(tmp_path / "absent.json") == FirstRunState()


def test_load_corrupt_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "first_run.json"
    path.write_text("not json {", encoding="utf-8")
    assert load_first_run_state(path) == FirstRunState()


def test_load_wrong_schema_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "first_run.json"
    path.write_text('{"version": 999, "detailed_logs_verified": true}', encoding="utf-8")
    assert load_first_run_state(path) == FirstRunState()
