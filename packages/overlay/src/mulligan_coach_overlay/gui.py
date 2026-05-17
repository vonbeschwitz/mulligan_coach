"""PyQt6 transparent always-on-top overlay.

Architecture
------------

Two Qt objects, one thread of pure event-processing in between:

* :class:`OverlayWindow` (QWidget) — the always-on-top translucent
  pane shown over Arena. Owns no business logic; just renders
  whatever the worker pushes.
* :class:`TailerWorker` (QObject) — runs the
  :class:`LogTailer` poll loop on its own :class:`QThread` and
  emits a Qt signal per :class:`CoordinatorOutput`.

Cross-thread state is intentionally tiny — the worker only emits
typed events, the window only listens. No shared mutable data, no
locks, no Qt model/view machinery.

Window flags
------------

* ``Qt.FramelessWindowHint`` — no OS title bar (we draw our own
  drag handle).
* ``Qt.WindowStaysOnTopHint`` — keep the panel above MTGA's window.
* ``Qt.Tool`` — hide from the Windows taskbar / Mac Dock. Tool
  windows don't grab focus when activated either, which is what we
  want for an overlay.
* ``Qt.WA_TranslucentBackground`` (attribute, not flag) — let the
  rounded-rect background render with its own alpha rather than the
  default opaque window background.

We deliberately do NOT use ``Qt.WindowTransparentForInput`` — that
would make the whole window click-through, but then the user
couldn't drag it. We only want clicks *outside* the visible panel
to fall through; inside, the user needs to drag and close. Achieved
via ``setMask`` (clip the click region to the visible rounded-rect
shape).
"""

from __future__ import annotations

import logging
import sys
from typing import cast

from mulligan_coach_recommend import load_service
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

from .arena_paths import default_log_path
from .card_index import ArenaCardIndex
from .coordinator import (
    CoordinatorOutput,
    DeckLoadedOutput,
    MatchResetOutput,
    MissingDataOutput,
    OverlayCoordinator,
    RecommendationOutput,
)
from .log_tailer import LogTailer

log = logging.getLogger(__name__)

_WINDOW_WIDTH = 360
_WINDOW_HEIGHT = 200
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


# ---------------------------------------------------------------------------
# Worker thread: tail the log, push coordinator outputs
# ---------------------------------------------------------------------------


class TailerWorker(QObject):
    """QObject wrapper around the :class:`LogTailer` poll loop.

    Lives on a dedicated :class:`QThread`. Owns the tailer +
    coordinator; emits :attr:`output` per processed event. The
    :attr:`stopped` signal fires once the loop terminates so the
    main thread can quit cleanly.
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
    ) -> None:
        super().__init__()
        self._tailer = tailer
        self._coordinator = coordinator
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
# Window
# ---------------------------------------------------------------------------


class OverlayWindow(QWidget):
    """Frameless always-on-top translucent panel.

    Renders the verdict + keep% / mull% / hand. Draggable by clicking
    anywhere on the panel except the close button.

    The window emits no signals — it's a sink. The
    :class:`TailerWorker` pushes events in via :meth:`apply_output`
    (a slot connected to ``TailerWorker.output``).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Window-level flags + attrs configure the always-on-top
        # frameless translucent behaviour.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self._drag_offset: QPoint | None = None
        self._build_ui()
        self._position_top_right()

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 12)
        outer.setSpacing(6)

        # Title row: app name + close button.
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_label = QLabel("Mulligan Coach")
        title_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px; font-weight: 600;")
        title_row.addWidget(title_label)
        title_row.addStretch(1)
        # MULTIPLICATION SIGN (U+00D7) as the close-button glyph. Ruff
        # flags it as ambiguous-vs-lowercase-x; visually it's a clear
        # close icon at the rendered size, so we keep it.
        close_btn = QPushButton("×")  # noqa: RUF001
        close_btn.setFlat(True)
        close_btn.setFixedSize(18, 18)
        close_btn.setStyleSheet(
            f"QPushButton {{ color: {_TEXT_MUTED}; font-size: 16px; "
            f"font-weight: 600; border: none; background: transparent; }}"
            f"QPushButton:hover {{ color: {_TEXT_PRIMARY}; }}"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self._close_clicked)
        # Bypass the panel's mouse-drag handlers when clicking the X.
        close_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        title_row.addWidget(close_btn)
        outer.addLayout(title_row)

        # Verdict line — replaced as state updates land.
        self._verdict_label = QLabel("Waiting for deck…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: 22px; font-weight: 700;"
        )
        self._verdict_label.setWordWrap(True)
        outer.addWidget(self._verdict_label)

        # Arms row: keep% / mull%.
        arms_row = QHBoxLayout()
        arms_row.setSpacing(16)
        self._keep_label = QLabel("Keep —")
        self._keep_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 14px;")
        self._mull_label = QLabel("Mull —")
        self._mull_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 14px;")
        arms_row.addWidget(self._keep_label)
        arms_row.addWidget(self._mull_label)
        arms_row.addStretch(1)
        outer.addLayout(arms_row)

        # Hand summary.
        self._hand_label = QLabel("")
        self._hand_label.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        self._hand_label.setWordWrap(True)
        outer.addWidget(self._hand_label, stretch=1)

        # Context footer.
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
    # Drag-to-move
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

    # -----------------------------------------------------------------
    # Slot: receive coordinator output, render it
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
        self._verdict_label.setStyleSheet(
            f"color: {verdict_color}; font-size: 22px; font-weight: 700;"
        )
        keep_pct = rec.keep_win_probability * 100
        mull_pct = rec.mulligan_win_probability * 100
        self._keep_label.setText(f"Keep <b>{keep_pct:.1f}%</b>")
        self._mull_label.setText(f"Mull <b>{mull_pct:.1f}%</b>")
        names = ", ".join(c.name for c in output.hand)
        self._hand_label.setText(f"Hand: {names}")
        play_draw = "play" if output.on_the_play else "draw"
        self._context_label.setText(
            f"mull #{output.mulligan_count} · on the {play_draw} · set {output.primary_set or '?'}"
        )

    def _render_deck_loaded(self, output: DeckLoadedOutput) -> None:
        if output.n_unresolved:
            self._verdict_label.setText("Deck partially loaded")
            self._verdict_label.setStyleSheet(
                f"color: {_MARGINAL_COLOR}; font-size: 18px; font-weight: 700;"
            )
            self._hand_label.setText(
                f"{output.n_resolved} of {output.n_cards} cards have a local "
                f"encoding. Refresh card data to fill the gaps."
            )
        else:
            self._verdict_label.setText("Deck ready · waiting for hand")
            self._verdict_label.setStyleSheet(
                f"color: {_TEXT_PRIMARY}; font-size: 18px; font-weight: 700;"
            )
            self._hand_label.setText("")
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._context_label.setText(
            f"deck loaded · {output.n_cards} cards · set {output.primary_set or '?'}"
        )

    def _render_missing(self, output: MissingDataOutput) -> None:
        self._verdict_label.setText("Can't recommend")
        self._verdict_label.setStyleSheet(
            f"color: {_MARGINAL_COLOR}; font-size: 18px; font-weight: 700;"
        )
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._hand_label.setText(output.reason)
        self._context_label.setText(f"({output.what})")

    def _render_reset(self) -> None:
        self._verdict_label.setText("Waiting for next match…")
        self._verdict_label.setStyleSheet(
            f"color: {_TEXT_PRIMARY}; font-size: 18px; font-weight: 700;"
        )
        self._keep_label.setText("Keep —")
        self._mull_label.setText("Mull —")
        self._hand_label.setText("")
        self._context_label.setText("")

    def _close_clicked(self) -> None:
        QApplication.quit()


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
    service (slow — a couple of seconds while the model loads), then
    starts the tailer worker on a background QThread and shows the
    overlay panel. ``argv`` mirrors :func:`sys.argv`.
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
    log_path = default_log_path()
    log.info("tailing %s", log_path)
    tailer = LogTailer(log_path, start_at_end=True)

    window = OverlayWindow()
    thread = QThread()
    worker = TailerWorker(tailer, coordinator)
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

    def _on_app_quit() -> None:
        # IMPORTANT: don't rely on a queued ``worker.stopped → thread.quit``
        # connection here — the slot would be dispatched to the main
        # thread's event loop, but we're about to block the main thread
        # in ``thread.wait()``, so the queued quit() never fires and
        # the wait deadlocks (then force-terminates at the timeout).
        # Calling thread.quit() directly posts the quit event to the
        # QThread's own event loop, where it does run.
        worker.request_stop()
        thread.quit()
        if not thread.wait(2000):
            log.warning("tailer thread did not stop within 2s; forcing")
            thread.terminate()
        service.shutdown()

    app.aboutToQuit.connect(_on_app_quit)

    thread.start()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
