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
