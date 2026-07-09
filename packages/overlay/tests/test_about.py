"""Unit tests for :mod:`mulligan_coach_overlay.about`.

The About text carries compliance-mandated content (the verbatim Fan
Content Policy disclaimer + the 17Lands / Scryfall / MTGJSON
attributions), so these tests assert that content is present and
well-formed — they're a guard against someone paraphrasing the
disclaimer or dropping an attribution during a refactor. Pure string
assertions; no Qt context.
"""

from __future__ import annotations

from mulligan_coach_overlay import about


def test_about_html_includes_versions() -> None:
    html = about.about_html(app_version="20260707T233501Z+42d110d", data_version="2026-07-07")
    assert "20260707T233501Z+42d110d" in html
    assert "2026-07-07" in html


def test_about_html_includes_verbatim_fcp_disclaimer() -> None:
    html = about.about_html(app_version="v", data_version="d")
    # Verbatim — the FCP requires this exact wording, so assert the whole
    # string rather than a fragment.
    assert about.FCP_DISCLAIMER in html
    assert "unofficial Fan Content permitted under the Fan Content Policy" in html
    assert "©Wizards of the Coast LLC." in html


def test_about_html_includes_all_attributions_with_links() -> None:
    html = about.about_html(app_version="v", data_version="d")
    for name, url, _note in about.ATTRIBUTIONS:
        assert name in html
        assert f'href="{url}"' in html
    # 17Lands citation must be present and must not imply endorsement.
    assert "17Lands does not endorse this tool." in html
    assert "CC BY 4.0" in html


def test_about_html_includes_privacy_note_and_project_link() -> None:
    html = about.about_html(app_version="v", data_version="d")
    assert about.PRIVACY_NOTE in html
    assert about.PROJECT_URL in html


def test_about_text_smoke() -> None:
    # End-to-end through the live version helpers: returns non-empty HTML
    # carrying the disclaimer, without touching Qt.
    text = about.about_text()
    assert isinstance(text, str) and text
    assert about.FCP_DISCLAIMER in text
