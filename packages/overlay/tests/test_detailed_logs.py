"""Unit tests for :mod:`mulligan_coach_overlay.detailed_logs`."""

from __future__ import annotations

from pathlib import Path

from mulligan_coach_overlay.detailed_logs import (
    detect_detailed_logs,
    detect_detailed_logs_from_path,
)

_HEADER = "[UnityCrossThreadLogger]6/24/2026 7:23:00 PM"


def test_enabled_marker() -> None:
    text = f"{_HEADER}\nDETAILED LOGS: ENABLED\nsome other line\n"
    assert detect_detailed_logs(text) == "enabled"


def test_disabled_marker() -> None:
    text = f"{_HEADER}\nDETAILED LOGS: DISABLED\n"
    assert detect_detailed_logs(text) == "disabled"


def test_last_marker_wins_disabled_then_enabled() -> None:
    # Should never happen in one session, but the rule is deterministic:
    # the later marker in the byte window is authoritative.
    text = "DETAILED LOGS: DISABLED\n...\nDETAILED LOGS: ENABLED\n"
    assert detect_detailed_logs(text) == "enabled"


def test_last_marker_wins_enabled_then_disabled() -> None:
    text = "DETAILED LOGS: ENABLED\n...\nDETAILED LOGS: DISABLED\n"
    assert detect_detailed_logs(text) == "disabled"


def test_gre_evidence_without_marker_is_enabled() -> None:
    # No explicit marker, but detailed GRE messages only appear when the
    # setting is on.
    text = 'greToClientEvent ... "type": "GREMessageType_GameStateMessage" ...'
    assert detect_detailed_logs(text) == "enabled"


def test_no_signal_is_unknown() -> None:
    assert detect_detailed_logs("just some boring unity log lines\n") == "unknown"


def test_from_path_reads_file(tmp_path: Path) -> None:
    log = tmp_path / "Player.log"
    log.write_text(f"{_HEADER}\nDETAILED LOGS: ENABLED\n", encoding="utf-8")
    assert detect_detailed_logs_from_path(log) == "enabled"


def test_from_path_missing_file_is_unknown(tmp_path: Path) -> None:
    assert detect_detailed_logs_from_path(tmp_path / "nope.log") == "unknown"


def test_from_path_scan_limit_reads_head(tmp_path: Path) -> None:
    # The marker lives at the top; even with a tiny scan window we catch
    # it because we read the head, not the tail.
    log = tmp_path / "Player.log"
    log.write_text("DETAILED LOGS: ENABLED\n" + ("x" * 10_000), encoding="utf-8")
    assert detect_detailed_logs_from_path(log, scan_bytes=64) == "enabled"
