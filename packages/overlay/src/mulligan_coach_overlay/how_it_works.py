"""Locate and load the bundled "How Mulligan Coach works" document.

Why this exists
---------------

``docs/how_it_works.md`` explains, in user-facing language, where the
keep/mulligan number comes from and what the algorithm does and does
not model. The main repo is private, so the document can't just be a
web link — it ships *inside* the app (the PyInstaller spec bundles it
under ``docs/`` next to the data payload) and both the tray menu and
the overlay's gear menu open it in a scrollable dialog.

Like :mod:`about` and :mod:`feedback`, this module is pure and
unit-tested; the Qt dialog that renders the markdown lives in
:mod:`how_it_works_dialog` (the same Qt-free-logic / thin-Qt-glue
split used throughout the overlay).
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import _frozen, about

log = logging.getLogger(__name__)

HOW_IT_WORKS_TITLE = "How Mulligan Coach works"

# Shown in place of the document if the file is missing or unreadable
# (a broken install, an over-zealous cleanup tool). Points at the
# public data repo — the only URL end users can actually reach.
FALLBACK_TEXT = (
    "# How Mulligan Coach works\n\n"
    "The bundled documentation file could not be found. Your install "
    "may be incomplete — reinstalling should fix it.\n\n"
    f"Project page: <{about.PROJECT_URL}>\n"
)


def how_it_works_path() -> Path:
    """Return the expected on-disk path of ``how_it_works.md``.

    Frozen (PyInstaller) builds ship the file at
    ``_internal/docs/how_it_works.md`` — the spec's ``DATAS`` entry —
    so it resolves relative to the bundle root. From source it lives
    at ``<repo>/docs/how_it_works.md``; the ``parents[4]`` walk from
    this file mirrors the repo-root resolution the other packages use
    (see :mod:`_frozen`'s module docstring).
    """
    bundle = _frozen.bundle_root()
    if bundle is not None:
        return bundle / "docs" / "how_it_works.md"
    return Path(__file__).resolve().parents[4] / "docs" / "how_it_works.md"


def how_it_works_markdown(path: Path | None = None) -> str:
    """Read the document's markdown, falling back to a stub on failure.

    *path* is injectable for tests; production callers pass nothing
    and get :func:`how_it_works_path`. Never raises — a missing doc
    should degrade to a "reinstall" note, not a crash dialog, because
    the caller is a menu click handler.
    """
    doc_path = how_it_works_path() if path is None else path
    try:
        return doc_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("could not read how-it-works doc at %s: %s", doc_path, exc)
        return FALLBACK_TEXT
