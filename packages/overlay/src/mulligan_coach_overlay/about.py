"""Build the in-app "About" text: version, Fan Content Policy disclaimer, attributions.

Why this exists
---------------

``docs/compliance_position.md`` lists — as a launch prerequisite — that
the Wizards **Fan Content Policy (FCP) disclaimer** and the **17Lands
(CC BY 4.0) / Scryfall / MTGJSON attributions** must be shown *inside the
app*, "clearly visible at the top level, not hidden in a footnote," with a
link and without implying endorsement. This module builds that text.

Like :mod:`feedback`, it's pure and unit-tested; the ``QMessageBox`` that
actually displays it lives in :mod:`tray` and :mod:`gui` (the same
Qt-free-logic / thin-Qt-glue split used throughout the overlay). Keeping
the strings here — not inline in the Qt code — lets the tests assert the
disclaimer is present verbatim and lets a future landing-page/README
generator reuse the exact same constants.
"""

from __future__ import annotations

from . import feedback

ABOUT_TITLE = "About Mulligan Coach"

PROJECT_URL = "https://github.com/vonbeschwitz/mulligan_coach_data"
"""Public repo users land on — the takedown/contact surface + downloads."""

CONTACT_EMAIL = "bastian.vonbeschwitz@gmail.com"

# Verbatim per docs/compliance_position.md's pre-launch checklist. Do NOT
# paraphrase — the FCP requires this exact wording.
FCP_DISCLAIMER = (
    "Mulligan Coach is unofficial Fan Content permitted under the Fan "
    "Content Policy. Not approved/endorsed by Wizards. Portions of the "
    "materials used are property of Wizards of the Coast. ©Wizards of the "
    "Coast LLC."
)

# (name, url, note) per data source. 17Lands' usage guidelines require a
# top-level, linked citation that does NOT imply endorsement (hence the
# explicit "does not endorse" clause and the capital-L styling); the public
# datasets the model trains on are CC BY 4.0. Scryfall + MTGJSON get
# courtesy attribution, no logos.
ATTRIBUTIONS: tuple[tuple[str, str, str], ...] = (
    (
        "17Lands",
        "https://www.17lands.com",
        "Card statistics derived from 17Lands public data (CC BY 4.0). "
        "17Lands does not endorse this tool.",
    ),
    ("Scryfall", "https://scryfall.com", "Card data from Scryfall."),
    ("MTGJSON", "https://mtgjson.com", "Arena card identifiers from MTGJSON."),
)

PRIVACY_NOTE = (
    "Mulligan Coach reads only your MTG Arena log file to detect your hand "
    "and deck. Nothing about your games ever leaves your computer."
)


def about_html(*, app_version: str, data_version: str) -> str:
    """Build the About dialog's rich text (HTML), given the version context.

    Pure function of its arguments + the module constants, so tests can
    assert the disclaimer / attributions are present without a Qt context.
    Rendered by ``QMessageBox.about`` in :mod:`tray` / :mod:`gui`; the
    ``<a href>`` links are shown as clickable links there and remain
    readable as plain text if a platform doesn't activate them.
    """
    attribution_items = "".join(
        f'<li><a href="{url}">{name}</a> — {note}</li>' for name, url, note in ATTRIBUTIONS
    )
    return (
        "<h3>Mulligan Coach</h3>"
        "<p>A keep / mulligan decision helper for MTG Arena Limited.</p>"
        f"<p><b>App version:</b> {app_version}<br>"
        f"<b>Data version:</b> {data_version}</p>"
        f"<p>{PRIVACY_NOTE}</p>"
        "<p><b>Data sources</b></p>"
        f"<ul>{attribution_items}</ul>"
        f"<p><small>{FCP_DISCLAIMER}</small></p>"
        f'<p><a href="{PROJECT_URL}">{PROJECT_URL}</a><br>'
        f'Contact: <a href="mailto:{CONTACT_EMAIL}">{CONTACT_EMAIL}</a></p>'
    )


def about_text() -> str:
    """Gather live version context and build the About HTML.

    Convenience wrapper the tray + gui call; :func:`about_html` is the
    pure, tested core. Reuses :mod:`feedback`'s version helpers so the
    ``"source"`` / ``"unknown"`` fallbacks match the feedback form.
    """
    return about_html(app_version=feedback.app_version(), data_version=feedback.data_version())
