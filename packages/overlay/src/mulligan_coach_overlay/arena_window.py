"""Watch Arena's main window and tell the overlay how to follow it.

The overlay should behave like untapped.gg's: visible *on top of*
Arena, but *not* on top of the user's other apps. If Arena gets
minimised, the overlay minimises too. If Arena is closed, the overlay
hides itself. If a non-Arena app is in focus, the overlay drops out
of the topmost z-order so it can be covered.

Implementation
--------------

We poll Win32 every 250 ms — one ``EnumWindows`` walk if we don't
yet have Arena's HWND, plus a handful of cheap state checks
(``IsIconic``, ``GetForegroundWindow``) once we do. That's
imperceptible compared to the model inference cost and avoids the
complexity of pumping a ``SetWinEventHook`` into Qt's event loop.

The watcher emits a single :class:`pyqtSignal` whenever Arena's
state transitions between four modes:

* ``foreground`` — Arena (or our own overlay) is the active window.
                   Overlay should be visible + topmost + restored.
* ``background`` — a different app is in front. Overlay should be
                   visible but **not** topmost.
* ``minimized``  — Arena is minimised. Overlay minimises too.
* ``absent``     — Arena is not running. Overlay hides.

Non-Windows platforms
---------------------

On non-Windows we have no equivalent Win32 surface and the Arena
client doesn't ship for Linux. The watcher degenerates into a
constant-``foreground`` no-op so the overlay keeps the old
"always on top" behaviour without anyone having to special-case
the platform at the call site.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Iterator
from typing import Final

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

log = logging.getLogger(__name__)


# Poll cadence. 250 ms is fast enough that an Alt-Tab feels instant
# and slow enough that the cost is negligible (each tick is one
# foreground-window read and a handful of state checks).
_POLL_INTERVAL_MS: Final = 250

# Arena's main window title is the literal string "MTGA" on Windows.
# Matching by title is brittle (Arena could change it on update), so
# we match permissively: any visible top-level window whose title
# *starts with* "MTGA". If WotC ever ship a localised title we'll
# need to widen this.
_ARENA_TITLE_PREFIX: Final = "MTGA"

# Win32 ShowWindow nCmdShow constants.
_SW_MINIMIZE: Final = 6
_SW_RESTORE: Final = 9
_SW_SHOWNOACTIVATE: Final = 4

# Win32 SetWindowPos hWndInsertAfter sentinels (negative = no real HWND).
_HWND_TOPMOST: Final = -1
_HWND_NOTOPMOST: Final = -2

# Win32 SetWindowPos uFlags bits.
_SWP_NOSIZE: Final = 0x0001
_SWP_NOMOVE: Final = 0x0002
_SWP_NOACTIVATE: Final = 0x0010

# ``WindowState`` is the discriminator on the state_changed signal.
# Plain strings (not an Enum) because the consumer code matches on
# them in if/elif chains and string literals read more clearly than
# ``WindowState.FOREGROUND`` etc.
WindowState = str
"""One of ``"foreground"``, ``"background"``, ``"minimized"``,
``"absent"``."""


class ArenaWindowWatcher(QObject):
    """Polls Win32 for Arena's window state, emits on change.

    The watcher does not touch the overlay window itself — it only
    publishes the state. The :class:`OverlayWindow` listens and
    decides how to react (topmost on/off, minimise, hide, …).

    A reference to our own overlay HWND can be set via
    :meth:`set_overlay_hwnd` so the watcher treats "user is interacting
    with the overlay" as equivalent to "Arena is foreground" — without
    this, clicking the overlay would flip it into ``background`` mode
    and demote it.
    """

    state_changed = pyqtSignal(str)
    """Emitted whenever the resolved :data:`WindowState` differs from
    the previously-emitted one. The payload is the new state."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._arena_hwnd: int | None = None
        self._overlay_hwnd: int | None = None
        self._last_state: WindowState | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def start(self) -> None:
        """Begin polling. Idempotent."""
        if not self._timer.isActive():
            self._timer.start()
            # Fire one tick immediately so consumers don't have to wait
            # for the first 250ms interval to learn the initial state.
            self._poll()

    def stop(self) -> None:
        """Stop polling. Idempotent."""
        self._timer.stop()

    def last_state(self) -> WindowState | None:
        """Most recently emitted state; ``None`` before the first poll.

        :meth:`start` polls (and emits) synchronously, so callers that
        need the initial state can read this right after calling it —
        no need to subscribe to :data:`state_changed` just to learn
        whether Arena was running at launch.
        """
        return self._last_state

    def set_overlay_hwnd(self, hwnd: int | None) -> None:
        """Tell the watcher which HWND is our own overlay.

        Called once after :meth:`OverlayWindow.show`; lets the
        foreground check treat the overlay as "Arena-adjacent" so
        clicking the overlay doesn't demote it.
        """
        self._overlay_hwnd = hwnd

    @property
    def arena_hwnd(self) -> int | None:
        """Current Arena HWND, if known. Useful for tests / debug."""
        return self._arena_hwnd

    # -----------------------------------------------------------------
    # Polling
    # -----------------------------------------------------------------

    def _poll(self) -> None:
        new_state = self._compute_state()
        if new_state != self._last_state:
            self._last_state = new_state
            self.state_changed.emit(new_state)

    def _compute_state(self) -> WindowState:
        # Non-Windows: always "foreground". The overlay collapses to
        # its previous always-on-top behaviour without any platform
        # checks at the call site.
        if sys.platform != "win32":
            return "foreground"

        # Cache hit fast path: do we still have a valid Arena HWND?
        if self._arena_hwnd is not None and not _is_window(self._arena_hwnd):
            # The window we cached is gone. Reset so we re-scan below.
            self._arena_hwnd = None

        if self._arena_hwnd is None:
            self._arena_hwnd = _find_arena_hwnd()

        if self._arena_hwnd is None:
            return "absent"

        if _is_iconic(self._arena_hwnd):
            return "minimized"

        fg = _foreground_window()
        if fg == self._arena_hwnd or (self._overlay_hwnd is not None and fg == self._overlay_hwnd):
            return "foreground"

        return "background"


# ---------------------------------------------------------------------------
# Win32 surface — guarded so the module imports on non-Windows.
# ---------------------------------------------------------------------------


if sys.platform == "win32":
    # ctypes bindings. We deliberately avoid pywin32 to keep the dep
    # graph minimal — the overlay already pulls in PyQt6 + the
    # recommendation pipeline; adding pywin32 just for ~5 functions is
    # not worth the install-size hit.
    _user32 = ctypes.windll.user32
    _user32.IsWindow.argtypes = [ctypes.c_void_p]
    _user32.IsWindow.restype = ctypes.c_int
    _user32.IsIconic.argtypes = [ctypes.c_void_p]
    _user32.IsIconic.restype = ctypes.c_int
    _user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    _user32.IsWindowVisible.restype = ctypes.c_int
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    _user32.SetWindowPos.restype = ctypes.c_int
    _user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.ShowWindow.restype = ctypes.c_int

    _ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    _user32.EnumWindows.argtypes = [_ENUM_PROC, ctypes.c_void_p]
    _user32.EnumWindows.restype = ctypes.c_int

    def _is_window(hwnd: int) -> bool:
        return bool(_user32.IsWindow(hwnd))

    def _is_iconic(hwnd: int) -> bool:
        return bool(_user32.IsIconic(hwnd))

    def _foreground_window() -> int | None:
        h = _user32.GetForegroundWindow()
        return int(h) if h else None

    def _window_title(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value

    def _iter_top_level_windows() -> Iterator[int]:
        # Use a Python list as the storage cell; the callback appends
        # candidate HWNDs and returns 1 to keep enumerating.
        found: list[int] = []

        def _callback(hwnd: int, _lparam: object) -> int:
            found.append(hwnd)
            return 1

        _user32.EnumWindows(_ENUM_PROC(_callback), 0)
        yield from found

    def _find_arena_hwnd() -> int | None:
        """First visible top-level window whose title starts with "MTGA"."""
        for hwnd in _iter_top_level_windows():
            if not _user32.IsWindowVisible(hwnd):
                continue
            title = _window_title(hwnd)
            if title.startswith(_ARENA_TITLE_PREFIX):
                return hwnd
        return None

    def set_topmost(hwnd: int, *, topmost: bool) -> None:
        """Toggle a window between TOPMOST and NOTOPMOST z-order."""
        if not hwnd:
            return
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
        insert_after = _HWND_TOPMOST if topmost else _HWND_NOTOPMOST
        _user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, flags)

    def show_window(hwnd: int, *, minimize: bool = False, restore: bool = False) -> None:
        """Minimise / restore a window via ``ShowWindow``.

        Restore uses ``SW_SHOWNOACTIVATE`` rather than ``SW_RESTORE``
        when ``restore`` is true and the window is *not* currently
        minimised — this brings the overlay back into a visible /
        topmost-eligible state without stealing focus from Arena.
        """
        if not hwnd:
            return
        if minimize:
            _user32.ShowWindow(hwnd, _SW_MINIMIZE)
        elif restore:
            # SW_RESTORE if currently iconic (minimised); otherwise a
            # non-activating show so we don't steal focus on every
            # background-to-foreground transition.
            cmd = _SW_RESTORE if _is_iconic(hwnd) else _SW_SHOWNOACTIVATE
            _user32.ShowWindow(hwnd, cmd)

else:
    # Non-Windows shims. The watcher's state machine bails out before
    # calling any of these (it returns "foreground" without inspecting
    # any HWND), but we still provide stubs so the module's public
    # surface is uniform and type-checkers don't complain at callsites
    # that branch on ``sys.platform`` themselves.

    def _is_window(hwnd: int) -> bool:  # pragma: no cover - non-Windows only
        return False

    def _is_iconic(hwnd: int) -> bool:  # pragma: no cover - non-Windows only
        return False

    def _foreground_window() -> int | None:  # pragma: no cover - non-Windows only
        return None

    def _find_arena_hwnd() -> int | None:  # pragma: no cover - non-Windows only
        return None

    def set_topmost(hwnd: int, *, topmost: bool) -> None:  # pragma: no cover - non-Windows
        pass

    def show_window(
        hwnd: int, *, minimize: bool = False, restore: bool = False
    ) -> None:  # pragma: no cover - non-Windows
        pass
