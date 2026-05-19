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
* **Collapse / expand**: a compact pill (verdict + keep%/mull% only)
  vs. the full panel (adds the resolved hand + a debug footer).
  Click the title's collapse button or hit ``Ctrl+Shift+M`` (a Win32
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
from typing import cast

from mulligan_coach_recommend import load_service
from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
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
    CoordinatorOutput,
    DeckLoadedOutput,
    MatchResetOutput,
    MissingDataOutput,
    OverlayCoordinator,
    RecommendationOutput,
)
from .deck_persistence import default_persistence_path, load_last_deck, save_last_deck
from .events import DeckSubmitted
from .log_tailer import LogTailer

log = logging.getLogger(__name__)

# Two layouts: full panel and compact pill. The window is the same
# widget — we just toggle child widget visibility and shrink it.
_NORMAL_WIDTH = 360
_NORMAL_HEIGHT = 200
_COMPACT_WIDTH = 220
_COMPACT_HEIGHT = 64
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

# Win32 hotkey constants. Ctrl+Shift+M = MOD_CONTROL | MOD_SHIFT, VK_M.
# Picked because (1) no common app I know of binds Ctrl+Shift+M
# globally and (2) "M for Mulligan" is mnemonic.
_HOTKEY_MOD_ALT = 0x0001
_HOTKEY_MOD_CONTROL = 0x0002
_HOTKEY_MOD_SHIFT = 0x0004
_VK_M = 0x4D
_WM_HOTKEY = 0x0312


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
# Global hotkey filter
# ---------------------------------------------------------------------------


class _HotkeyFilter(QObject, QAbstractNativeEventFilter):
    """Catches Win32 ``WM_HOTKEY`` messages and re-emits as a Qt signal.

    Installed via :meth:`QApplication.installNativeEventFilter`, which
    routes every native event through us regardless of which window
    is the recipient. That's important here because ``Qt.Tool``
    windows don't accept keyboard focus, so :class:`QShortcut`
    bindings wouldn't fire.

    On non-Windows platforms the filter still installs but never
    receives a matching event (the platform's native message format
    differs); :func:`register_global_hotkey` is the actual Win32
    binding step.
    """

    triggered = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        QObject.__init__(self, parent)
        QAbstractNativeEventFilter.__init__(self)

    def nativeEventFilter(  # type: ignore[override]
        self, eventType: bytes | bytearray, message: int
    ) -> tuple[bool, int]:
        # PyQt passes ``message`` as a sip.voidptr; ``int(message)``
        # gives us the address of the underlying Win32 MSG struct.
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            try:
                msg_struct = _MSG.from_address(int(message))
            except (ValueError, OSError):
                return False, 0
            if msg_struct.message == _WM_HOTKEY:
                self.triggered.emit(int(msg_struct.wParam))
                return True, 0
        return False, 0


if sys.platform == "win32":
    # Layout of the Win32 ``MSG`` struct. ``wParam`` is platform-width
    # (32-bit on x86, 64-bit on x64); we use the c_uintptr alias that
    # ctypes provides via wintypes to stay correct on both. PyQt6 only
    # ships 64-bit on Windows so c_void_p sizes match in practice, but
    # we don't bake that assumption in.
    class _MSG(ctypes.Structure):
        _fields_ = (
            ("hwnd", ctypes.c_void_p),
            ("message", ctypes.c_uint),
            ("wParam", ctypes.c_void_p),
            ("lParam", ctypes.c_void_p),
            ("time", ctypes.c_uint),
            ("pt_x", ctypes.c_long),
            ("pt_y", ctypes.c_long),
        )

    def register_global_hotkey(hwnd: int, hotkey_id: int) -> bool:
        """Ask Windows to send ``WM_HOTKEY`` to *hwnd* when Ctrl+Shift+M fires.

        Returns ``True`` on success, ``False`` if Windows rejected the
        registration (most often because another app already owns the
        same key combination — the overlay should keep working without
        the hotkey rather than refuse to start).
        """
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        user32.RegisterHotKey.restype = ctypes.c_int
        ok = user32.RegisterHotKey(
            hwnd,
            hotkey_id,
            _HOTKEY_MOD_CONTROL | _HOTKEY_MOD_SHIFT,
            _VK_M,
        )
        return bool(ok)

    def unregister_global_hotkey(hwnd: int, hotkey_id: int) -> None:
        user32 = ctypes.windll.user32
        user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.UnregisterHotKey.restype = ctypes.c_int
        user32.UnregisterHotKey(hwnd, hotkey_id)

else:

    class _MSG(ctypes.Structure):  # pragma: no cover - non-Windows stub
        _fields_ = ()

    def register_global_hotkey(hwnd: int, hotkey_id: int) -> bool:  # pragma: no cover
        return False

    def unregister_global_hotkey(hwnd: int, hotkey_id: int) -> None:  # pragma: no cover
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

        # Verdict line — replaced as state updates land.
        self._verdict_label = QLabel("Waiting for deck…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: 22px; font-weight: 700;"
        )
        self._verdict_label.setWordWrap(True)
        outer.addWidget(self._verdict_label)

        # Arms row: keep% / mull%.
        self._arms_row_widget = QWidget()
        arms_row = QHBoxLayout(self._arms_row_widget)
        arms_row.setContentsMargins(0, 0, 0, 0)
        arms_row.setSpacing(16)
        self._keep_label = QLabel("Keep —")
        self._keep_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 14px;")
        self._mull_label = QLabel("Mull —")
        self._mull_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 14px;")
        arms_row.addWidget(self._keep_label)
        arms_row.addWidget(self._mull_label)
        arms_row.addStretch(1)
        outer.addWidget(self._arms_row_widget)

        # Hand summary (full layout only).
        self._hand_label = QLabel("")
        self._hand_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        self._hand_label.setWordWrap(True)
        outer.addWidget(self._hand_label, stretch=1)

        # Context footer (full layout only).
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
            # Compact pill: just the verdict + arms. Title bar (and
            # therefore the close X) hides. The user can re-expand
            # via the hotkey or by clicking; once expanded, the X is
            # back.
            self._title_row_widget.setVisible(False)
            self._hand_label.setVisible(False)
            self._context_label.setVisible(False)
            self._verdict_label.setStyleSheet(
                f"color: {_TEXT_PRIMARY}; font-size: 16px; font-weight: 700;"
            )
            self.setFixedSize(_COMPACT_WIDTH, _COMPACT_HEIGHT)
            self._collapse_btn.setText("▢")
        else:
            self._title_row_widget.setVisible(True)
            self._hand_label.setVisible(True)
            self._context_label.setVisible(True)
            self.setFixedSize(_NORMAL_WIDTH, _NORMAL_HEIGHT)
            self._collapse_btn.setText("-")
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
        elif isinstance(output, DeckLoadedOutput):
            self._render_deck_loaded(output)
        elif isinstance(output, MissingDataOutput):
            self._render_missing(output)
        elif isinstance(output, MatchResetOutput):
            self._render_reset()

    def _render_recommendation(self, output: RecommendationOutput) -> None:
        rec = output.recommendation
        assert rec is not None  # invariant of RecommendationOutput
        verdict_label, verdict_color = _verdict_display(rec.verdict)
        self._verdict_label.setText(verdict_label)
        # In compact mode, font is smaller; in normal mode, larger.
        font_size = 16 if self._compact else 22
        self._verdict_label.setStyleSheet(
            f"color: {verdict_color}; font-size: {font_size}px; font-weight: 700;"
        )
        keep_pct = rec.keep_win_probability * 100
        # Display the bias-adjusted mulligan WR so the user sees the
        # number that actually drives the verdict (raw P(win) at the
        # mulligan level + the empirical 4 pp correction; see
        # MULLIGAN_BIAS in mulligan_coach_recommend.service).
        mull_pct = (rec.mulligan_win_probability + rec.mulligan_bias) * 100
        self._keep_label.setText(f"Keep <b>{keep_pct:.1f}%</b>")
        self._mull_label.setText(f"Mull <b>{mull_pct:.1f}%</b>")
        names = ", ".join(c.name for c in output.hand)
        self._hand_label.setText(f"Hand: {names}")
        play_draw = "play" if output.on_the_play else "draw"
        self._context_label.setText(
            f"mull #{output.mulligan_count} · on the {play_draw} · set {output.primary_set or '?'}"
        )

    def _render_deck_loaded(self, output: DeckLoadedOutput) -> None:
        font_size = 14 if self._compact else 18
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
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._context_label.setText(
            f"deck loaded · {output.n_cards} cards · set {output.primary_set or '?'}"
        )

    def _render_missing(self, output: MissingDataOutput) -> None:
        font_size = 14 if self._compact else 18
        self._verdict_label.setText("Can't recommend")
        self._verdict_label.setStyleSheet(
            f"color: {_MARGINAL_COLOR}; font-size: {font_size}px; font-weight: 700;"
        )
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._hand_label.setText(output.reason)
        self._context_label.setText(f"({output.what})")

    def _render_reset(self) -> None:
        font_size = 14 if self._compact else 18
        self._verdict_label.setText("Waiting for next match…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: {font_size}px; font-weight: 700;"
        )
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._hand_label.setText("")
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


def _verdict_display(verdict: str) -> tuple[str, str]:
    """Human label + colour for one of the four verdict tags."""
    if verdict == "clear_keep":
        return "CLEAR KEEP", _KEEP_COLOR
    if verdict == "marginal_keep":
        return "Marginal keep", _MARGINAL_COLOR
    if verdict == "marginal_mulligan":
        return "Marginal mulligan", _MARGINAL_COLOR
    if verdict == "clear_mulligan":
        return "CLEAR MULLIGAN", _MULL_COLOR
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

    # Global hotkey: Ctrl+Shift+M to toggle compact / expanded.
    hotkey_filter = _HotkeyFilter()
    hotkey_filter.triggered.connect(window.toggle_compact)
    app.installNativeEventFilter(hotkey_filter)

    def _on_app_quit() -> None:
        # IMPORTANT: don't rely on a queued ``worker.stopped → thread.quit``
        # connection here — the slot would be dispatched to the main
        # thread's event loop, but we're about to block the main thread
        # in ``thread.wait()``, so the queued quit() never fires and
        # the wait deadlocks (then force-terminates at the timeout).
        # Calling thread.quit() directly posts the quit event to the
        # QThread's own event loop, where it does run.
        watcher.stop()
        if sys.platform == "win32":
            unregister_global_hotkey(int(window.winId()), 1)
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
        # Register the global hotkey under the window's HWND so the
        # WM_HOTKEY message routes through our event filter.
        if register_global_hotkey(hwnd, 1):
            log.info("global hotkey registered: Ctrl+Shift+M toggles compact mode")
        else:
            log.warning(
                "could not register Ctrl+Shift+M global hotkey "
                "(another app may already own it); use the title-bar "
                "collapse button or double-click instead"
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
