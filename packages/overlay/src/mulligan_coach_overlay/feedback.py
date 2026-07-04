"""Build the "Send feedback" URL the tray entry opens.

What this does
--------------

The tray's **"Send feedback…"** entry opens a feedback form in the
user's default browser, pre-filled with the three things that make a
bug report triageable without a back-and-forth: the app (EXE build)
version, the data-bundle version, and the OS. The URL construction is
kept here — pure and unit-tested — while the actual
``QDesktopServices.openUrl`` call lives in :mod:`tray` (the same
Qt-free-logic / thin-Qt-glue split as the rest of the overlay).

Two destinations
-----------------

1. **A Google Form** (preferred; configured in the OWNER CONFIGURATION
   block below). Google Forms supports pre-filling short-answer
   questions via ``entry.<id>=value`` query parameters, so the
   version/OS fields arrive already populated.
2. **GitHub Issues on the public data repo** as the fallback. This works
   *today* (the public ``mulligan_coach_data`` repo has Issues enabled
   even while the main code repo stays private), so the feature ships
   fully functional before the Google Form exists — the entry never
   dead-ends. Issue *templates* for that repo are staged for the owner
   to copy under ``packaging/data_repo_files/`` (see its README).

Until ``FEEDBACK_FORM_URL`` is set, :func:`build_feedback_url` returns
the Issues URL; after it's set, it returns the pre-filled Google Form
URL. Nothing else in the app changes.
"""

from __future__ import annotations

import platform
from urllib.parse import urlencode

from ._frozen import running_bundle_version
from .user_data import seeded_data_version

# ============================================================================
# OWNER CONFIGURATION — Google Form feedback
# ============================================================================
# Configured 2026-07-03 against the owner's live form
# (short link: https://forms.gle/xZKc6Fm1UuEDpiDXA). The entry ids were
# read from the form's public viewform page (Google embeds each
# question's pre-fill id in FB_PUBLIC_LOAD_DATA_ in the page source).
#
# If the form is ever RECREATED (deleting a question and re-adding it
# also changes its id), refresh these four strings:
#   1. Open the form editor -> three-dots menu -> "Get pre-filled link".
#      Type a placeholder into the three version questions, click
#      "Get link". Google produces a URL shaped like:
#         https://docs.google.com/forms/d/e/<FORM_ID>/viewform?usp=pp_url&entry.111=App&entry.222=Data&entry.333=OS
#   2. Paste the part up to and including "/viewform" into
#      FEEDBACK_FORM_URL below.
#   3. Paste each "entry.<NNN>" *number* into the matching _ENTRY_* below
#      (just the digits: "entry.111" -> "111"). Match them to the right
#      question by the placeholder text you typed in step 1.
#
# Setting FEEDBACK_FORM_URL = "" reverts to the GitHub Issues fallback.
FEEDBACK_FORM_URL = (
    "https://docs.google.com/forms/d/e/"
    "1FAIpQLSf4LpTPVScCZsXIGNXZ53y7_S2rRK2bVFLYfL1-ffbIFL0jMw/viewform"
)
_ENTRY_APP_VERSION = "1189621491"  # entry id for the "App version" question
_ENTRY_DATA_VERSION = "1045681741"  # entry id for the "Data version" question
_ENTRY_OS = "1230678341"  # entry id for the "Operating system" question

# Fallback destination while the Google Form is unconfigured (and a
# perfectly good permanent option). Issues on the PUBLIC data repo, which
# is reachable to users even though the code repo is private.
ISSUES_FALLBACK_URL = "https://github.com/vonbeschwitz/mulligan_coach_data/issues"

# Google's own recommended marker on a pre-filled link. Harmless if
# absent, but included so the URL matches what "Get pre-filled link"
# produces.
_PREFILL_MARKER = "usp=pp_url"


def form_is_configured() -> bool:
    """True once the owner has pasted a Google Form URL into the config."""
    return bool(FEEDBACK_FORM_URL.strip())


def app_version() -> str:
    """The running EXE's build stamp, or ``"source"`` when run from a checkout."""
    return running_bundle_version() or "source"


def data_version() -> str:
    """The seeded data-bundle version, or ``"unknown"`` if not yet seeded.

    See :func:`user_data.seeded_data_version` for the caveat that this is
    the seed baseline, not necessarily the live auto-updated data.
    """
    return seeded_data_version() or "unknown"


def os_descriptor() -> str:
    """A concise OS string, e.g. ``"Windows-11-10.0.26200"``.

    ``platform.platform()`` folds system + release + version into one
    field, which is all the triage this needs.
    """
    return platform.platform()


def build_feedback_url(app: str, data: str, os_name: str) -> str:
    """Return the URL to open for feedback, given the version/OS context.

    * If a Google Form is configured (:func:`form_is_configured`), returns
      the ``viewform`` URL with the app/data/OS values pre-filled as
      ``entry.<id>=<value>`` query parameters (properly URL-encoded). Only
      the entry ids that are actually filled in are appended, so a
      partially-configured form still works.
    * Otherwise returns :data:`ISSUES_FALLBACK_URL` unchanged — the form
      isn't set up, so there's nothing to pre-fill.

    Pure function of its arguments + the module-level config, so the tests
    can exercise both branches without touching Qt, the network, or the
    real environment.
    """
    if not form_is_configured():
        return ISSUES_FALLBACK_URL

    params: list[tuple[str, str]] = []
    for entry_id, value in (
        (_ENTRY_APP_VERSION, app),
        (_ENTRY_DATA_VERSION, data),
        (_ENTRY_OS, os_name),
    ):
        if entry_id.strip():
            params.append((f"entry.{entry_id.strip()}", value))

    base = FEEDBACK_FORM_URL.strip()
    query = urlencode(params)
    if not query:
        # Form URL set but no entry ids yet — open the bare form.
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{_PREFILL_MARKER}&{query}"


def feedback_url() -> str:
    """Gather the live version/OS context and build the feedback URL.

    Convenience wrapper the tray calls; :func:`build_feedback_url` is the
    pure, tested core.
    """
    return build_feedback_url(app_version(), data_version(), os_descriptor())
