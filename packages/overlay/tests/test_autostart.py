"""Tests for the Windows autostart registry helper.

The registry round-trip is gated on a private import of
:mod:`winreg`, so the helper's outer surface is testable on any
platform: the ``supported()`` predicate, the no-op behaviour of
``enable`` / ``disable`` / ``is_enabled`` from a non-frozen / non-
Windows context, and the parsing of stored registry values.

The actual ``winreg.SetValueEx`` / ``DeleteValue`` round-trip is
covered by a manual smoke check, not here — pytest must not touch
the host's real Run key.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mulligan_coach_overlay import autostart


def test_supported_false_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Source runs must report unsupported regardless of platform.

    ``supported()`` gates the whole helper: from source the title-bar
    settings menu hides the entry, so a stray dev click can't wire
    ``python.exe`` into the user's autostart list.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.supported() is False


def test_supported_false_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Windows platforms always report unsupported.

    Even if a future frozen build runs on macOS, there's no
    ``HKEY_CURRENT_USER\\...\\Run`` analogue we'd hit through this
    module — a macOS LaunchAgent would land in a different helper.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert autostart.supported() is False


def test_is_enabled_false_when_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """``is_enabled`` short-circuits to False on an unsupported host.

    Critical for the UI: ``QAction.setChecked(autostart.is_enabled())``
    must not crash when the menu is shown on a dev box even though
    we hide the action there in practice.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert autostart.is_enabled() is False


def test_enable_disable_noop_when_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    """``enable`` / ``disable`` are no-ops off-platform.

    The UI calls these unconditionally on toggle. They must not
    raise (so the slot can stay slot-shaped) and must not attempt
    a registry write that would fail in obscure ways.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    # Both should return cleanly. We don't assert no side effects
    # explicitly — supported() returning False is the guard — but a
    # raised exception here would fail the test.
    autostart.enable()
    autostart.disable()


def test_enable_default_noop_when_unsupported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First-run helper short-circuits when the platform doesn't support it.

    Specifically: no marker file is written. We *want* the next launch
    on a frozen build to retry — so a dev who runs from source first
    doesn't accidentally suppress the default-on behaviour for the
    EXE they ship later.
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    state = tmp_path / "state"
    assert autostart.enable_default_if_first_run(state) is False
    assert not (state / "_autostart_seeded.txt").exists()


def test_enable_default_writes_marker_on_first_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """First call writes the marker; ``enable()`` is invoked once.

    The marker contents are the resolved EXE path — exercised here
    against a stubbed ``_executable_path`` so we don't touch the real
    interpreter location during the test.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(autostart, "_executable_path", lambda: Path("C:\\fake\\MulliganCoach.exe"))

    enable_calls: list[None] = []
    monkeypatch.setattr(autostart, "enable", lambda: enable_calls.append(None))

    state = tmp_path / "state"
    assert autostart.enable_default_if_first_run(state) is True
    assert len(enable_calls) == 1
    marker = state / "_autostart_seeded.txt"
    assert marker.read_text(encoding="utf-8") == "C:\\fake\\MulliganCoach.exe"


def test_enable_default_skips_when_marker_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Second launch must not touch the registry — that's how a
    deliberate un-tick of the toggle survives across launches."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    enable_calls: list[None] = []
    monkeypatch.setattr(autostart, "enable", lambda: enable_calls.append(None))

    state = tmp_path / "state"
    state.mkdir()
    (state / "_autostart_seeded.txt").write_text("anything", encoding="utf-8")

    assert autostart.enable_default_if_first_run(state) is False
    assert enable_calls == []
