"""First-run setup wizard dialog (thin Qt layer over :mod:`first_run`).

Renders the :class:`~mulligan_coach_overlay.first_run.SetupStatus`
produced by the tested :mod:`first_run` logic and walks a first-time
user through the one manual step the overlay needs — enabling Arena's
**Detailed Logs (Plugin Support)** setting — plus surfaces "Arena not
found" and "card database not found" clearly instead of the overlay
just sitting silent.

Deliberately thin: every non-trivial decision (what's wrong, whether to
show at all, where the CardDatabase is, what to persist) lives in the
Qt-free :mod:`first_run` / :mod:`arena_card_db` modules, which are unit
tested. This file only maps a ``SetupStatus`` to labels + buttons and
wires the folder picker — the same untested-Qt-glue pattern as
:mod:`gui` / :mod:`tray`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .arena_card_db import find_card_database_under
from .first_run import (
    FirstRunState,
    SetupStatus,
    assess_setup,
    reconcile_state,
    save_first_run_state,
)
from .stats_html import (
    _KEEP_COLOR,
    _MARGINAL_COLOR,
    _MULL_COLOR,
    _TEXT_MUTED,
    _TEXT_PRIMARY,
)

log = logging.getLogger(__name__)

_OK = "✓"  # ✓
_BAD = "✗"  # ✗
_QMARK = "?"

# Step-by-step guide shown when Detailed Logs isn't confirmed on. Plain
# text (no mana symbols / WotC glyphs — see the compliance red lines).
_DETAILED_LOGS_GUIDE = (
    "The overlay needs Arena's <b>Detailed Logs (Plugin Support)</b> setting. "
    "To turn it on:<br>"
    "&nbsp;&nbsp;1. Open MTG Arena.<br>"
    "&nbsp;&nbsp;2. Click the gear (Options) icon, top-right.<br>"
    "&nbsp;&nbsp;3. Open the <b>Account</b> tab.<br>"
    "&nbsp;&nbsp;4. Tick <b>Detailed Logs (Plugin Support)</b>.<br>"
    "&nbsp;&nbsp;5. Restart MTG Arena, then click <b>Re-check</b> below."
)


class FirstRunDialog(QDialog):
    """Setup status + guidance, with Re-check and a folder-picker fallback.

    Owns the persisted :class:`FirstRunState` so a folder the user picks
    is remembered for next launch. Re-assessment goes back through
    :func:`first_run.assess_setup` so the dialog always reflects live
    state, never a stale snapshot.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        status: SetupStatus,
        state: FirstRunState,
        state_path: Path,
        log_path: Path | None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._state_path = state_path
        self._log_path = log_path
        self._status = status

        self.setWindowTitle("Mulligan Coach — Setup")
        self.setMinimumWidth(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        outer.setSpacing(10)

        self._heading = QLabel()
        self._heading.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 15px; font-weight: 700;")
        self._heading.setWordWrap(True)
        outer.addWidget(self._heading)

        # Three status rows: Arena log, Detailed Logs, Card database.
        self._arena_row = _StatusRow("MTG Arena log")
        self._detailed_row = _StatusRow("Detailed Logs setting")
        self._carddb_row = _StatusRow("Arena card database")
        for row in (self._arena_row, self._detailed_row, self._carddb_row):
            outer.addWidget(row.widget)

        self._guide = QLabel()
        self._guide.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 11px;")
        self._guide.setTextFormat(Qt.TextFormat.RichText)
        self._guide.setWordWrap(True)
        outer.addWidget(self._guide)

        self._detail = QLabel()
        self._detail.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 10px;")
        self._detail.setWordWrap(True)
        outer.addWidget(self._detail)

        # Buttons.
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self._recheck_btn = QPushButton("Re-check")
        self._recheck_btn.clicked.connect(self._on_recheck)
        self._locate_btn = QPushButton("Locate Arena install…")
        self._locate_btn.clicked.connect(self._on_locate)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_row.addWidget(self._recheck_btn)
        button_row.addWidget(self._locate_btn)
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        outer.addLayout(button_row)

        self._render()

    # -- public ------------------------------------------------------

    def refresh(self) -> None:
        """Re-assess from scratch and re-render. Used when re-opened."""
        self._on_recheck()

    # -- rendering ---------------------------------------------------

    def _render(self) -> None:
        status = self._status
        if status.all_clear:
            self._heading.setText("You're all set ✓")
        elif not status.arena_log_found:
            self._heading.setText("Waiting for MTG Arena")
        else:
            self._heading.setText("One quick setup step")

        self._arena_row.set(
            _OK if status.arena_log_found else _QMARK,
            _KEEP_COLOR if status.arena_log_found else _MARGINAL_COLOR,
            "found" if status.arena_log_found else "not found yet — is Arena installed and run?",
        )
        self._detailed_row.set(
            *_detailed_indicator(status.detailed_logs),
        )
        self._carddb_row.set(
            _OK if status.card_db_found else _BAD,
            _KEEP_COLOR if status.card_db_found else _MULL_COLOR,
            "found" if status.card_db_found else "not found — Arena installed? try Locate below",
        )

        # Guide only when Detailed Logs isn't confirmed on.
        if status.detailed_logs != "enabled":
            self._guide.setText(_DETAILED_LOGS_GUIDE)
            self._guide.setVisible(True)
        else:
            self._guide.setVisible(False)

        # Detail line: show the resolved paths so a user filing a bug
        # (or hunting a non-standard install) can see what we looked at.
        details: list[str] = []
        if status.log_path is not None:
            details.append(f"Log: {status.log_path}")
        if status.card_db_path is not None:
            details.append(f"Card DB: {status.card_db_path}")
        self._detail.setText("\n".join(details))

        # The folder picker is only useful when the card DB is missing.
        self._locate_btn.setVisible(not status.card_db_found)

    # -- actions -----------------------------------------------------

    def _on_recheck(self) -> None:
        self._status = assess_setup(log_path=self._log_path, card_db_dir=self._state.card_db_dir)
        # Persist the "verified" flag the first time everything is green,
        # so we stop auto-popping the wizard on later launches.
        new_state = reconcile_state(self._status, self._state)
        if new_state != self._state:
            self._state = new_state
            self._save_state()
        self._render()

    def _on_locate(self) -> None:
        picked = QFileDialog.getExistingDirectory(
            self,
            "Select your MTG Arena install folder",
            "",
        )
        if not picked:
            return
        db = find_card_database_under(Path(picked))
        if db is None:
            self._detail.setText(
                f"No Raw_CardDatabase file found under {picked}. "
                "Pick the folder that contains MTGA_Data (or MTGA_Data\\Downloads\\Raw)."
            )
            return
        # Persist the *directory* (survives Arena content patches renaming
        # the file), then re-assess so the row flips to found.
        self._state = FirstRunState(
            detailed_logs_verified=self._state.detailed_logs_verified,
            card_db_dir=str(db.parent),
        )
        self._save_state()
        self._on_recheck()

    def _save_state(self) -> None:
        try:
            save_first_run_state(self._state_path, self._state)
        except OSError:
            log.exception("could not persist first-run state to %s", self._state_path)

    # State is exposed so the caller (gui.main) can pick up a card_db_dir
    # the user set here without re-reading the file.
    @property
    def state(self) -> FirstRunState:
        return self._state


class _StatusRow:
    """A single "glyph + name + detail" row inside the dialog."""

    def __init__(self, name: str) -> None:
        self.widget = QWidget()
        layout = QHBoxLayout(self.widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._glyph = QLabel()
        self._glyph.setFixedWidth(16)
        self._glyph.setStyleSheet("font-size: 13px; font-weight: 700;")
        name_label = QLabel(name)
        name_label.setStyleSheet(f"color: {_TEXT_PRIMARY}; font-size: 12px;")
        name_label.setFixedWidth(150)
        self._detail = QLabel()
        self._detail.setStyleSheet(f"color: {_TEXT_MUTED}; font-size: 12px;")
        self._detail.setWordWrap(True)
        layout.addWidget(self._glyph)
        layout.addWidget(name_label)
        layout.addWidget(self._detail, stretch=1)

    def set(self, glyph: str, color: str, detail: str) -> None:
        self._glyph.setText(glyph)
        self._glyph.setStyleSheet(f"color: {color}; font-size: 13px; font-weight: 700;")
        self._detail.setText(detail)


def _detailed_indicator(status: str) -> tuple[str, str, str]:
    """(glyph, colour, detail) for a Detailed-Logs status value."""
    if status == "enabled":
        return _OK, _KEEP_COLOR, "enabled"
    if status == "disabled":
        return _BAD, _MULL_COLOR, "disabled in Arena — see below"
    return _QMARK, _MARGINAL_COLOR, "unknown — enable it in Arena, then Re-check"
