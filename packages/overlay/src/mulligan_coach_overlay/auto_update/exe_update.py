"""EXE update *notification* channel (notify-only).

Unlike the data auto-updater in this same subpackage — which silently
downloads + swaps ratings / parsed-cards / model artifacts in place —
this module never downloads or replaces the running executable. It
only *checks* whether a newer EXE build has been published and hands
the caller a small result the GUI turns into a "new version available"
notice with a button that opens the download page.

Why notify-only (owner decision 2026-07-03, see
``docs/going_public_plan.md`` decision #1): the EXE ships unsigned for
the open beta. An unsigned binary that silently rewrites its own
executables is the single most antivirus-suspicious pattern there is.
Full swap-on-restart self-update is deferred to Phase 2, gated on code
signing. Until then the user does the download + extract themselves,
which is exactly what SmartScreen/AV expect a human to do.

What it polls
-------------

``publish_exe_release.py`` uploads an ``exe_version.json`` sidecar to
the ``exe-latest`` release on the public ``mulligan_coach_data`` repo.
Its shape (schema_version=1)::

    {
      "schema_version": 1,
      "generated_at": "2026-05-25T23:59:06Z",
      "bundle_version": "20260525T235826Z+177d370",
      "download_url": "https://github.com/.../exe-latest/MulliganCoach.zip",
      "sha256": "81ee80dd...",
      "size_bytes": 127355710,
      "release_page": "https://github.com/.../releases/tag/exe-latest"
    }

We compare the sidecar's ``bundle_version`` to the *running* EXE's
stamp (``_internal/_bundle_version.txt``, written by
``build_distribution.py``). Both are ``"<UTC-timestamp>+<git-hash>"``
strings, e.g. ``20260525T235826Z+177d370``; the timestamp prefix sorts
lexicographically and parses with ``%Y%m%dT%H%M%SZ``.

Error tolerance
---------------

Mirrors the data updater: a failed check never raises to the caller
and never disturbs the overlay. Offline users, a 404, malformed JSON,
a too-new schema — all collapse to a ``"unknown"`` result with a
diagnostic message. The GUI stays silent on ``unknown`` for automatic
checks and shows a gentle "couldn't check" only when the *user* asked
(manual "Check for updates").
"""

from __future__ import annotations

import datetime
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from .manifest import validate_fetch_url

log = logging.getLogger(__name__)


DEFAULT_EXE_VERSION_URL = (
    "https://github.com/vonbeschwitz/mulligan_coach_data/"
    "releases/download/exe-latest/exe_version.json"
)
"""Where the published ``exe_version.json`` sidecar lives.

Points at the floating ``exe-latest`` release on the public
``mulligan_coach_data`` repo — the same host the data manifest uses,
and the exact URL ``publish_exe_release.py`` uploads to. Overridable
via :envvar:`MULLIGAN_COACH_EXE_VERSION_URL` (set it empty to disable
the EXE update check entirely, mirroring the data updater's
``MULLIGAN_COACH_MANIFEST_URL`` switch)."""


_EXE_VERSION_SCHEMA_VERSION = 1
"""Highest ``exe_version.json`` schema this client understands.

A published sidecar with a higher ``schema_version`` is refused (we'd
be guessing at fields we don't know) — the check returns ``unknown``
rather than misreporting, which nudges the user to update through the
normal channel anyway."""


_CHECK_TIMEOUT_SECONDS = 15.0
"""Per-request timeout for the sidecar fetch.

The sidecar is a few hundred bytes; this is generous. A short timeout
means a flaky connection gives up fast and the next scheduled check
retries, rather than hanging a background thread."""


_STAMP_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
"""Format of the timestamp prefix in a bundle-version stamp.

Matches what ``build_distribution._stamp_bundle_version`` writes:
``datetime.strftime("%Y%m%dT%H%M%SZ") + "+" + short_git_hash``."""


UpdateStatus = Literal["update_available", "up_to_date", "unknown"]
"""Outcome of an EXE update check.

* ``update_available`` — a strictly-newer build is published.
* ``up_to_date`` — running the latest (or newer, for a dev build).
* ``unknown`` — couldn't determine (offline, parse error, running
  from source with no bundle stamp, EXE-check disabled). Never an
  error the caller has to handle — just "no actionable answer".
"""


class ExeVersionParseError(ValueError):
    """The ``exe_version.json`` sidecar couldn't be decoded.

    Raised internally by :func:`parse_exe_version` and always caught
    by :meth:`ExeUpdateChecker.check`, which folds it into an
    ``unknown`` result. Public only so tests can assert on the parse
    layer directly.
    """


@dataclass(frozen=True)
class ExeVersionInfo:
    """Parsed ``exe_version.json`` sidecar.

    ``download_url`` is the direct ZIP; ``release_page`` is the
    human-facing GitHub release (notes + SmartScreen context). The GUI
    opens ``release_page`` by default so the user lands somewhere that
    explains the SmartScreen prompt rather than triggering a raw
    ~130 MB download on click. ``sha256`` / ``size_bytes`` are carried
    for display / diagnostics only — notify-only means we never fetch
    the ZIP, so we never verify them here.
    """

    bundle_version: str
    download_url: str
    release_page: str
    sha256: str | None = None
    size_bytes: int | None = None
    schema_version: int = _EXE_VERSION_SCHEMA_VERSION


@dataclass(frozen=True)
class ExeUpdateResult:
    """Result of one :meth:`ExeUpdateChecker.check` call.

    ``latest`` is populated whenever the sidecar parsed, even on an
    ``unknown`` result (e.g. we fetched it but couldn't read the
    running version) — so a manual check can still tell the user what
    the newest published build is. ``message`` is a short diagnostic
    for the log and, on ``unknown`` after a *manual* check, the balloon
    body.
    """

    status: UpdateStatus
    running_version: str | None
    latest: ExeVersionInfo | None
    message: str = ""


def parse_exe_version(raw: str | bytes) -> ExeVersionInfo:
    """Decode the ``exe_version.json`` bytes/text into :class:`ExeVersionInfo`.

    Strict on the fields we depend on (``bundle_version``,
    ``download_url``, ``release_page`` must be non-empty strings; the
    two URLs must pass the HTTP(S) scheme allowlist shared with the
    data manifest). Tolerant of unknown extra keys. Refuses a
    ``schema_version`` newer than this client supports.

    Raises
    ------
    ExeVersionParseError:
        Malformed JSON, wrong top-level shape, missing/blank required
        field, disallowed URL scheme, or a too-new schema version.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExeVersionParseError(f"exe_version.json is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ExeVersionParseError(
            f"exe_version.json top-level must be an object; got {type(payload).__name__}"
        )

    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ExeVersionParseError(
            f"exe_version.json missing or invalid 'schema_version' (got {schema_version!r})"
        )
    if schema_version > _EXE_VERSION_SCHEMA_VERSION:
        raise ExeVersionParseError(
            f"exe_version.json schema_version={schema_version} is newer than this client "
            f"supports (max={_EXE_VERSION_SCHEMA_VERSION}); update the overlay."
        )

    bundle_version = _require_str(payload, "bundle_version")
    download_url = _require_str(payload, "download_url")
    release_page = _require_str(payload, "release_page")
    for field_name, url in (("download_url", download_url), ("release_page", release_page)):
        try:
            validate_fetch_url(url, context=f"exe_version.{field_name}")
        except ValueError as exc:
            raise ExeVersionParseError(str(exc)) from exc

    sha256 = payload.get("sha256")
    if sha256 is not None and not isinstance(sha256, str):
        raise ExeVersionParseError(f"exe_version.json 'sha256' must be a string (got {sha256!r})")

    size_bytes = payload.get("size_bytes")
    if size_bytes is not None and (
        not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0
    ):
        raise ExeVersionParseError(
            f"exe_version.json 'size_bytes' must be a non-negative int (got {size_bytes!r})"
        )

    return ExeVersionInfo(
        bundle_version=bundle_version,
        download_url=download_url,
        release_page=release_page,
        sha256=sha256,
        size_bytes=size_bytes,
        schema_version=schema_version,
    )


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExeVersionParseError(f"exe_version.json missing non-empty '{key}' string")
    return value


def _parse_stamp_timestamp(stamp: str) -> datetime.datetime | None:
    """Extract the UTC timestamp from a ``"<timestamp>+<hash>"`` stamp.

    Returns ``None`` if the prefix doesn't parse — e.g. a
    timestamp-only ``"nogit"`` fallback stamp, or a hand-edited value.
    Callers treat ``None`` as "can't order this stamp".
    """
    prefix = stamp.split("+", 1)[0].strip()
    try:
        return datetime.datetime.strptime(prefix, _STAMP_TIMESTAMP_FORMAT).replace(
            tzinfo=datetime.UTC
        )
    except ValueError:
        return None


def is_newer_bundle_version(published: str, running: str) -> bool:
    """``True`` if *published* is a strictly newer build than *running*.

    Primary path: compare the parsed UTC timestamp prefixes. Both
    stamps are minted by ``build_distribution.py`` from
    ``datetime.now(UTC)`` at build time, so a later build always has a
    later timestamp regardless of git history.

    Fallback (either prefix unparseable): identical strings are never
    an update; any *difference* is reported as an available update.
    This is the notify-only-safe bias — a spurious "update available"
    notice costs the user one dismissed balloon, whereas a missed
    update on a frozen EXE could leave them stuck on a broken build
    after an Arena log-format change.
    """
    published = published.strip()
    running = running.strip()
    if published == running:
        return False
    pub_ts = _parse_stamp_timestamp(published)
    run_ts = _parse_stamp_timestamp(running)
    if pub_ts is not None and run_ts is not None:
        return pub_ts > run_ts
    # Unparseable on at least one side; strings already known unequal.
    return True


@dataclass
class ExeUpdateChecker:
    """Fetches the sidecar and compares it to the running EXE's stamp.

    Pure of Qt / disk-write side effects: the only I/O is the single
    read-only HTTP GET of the sidecar. The GUI runs :meth:`check` on a
    daemon thread and marshals the :class:`ExeUpdateResult` back to the
    UI thread.

    Attributes
    ----------
    version_url:
        URL of the ``exe_version.json`` sidecar. Empty string disables
        the check (``check`` returns ``unknown`` with a "disabled"
        message) — the GUI treats an empty configured URL as "don't
        wire the menu entry at all", so this only bites a directly
        constructed checker.
    running_version:
        The running EXE's ``bundle_version`` stamp, or ``None`` when it
        can't be determined (running from source, or a bundle missing
        its stamp file). ``None`` forces an ``unknown`` result — we
        can't say "newer than what?".
    timeout_seconds:
        Per-request socket timeout for the fetch.
    """

    version_url: str
    running_version: str | None
    timeout_seconds: float = _CHECK_TIMEOUT_SECONDS

    def check(self) -> ExeUpdateResult:
        """One check pass; never raises.

        Any failure — disabled, offline, HTTP error, malformed JSON,
        unknown running version — becomes an ``unknown`` result with a
        diagnostic ``message``. Only a clean fetch + parse + comparison
        yields ``update_available`` / ``up_to_date``.
        """
        if not self.version_url.strip():
            return ExeUpdateResult(
                status="unknown",
                running_version=self.running_version,
                latest=None,
                message="EXE update check disabled (no version URL configured).",
            )

        try:
            latest = self._fetch_latest()
        except _ExeCheckError as exc:
            log.info("exe-update: check could not complete: %s", exc)
            return ExeUpdateResult(
                status="unknown",
                running_version=self.running_version,
                latest=None,
                message=str(exc),
            )

        if self.running_version is None:
            # We know the latest but not what we're running (dev / from
            # source / missing stamp). Report it without a verdict so a
            # manual check can still surface the newest published build.
            return ExeUpdateResult(
                status="unknown",
                running_version=None,
                latest=latest,
                message="Running version unknown (not a packaged build).",
            )

        if is_newer_bundle_version(latest.bundle_version, self.running_version):
            return ExeUpdateResult(
                status="update_available",
                running_version=self.running_version,
                latest=latest,
                message=(
                    f"newer build available: {latest.bundle_version} "
                    f"(running {self.running_version})"
                ),
            )
        return ExeUpdateResult(
            status="up_to_date",
            running_version=self.running_version,
            latest=latest,
            message=f"up to date ({self.running_version})",
        )

    def _fetch_latest(self) -> ExeVersionInfo:
        """Fetch + parse the sidecar; wrap every failure in ``_ExeCheckError``."""
        try:
            validate_fetch_url(self.version_url, context="exe_version_url")
        except ValueError as exc:
            raise _ExeCheckError(str(exc)) from exc
        request = urllib.request.Request(
            self.version_url,
            headers={"User-Agent": "mulligan-coach-overlay/exe-update-check"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            raise _ExeCheckError(f"fetch error: {exc}") from exc
        try:
            return parse_exe_version(raw)
        except ExeVersionParseError as exc:
            raise _ExeCheckError(f"parse error: {exc}") from exc


class _ExeCheckError(RuntimeError):
    """Internal "couldn't get a usable sidecar" marker.

    Caught by :meth:`ExeUpdateChecker.check` and folded into an
    ``unknown`` result — not part of the public surface.
    """


def manual_check_message(result: ExeUpdateResult) -> tuple[str, str]:
    """Balloon (title, body) to show after a *user-initiated* check.

    Manual checks always give feedback — the user clicked "Check for
    updates" and expects an answer, including "you're up to date" and
    "couldn't check". Automatic checks reuse only the
    ``update_available`` title/body (via :func:`update_available_message`)
    and stay silent otherwise.
    """
    if result.status == "update_available":
        return update_available_message(result)
    if result.status == "up_to_date":
        return (
            "Mulligan Coach is up to date",
            "You're running the latest published version.",
        )
    # unknown
    return (
        "Couldn't check for updates",
        "Mulligan Coach couldn't reach the update server. "
        "Check your connection and try again later.",
    )


def update_available_message(result: ExeUpdateResult) -> tuple[str, str]:
    """Balloon (title, body) for an available update (manual or automatic)."""
    version = result.latest.bundle_version if result.latest is not None else "a new version"
    return (
        "Update available",
        f"A newer Mulligan Coach build ({version}) is available. "
        "Click here to open the download page.",
    )
