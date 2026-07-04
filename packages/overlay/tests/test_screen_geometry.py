"""Unit tests for :mod:`mulligan_coach_overlay.screen_geometry`."""

from __future__ import annotations

from mulligan_coach_overlay.screen_geometry import clamp_to_screens

# A single 1920x1080 primary at the origin.
_PRIMARY = (0, 0, 1920, 1080)
# A second monitor to the right (as if extended desktop).
_SECONDARY = (1920, 0, 1920, 1080)


def test_position_fully_on_screen_is_unchanged() -> None:
    assert clamp_to_screens((100, 100), (320, 400), [_PRIMARY]) == (100, 100)


def test_empty_screens_returns_unchanged() -> None:
    assert clamp_to_screens((5000, 5000), (320, 400), []) == (5000, 5000)


def test_position_on_secondary_kept_when_secondary_present() -> None:
    pos = (2000, 100)
    assert clamp_to_screens(pos, (320, 400), [_PRIMARY, _SECONDARY]) == pos


def test_unplugged_monitor_position_is_pulled_back_onto_primary() -> None:
    # Saved on the (now-unplugged) secondary; only primary remains.
    x, y = clamp_to_screens((2400, 200), (320, 400), [_PRIMARY])
    # Fully back on the primary screen.
    assert 0 <= x <= 1920 - 320
    assert 0 <= y <= 1080 - 400


def test_mostly_offscreen_right_edge_is_clamped() -> None:
    # Only ~10 px of the window peeks onto the primary's right edge —
    # below the grabbable threshold, so it gets clamped fully on.
    x, y = clamp_to_screens((1910, 100), (320, 400), [_PRIMARY])
    assert x == 1920 - 320  # right-aligned, fully visible
    assert y == 100


def test_small_visible_strip_above_threshold_is_kept() -> None:
    # 60 px visible in each axis — enough to grab, so it's left alone.
    pos = (1920 - 60, 1080 - 60)
    assert clamp_to_screens(pos, (320, 400), [_PRIMARY]) == pos


def test_window_larger_than_screen_pins_to_top_left() -> None:
    # A window taller/wider than the screen can't fit; it pins to the
    # screen origin rather than going negative.
    x, y = clamp_to_screens((5000, 5000), (3000, 2000), [_PRIMARY])
    assert (x, y) == (0, 0)


def test_negative_offscreen_position_clamped() -> None:
    x, y = clamp_to_screens((-500, -500), (320, 400), [_PRIMARY])
    assert x == 0
    assert y == 0
