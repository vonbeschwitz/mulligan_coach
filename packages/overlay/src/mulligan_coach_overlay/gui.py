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
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from mulligan_coach_recommend import RecommendationExplanation, load_service
from PyQt6.QtCore import (
    QObject,
    QPoint,
    QRect,
    Qt,
    QThread,
    pyqtSignal,
    pyqtSlot,
)
from PyQt6.QtGui import (
    QColor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import arena_window
from .arena_paths import default_log_path
from .arena_window import ArenaWindowWatcher
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
from .log_tailer import LogTailer

log = logging.getLogger(__name__)

# Two layouts: full panel and compact pill. The window is the same
# widget — we just toggle child widget visibility and shrink it.
# Normal panel is taller now that it surfaces playability stats
# (mana base, curve hits, per-card grid) alongside the verdict.
_NORMAL_WIDTH = 380
_NORMAL_HEIGHT = 540
# Compact pill: verdict + single mulligan-% on one line. The choice
# model collapses the keep/mull decision into one number, so the
# pill only needs room for "Clear keep · mull 12%" rather than two
# arm percentages.
_COMPACT_WIDTH = 220
_COMPACT_HEIGHT = 32
_CORNER_RADIUS = 12

# Colour palette — kept in lockstep with the website's dark theme so
# users moving between the two surfaces see a recognisable look. The
# alpha channel on the panel background is what gives the overlay its
# slight see-through quality.
_PANEL_BG = QColor(20, 20, 24, 235)
_PANEL_BORDER = QColor(60, 60, 70, 255)
_TEXT_PRIMARY = "#e8e8ec"
_TEXT_MUTED = "#909098"
_KEEP_COLOR = "#7be57b"  # green
_MULL_COLOR = "#e57b7b"  # red
_MARGINAL_COLOR = "#e5c87b"  # amber

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

    def __init__(self, parent: QWidget | None = None) -> None:
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
        self._compact = False
        self._drag_offset: QPoint | None = None
        # Cached payload from the last RecommendationOutput so the
        # compact <-> expanded toggle can re-render without waiting on
        # a fresh sim. Reset on any state-changing event other than
        # a successful recommendation.
        self._last_rec: RecommendationOutput | None = None
        self._build_ui()
        self._apply_layout()
        self._position_top_right()

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        # Title row: app name + collapse toggle + close button.
        self._title_row_widget = QWidget()
        title_row = QHBoxLayout(self._title_row_widget)
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_label = QLabel("Mulligan Coach")
        title_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        title_row.addWidget(title_label)
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

        # Verdict line — replaced as state updates land. In compact
        # mode this single label carries everything (verdict + arms);
        # in expanded mode the arms move to their own row below.
        self._verdict_label = QLabel("Waiting for deck…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: 22px; font-weight: 700;"
        )
        self._verdict_label.setWordWrap(True)
        outer.addWidget(self._verdict_label)

        # Score row: a single "Should mulligan: NN%" display. The
        # choice model collapses the keep/mull decision into one
        # probability (P(skilled player keeps)), so we show just the
        # complementary mulligan percentage rather than two arms.
        # Expanded mode only — the compact pill folds the number
        # into the verdict label.
        self._arms_row_widget = QWidget()
        arms_row = QHBoxLayout(self._arms_row_widget)
        arms_row.setContentsMargins(0, 0, 0, 0)
        arms_row.setSpacing(8)
        self._mull_label = QLabel("Should mulligan —")
        self._mull_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 14px;")
        arms_row.addWidget(self._mull_label)
        arms_row.addStretch(1)
        outer.addWidget(self._arms_row_widget)

        # Hand summary (expanded layout only).
        self._hand_label = QLabel("")
        self._hand_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        self._hand_label.setWordWrap(True)
        outer.addWidget(self._hand_label)

        # Playability stats (expanded layout only). Mirror of the
        # website's "Why this hand plays out the way it does" panel —
        # mana base + curve hits + per-card playability table — but
        # rendered as a single QLabel with rich-text HTML so we don't
        # have to build a dozen child widgets here.
        self._stats_label = QLabel("")
        self._stats_label.setTextFormat(Qt.TextFormat.RichText)
        self._stats_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 11px;")
        self._stats_label.setWordWrap(True)
        outer.addWidget(self._stats_label, stretch=1)

        # Context footer (expanded layout only).
        self._context_label = QLabel("")
        self._context_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px;")
        outer.addWidget(self._context_label)

    def _position_top_right(self) -> None:
        """Place the window in the top-right of the primary screen."""
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.right() - self.width() - 20
        y = available.top() + 20
        self.move(x, y)

    # -----------------------------------------------------------------
    # Compact / expanded layout
    # -----------------------------------------------------------------

    @pyqtSlot()
    def toggle_compact(self) -> None:
        """Flip between full panel and compact pill."""
        self._compact = not self._compact
        self._apply_layout()

    def _apply_layout(self) -> None:
        if self._compact:
            # Compact pill: a single line carrying verdict + both
            # percentages. Title bar, arms row, hand summary, stats
            # panel, and context footer all hide. Re-expand via the
            # hotkey, the title-bar button (once title is back), or
            # a double-click anywhere on the panel.
            self._title_row_widget.setVisible(False)
            self._arms_row_widget.setVisible(False)
            self._hand_label.setVisible(False)
            self._stats_label.setVisible(False)
            self._context_label.setVisible(False)
            self._verdict_label.setStyleSheet(
                f"color: {_TEXT_PRIMARY}; font-size: 12px; font-weight: 700;"
            )
            self.setFixedSize(_COMPACT_WIDTH, _COMPACT_HEIGHT)
            self._collapse_btn.setText("▢")
        else:
            self._title_row_widget.setVisible(True)
            self._arms_row_widget.setVisible(True)
            self._hand_label.setVisible(True)
            self._stats_label.setVisible(True)
            self._context_label.setVisible(True)
            self.setFixedSize(_NORMAL_WIDTH, _NORMAL_HEIGHT)
            self._collapse_btn.setText("-")
        # Re-render the verdict label so its text matches the new
        # layout — the compact format is single-line "verdict ·
        # X% vs Y%", the expanded one is just the verdict alone.
        if self._last_rec is not None:
            self._render_recommendation_from_cached()
        # Keep the top-right anchor when shrinking so the pill doesn't
        # appear to drift across the screen on toggle. (Position is in
        # the *primary* screen's coordinate space — fine for our
        # right-corner default; if the user has dragged elsewhere the
        # current self.pos() is preserved by Qt automatically.)

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
        """Update the UI from a :class:`CoordinatorOutput`."""
        output = cast(CoordinatorOutput, payload)
        if isinstance(output, RecommendationOutput):
            self._render_recommendation(output)
        elif isinstance(output, ComputingOutput):
            self._render_computing(output)
        elif isinstance(output, DeckLoadedOutput):
            self._last_rec = None
            self._render_deck_loaded(output)
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
        if self._compact:
            # Compact pill: "Clear keep · mull 12%". One signed number
            # is enough once the user knows the model is reporting a
            # mulligan tendency, not two arms.
            text = f"{short_verdict} · mull {mull_pct:.0f}%"
            self._verdict_label.setText(text)
            self._verdict_label.setStyleSheet(
                f"color: {verdict_color}; font-size: 12px; font-weight: 700;"
            )
        else:
            self._verdict_label.setText(short_verdict)
            self._verdict_label.setStyleSheet(
                f"color: {verdict_color}; font-size: 22px; font-weight: 700;"
            )
            self._mull_label.setText(f"Should mulligan <b>{mull_pct:.0f}%</b>")
            names = ", ".join(c.name for c in output.hand)
            self._hand_label.setText(f"Hand: {names}")
            self._stats_label.setText(_build_stats_html(rec.explanation))
            play_draw = "play" if output.on_the_play else "draw"
            self._context_label.setText(
                f"mull #{output.mulligan_count} · on the {play_draw} · "
                f"set {output.primary_set or '?'}"
            )

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
                f"color: {_MARGINAL_COLOR}; font-size: 22px; font-weight: 700;"
            )
            self._mull_label.setText("Should mulligan —")
            self._hand_label.setText("")
            self._stats_label.setText("")
            self._context_label.setText(
                f"mull #{output.mulligan_count} · on the {play_draw} · computing…"
            )

    def _render_deck_loaded(self, output: DeckLoadedOutput) -> None:
        font_size = 12 if self._compact else 18
        if output.n_unresolved:
            self._verdict_label.setText("Deck partially loaded")
            self._verdict_label.setStyleSheet(
                f"color: {_MARGINAL_COLOR}; font-size: {font_size}px; font-weight: 700;"
            )
            self._hand_label.setText(
                f"{output.n_resolved} of {output.n_cards} cards have a local "
                f"encoding. Refresh card data to fill the gaps."
            )
        else:
            self._verdict_label.setText("Deck ready · waiting for hand")
            self._verdict_label.setStyleSheet(
                f"color: {_TEXT_PRIMARY}; font-size: {font_size}px; font-weight: 700;"
            )
            self._hand_label.setText("")
        self._mull_label.setText("Should mulligan —")
        self._stats_label.setText("")
        self._context_label.setText(
            f"deck loaded · {output.n_cards} cards · set {output.primary_set or '?'}"
        )

    def _render_missing(self, output: MissingDataOutput) -> None:
        font_size = 12 if self._compact else 18
        self._verdict_label.setText("Can't recommend")
        self._verdict_label.setStyleSheet(
            f"color: {_MARGINAL_COLOR}; font-size: {font_size}px; font-weight: 700;"
        )
        self._mull_label.setText("Should mulligan —")
        self._hand_label.setText(output.reason)
        self._stats_label.setText("")
        self._context_label.setText(f"({output.what})")

    def _render_reset(self) -> None:
        font_size = 12 if self._compact else 18
        self._verdict_label.setText("Waiting for next match…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: {font_size}px; font-weight: 700;"
        )
        self._mull_label.setText("Should mulligan —")
        self._hand_label.setText("")
        self._stats_label.setText("")
        self._context_label.setText("")

    def _close_clicked(self) -> None:
        QApplication.quit()


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
# Stats panel HTML — mirror of the website's "Why this hand plays out"
# block, but flattened into a single QLabel rich-text string so we
# don't have to manage two dozen child widgets.
# ---------------------------------------------------------------------------


def _build_stats_html(exp: RecommendationExplanation) -> str:
    """Render the keep-arm playability stats as a QLabel-safe HTML string.

    QLabel's rich-text rendering supports a small HTML subset
    (paragraphs, basic tables, inline ``<b>`` / ``<span>``, percent
    widths). Stick to that — no CSS in style attributes other than
    ``color`` / ``font-size`` / ``text-align`` / ``padding``.
    """

    def pct(p: float) -> str:
        return f"{round(p * 100):d}%"

    # Mana base + curve hits: two-column key/value tables stacked.
    mana_rows = [
        ("2nd land by T2", pct(exp.p_make_2nd_land_by_t2)),
        ("3rd land by T3", pct(exp.p_make_3rd_land_by_t3)),
        ("4th land by T4", pct(exp.p_make_4th_land_by_t4)),
        ("Avg mana at T4", f"{exp.expected_mana_at_t4:.1f}"),
    ]
    curve_rows = [
        ("Cast any spell T1", pct(exp.p_cast_any_spell_t1)),
        ("Cast a creature T2", pct(exp.p_cast_any_creature_t2)),
        ("Cast removal T2", pct(exp.p_cast_any_removal_t2)),
        ("Cast 2-drop by T3", pct(exp.p_cast_small_creature_by_t3)),
        ("Cast 3-drop by T4", pct(exp.p_cast_3drop_by_t4)),
        ("Colors fixed by T4", pct(exp.color_fix_by_t4)),
    ]

    def kv_table(title: str, rows: list[tuple[str, str]]) -> str:
        body = "".join(
            f"<tr><td style='color:{_TEXT_MUTED};'>{k}</td><td align='right'>{v}</td></tr>"
            for k, v in rows
        )
        return (
            f"<p style='margin-top:4px;margin-bottom:2px;'>"
            f"<b>{title}</b></p>"
            f"<table width='100%' cellspacing='0' cellpadding='1'>{body}</table>"
        )

    # Per-card playability table: card name | MV | T1..T4 columns.
    card_header = (
        "<tr>"
        f"<th align='left' style='color:{_TEXT_MUTED};font-weight:600;'>Card</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>MV</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T1</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T2</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T3</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T4</th>"
        "</tr>"
    )
    card_rows: list[str] = []
    for hc in exp.hand_cards:
        if hc.is_land:
            cells = [f"<td align='center' style='color:{_TEXT_MUTED};'>&mdash;</td>"] * 5
        else:
            mv_cell = f"<td align='center'>{hc.mana_value}</td>"
            turn_cells = "".join(f"<td align='center'>{pct(p)}</td>" for p in hc.p_castable_by_turn)
            cells = [mv_cell + turn_cells]
        name_color = _TEXT_MUTED if hc.is_land else _TEXT_PRIMARY
        card_rows.append(f"<tr><td style='color:{name_color};'>{hc.name}</td>{''.join(cells)}</tr>")
    card_table = (
        f"<p style='margin-top:6px;margin-bottom:2px;'><b>Per-card playability</b></p>"
        f"<table width='100%' cellspacing='0' cellpadding='1'>"
        f"{card_header}{''.join(card_rows)}"
        f"</table>"
    )

    return kv_table("Mana base", mana_rows) + kv_table("Curve hits", curve_rows) + card_table


def _verdict_display(verdict: str) -> tuple[str, str]:
    """Human label + colour for one of the four verdict tags.

    Labels use "mull" rather than "mulligan" so they fit on a single
    line in the compact pill at 12px and don't need to wrap in the
    expanded panel either.
    """
    if verdict == "clear_keep":
        return "CLEAR KEEP", _KEEP_COLOR
    if verdict == "marginal_keep":
        return "Marginal keep", _MARGINAL_COLOR
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
    app = QApplication(argv if argv is not None else sys.argv)

    log.info("loading card index + recommendation service…")
    card_index = ArenaCardIndex.build()
    service = load_service(card_index.loaded_sets)
    coordinator = OverlayCoordinator(card_index, service)

    # Seed the coordinator with the previous session's deck (if any)
    # so a missed Arena submission doesn't leave us with no deck. We
    # do this BEFORE starting the tailer — that way the first Arena
    # event arriving is layered on top of a deck that's already there.
    persistence_path = default_persistence_path()
    seeded_output = _seed_from_disk(coordinator, persistence_path)

    log_path = default_log_path()
    log.info("tailing %s", log_path)
    tailer = LogTailer(log_path, start_at_end=True)

    window = OverlayWindow()
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

    def _on_app_quit() -> None:
        # IMPORTANT: don't rely on a queued ``worker.stopped → thread.quit``
        # connection here — the slot would be dispatched to the main
        # thread's event loop, but we're about to block the main thread
        # in ``thread.wait()``, so the queued quit() never fires and
        # the wait deadlocks (then force-terminates at the timeout).
        # Calling thread.quit() directly posts the quit event to the
        # QThread's own event loop, where it does run.
        watcher.stop()
        hotkey_hook.uninstall()
        worker.request_stop()
        thread.quit()
        if not thread.wait(2000):
            log.warning("tailer thread did not stop within 2s; forcing")
            thread.terminate()
        service.shutdown()

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
        else:
            log.warning(
                "could not install Alt+E low-level keyboard hook; "
                "use the title-bar collapse button or double-click instead"
            )

    watcher.start()

    # If we seeded from disk, apply the resulting DeckLoadedOutput to
    # the UI now (after show) so the initial label reflects the
    # available deck instead of "Waiting for deck…".
    if seeded_output is not None:
        window.apply_output(seeded_output)

    return app.exec()


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
