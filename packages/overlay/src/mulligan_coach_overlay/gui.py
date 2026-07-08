"""PyQt6 transparent overlay that follows MTG Arena's window.

Architecture
------------

Three Qt objects, one thread of pure event-processing in between:

* :class:`OverlayWindow` (QWidget) — the translucent pane shown over
  Arena. Owns no business logic; renders whatever the worker pushes
  and reacts to window-state events from the Arena watcher.
* :class:`TailerWorker` (QObject) — runs the :class:`LogTailer` poll
  loop on its own :class:`QThread` and emits a Qt signal per
  :class:`CoordinatorOutput`. Also persists any fully-resolved
  ``DeckSubmitted`` to disk so the next overlay launch can fall back
  to it if Arena's submit happened before we started tailing.
* :class:`ArenaWindowWatcher` (QObject) — Win32 poller that tracks
  Arena's foreground / minimised / present state and tells the
  overlay how to behave. See ``arena_window.py``.

Cross-thread state is intentionally tiny — the worker only emits
typed events, the window only listens. No shared mutable data, no
locks, no Qt model/view machinery.

Window behaviour
----------------

* Frameless, translucent rounded-rect panel — we draw our own
  background and a small drag handle.
* Hidden from the Windows taskbar via ``Qt.Tool``.
* **Follows Arena's z-order**: topmost only when Arena (or the
  overlay itself) is the foreground window. When the user Alt-Tabs
  to another app the overlay drops out of the topmost ring so it can
  be covered. When Arena minimises, the overlay minimises too. When
  Arena exits, the overlay hides. This is the behaviour
  untapped.gg's overlay has; without it the panel is annoying to
  work around when doing anything outside Arena.
* **Collapse / expand**: a compact pill (verdict + mulligan-% only)
  vs. the full panel (adds the resolved hand + a debug footer).
  Click the title's collapse button or hit ``Alt+E`` (a Win32
  global hotkey) to toggle. The user doesn't have to close + relaunch
  the overlay every game — collapse it and it stays out of the way.
* **Deck persistence**: each fully-resolved ``DeckSubmitted`` is
  written to ``%LOCALAPPDATA%\\MulliganCoach\\last_deck.json`` (or
  the platform equivalent). On startup we load it and seed the
  coordinator so a missed submission falls back to "previous deck"
  rather than "no deck at all".
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from mulligan_coach_recommend import load_service
from PyQt6.QtCore import (
    QObject,
    QPoint,
    QRect,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import about, arena_window, autostart, feedback, first_run, user_data
from ._frozen import configure_bundle_paths, configure_frozen_logging, running_bundle_version
from .arena_card_db import find_card_database_in
from .arena_paths import default_log_path
from .arena_window import ArenaWindowWatcher
from .auto_update import UpdateRunner
from .auto_update.exe_update import (
    DEFAULT_EXE_VERSION_URL,
    ExeUpdateChecker,
    ExeUpdateResult,
    manual_check_message,
    update_available_message,
)
from .card_index import ArenaCardIndex
from .coordinator import (
    ComputingOutput,
    CoordinatorOutput,
    DeckLoadedOutput,
    MatchResetOutput,
    MissingDataOutput,
    OverlayCoordinator,
    RecommendationOutput,
)
from .deck_persistence import default_persistence_path, load_last_deck, save_last_deck
from .events import DeckSubmitted, MulliganDecisionRequest
from .first_run_dialog import FirstRunDialog
from .log_tailer import LogTailer
from .position_persistence import default_positions_path, load_positions, save_positions
from .screen_geometry import clamp_to_screens
from .stats_html import (
    _KEEP_COLOR,
    _MARGINAL_COLOR,
    _MULL_COLOR,
    _TEXT_MUTED,
    _TEXT_PRIMARY,
)
from .stats_html import build_stats_html as _build_stats_html
from .tray import OverlayTray, create_tray

log = logging.getLogger(__name__)

# Two layouts: full panel and compact pill. The window is the same
# widget — we just toggle child widget visibility and shrink it.
# Width is fixed; height is content-driven in expanded mode (we call
# adjustSize() after rendering) so the panel never grows taller than
# the actual rows of stats. That keeps the Arena mulligan UI as
# uncovered as we can manage.
_NORMAL_WIDTH = 320
# Compact pill: width and height are content-driven (we let
# ``adjustSize()`` shrink the box to the natural verdict-label size),
# so there are no _COMPACT_WIDTH / _COMPACT_HEIGHT constants. The
# previous fixed 220 x 32 box left a noticeable strip of empty space
# to the right of short verdicts like "CLEAR KEEP · mull 1%".
_CORNER_RADIUS = 12
# QWIDGETSIZE_MAX (defined in Qt's qwidget.h) — used to undo a
# setFixedSize when switching from compact back to expanded, so the
# vertical extent becomes content-driven again.
_QT_WIDGETSIZE_MAX = 16_777_215

# Colour palette — colour STRINGS live in ``stats_html`` so the
# Qt-free HTML helpers there can colour-code cells without dragging
# in PyQt6 (the CI runner is headless and can't load libEGL). We
# re-import the names here so the rest of gui.py can keep using the
# leading-underscore convention. The QColor pair below is the only
# Qt-typed colour state — it's drawn directly via QPainter.
_PANEL_BG = QColor(20, 20, 24, 235)
_PANEL_BORDER = QColor(60, 60, 70, 255)

# Amber for the degradation footer — same warning register as the
# website's `.warn` lines, distinct from the verdict colours.
_DEGRADATION_COLOR = "#d9a648"

# Alt+E is the collapse / expand toggle (see the
# :class:`_KeyboardHook` block below for the binding). Picked over
# the previous Ctrl+Shift+M because Arena binds Ctrl combos
# (full-control / pile-selection / etc.) — leaving the toggle on
# Ctrl was eating combos the player needed in-game.
_VK_E = 0x45


# ---------------------------------------------------------------------------
# Worker thread: tail the log, push coordinator outputs
# ---------------------------------------------------------------------------


class TailerWorker(QObject):
    """QObject wrapper around the :class:`LogTailer` poll loop.

    Lives on a dedicated :class:`QThread`. Owns the tailer +
    coordinator; emits :attr:`output` per processed event. The
    :attr:`stopped` signal fires once the loop terminates so the
    main thread can quit cleanly.

    ``on_deck_persisted``, if provided, is called *on the worker
    thread* whenever a deck submission resolves to a complete 40-card
    deck (i.e. the coordinator's response is a
    :class:`DeckLoadedOutput` with ``n_unresolved == 0``). The hook
    should be cheap and safe to run off the main thread — typically a
    JSON write via :func:`deck_persistence.save_last_deck`.
    """

    output = pyqtSignal(object)
    """Carries :class:`CoordinatorOutput` instances. Qt's signal
    type-checker doesn't introspect ``object`` payloads, so the
    handler is responsible for ``isinstance`` dispatch."""

    stopped = pyqtSignal()

    def __init__(
        self,
        tailer: LogTailer,
        coordinator: OverlayCoordinator,
        on_deck_persisted: Callable[[list[int]], None] | None = None,
    ) -> None:
        super().__init__()
        self._tailer = tailer
        self._coordinator = coordinator
        self._on_deck_persisted = on_deck_persisted
        self._stop_requested = False

    @pyqtSlot()
    def run(self) -> None:
        """Block on the tailer's generator until :meth:`stop` is called."""
        # threading.Event is what the tailer's stop_event parameter
        # expects; build one here and flip it from request_stop().
        from threading import Event as _Event

        self._stop_event = _Event()
        try:
            for event in self._tailer.tail(follow=True, stop_event=self._stop_event):
                if self._stop_requested:
                    break
                # Mulligan decision = blocking recommend call. Push a
                # "Computing" state before it so the user sees the UI
                # acknowledge the request immediately, separating
                # simulator latency from Arena-log-delivery latency.
                if isinstance(event, MulliganDecisionRequest):
                    self.output.emit(
                        ComputingOutput(
                            mulligan_count=event.mulligan_count,
                            on_the_play=event.on_the_play,
                        )
                    )
                try:
                    out = self._coordinator.handle_event(event)
                except Exception:
                    log.exception("coordinator raised on event %s", event)
                    continue
                # Persist a fully-resolved deck before emitting the
                # output. Partially-resolved decks aren't useful as a
                # seed for next launch (the coordinator refuses to
                # recommend against them) so we skip those.
                if (
                    isinstance(event, DeckSubmitted)
                    and isinstance(out, DeckLoadedOutput)
                    and out.n_unresolved == 0
                    and self._on_deck_persisted is not None
                ):
                    try:
                        self._on_deck_persisted(event.arena_ids)
                    except Exception:
                        log.exception("on_deck_persisted hook raised")
                if out is not None:
                    self.output.emit(out)
        finally:
            self.stopped.emit()

    @pyqtSlot()
    def request_stop(self) -> None:
        """Ask the tailer to exit at the next poll boundary."""
        self._stop_requested = True
        if hasattr(self, "_stop_event"):
            self._stop_event.set()


# ---------------------------------------------------------------------------
# Global hotkey: low-level Win32 keyboard hook
# ---------------------------------------------------------------------------
#
# Why a low-level hook instead of ``RegisterHotKey`` + a Qt native
# event filter (the previous implementation): on this build of PyQt6
# the ``WM_HOTKEY`` message was not reliably reaching the application-
# wide ``QAbstractNativeEventFilter`` when the registration was tied
# to a ``Qt.Tool`` + ``WindowDoesNotAcceptFocus`` window. The
# registration would succeed, but the toggle never fired in practice.
#
# ``SetWindowsHookEx(WH_KEYBOARD_LL)`` sees every keystroke before
# any window receives it, regardless of focus or window style, and
# delivers it via a plain C callback we control directly. It also
# lets us *swallow* the key (return 1) so Alt+E doesn't reach Arena's
# accelerator handler.
#
# The hook callback runs on the thread that called
# ``SetWindowsHookEx``. We install on the Qt main thread and use a
# queued signal connection so the actual layout toggle happens on the
# next event-loop iteration rather than inside the hook callback.


if sys.platform == "win32":
    _WH_KEYBOARD_LL = 13
    _WM_KEYDOWN = 0x0100
    _WM_SYSKEYDOWN = 0x0104
    _VK_MENU = 0x12  # any Alt (handy with GetAsyncKeyState)

    class _KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = (
            ("vkCode", ctypes.c_uint),
            ("scanCode", ctypes.c_uint),
            ("flags", ctypes.c_uint),
            ("time", ctypes.c_uint),
            ("dwExtraInfo", ctypes.c_void_p),
        )

    # LRESULT (LONG_PTR) is platform-width — c_ssize_t on both x86/x64.
    _LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t,
        ctypes.c_int,
        ctypes.c_ssize_t,
        ctypes.POINTER(_KBDLLHOOKSTRUCT),
    )

    class _KeyboardHook(QObject):
        """Low-level Win32 keyboard hook; emits ``triggered`` on Alt+E.

        The hook callback runs on the thread that installed the hook
        — the Qt main thread — so the connected slot fires
        synchronously inside the callback. Keep slots fast (the
        layout toggle is well under 50 ms) so we don't trip Windows'
        ``LowLevelHooksTimeout`` watchdog and get un-hooked.
        """

        triggered = pyqtSignal()

        def __init__(self, parent: QObject | None = None) -> None:
            super().__init__(parent)
            self._handle: int | None = None
            # Strong reference to the ctypes-wrapped callback so
            # Windows never sees a dangling function pointer.
            self._proc = _LowLevelKeyboardProc(self._callback)

        def install(self) -> bool:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            user32.SetWindowsHookExW.argtypes = [
                ctypes.c_int,
                _LowLevelKeyboardProc,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            user32.SetWindowsHookExW.restype = ctypes.c_void_p
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            # For WH_KEYBOARD_LL the hMod arg must be the module
            # handle of the EXE/DLL containing the proc — the
            # main EXE handle is the standard idiom for in-process
            # hooks.
            h_module = kernel32.GetModuleHandleW(None)
            handle = user32.SetWindowsHookExW(_WH_KEYBOARD_LL, self._proc, h_module, 0)
            if not handle:
                return False
            self._handle = int(handle)
            return True

        def uninstall(self) -> None:
            if self._handle is None:
                return
            user32 = ctypes.windll.user32
            user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
            user32.UnhookWindowsHookEx.restype = ctypes.c_int
            user32.UnhookWindowsHookEx(self._handle)
            self._handle = None

        def _callback(self, n_code: int, w_param: int, l_param: Any) -> int:
            # Always chain to ``CallNextHookEx`` unless we explicitly
            # swallow the key (return 1) — and we only swallow the
            # matched Alt+E down stroke.
            try:
                if n_code >= 0 and w_param in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
                    evt = l_param.contents
                    if evt.vkCode == _VK_E:
                        user32 = ctypes.windll.user32
                        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
                        user32.GetAsyncKeyState.restype = ctypes.c_short
                        if user32.GetAsyncKeyState(_VK_MENU) & 0x8000:
                            self.triggered.emit()
                            return 1
            except Exception:
                log.exception("keyboard hook raised; passing event through")
            user32 = ctypes.windll.user32
            user32.CallNextHookEx.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_ssize_t,
                ctypes.c_void_p,
            ]
            user32.CallNextHookEx.restype = ctypes.c_ssize_t
            return int(
                user32.CallNextHookEx(0, n_code, w_param, ctypes.cast(l_param, ctypes.c_void_p))
            )

else:

    class _KeyboardHook(QObject):
        """Non-Windows stub. ``install`` is a no-op returning False."""

        triggered = pyqtSignal()

        def install(self) -> bool:  # pragma: no cover
            return False

        def uninstall(self) -> None:  # pragma: no cover
            pass


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------


class OverlayWindow(QWidget):
    """Frameless translucent panel that follows Arena's z-order.

    The window starts in "expanded" layout; clicking the collapse
    button (or hitting the global hotkey) toggles to a compact pill
    that shows just the verdict + keep%/mull%. Either layout is
    draggable by clicking anywhere except the close / collapse
    buttons.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        compact_pos: tuple[int, int] | None = None,
        expanded_pos: tuple[int, int] | None = None,
    ) -> None:
        super().__init__(parent)
        # Frameless tool-window with a translucent background.
        # We deliberately do NOT set Qt.WindowStaysOnTopHint here —
        # topmost is managed dynamically by the Arena watcher via
        # Win32 SetWindowPos. Setting it as a Qt flag would force-on
        # topmost any time Qt re-created the window.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # ``WindowDoesNotAcceptFocus`` (set above) makes the overlay an
        # "inactive" window in Qt's eyes — and by default Qt suppresses
        # tooltips on inactive windows, so the compact pill's
        # "Alt+E to expand" tooltip never showed without this flag.
        # ``WA_AlwaysShowToolTips`` is documented for exactly this
        # case: allow tooltips for widgets in inactive windows.
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips)
        self._compact = False
        self._drag_offset: QPoint | None = None
        # Cached payload from the last RecommendationOutput so the
        # compact <-> expanded toggle can re-render without waiting on
        # a fresh sim. Reset on any state-changing event other than
        # a successful recommendation.
        self._last_rec: RecommendationOutput | None = None
        # Remembered window positions per layout. The user typically
        # wants the compact pill parked somewhere out-of-the-way
        # (e.g. the bottom of the screen) and the expanded panel near
        # the mulligan UI — a single shared position would drag one
        # layout to the other's spot on every toggle. Slots are seeded
        # from disk by the caller and persist back on quit; in-session
        # updates happen on drag-release and on layout toggle.
        self._compact_pos: QPoint | None = QPoint(*compact_pos) if compact_pos is not None else None
        self._expanded_pos: QPoint | None = (
            QPoint(*expanded_pos) if expanded_pos is not None else None
        )
        self._build_ui()
        # _apply_layout handles both arms of the position decision:
        # restores the saved spot for the active layout when one
        # exists, otherwise falls back to the layout-specific default
        # (top-right for expanded, bottom-right for compact). No
        # separate post-init placement is needed here.
        self._apply_layout()

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        # Tight margins + spacing — the overlay sits on top of Arena's
        # mulligan-decision UI, so every pixel of vertical space saved
        # is one less card covered. Inner table padding handles row
        # readability independently.
        outer.setContentsMargins(10, 4, 10, 6)
        outer.setSpacing(2)

        # Title row: collapse + settings + close buttons, right-aligned.
        # No "Mulligan Coach" text — the overlay's purpose is obvious
        # from the verdict immediately below, and the title was just
        # eating horizontal space that the window doesn't have to spare.
        self._title_row_widget = QWidget()
        title_row = QHBoxLayout(self._title_row_widget)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(4)
        # Tiny hotkey hint, left of the stretch so it hugs the panel's
        # top-left corner. Teaches the collapse shortcut passively — the
        # compact pill already tooltips "Alt+E to expand", but nothing
        # advertised the hotkey while expanded. Hidden until main()
        # confirms the Win32 hook actually installed
        # (:meth:`show_hotkey_hint`), so we never advertise a shortcut
        # that doesn't work (non-Windows, hook registration failure).
        self._hotkey_hint = QLabel("Alt+E to minimize")
        self._hotkey_hint.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 9px;")
        self._hotkey_hint.setVisible(False)
        title_row.addWidget(self._hotkey_hint)
        title_row.addStretch(1)
        # Collapse toggle. "-" when in normal (collapse-to-compact),
        # square when compact (expand-to-normal). Kept as text rather
        # than an icon so we don't ship binary assets in the wheel.
        self._collapse_btn = QPushButton("-")
        self._collapse_btn.setFlat(True)
        self._collapse_btn.setFixedSize(18, 18)
        self._collapse_btn.setStyleSheet(_TITLE_BTN_STYLE)
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.clicked.connect(self.toggle_compact)
        self._collapse_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_row.addWidget(self._collapse_btn)
        # Settings (gear) button. Always present: the menu mirrors the
        # tray's entries ("Check for updates" / "Setup &
        # troubleshooting…" / "Send feedback…"), so there's something
        # useful behind it even from source, where the Windows-only
        # autostart toggle is hidden. Callback-backed entries appear
        # once main() wires them via enable_update_check /
        # enable_setup — the menu is rebuilt on every open, so late
        # wiring just works.
        # GEAR (U+2699) as the glyph. Same visual register as the
        # collapse / close buttons; the menu opens on click rather
        # than hover so accidental triggers are rare.
        self._on_check_updates: Callable[[], None] | None = None
        self._on_open_setup: Callable[[], None] | None = None
        self._settings_btn = QPushButton("⚙")
        self._settings_btn.setFlat(True)
        self._settings_btn.setFixedSize(18, 18)
        self._settings_btn.setStyleSheet(_TITLE_BTN_STYLE)
        self._settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._settings_btn.clicked.connect(self._open_settings_menu)
        self._settings_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_row.addWidget(self._settings_btn)
        # MULTIPLICATION SIGN (U+00D7) as the close-button glyph. Ruff
        # flags it as ambiguous-vs-lowercase-x; visually it's a clear
        # close icon at the rendered size, so we keep it.
        close_btn = QPushButton("×")  # noqa: RUF001
        close_btn.setFlat(True)
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(_TITLE_BTN_STYLE)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._close_clicked)
        close_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_row.addWidget(close_btn)
        outer.addWidget(self._title_row_widget)

        # Verdict + mulligan-probability row. In expanded mode the
        # verdict and the mull% share a single line — verdict on the
        # left, mull% right-aligned. In compact mode only the verdict
        # label is shown and it carries the whole "verdict · mull X%"
        # string on its own.
        #
        # "Mulligan probability" rather than "Should mulligan" because
        # the imperative form invited misreading ("Should mulligan 23%"
        # → keep, but reads like advice to mull).
        self._verdict_row_widget = QWidget()
        verdict_row = QHBoxLayout(self._verdict_row_widget)
        verdict_row.setContentsMargins(0, 0, 0, 0)
        verdict_row.setSpacing(8)
        self._verdict_label = QLabel("Waiting for mulligan…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: 17px; font-weight: 700;"
        )
        # No word-wrap — the verdict text is always short ("CLEAR
        # KEEP", "Marginal mull", etc.) and turning wrap off lets the
        # mull% sit beside it instead of being pushed to a second line.
        verdict_row.addWidget(self._verdict_label)
        verdict_row.addStretch(1)
        # Empty default: on fresh launch (no Arena events yet) the
        # pane just shows the "Waiting for mulligan…" verdict label
        # without a dangling "Mull —" placeholder next to it. Each
        # render path that needs a placeholder (``_render_computing``,
        # ``_render_missing``) sets it explicitly.
        self._mull_label = QLabel("")
        self._mull_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 12px;")
        self._mull_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        verdict_row.addWidget(self._mull_label)
        outer.addWidget(self._verdict_row_widget)

        # Playability stats (expanded layout only). Mirror of the
        # website's "Why this hand plays out the way it does" panel —
        # mana base + curve hits + per-card playability table — but
        # rendered as a single QLabel with rich-text HTML so we don't
        # have to build a dozen child widgets here.
        self._stats_label = QLabel("")
        self._stats_label.setTextFormat(Qt.TextFormat.RichText)
        self._stats_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 11px;")
        self._stats_label.setWordWrap(True)
        # No vertical stretch — the window resizes to the label's
        # natural height via adjustSize() after every render, so
        # giving the label a stretch factor would just leave dead
        # space when content is short.
        outer.addWidget(self._stats_label)

        # Context footer (expanded layout only).
        self._context_label = QLabel("")
        self._context_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px;")
        outer.addWidget(self._context_label)

        # Degradation footer (expanded layout only). Amber, word-wrapped,
        # hidden when there's nothing to warn about. Surfaces the choice
        # recommendation's `degradations` (no ratings loaded / partial
        # coverage / set unknown to the model / pipeline-version
        # mismatch) so a below-full-fidelity verdict isn't silent.
        self._degradations_label = QLabel("")
        self._degradations_label.setStyleSheet(f"color: {_DEGRADATION_COLOR}; font-size: 10px;")
        self._degradations_label.setWordWrap(True)
        outer.addWidget(self._degradations_label)

    def _position_top_right(self) -> None:
        """Flush top-right of the primary screen.

        Anchors the expanded panel above Arena's mulligan UI with no
        inset from either edge. ``screen.geometry()`` (full screen)
        rather than ``availableGeometry()`` because Arena runs
        fullscreen — the Windows taskbar isn't visible, so we want the
        panel reaching the absolute top-right pixel of the display
        rather than respecting an invisible workingArea boundary.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.geometry()
        # ``QRect.right()`` returns ``x + width - 1`` (last pixel index),
        # so ``right() - width + 1`` lands our left edge exactly where
        # the panel's right edge meets the screen's right edge.
        x = geom.right() - self.width() + 1
        y = geom.top()
        self.move(x, y)

    def _position_bottom_right(self) -> None:
        """Flush right, 8 px above the bottom of the primary screen.

        Same fullscreen-Arena rationale as :meth:`_position_top_right`:
        the pill should sit at the visually-bottom edge of the display,
        not above an invisible Windows taskbar. The 8 px lift gives
        a tiny visual buffer so the pill doesn't kiss the screen
        edge — matches the position the project owner settled on after
        dragging the pill into place.
        """
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geom = screen.geometry()
        x = geom.right() - self.width() + 1
        y = geom.bottom() - self.height() + 1 - 8
        self.move(x, y)

    def _position_default_for_current_layout(self) -> None:
        """Apply the per-layout default position.

        Expanded panel → top-right (near the mulligan UI). Compact pill
        → bottom-right (out of the way during normal play). Called when
        no saved position exists for the active layout — typically the
        very first time the user toggles into that layout.
        """
        if self._compact:
            self._position_bottom_right()
        else:
            self._position_top_right()

    # -----------------------------------------------------------------
    # Compact / expanded layout
    # -----------------------------------------------------------------

    @pyqtSlot()
    def toggle_compact(self) -> None:
        """Flip between full panel and compact pill.

        Captures the current pos in the outgoing layout's slot before
        flipping so the user's "wherever I last left it" preference
        is preserved per layout. The new layout's saved pos (if any)
        is then restored inside :meth:`_apply_layout`.
        """
        self.save_current_position()
        self._compact = not self._compact
        self._apply_layout()

    def save_current_position(self) -> None:
        """Record :meth:`pos` into the slot for the current layout.

        Called on layout toggle, on drag-release, and at app-quit.
        Cheap (just a ``QPoint`` copy) so we can call it liberally
        without measuring whether the position actually changed.
        """
        if self._compact:
            self._compact_pos = QPoint(self.pos())
        else:
            self._expanded_pos = QPoint(self.pos())

    def compact_position(self) -> tuple[int, int] | None:
        """Return the last-known compact-layout position, if any.

        Used by :func:`save_positions` at app-quit. Returns plain
        ``(x, y)`` so we don't leak Qt types into the on-disk schema.
        """
        if self._compact_pos is None:
            return None
        return (self._compact_pos.x(), self._compact_pos.y())

    def expanded_position(self) -> tuple[int, int] | None:
        """Return the last-known expanded-layout position, if any."""
        if self._expanded_pos is None:
            return None
        return (self._expanded_pos.x(), self._expanded_pos.y())

    def _apply_layout(self) -> None:
        outer = self.layout()
        if self._compact:
            # Compact pill: a single line carrying verdict + mull%.
            # Title bar, mull label, stats panel, context footer all
            # hide. Re-expand via the hotkey, the title-bar button
            # (once title is back), or a double-click anywhere on the
            # panel.
            self._title_row_widget.setVisible(False)
            self._mull_label.setVisible(False)
            self._stats_label.setVisible(False)
            self._context_label.setVisible(False)
            self._degradations_label.setVisible(False)
            self._verdict_label.setStyleSheet(
                f"color: {_TEXT_PRIMARY}; font-size: 12px; font-weight: 700;"
            )
            # Tighter padding: the expanded panel's 10/4/10/6 margins
            # were sized to keep the stats table from kissing the
            # rounded corners. With only the verdict label visible we
            # can shave 2 px off each side and 1 px top/bottom without
            # the text feeling crowded.
            if outer is not None:
                outer.setContentsMargins(8, 3, 8, 3)
            # Drop any fixed width/height left over from the expanded
            # layout so adjustSize() below can shrink the pill to the
            # verdict text.
            self.setMinimumSize(0, 0)
            self.setMaximumSize(_QT_WIDGETSIZE_MAX, _QT_WIDGETSIZE_MAX)
            self._collapse_btn.setText("▢")
            # In compact mode the title-bar collapse button is hidden,
            # so the global hotkey is the only quick way back out.
            # A hover tooltip surfaces the binding for users who
            # haven't seen the expanded panel yet.
            #
            # Qt routes hover/tooltip events to the widget directly
            # under the cursor and does NOT auto-propagate an empty
            # tooltip to the parent, so we set the same text on the
            # verdict label (the only visible child in compact mode)
            # as well as on the window itself.
            tip = "Alt+E to expand"
            self.setToolTip(tip)
            self._verdict_label.setToolTip(tip)
        else:
            self._title_row_widget.setVisible(True)
            self._mull_label.setVisible(True)
            self._stats_label.setVisible(True)
            self._context_label.setVisible(True)
            self._degradations_label.setVisible(True)
            if outer is not None:
                outer.setContentsMargins(10, 4, 10, 6)
            # Undo the compact-mode constraints (which had no fixed
            # size but the prior call may have pinned a small box),
            # then re-pin the width only. Height is content-driven
            # below via adjustSize() after the layout has re-rendered.
            self.setMinimumSize(0, 0)
            self.setMaximumSize(_QT_WIDGETSIZE_MAX, _QT_WIDGETSIZE_MAX)
            self.setFixedWidth(_NORMAL_WIDTH)
            self._collapse_btn.setText("-")
            self.setToolTip("")
            self._verdict_label.setToolTip("")
        # Re-render the verdict label so its text matches the new
        # layout — the compact format is single-line "verdict ·
        # X% vs Y%", the expanded one is just the verdict alone.
        if self._last_rec is not None:
            self._render_recommendation_from_cached()
        # Content-driven size for both layouts. Compact has no width
        # pin, so adjustSize() shrinks both dimensions to the natural
        # label size; expanded has setFixedWidth above, so only height
        # changes here to fit the current stats-panel row count. Has
        # to run AFTER the render above so the rich-text label heights
        # are up to date when adjustSize asks for them.
        self.adjustSize()
        # Move to the saved position for the new layout, if any. The
        # caller (toggle_compact or __init__) is responsible for
        # having saved the *previous* layout's position before we
        # arrive here. When no position is saved for this layout we
        # fall back to the layout-specific default — top-right for
        # expanded, bottom-right for compact. The defaults are picked
        # to land the layout in its natural "home": expanded sits
        # above the mulligan UI for reading, compact tucks away in
        # the bottom-right where it doesn't cover anything important
        # during normal play. The user can still drag either layout
        # anywhere and the new position is persisted on quit.
        target_pos = self._compact_pos if self._compact else self._expanded_pos
        if target_pos is not None:
            # Clamp to a currently-attached screen: a position saved on a
            # monitor that's since been unplugged (or a rearranged /
            # lower-res desktop) would otherwise place this frameless,
            # taskbar-hidden window off every display — invisible and
            # un-grabbable. clamp_to_screens leaves a deliberately-parked
            # position untouched and only rescues one that's stranded.
            self.move(self._clamp_to_screens(target_pos))
        else:
            self._position_default_for_current_layout()

    def _clamp_to_screens(self, pos: QPoint) -> QPoint:
        """Return *pos* nudged onto an attached screen if it's stranded.

        Pure geometry lives in :func:`screen_geometry.clamp_to_screens`;
        here we just marshal Qt's ``QScreen.geometry()`` rectangles into
        the plain-tuple form it expects and back.
        """
        rects = [
            (g.left(), g.top(), g.width(), g.height())
            for g in (screen.geometry() for screen in QGuiApplication.screens())
        ]
        x, y = clamp_to_screens((pos.x(), pos.y()), (self.width(), self.height()), rects)
        return QPoint(x, y)

    # -----------------------------------------------------------------
    # Paint event: rounded-rect background with a subtle border
    # -----------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent | None) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRect(0, 0, self.width(), self.height())
            painter.setBrush(_PANEL_BG)
            painter.setPen(_PANEL_BORDER)
            painter.drawRoundedRect(rect, _CORNER_RADIUS, _CORNER_RADIUS)
        finally:
            painter.end()

    # -----------------------------------------------------------------
    # Drag-to-move + double-click expand
    # -----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Capture the offset from the window top-left to the click
            # point; move() math in the move-event keeps that offset.
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:
        if event is None or self._drag_offset is None:
            return
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            # Snapshot the (possibly newly-dragged) position into the
            # current layout's slot so the next toggle restores
            # exactly here. Safe to do unconditionally; a click that
            # didn't actually drag just re-writes the same coords.
            self.save_current_position()
            self._drag_offset = None
            event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:
        """Double-click anywhere on the panel toggles compact mode.

        Provides a discoverable alternative to the global hotkey for
        users who don't know the binding.
        """
        if event is None:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_compact()
            event.accept()

    # -----------------------------------------------------------------
    # Arena window state -> overlay visibility / z-order
    # -----------------------------------------------------------------

    @pyqtSlot(str)
    def on_arena_state_changed(self, state: str) -> None:
        """React to :class:`ArenaWindowWatcher` transitions.

        See ``arena_window.py`` for the four state values and what
        each means. Visibility uses Qt; topmost toggling uses Win32
        directly so Qt's flag bookkeeping doesn't fight us.
        """
        log.debug("arena state -> %s", state)
        if state == "absent":
            self.hide()
            return
        if state == "minimized":
            if not self.isMinimized():
                self.showMinimized()
            return
        # foreground or background: ensure we're shown and (un)restored.
        if self.isMinimized():
            self.showNormal()
        elif self.isHidden():
            self.show()
        if sys.platform == "win32":
            arena_window.set_topmost(int(self.winId()), topmost=(state == "foreground"))

    # -----------------------------------------------------------------
    # Recommendation rendering — unchanged from prior version
    # -----------------------------------------------------------------

    @pyqtSlot(object)
    def apply_output(self, payload: object) -> None:
        """Update the UI from a :class:`CoordinatorOutput`.

        :class:`DeckLoadedOutput` is intentionally *not* rendered.
        The recommendation pipeline now keys on the choice model
        rather than a separate win-vs-mull sim comparison, so the
        user doesn't need a per-deck "deck ready" handshake — the
        first mulligan event is the meaningful trigger. The worker
        still receives the output (to drive disk persistence of the
        last-seen deck), but it's silent in the UI.
        """
        output = cast(CoordinatorOutput, payload)
        if isinstance(output, RecommendationOutput):
            self._render_recommendation(output)
        elif isinstance(output, ComputingOutput):
            self._render_computing(output)
        elif isinstance(output, DeckLoadedOutput):
            # Silent — we still track decks internally for the
            # simulator, but the user-facing surface only updates on
            # mulligan / reset / missing-data events.
            return
        elif isinstance(output, MissingDataOutput):
            self._last_rec = None
            self._render_missing(output)
        elif isinstance(output, MatchResetOutput):
            self._last_rec = None
            self._render_reset()

    def _render_recommendation(self, output: RecommendationOutput) -> None:
        """Render a finalised recommendation in either layout."""
        self._last_rec = output
        self._render_recommendation_from_cached()

    def _render_recommendation_from_cached(self) -> None:
        """Re-render the cached recommendation. Used on layout toggle
        so the verdict text picks up the compact / expanded format
        without waiting on a fresh sim."""
        output = self._last_rec
        if output is None:
            return
        rec = output.recommendation
        assert rec is not None  # invariant of RecommendationOutput
        short_verdict, verdict_color = _verdict_display(rec.verdict)
        # Single "should-mulligan" percentage in [0, 100]. The choice
        # model predicts P(skilled player keeps); the displayed number
        # is 100 - that, rounded to a single percent so the user reads
        # one stable digit count rather than 47.3% vs 51.8%.
        mull_pct = rec.mulligan_percent
        degradations = tuple(rec.degradations)
        if self._compact:
            # Compact pill: "Clear keep · mull 12%". One signed number
            # is enough once the user knows the model is reporting a
            # mulligan tendency, not two arms. A trailing " ⚠" flags a
            # degraded recommendation — no room for prose in the pill.
            warn = " ⚠" if degradations else ""
            text = f"{short_verdict} · mull {mull_pct:.0f}%{warn}"
            self._verdict_label.setText(text)
            self._verdict_label.setStyleSheet(
                f"color: {verdict_color}; font-size: 12px; font-weight: 700;"
            )
        else:
            self._verdict_label.setText(short_verdict)
            self._verdict_label.setStyleSheet(
                f"color: {verdict_color}; font-size: 17px; font-weight: 700;"
            )
            # Compact label form because it shares a row with the
            # (much larger) verdict — "Mull <b>23%</b>" reads as
            # "mulligan probability 23%" at a glance once the user
            # knows the panel.
            self._mull_label.setText(f"Mull <b>{mull_pct:.0f}%</b>")
            # No standalone "Hand: ..." line — the per-card playability
            # table inside the stats panel already shows every spell
            # in the opening hand, and the line was eating vertical
            # space the user wanted back. Lands aren't surfaced here
            # but their count is implicit in the turn-grid land row.
            self._stats_label.setText(_build_stats_html(rec.explanation))
            play_draw = "play" if output.on_the_play else "draw"
            self._context_label.setText(
                f"mull #{output.mulligan_count} · on the {play_draw} · "
                f"set {output.primary_set or '?'} · based on 17Lands data"
            )
            # Degradation footer: joined with "  ·  " so several fit on
            # a few wrapped lines. Empty string collapses the label.
            self._degradations_label.setText("  ·  ".join(degradations))
        # Re-size to fit the new content. In expanded mode the per-card
        # row count varies by hand, so a fixed height would either
        # truncate or leave dead space; in compact mode the verdict
        # string changes width too ("Running simulation…" vs "CLEAR
        # KEEP · mull 1%"), and the pill should shrink/grow to match
        # rather than always sit at the widest text's box.
        self.adjustSize()

    def _render_computing(self, output: ComputingOutput) -> None:
        """Render the "running simulation" placeholder."""
        # The recommendation that's about to be produced doesn't exist
        # yet, so the layout-toggle re-render shouldn't bring back a
        # stale verdict either.
        self._last_rec = None
        play_draw = "play" if output.on_the_play else "draw"
        msg = "Running simulation…"
        if self._compact:
            self._verdict_label.setText(msg)
            self._verdict_label.setStyleSheet(
                f"color: {_MARGINAL_COLOR}; font-size: 12px; font-weight: 700;"
            )
        else:
            self._verdict_label.setText(msg)
            self._verdict_label.setStyleSheet(
                f"color: {_MARGINAL_COLOR}; font-size: 17px; font-weight: 700;"
            )
            self._mull_label.setText("Mull —")
            self._stats_label.setText("")
            self._context_label.setText(
                f"mull #{output.mulligan_count} · on the {play_draw} · computing…"
            )
            self._degradations_label.setText("")
        self.adjustSize()

    def _render_missing(self, output: MissingDataOutput) -> None:
        # Re-use the stats label for the error reason; it's the only
        # widget with word-wrap + a usable height in the expanded
        # layout. The previous dedicated hand-label widget was dropped
        # to claw vertical space back.
        font_size = 12 if self._compact else 15
        self._verdict_label.setText("Can't recommend")
        self._verdict_label.setStyleSheet(
            f"color: {_MARGINAL_COLOR}; font-size: {font_size}px; font-weight: 700;"
        )
        self._mull_label.setText("Mull —")
        self._stats_label.setText(output.reason)
        self._context_label.setText(f"({output.what})")
        self._degradations_label.setText("")
        self.adjustSize()

    def _render_reset(self) -> None:
        font_size = 12 if self._compact else 15
        self._verdict_label.setText("Waiting for next match…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: {font_size}px; font-weight: 700;"
        )
        # No "Mull —" placeholder in the idle state — between matches
        # there's nothing to bucket, so the dash is just visual noise
        # next to the "Waiting…" text. The other render paths
        # (computing / missing) keep the dash because they pair with
        # a concrete verdict, not an idle one.
        self._mull_label.setText("")
        self._stats_label.setText("")
        self._context_label.setText("")
        self._degradations_label.setText("")
        self.adjustSize()

    def _close_clicked(self) -> None:
        QApplication.quit()

    # -----------------------------------------------------------------
    # Settings menu (gear button)
    # -----------------------------------------------------------------

    def enable_update_check(self, on_check: Callable[[], None]) -> None:
        """Reveal the gear menu's "Check for updates" entry.

        Same contract as :meth:`tray.OverlayTray.enable_update_check`:
        main() calls this only when an EXE update checker is
        configured, and *on_check* kicks the network check onto a
        worker thread. Until wired, the menu simply omits the entry.
        """
        self._on_check_updates = on_check

    def enable_setup(self, on_open: Callable[[], None]) -> None:
        """Reveal the gear menu's "Setup & troubleshooting…" entry.

        Same contract as :meth:`tray.OverlayTray.enable_setup`:
        *on_open* re-assesses the setup and shows the first-run
        wizard dialog.
        """
        self._on_open_setup = on_open

    def show_hotkey_hint(self) -> None:
        """Reveal the "Alt+E to minimize" title-row hint.

        Called by main() only after the global Win32 hotkey hook
        reports a successful install.
        """
        self._hotkey_hint.setVisible(True)

    def _open_settings_menu(self) -> None:
        """Pop the title-bar settings menu under the gear button.

        The menu mirrors the tray icon's entries so everything is
        reachable from the overlay itself — users mid-draft shouldn't
        have to hunt for the tray icon. It's rebuilt from scratch on
        every open: the autostart checked state is re-read from
        :func:`autostart.is_enabled` (so an external registry change
        is reflected without restarting), and entries whose callbacks
        aren't wired yet are simply absent.
        """
        menu = QMenu(self)
        # ``QMenu.addAction(str)`` is typed as ``QAction | None`` in
        # PyQt6's stubs but never actually returns None for the
        # text-only overload — hence the asserts, which also narrow
        # the type for mypy.
        if self._on_check_updates is not None:
            check_action = menu.addAction("Check for updates")
            assert check_action is not None
            on_check = self._on_check_updates
            check_action.triggered.connect(lambda _checked=False: on_check())
        if self._on_open_setup is not None:
            setup_action = menu.addAction("Setup && troubleshooting…")
            assert setup_action is not None
            on_setup = self._on_open_setup
            setup_action.triggered.connect(lambda _checked=False: on_setup())
        feedback_action = menu.addAction("Send feedback…")
        assert feedback_action is not None
        feedback_action.triggered.connect(lambda _checked=False: self._open_feedback())
        about_action = menu.addAction("About Mulligan Coach")
        assert about_action is not None
        about_action.triggered.connect(lambda _checked=False: self._open_about())
        if autostart.supported():
            menu.addSeparator()
            action = menu.addAction("Start with Windows")
            assert action is not None
            action.setCheckable(True)
            action.setChecked(autostart.is_enabled())
            action.triggered.connect(self._toggle_autostart)
        # Anchor the menu's top-right corner under the gear button's
        # bottom-right corner so it opens "into" the panel rather than
        # off the right edge of the screen for top-right-anchored
        # users (the default position).
        button = self._settings_btn
        anchor = button.mapToGlobal(button.rect().bottomRight())
        # QMenu has no built-in "open with right edge here" helper, so
        # we shift left by the menu's size hint after a preliminary
        # show. Computing the size pre-show requires ``sizeHint()``.
        menu_w = menu.sizeHint().width()
        menu.exec(QPoint(anchor.x() - menu_w, anchor.y()))

    def _open_feedback(self) -> None:
        """Open the feedback form (or Issues fallback) in the browser.

        Mirrors ``tray.OverlayTray._open_feedback``: the URL is built
        by the tested, Qt-free :mod:`feedback` module; only the
        ``openUrl`` call lives here.
        """
        url = feedback.feedback_url()
        log.info("opening feedback URL: %s", url)
        QDesktopServices.openUrl(QUrl(url))

    def _open_about(self) -> None:
        """Show the About box (version + FCP disclaimer + data attributions).

        Mirrors ``tray.OverlayTray._open_about``: the text is built by the
        tested, Qt-free :mod:`about` module; only the ``QMessageBox`` lives
        here. Parented to the overlay window so it centres over the panel.
        """
        QMessageBox.about(self, about.ABOUT_TITLE, about.about_text())

    @pyqtSlot(bool)
    def _toggle_autostart(self, checked: bool) -> None:
        """Apply the user's choice to the registry, ignoring read-back errors.

        Failures already log via the helper. The UI state will catch
        up on the next menu open (which re-reads ``is_enabled``), so
        we don't need to roll the action's checked state back here.
        """
        if checked:
            autostart.enable()
        else:
            autostart.disable()


# Shared close / collapse button style. Defined once to avoid two
# slightly-divergent copies drifting apart on tweaks.
_TITLE_BTN_STYLE = (
    f"QPushButton {{ color: {_TEXT_MUTED}; font-size: 16px; "
    f"font-weight: 600; border: none; background: transparent; }}"
    f"QPushButton:hover {{ color: {_TEXT_PRIMARY}; }}"
)


# ---------------------------------------------------------------------------
# Verdict label / color mapping
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Stats panel — implementation lives in :mod:`stats_html` (Qt-free so
# CI can unit-test it without libEGL). gui.py re-exports the entry
# point above; nothing further needed here.
# ---------------------------------------------------------------------------


def _verdict_display(verdict: str) -> tuple[str, str]:
    """Human label + colour for one of the five verdict tags.

    Labels use "mull" rather than "mulligan" so they fit on a single
    line in the compact pill at 12px and don't need to wrap in the
    expanded panel either. ``borderline`` is grey (neither the keep
    green nor the amber of the marginal bands) — the model withholds
    judgement on these hands; elite players mull about half of them.
    """
    if verdict == "clear_keep":
        return "CLEAR KEEP", _KEEP_COLOR
    if verdict == "marginal_keep":
        return "Marginal keep", _MARGINAL_COLOR
    if verdict == "borderline":
        return "Borderline", _TEXT_MUTED
    if verdict == "marginal_mulligan":
        return "Marginal mull", _MARGINAL_COLOR
    if verdict == "clear_mulligan":
        return "CLEAR MULL", _MULL_COLOR
    return verdict, _TEXT_PRIMARY


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """``uv run mulligan-coach-overlay`` entry point.

    Boots the QApplication, builds the card index and recommendation
    service (slow — a couple of seconds while the model loads), seeds
    the coordinator with the previous-session deck if one is on disk,
    starts the tailer worker on a background QThread, installs the
    Arena window watcher, registers the collapse hotkey, and shows
    the overlay panel. ``argv`` mirrors :func:`sys.argv`.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # When frozen by PyInstaller, point the upstream data + model
    # path resolvers at the bundled copies. No-op when running from
    # source. See ``_frozen.configure_bundle_paths`` for details.
    configure_bundle_paths()
    # The frozen .exe is GUI-only (console=False in the spec), so
    # stdout/stderr are dropped. Attach a rotating file handler under
    # %LOCALAPPDATA%\MulliganCoach\logs so the user can inspect or
    # report errors. No-op from source.
    configure_frozen_logging()
    # Default-on autostart for fresh installs: the FIRST time a
    # frozen EXE launches on this account, write the Start-with-
    # Windows registry entry so the friend doesn't have to discover
    # the gear-menu toggle. A marker file in the user state dir
    # records that we did this; subsequent launches leave the
    # registry alone so a deliberate un-tick from the gear menu
    # sticks. No-op from source (autostart.supported() is False).
    autostart.enable_default_if_first_run(user_data.user_state_root())
    # Entries written by older builds lack the --autostart flag (their
    # login launches would show the manual-launch tray balloon every
    # boot); rewrite ours to the current format if needed.
    autostart.ensure_entry_current()

    # Strip our private --autostart flag (baked into the registry Run
    # entry) before Qt parses the arguments. Whether it was present
    # drives the tray launch-balloon decision below.
    raw_argv = list(argv) if argv is not None else list(sys.argv)
    qt_argv, autostart_launch = autostart.pop_autostart_flag(raw_argv)

    app = QApplication(qt_argv)
    # Tray-app lifetime: Qt's default quits when the last visible window
    # closes. The overlay hides itself whenever Arena isn't running, so
    # with Arena closed a secondary window (the setup wizard, an update
    # dialog) is often the ONLY visible window — closing it would kill
    # the whole app. Quitting is explicit only: the overlay's ✕ button
    # and the tray's "Quit Mulligan Coach" both call QApplication.quit().
    app.setQuitOnLastWindowClosed(False)
    # Tooltip styling MUST live at the QApplication stylesheet level —
    # QToolTip is its own top-level window, not a child of the
    # OverlayWindow, so a widget-scoped stylesheet doesn't reach it.
    # The Windows default (greyish bg + light text) was unreadable on
    # the dark overlay; this pins a dark panel + bright text that
    # matches the rest of the overlay's palette.
    app.setStyleSheet(
        "QToolTip {"
        f" color: {_TEXT_PRIMARY};"
        " background-color: #141418;"
        " border: 1px solid #3c3c46;"
        " padding: 4px 6px;"
        " font-size: 11px;"
        "}"
    )

    # Tray icon first — before the slow model load — so a manual
    # launch shows *some* sign of life within a second even though
    # the overlay window won't appear until Arena does. Also the only
    # Quit / Start-with-Windows surface while the window is hidden.
    tray = create_tray(app)
    if tray is not None:
        tray.show()

    # Resolve Arena's log path + persisted first-run state up front. A
    # card-database directory the user pinned via the wizard's folder
    # picker (when auto-detection failed) feeds the card-index build,
    # and the same log path drives the setup assessment wired in below.
    log_path = default_log_path()
    first_run_path = first_run.default_first_run_path()
    fr_state = first_run.load_first_run_state(first_run_path)
    user_card_db = (
        find_card_database_in(Path(fr_state.card_db_dir)) if fr_state.card_db_dir else None
    )

    log.info("loading card index + recommendation service…")
    # arena_db_path=None lets ArenaCardIndex auto-detect the CardDatabase
    # (now including Epic Games Store + log-derived install locations);
    # a user-pinned dir overrides that.
    card_index = ArenaCardIndex.build(arena_db_path=user_card_db)
    service = load_service(card_index.loaded_sets)
    coordinator = OverlayCoordinator(card_index, service)

    # Seed the coordinator with the previous session's deck (if any)
    # so a missed Arena submission doesn't leave us with no deck. We
    # do this BEFORE starting the tailer — that way the first Arena
    # event arriving is layered on top of a deck that's already there.
    persistence_path = default_persistence_path()
    seeded_output = _seed_from_disk(coordinator, persistence_path)

    log.info("tailing %s", log_path)
    tailer = LogTailer(log_path, start_at_end=True)

    # Load the per-layout positions saved on the previous quit. Either
    # arm may be absent — the OverlayWindow falls back to its top-right
    # default for any slot that comes back None.
    positions_path = default_positions_path()
    saved_positions = load_positions(positions_path)
    window = OverlayWindow(
        compact_pos=saved_positions.compact,
        expanded_pos=saved_positions.expanded,
    )
    thread = QThread()
    worker = TailerWorker(
        tailer,
        coordinator,
        on_deck_persisted=lambda ids: save_last_deck(persistence_path, ids),
    )
    worker.moveToThread(thread)
    # Signal wiring:
    # * When the thread starts, the worker's run() method begins
    #   tailing. This is the standard Qt "worker on a thread" pattern.
    # * Each output payload from the worker is delivered to the
    #   window's apply_output slot on the main (GUI) thread, since
    #   the signal/slot connection across threads queues into the
    #   receiver's event loop.
    thread.started.connect(worker.run)
    worker.output.connect(window.apply_output)

    # Arena window watcher: follow Arena's z-order and minimised state.
    watcher = ArenaWindowWatcher()
    watcher.state_changed.connect(window.on_arena_state_changed)

    # Global hotkey: Alt+E to toggle compact / expanded. The hook
    # callback runs on the Qt main thread, so the slot fires
    # synchronously inside the callback; ``toggle_compact`` is well
    # under Windows' LowLevelHooksTimeout watchdog so this is safe.
    hotkey_hook = _KeyboardHook()
    hotkey_hook.triggered.connect(window.toggle_compact)

    # Auto-update: poll the configured manifest URL at launch and on
    # a slow timer afterwards, refreshing ratings / parsed cards /
    # the choice model in-place when newer versions are available.
    # Returns ``None`` (and logs) when no manifest URL is configured.
    update_timer = _install_auto_update(service, coordinator)

    # EXE update *notification* (notify-only): poll the exe_version.json
    # sidecar and, when a newer build is published, show a tray balloon
    # + "Download update" entry that open the release page. Needs the
    # tray as its UI surface (balloons), so skipped on desktops without
    # one. The overlay's gear menu gets a matching "Check for updates"
    # entry driving the same controller.
    exe_update = _install_exe_update_check(tray) if tray is not None else None
    if exe_update is not None:
        controller = exe_update[0]
        window.enable_update_check(lambda: controller.check_async(manual=True))

    # First-run wizard: assess the Detailed-Logs / Arena / card-database
    # setup and, only when something needs fixing and the user hasn't
    # been onboarded before, pop a guide dialog. Always wires the tray's
    # and the gear menu's "Setup & troubleshooting…" entries so it stays
    # reachable later. Held in a local so the QDialog isn't
    # garbage-collected.
    first_run_dialog = _install_first_run_wizard(  # noqa: F841 — keeps the dialog alive
        tray, window, log_path=log_path, first_run_path=first_run_path, state=fr_state
    )

    def _on_app_quit() -> None:
        # IMPORTANT: don't rely on a queued ``worker.stopped → thread.quit``
        # connection here — the slot would be dispatched to the main
        # thread's event loop, but we're about to block the main thread
        # in ``thread.wait()``, so the queued quit() never fires and
        # the wait deadlocks (then force-terminates at the timeout).
        # Calling thread.quit() directly posts the quit event to the
        # QThread's own event loop, where it does run.
        if update_timer is not None:
            update_timer.stop()
        if exe_update is not None:
            exe_update[1].stop()
        # Hide the tray icon explicitly — Windows otherwise leaves a
        # ghost icon in the tray until the user mouses over it.
        if tray is not None:
            tray.hide()
        watcher.stop()
        hotkey_hook.uninstall()
        worker.request_stop()
        thread.quit()
        if not thread.wait(2000):
            log.warning("tailer thread did not stop within 2s; forcing")
            thread.terminate()
        service.shutdown()
        # Persist the per-layout window positions for the next launch.
        # Snapshot the *current* position into the active layout's
        # slot first so a drag-then-quit-without-toggle still sticks.
        try:
            window.save_current_position()
            save_positions(
                positions_path,
                compact=window.compact_position(),
                expanded=window.expanded_position(),
            )
        except Exception:
            log.exception("could not persist overlay positions")

    app.aboutToQuit.connect(_on_app_quit)

    thread.start()
    window.show()

    # Now that the window has a native HWND, set up Win32-only hooks.
    # winId() returns 0 before the window is realised; calling show()
    # forces realisation so this is safe to do post-show.
    if sys.platform == "win32":
        hwnd = int(window.winId())
        watcher.set_overlay_hwnd(hwnd)
        if hotkey_hook.install():
            log.info("global hotkey installed: Alt+E toggles compact mode")
            window.show_hotkey_hint()
        else:
            log.warning(
                "could not install Alt+E low-level keyboard hook; "
                "use the title-bar collapse button or double-click instead"
            )

    watcher.start()

    # Manual launch with Arena closed: the watcher's initial poll just
    # hid the window, so without feedback the launch looks like it
    # failed — show the tray balloon. Autostart launches stay silent
    # (a balloon at every login reads as nagging), and launches with
    # Arena already running don't need one (the panel is visible).
    if tray is not None and not autostart_launch and watcher.last_state() == "absent":
        tray.show_started_message()

    # We still call apply_output for the seeded deck so any future
    # non-DeckLoadedOutput shapes coming back from _seed_from_disk
    # render — but the current DeckLoadedOutput path is silent in the
    # UI, so this is a no-op on the common case. The seed itself
    # already populated the coordinator above.
    if seeded_output is not None:
        window.apply_output(seeded_output)

    return app.exec()


# Default manifest URL used when ``MULLIGAN_COACH_MANIFEST_URL`` is
# not set in the environment. Pointed at the floating ``data-current``
# release on the public ``mulligan_coach_data`` repo, which the
# publisher CLI (``packages/overlay/packaging/publish_data_release.py``)
# keeps up to date. The split exists because the main ``mulligan_coach``
# repo is private — the auto-updater needs anonymous downloads so
# friends running the overlay don't carry a GitHub token.
#
# Power users can override via the env var (e.g. to point at a fork
# or a local manifest while testing).
_DEFAULT_MANIFEST_URL = "https://github.com/vonbeschwitz/mulligan_coach_data/releases/download/data-current/manifest.json"


def _install_auto_update(service: Any, coordinator: OverlayCoordinator) -> QTimer | None:
    """Wire the auto-update runner onto a background QTimer.

    Returns the timer so the caller can stop it at app shutdown;
    ``None`` when auto-update is disabled (env var explicitly set
    to an empty string).

    Strategy: a single :class:`QTimer` running on the GUI thread
    dispatches every N hours; each tick spawns a fresh daemon
    :class:`threading.Thread` to do the slow network + disk I/O.
    The threads use the reload callbacks below — which are already
    thread-safe (see slice 2's ``_reload_lock``) — to invoke
    ``service.reload_*`` and ``coordinator.replace_card_index``
    inline. No Qt signals required: the reload hooks themselves
    are the cross-thread interface.

    The first check fires shortly after startup (4 s, so the
    overlay is fully up and responsive before we hit the network),
    and a periodic check runs every 6 hours. 6 h is a good fit for
    the cadence the user described — weekly+ ratings refreshes,
    with more frequent updates early in a format — without being
    chatty in steady state.
    """
    manifest_url = os.environ.get("MULLIGAN_COACH_MANIFEST_URL", _DEFAULT_MANIFEST_URL).strip()
    if not manifest_url:
        log.info("auto-update disabled (MULLIGAN_COACH_MANIFEST_URL is empty)")
        return None

    def _rebuild_card_index_and_swap(set_codes: set[str]) -> None:
        """Reload parsed-cards JSONs into a fresh ArenaCardIndex.

        Rebuilding the index is fast (single-digit ms even with all
        four sets) so we don't bother with incremental updates;
        a full rebuild is simpler and avoids subtle correctness
        traps around set-overlap.
        """
        log.info("auto-update: rebuilding card index for refreshed sets %s", sorted(set_codes))
        new_index = ArenaCardIndex.build()
        coordinator.replace_card_index(new_index)

    def _reload_choice_model_named(name: str) -> None:
        """Reload only when the manifest refreshed the production model.

        We track the legacy win model separately in slice 2's API;
        the auto-updater's ``reload_model`` callback gets the manifest
        artifact's ``name``, so we filter here. The production model
        ships under the version-neutral slot ``choice_prod`` — the
        ``choice_`` prefix test catches it (and any future
        ``choice_*`` variant) without a code change.
        """
        if name.startswith("choice_"):
            service.reload_choice_model()
        else:
            log.info("auto-update: ignoring unknown model %r", name)

    runner = UpdateRunner(
        manifest_url=manifest_url,
        user_data_root=user_data.user_data_root(),
        user_models_root=user_data.user_models_root(),
        reload_ratings=service.reload_ratings,
        reload_parsed_cards=_rebuild_card_index_and_swap,
        reload_model=_reload_choice_model_named,
    )

    def _run_once_off_thread() -> None:
        """Kick the runner on a daemon thread.

        Daemon = doesn't block process exit. The runner can take up
        to ~30 s on a slow connection (manifest fetch + a couple of
        downloads). Long enough that we definitely don't want it on
        the GUI thread.
        """

        def _do_run() -> None:
            try:
                report = runner.run()
            except Exception:
                log.exception("auto-update: runner raised unexpectedly")
                return
            if report.status == "failed":
                log.warning("auto-update: %s", report.manifest_error)
                return
            if report.any_refreshed:
                log.info(
                    "auto-update: refreshed ratings=%s parsed_cards=%s models=%s",
                    sorted(report.refreshed_ratings),
                    sorted(report.refreshed_parsed_cards),
                    sorted(report.refreshed_models),
                )
            else:
                log.info("auto-update: nothing to do (all artifacts up-to-date)")

        threading.Thread(target=_do_run, name="auto-update", daemon=True).start()

    # First check shortly after startup so the overlay is fully up
    # and the user isn't staring at a busy spinner during launch.
    QTimer.singleShot(4_000, _run_once_off_thread)

    timer = QTimer()
    timer.setInterval(6 * 60 * 60 * 1000)  # 6 hours
    timer.timeout.connect(_run_once_off_thread)
    timer.start()
    log.info("auto-update: scheduled every 6h against %s", manifest_url)
    return timer


# Default sidecar URL used when ``MULLIGAN_COACH_EXE_VERSION_URL`` is
# not set. Points at the ``exe_version.json`` that
# ``packages/overlay/packaging/publish_exe_release.py`` uploads to the
# floating ``exe-latest`` release on the public ``mulligan_coach_data``
# repo. Set the env var empty to disable the EXE update check entirely
# (mirrors ``MULLIGAN_COACH_MANIFEST_URL`` for the data channel).
_DEFAULT_EXE_VERSION_URL = DEFAULT_EXE_VERSION_URL

_EXE_UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000  # 6 hours


class _ExeUpdateController(QObject):
    """Runs EXE update checks off-thread and surfaces results on the tray.

    Notify-only (owner decision 2026-07-03): a check never downloads or
    replaces the running executable — it only fetches the tiny
    ``exe_version.json`` sidecar and, when a newer build is published,
    shows a tray balloon + "Download update" menu entry that open the
    release page.

    ``ExeUpdateChecker.check`` does a blocking network fetch, so it runs
    on a short-lived daemon thread; the result comes back to the GUI
    thread through ``_result_ready`` (a queued signal) where
    :meth:`_on_result` decides what — if anything — to show. A failed
    check is folded into an ``unknown`` result upstream and never
    disturbs the overlay.
    """

    # (ExeUpdateResult, manual). ``object`` because ExeUpdateResult is a
    # plain dataclass, not a QObject — Qt marshals it as an opaque
    # Python object across the thread boundary.
    _result_ready = pyqtSignal(object, bool)

    def __init__(
        self,
        checker: ExeUpdateChecker,
        tray: OverlayTray,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._checker = checker
        self._tray = tray
        # Version we've already shown an *automatic* balloon for this
        # session, so the 6 h auto-check doesn't re-nag every tick.
        # Manual checks always give feedback regardless.
        self._notified_version: str | None = None
        self._result_ready.connect(self._on_result)

    def check_async(self, *, manual: bool) -> None:
        """Kick a check on a daemon thread; result arrives via the signal."""

        def _run() -> None:
            try:
                result = self._checker.check()
            except Exception:
                # ``check`` is contractually no-raise, but a daemon
                # thread dying silently would be worse than a log line.
                log.exception("exe-update: checker raised unexpectedly")
                return
            self._result_ready.emit(result, manual)

        threading.Thread(target=_run, name="exe-update-check", daemon=True).start()

    @pyqtSlot(object, bool)
    def _on_result(self, result: ExeUpdateResult, manual: bool) -> None:
        log.info("exe-update: status=%s manual=%s (%s)", result.status, manual, result.message)
        if result.status == "update_available" and result.latest is not None:
            version = result.latest.bundle_version
            # Automatic checks notify once per new version per session;
            # a manual check always gets feedback.
            if not manual and version == self._notified_version:
                return
            self._notified_version = version
            title, body = update_available_message(result)
            self._tray.show_update_available(title, body, result.latest.release_page)
            if manual:
                self._show_manual_dialog(title, body, url=result.latest.release_page)
            return
        # up_to_date / unknown: silent on automatic checks, but a manual
        # check told the user we'd look, so give an answer. A message box
        # rather than a tray balloon — Windows quietly diverts balloons
        # to the notification centre often enough that a manual check
        # looked like it did nothing at all (owner feedback 2026-07-07).
        if manual:
            title, body = manual_check_message(result)
            self._show_manual_dialog(title, body, url=None)

    def _show_manual_dialog(self, title: str, body: str, *, url: str | None) -> None:
        """Modal result box for a user-initiated check.

        *url* (the release page) adds an "Open download page" button.
        Parent is ``None`` on purpose: the overlay window is usually
        hidden when the user is fiddling with menus outside a match,
        and a message box parented to a hidden window can end up
        stranded off-screen. Safe now that the application no longer
        quits on last-window-close.
        """
        box = QMessageBox(
            QMessageBox.Icon.Information,
            title,
            body,
            QMessageBox.StandardButton.Close,
        )
        open_button = None
        if url is not None:
            open_button = box.addButton("Open download page", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if open_button is not None and box.clickedButton() is open_button:
            QDesktopServices.openUrl(QUrl(url))


def _install_exe_update_check(
    tray: OverlayTray,
) -> tuple[_ExeUpdateController, QTimer] | None:
    """Wire the notify-only EXE update check to the tray + a background timer.

    Returns ``(controller, timer)`` so the caller can stop the timer at
    shutdown, or ``None`` when the check is disabled (the version URL
    env var is set empty). The controller is returned (not just the
    timer) so the caller keeps a strong reference — a garbage-collected
    QObject would drop the queued-signal wiring.
    """
    version_url = os.environ.get("MULLIGAN_COACH_EXE_VERSION_URL", _DEFAULT_EXE_VERSION_URL).strip()
    if not version_url:
        log.info("exe-update check disabled (MULLIGAN_COACH_EXE_VERSION_URL is empty)")
        return None

    checker = ExeUpdateChecker(version_url=version_url, running_version=running_bundle_version())
    controller = _ExeUpdateController(checker, tray)
    # Manual "Check for updates" from the tray menu.
    tray.enable_update_check(lambda: controller.check_async(manual=True))

    # First automatic check a little after the data updater's 4 s kick
    # so the two background fetches don't stack up during launch.
    QTimer.singleShot(8_000, lambda: controller.check_async(manual=False))
    timer = QTimer()
    timer.setInterval(_EXE_UPDATE_INTERVAL_MS)
    timer.timeout.connect(lambda: controller.check_async(manual=False))
    timer.start()
    log.info("exe-update: scheduled every 6h against %s", version_url)
    return controller, timer


def _install_first_run_wizard(
    tray: OverlayTray | None,
    window: OverlayWindow,
    *,
    log_path: Path,
    first_run_path: Path,
    state: first_run.FirstRunState,
) -> FirstRunDialog:
    """Build the first-run wizard, wire the menu entries, auto-show if needed.

    Assesses the setup once here (Detailed Logs / Arena log / card DB),
    persists the "verified" flag on the first all-clear so the wizard
    stops auto-popping thereafter, and auto-shows the dialog only when
    something needs fixing *and* the user hasn't been onboarded before
    (:func:`first_run.should_auto_show`). The "Setup &
    troubleshooting…" entries — tray menu and the overlay's gear menu —
    re-assess and re-show on demand.

    Returns the dialog so ``main`` keeps a strong reference — a
    garbage-collected QDialog would silently disappear.
    """
    status = first_run.assess_setup(log_path=log_path, card_db_dir=state.card_db_dir)
    reconciled = first_run.reconcile_state(status, state)
    if reconciled != state:
        try:
            first_run.save_first_run_state(first_run_path, reconciled)
        except OSError:
            log.exception("could not persist first-run state to %s", first_run_path)

    dialog = FirstRunDialog(
        None,
        status=status,
        state=reconciled,
        state_path=first_run_path,
        log_path=log_path,
    )

    def _open_wizard() -> None:
        dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    window.enable_setup(_open_wizard)
    if tray is not None:
        tray.enable_setup(_open_wizard)

    if first_run.should_auto_show(status, reconciled):
        log.info("first-run: setup needs attention (%s); showing wizard", status)
        dialog.show()
    else:
        log.info("first-run: setup OK or already onboarded; wizard available from tray")

    return dialog


def _seed_from_disk(
    coordinator: OverlayCoordinator, persistence_path: Path
) -> CoordinatorOutput | None:
    """Replay the persisted last-deck into *coordinator* if present.

    Returns the coordinator output (so the caller can also render it
    in the UI), or ``None`` if there was nothing on disk.
    """
    arena_ids = load_last_deck(persistence_path)
    if not arena_ids:
        return None
    log.info(
        "seeding coordinator with previous-session deck (%d cards) from %s",
        len(arena_ids),
        persistence_path,
    )
    try:
        seed_event = DeckSubmitted(arena_ids=arena_ids)
        return coordinator.handle_event(seed_event)
    except Exception:
        log.exception("could not seed previous-session deck")
        return None


if __name__ == "__main__":
    sys.exit(main())
