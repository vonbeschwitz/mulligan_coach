"""Keep a restored overlay position on an actually-attached screen.

Motivation
----------

The overlay persists its per-layout window positions across launches
(see :mod:`position_persistence`). That's the right behaviour on a
stable setup, but a saved ``(x, y)`` can land entirely off every
currently-attached display when the user:

* unplugged the second monitor the overlay was parked on;
* changed their display arrangement / resolution;
* moved a laptop from a docking-station multi-monitor setup to the
  built-in panel alone.

Qt's ``move()`` does *not* clamp — it happily places a frameless,
taskbar-hidden ``Qt.Tool`` window at coordinates no monitor covers,
leaving the overlay invisible and un-grabbable (there's no title bar to
find in the taskbar). This module is the pure-geometry guard: given a
desired position, the window size, and the geometries of the attached
screens, it returns a position that keeps a grabbable strip of the
window on some screen.

Pure integer math, no Qt — so it unit-tests without a display. The GUI
supplies the screen rectangles from ``QGuiApplication.screens()``.
"""

from __future__ import annotations

# Minimum number of pixels of the window that must remain visible in
# *each* axis for the position to be considered acceptable. The overlay
# is dragged by clicking anywhere on its body (it has no OS title bar),
# so we need a genuinely clickable strip on-screen, not just a 1 px
# sliver. 48 px comfortably clears that while tolerating a user who
# likes the panel tucked mostly against an edge.
_MIN_VISIBLE_PX = 48

# A screen rectangle: (left, top, width, height) in virtual-desktop
# coordinates — exactly what ``QScreen.geometry()`` yields as a
# ``(x, y, w, h)`` tuple.
Rect = tuple[int, int, int, int]


def clamp_to_screens(
    pos: tuple[int, int],
    size: tuple[int, int],
    screens: list[Rect],
) -> tuple[int, int]:
    """Return a position that keeps the window grabbable on some screen.

    If the window at *pos* already shows at least :data:`_MIN_VISIBLE_PX`
    (or its full extent, whichever is smaller) on *some* screen, *pos* is
    returned unchanged — we never fight a position the user deliberately
    chose. Otherwise the window is clamped fully onto the screen it best
    belongs to (largest overlap, or nearest by centre when it overlaps
    none), so it reappears somewhere visible.

    ``screens`` empty (no display information available) ⇒ *pos* is
    returned unchanged; there's nothing meaningful to clamp against.
    """
    x, y = pos
    w, h = size
    if not screens or w <= 0 or h <= 0:
        return (x, y)

    need_w = min(w, _MIN_VISIBLE_PX)
    need_h = min(h, _MIN_VISIBLE_PX)
    for screen in screens:
        if _overlap(x, w, screen[0], screen[2]) >= need_w and (
            _overlap(y, h, screen[1], screen[3]) >= need_h
        ):
            # Sufficiently visible on this screen; leave it be.
            return (x, y)

    left, top, sw, sh = _target_screen(x, y, w, h, screens)
    nx = _clamp(x, left, max(left, left + sw - w))
    ny = _clamp(y, top, max(top, top + sh - h))
    return (nx, ny)


def _overlap(start: int, length: int, seg_start: int, seg_len: int) -> int:
    """Length of the 1-D overlap between ``[start, start+length)`` and the
    segment ``[seg_start, seg_start+seg_len)`` (0 if disjoint)."""
    lo = max(start, seg_start)
    hi = min(start + length, seg_start + seg_len)
    return max(0, hi - lo)


def _clamp(value: int, lo: int, hi: int) -> int:
    """Constrain *value* to ``[lo, hi]`` (assumes ``lo <= hi``)."""
    return max(lo, min(value, hi))


def _target_screen(x: int, y: int, w: int, h: int, screens: list[Rect]) -> Rect:
    """Pick the screen to clamp onto: largest window overlap, else nearest.

    A window that dangles off the right edge of a still-attached monitor
    should snap back onto *that* monitor (largest overlap). One that's
    off every screen (monitor unplugged) snaps to whichever screen's
    centre is closest — the least-surprising "it came back over here".
    """
    best = screens[0]
    best_overlap = -1
    best_dist = 0
    cx, cy = x + w // 2, y + h // 2
    for screen in screens:
        sl, st, sw, sh = screen
        area = _overlap(x, w, sl, sw) * _overlap(y, h, st, sh)
        scx, scy = sl + sw // 2, st + sh // 2
        dist = (cx - scx) ** 2 + (cy - scy) ** 2
        if area > best_overlap or (area == best_overlap and dist < best_dist):
            best = screen
            best_overlap = area
            best_dist = dist
    return best
