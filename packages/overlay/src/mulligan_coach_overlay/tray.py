"""System tray icon for the overlay.

Why a tray icon at all: the overlay window hides itself whenever MTG
Arena isn't running (see ``gui.OverlayWindow.on_arena_state_changed``),
and with Start-with-Windows enabled by default the app is typically
launched invisibly at login. Without a tray presence there is *no*
visible sign the app is running, no way to quit it, and no way to
reach the Start-with-Windows toggle until Arena is open — and a user
who double-clicks the EXE with Arena closed reasonably concludes the
launch failed. The tray icon fixes all four at once, the same way
comparable background apps (Untapped.gg, Discord, Steam) do.

Two pieces:

* :class:`OverlayTray` — the icon itself plus a right-click menu
  (Start with Windows, Quit) and the "Mulligan Coach is running"
  balloon shown on *manual* launches when Arena is closed. Autostart
  launches (identified by :data:`autostart.AUTOSTART_LAUNCH_FLAG` on
  the command line) stay silent: a balloon at every login would read
  as nagging and imply background activity that isn't happening.
* :func:`create_tray` — guard-railed factory; returns ``None`` on
  desktops without a system tray so the GUI can skip the wiring.

The icon is drawn programmatically (dark disc + "M") for the same
reason the title-bar buttons use text glyphs — no binary assets in
the wheel. Colours match the overlay palette (``stats_html.py`` /
``gui.py``): panel background ``#141418``, border ``#3c3c46``,
keep-green ``#7be57b``.
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, Qt, pyqtSlot
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart

log = logging.getLogger(__name__)


class OverlayTray(QSystemTrayIcon):
    """Tray icon + context menu; lives for the whole app session.

    Construct via :func:`create_tray` (which checks tray availability)
    rather than directly. The GUI calls :meth:`show` after
    construction and :meth:`show_started_message` when a manual
    launch lands with Arena closed.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.setIcon(_draw_tray_icon())
        self.setToolTip("Mulligan Coach")

        # Keep a Python reference to the menu: setContextMenu() does
        # not take ownership, and a garbage-collected QMenu means a
        # tray icon whose right-click silently does nothing.
        self._menu = QMenu()
        self._autostart_action: QAction | None = None
        if autostart.supported():
            action = self._menu.addAction("Start with Windows")
            # ``QMenu.addAction(str)`` is typed ``QAction | None`` in
            # PyQt6's stubs but never returns None for the text-only
            # overload. Guard so the type narrows (same as gui.py).
            assert action is not None
            action.setCheckable(True)
            action.triggered.connect(self._toggle_autostart)
            self._autostart_action = action
            self._menu.addSeparator()
        quit_action = self._menu.addAction("Quit Mulligan Coach")
        assert quit_action is not None
        quit_action.triggered.connect(_quit_application)
        # Re-read the registry each time the menu opens (mirrors the
        # title-bar gear menu) so an external change to the Run entry
        # is reflected without restarting the overlay.
        self._menu.aboutToShow.connect(self._sync_autostart_state)
        self.setContextMenu(self._menu)

    def show_started_message(self) -> None:
        """Balloon: the app is alive and will appear with Arena.

        Shown only on manual launches with Arena closed — the one
        situation where the overlay's launch is otherwise invisible
        and users conclude it didn't work. The duration is a hint;
        modern Windows toasts use their own system timing.
        """
        self.showMessage(
            "Mulligan Coach is running",
            "The overlay will appear when you open MTG Arena.",
            QSystemTrayIcon.MessageIcon.Information,
            8_000,
        )

    def _sync_autostart_state(self) -> None:
        if self._autostart_action is not None:
            self._autostart_action.setChecked(autostart.is_enabled())

    @pyqtSlot(bool)
    def _toggle_autostart(self, checked: bool) -> None:
        """Same contract as the gear-menu toggle: apply and don't roll
        back on failure — the next menu open re-reads ``is_enabled``."""
        if checked:
            autostart.enable()
        else:
            autostart.disable()


def create_tray(parent: QObject | None = None) -> OverlayTray | None:
    """Build the tray icon, or ``None`` where no system tray exists.

    Windows always has one; some Linux desktops don't. Returning
    ``None`` (and logging) lets the caller keep a single optional
    reference instead of branching on platform.
    """
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.info("no system tray available; skipping tray icon")
        return None
    return OverlayTray(parent)


def _quit_application() -> None:
    """Quit via the running QApplication (same path as the ✕ button)."""
    app = QApplication.instance()
    if app is not None:
        app.quit()


def _draw_tray_icon() -> QIcon:
    """Programmatic tray icon: dark disc with a keep-green "M".

    Rendered at several sizes so Windows picks a crisp bitmap for the
    tray's DPI rather than scaling a single one.
    """
    icon = QIcon()
    for size in (16, 20, 24, 32, 48, 64):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        margin = max(1, size // 16)
        painter.setBrush(QColor("#141418"))
        painter.setPen(QPen(QColor("#3c3c46"), max(1, size // 16)))
        painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
        font = QFont()
        font.setPixelSize(int(size * 0.56))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("#7be57b"))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")
        painter.end()
        icon.addPixmap(pixmap)
    return icon
