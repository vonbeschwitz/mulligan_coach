"""Unit tests for :mod:`mulligan_coach_overlay.feedback` URL construction.

Covers both destinations (unconfigured Issues fallback, configured
Google Form pre-fill), query encoding, and partial configuration. The
module-level config constants are monkeypatched so tests don't depend on
whether a real form URL has been pasted in yet.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from mulligan_coach_overlay import feedback

_FORM_BASE = "https://docs.google.com/forms/d/e/FAKEID/viewform"


def _configure_form(
    monkeypatch: pytest.MonkeyPatch,
    *,
    url: str = _FORM_BASE,
    app: str = "111",
    data: str = "222",
    os_id: str = "333",
) -> None:
    monkeypatch.setattr(feedback, "FEEDBACK_FORM_URL", url)
    monkeypatch.setattr(feedback, "_ENTRY_APP_VERSION", app)
    monkeypatch.setattr(feedback, "_ENTRY_DATA_VERSION", data)
    monkeypatch.setattr(feedback, "_ENTRY_OS", os_id)


# ---------------------------------------------------------------------------
# Unconfigured: fall back to Issues
# ---------------------------------------------------------------------------


def test_unconfigured_returns_issues_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "FEEDBACK_FORM_URL", "")
    assert feedback.form_is_configured() is False
    url = feedback.build_feedback_url("1.0", "2026-01-01", "Windows-11")
    assert url == feedback.ISSUES_FALLBACK_URL


def test_whitespace_only_form_url_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(feedback, "FEEDBACK_FORM_URL", "   ")
    assert feedback.form_is_configured() is False
    assert feedback.build_feedback_url("a", "b", "c") == feedback.ISSUES_FALLBACK_URL


# ---------------------------------------------------------------------------
# Configured: pre-filled Google Form
# ---------------------------------------------------------------------------


def test_configured_builds_prefilled_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_form(monkeypatch)
    url = feedback.build_feedback_url("20260703+abc123", "2026-06-30", "Windows-11-10.0.26200")
    split = urlsplit(url)
    assert url.startswith(_FORM_BASE + "?")
    assert split.path == "/forms/d/e/FAKEID/viewform"
    params = parse_qs(split.query)
    assert params["entry.111"] == ["20260703+abc123"]
    assert params["entry.222"] == ["2026-06-30"]
    assert params["entry.333"] == ["Windows-11-10.0.26200"]
    # Google's pre-fill marker is present.
    assert params["usp"] == ["pp_url"]


def test_values_are_url_encoded(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_form(monkeypatch)
    # Values with spaces, '+', '&', '=' must survive a round-trip intact.
    tricky_os = "Windows 11 & stuff = a+b"
    url = feedback.build_feedback_url("v1", "d1", tricky_os)
    # Raw '+' / '&' / '=' from the value must not appear unescaped in the
    # query as structural characters — parse_qs decoding must recover them.
    params = parse_qs(urlsplit(url).query)
    assert params["entry.333"] == [tricky_os]


def test_partial_config_omits_empty_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    # Form URL set, but only the app-version entry id filled in.
    _configure_form(monkeypatch, data="", os_id="")
    url = feedback.build_feedback_url("v1", "d1", "os1")
    params = parse_qs(urlsplit(url).query)
    assert params["entry.111"] == ["v1"]
    assert "entry.222" not in params
    assert "entry.333" not in params


def test_form_url_but_no_entries_opens_bare_form(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_form(monkeypatch, app="", data="", os_id="")
    url = feedback.build_feedback_url("v1", "d1", "os1")
    assert url == _FORM_BASE


def test_form_url_with_existing_query_uses_ampersand(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the owner pastes a URL that already carries a query string, we
    # must append with '&', not a second '?'.
    _configure_form(monkeypatch, url=_FORM_BASE + "?foo=bar")
    url = feedback.build_feedback_url("v1", "d1", "os1")
    assert url.count("?") == 1
    params = parse_qs(urlsplit(url).query)
    assert params["foo"] == ["bar"]
    assert params["entry.111"] == ["v1"]


# ---------------------------------------------------------------------------
# Live context helpers (smoke: always return non-empty strings)
# ---------------------------------------------------------------------------


def test_context_helpers_return_strings() -> None:
    assert isinstance(feedback.app_version(), str) and feedback.app_version()
    assert isinstance(feedback.data_version(), str) and feedback.data_version()
    assert isinstance(feedback.os_descriptor(), str) and feedback.os_descriptor()


def test_feedback_url_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unconfigured end-to-end: returns the fallback without touching Qt.
    monkeypatch.setattr(feedback, "FEEDBACK_FORM_URL", "")
    assert feedback.feedback_url() == feedback.ISSUES_FALLBACK_URL
