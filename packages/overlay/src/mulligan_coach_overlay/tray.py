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
  (update-check entries, "Setup & troubleshooting…", Start with
  Windows, Quit) and the "Mulligan Coach is running" balloon shown
  on *manual* launches when Arena is closed. Autostart
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
from collections.abc import Callable

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSlot
from PyQt6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from . import about, autostart, feedback

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

        # URL opened when the user clicks the last-shown "update
        # available" balloon or the "Download update" menu entry.
        # ``None`` = no pending update, so a balloon click is a no-op.
        self._update_url: str | None = None

        # Keep a Python reference to the menu: setContextMenu() does
        # not take ownership, and a garbage-collected QMenu means a
        # tray icon whose right-click silently does nothing.
        self._menu = QMenu()

        # Update-notification entries live at the top of the menu but
        # start hidden. ``enable_update_check`` reveals + wires "Check
        # for updates" (only when the GUI has an update checker to
        # drive it), and ``show_update_available`` reveals "Download
        # update". Building them up-front keeps the menu order stable.
        check_action = self._menu.addAction("Check for updates")
        assert check_action is not None
        check_action.setVisible(False)
        self._check_action = check_action

        download_action = self._menu.addAction("Download update…")
        assert download_action is not None
        download_action.setVisible(False)
        download_action.triggered.connect(self._open_update_url)
        self._download_action = download_action
        update_separator = self._menu.addSeparator()
        assert update_separator is not None
        update_separator.setVisible(False)
        self._update_separator = update_separator

        # First-run / troubleshooting wizard entry. Hidden until the GUI
        # wires it via ``enable_setup`` (there's nothing to open from a
        # headless context). Gives a user whose overlay stays silent —
        # Detailed Logs off, Arena in an odd install location — a way
        # back to the setup guide after the launch dialog is dismissed.
        # "&&" renders as a literal ampersand — a single "&" is Qt's
        # mnemonic marker and would be swallowed on display.
        setup_action = self._menu.addAction("Setup && troubleshooting…")
        assert setup_action is not None
        setup_action.setVisible(False)
        self._setup_action = setup_action

        # "Send feedback…" — always visible; self-contained (opens a URL
        # in the browser), so unlike the setup entry it needs no GUI
        # callback wired in later and works the moment the tray exists.
        feedback_action = self._menu.addAction("Send feedback…")
        assert feedback_action is not None
        feedback_action.triggered.connect(lambda _checked=False: self._open_feedback())
        self._feedback_action = feedback_action

        # "About Mulligan Coach" — always visible, self-contained. Carries
        # the Fan Content Policy disclaimer + 17Lands/Scryfall/MTGJSON
        # attributions the compliance position requires to be reachable
        # in-app (see :mod:`about`).
        about_action = self._menu.addAction("About Mulligan Coach")
        assert about_action is not None
        about_action.triggered.connect(lambda _checked=False: self._open_about())
        self._about_action = about_action

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

        # A click on the most recent balloon opens the pending update
        # URL (if any). Wired once here; the URL is swapped per notice.
        self.messageClicked.connect(self._open_update_url)

    def enable_update_check(self, on_check: Callable[[], None]) -> None:
        """Reveal + wire the "Check for updates" menu entry.

        Called by the GUI only when an :class:`ExeUpdateChecker` is
        configured (i.e. the version URL isn't disabled). *on_check* is
        invoked on the GUI thread when the user clicks the entry; it
        should kick the actual (network) check onto a worker thread so
        the menu doesn't block.
        """
        self._check_action.triggered.connect(lambda _checked=False: on_check())
        self._check_action.setVisible(True)

    def enable_setup(self, on_open: Callable[[], None]) -> None:
        """Reveal + wire the "Setup & troubleshooting…" menu entry.

        *on_open* is invoked on the GUI thread when the user clicks the
        entry; the GUI uses it to (re-assess and) show the first-run
        wizard dialog. Only called when a GUI is present, so a headless
        run never shows a dead menu item.
        """
        self._setup_action.triggered.connect(lambda _checked=False: on_open())
        self._setup_action.setVisible(True)

    def show_update_available(self, title: str, body: str, url: str) -> None:
        """Surface an available EXE update: balloon + "Download update" entry.

        *title* / *body* come from
        :func:`auto_update.exe_update.update_available_message` so the
        wording lives in one tested place. *url* is opened when the
        user clicks the balloon or the menu entry — the GUI passes the
        release page so the user lands on the download + install
        instructions rather than triggering a raw ZIP download.
        Notify-only: nothing is downloaded here.
        """
        self._update_url = url
        self._download_action.setVisible(True)
        self._update_separator.setVisible(True)
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 10_000)

    def notify(self, title: str, body: str, *, open_url: str | None = None) -> None:
        """Show a plain informational balloon.

        Used for manual-check feedback ("up to date", "couldn't
        check"). *open_url* sets what a click on this balloon opens
        (``None`` clears any pending URL so the click is a no-op).
        """
        self._update_url = open_url
        self.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 8_000)

    @pyqtSlot()
    def _open_update_url(self) -> None:
        """Open the pending update URL in the default browser, if set."""
        if self._update_url:
            QDesktopServices.openUrl(QUrl(self._update_url))

    def _open_feedback(self) -> None:
        """Open the feedback destination (Google Form or Issues) in the browser.

        The URL — pre-filled with app/data/OS versions when a Google Form
        is configured, otherwise the public data repo's Issues page — is
        built by the tested, Qt-free :mod:`feedback` module.
        """
        url = feedback.feedback_url()
        log.info("opening feedback URL: %s", url)
        QDesktopServices.openUrl(QUrl(url))

    def _open_about(self) -> None:
        """Show the About box (version + FCP disclaimer + data attributions).

        The text is built by the tested, Qt-free :mod:`about` module; only
        the ``QMessageBox`` lives here. Parent is ``None`` — the tray has no
        window, and the overlay hides itself whenever Arena isn't running, so
        a top-level dialog is the only reliable anchor.
        """
        QMessageBox.about(None, about.ABOUT_TITLE, about.about_text())

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
