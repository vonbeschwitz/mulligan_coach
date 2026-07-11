"""Scrollable "How Mulligan Coach works" dialog (thin Qt layer).

Renders the markdown loaded by the tested, Qt-free :mod:`how_it_works`
module in a ``QTextBrowser``. A plain ``QMessageBox`` (the About box's
surface) can't scroll, and the document is a few screens long — hence
a small dedicated dialog. Same untested-Qt-glue pattern as
:mod:`first_run_dialog`: every decision that can go wrong (where the
file lives, what to show when it's missing) is unit-tested upstream.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import how_it_works


def show_how_it_works(parent: QWidget | None = None) -> None:
    """Open the document in a modal, scrollable, resizable dialog.

    Modal (``exec``) for the same lifetime reason the About box is:
    the tray has no parent window to own a modeless dialog, and a
    garbage-collected QDialog silently vanishes. The user reads, then
    closes.
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle(how_it_works.HOW_IT_WORKS_TITLE)
    # Wide enough that the prose doesn't wrap awkwardly, tall enough
    # to show a full section; resizable beyond that.
    dialog.resize(680, 760)

    layout = QVBoxLayout(dialog)

    browser = QTextBrowser(dialog)
    # ``QTextBrowser`` renders markdown natively; the document's
    # external links (17Lands) should open in the system browser
    # rather than navigate the widget.
    browser.setMarkdown(how_it_works.how_it_works_markdown())
    browser.setOpenExternalLinks(True)
    layout.addWidget(browser)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)

    dialog.exec()
