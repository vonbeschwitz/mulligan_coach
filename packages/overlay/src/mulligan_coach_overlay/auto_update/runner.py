"""End-to-end auto-update orchestration.

What :meth:`UpdateRunner.run` does, in order:

1. Fetches the manifest JSON over HTTP into memory (small —
   a few KB; we don't bother streaming this one).
2. For each artifact the manifest lists:
   a. Computes the local file's SHA256.
   b. If it matches the manifest's SHA256, marks as up-to-date.
   c. Otherwise downloads the artifact to the user state dir,
      atomically replacing the prior file (see
      :mod:`.downloader`).
3. Fires the reload callbacks the caller provided so the running
   overlay picks up the new artifacts in-place (slice 2 wiring).
4. Returns an :class:`UpdateReport` summarising what happened.

Why callbacks instead of direct service references
--------------------------------------------------

This module sits inside ``mulligan_coach_overlay`` so we keep
``mulligan_coach_recommend`` Qt/HTTP-free. Letting the GUI wire up
the callbacks at startup means this orchestration code never has to
import :class:`RecommendationService` (or :class:`OverlayCoordinator`)
and stays trivially unit-testable with synthetic callables.
"""

from __future__ import annotations

import logging
import shutil
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .downloader import DownloadError, download_to_file, sha256_file
from .manifest import (
    Manifest,
    ManifestArtifact,
    ManifestParseError,
    parse_manifest,
)

log = logging.getLogger(__name__)

_MANIFEST_TIMEOUT_SECONDS = 15.0
"""Per-request timeout for the manifest fetch.

Small JSON, should be sub-second on any working connection. Short
timeout means a flaky network drops the update attempt fast and we
try again on the next tick rather than wedging the launch flow.
"""

# Type aliases for the reload-callback signatures. The runner emits a
# *set* of identifiers per kind (not a single id) so a single batch
# of downloads can collapse to one reload call per kind, regardless
# of how many sets the manifest refreshed.
ReloadRatingsCb = Callable[[set[str]], None]
"""``fn({"TLA", "TMT"})`` — called once when any ratings artifact swapped."""
ReloadParsedCardsCb = Callable[[set[str]], None]
"""``fn({"TLA"})`` — called once when any parsed_cards artifact swapped."""
ReloadModelCb = Callable[[str], None]
"""``fn("choice_v6")`` — called when a model artifact swapped."""


@dataclass(frozen=True)
class _ArtifactOutcome:
    """Per-artifact result of one update pass.

    The runner accumulates one of these per artifact in the manifest
    and rolls them up into the :class:`UpdateReport`.
    """

    label: str
    """Human-readable identifier (``"ratings:TLA"``, ``"model:choice_v6"``).
    Built by :meth:`ManifestArtifact.label`; surfaced in log lines."""
    status: str
    """One of ``"up_to_date"``, ``"refreshed"``, ``"failed"``."""
    message: str = ""
    """Diagnostic detail. Empty on success; the error text on failure."""


@dataclass
class UpdateReport:
    """Result of a complete :meth:`UpdateRunner.run` call.

    ``status`` is the headline: ``"ok"`` (manifest fetched + every
    artifact either up-to-date or refreshed), ``"partial"`` (some
    artifacts failed but others succeeded), ``"failed"`` (couldn't
    even fetch the manifest). The GUI surfaces these as a small
    status indicator; the per-artifact ``outcomes`` list goes to the
    log.
    """

    status: str = "ok"
    manifest_error: str | None = None
    outcomes: list[_ArtifactOutcome] = field(default_factory=list)
    refreshed_ratings: set[str] = field(default_factory=set)
    refreshed_parsed_cards: set[str] = field(default_factory=set)
    refreshed_models: set[str] = field(default_factory=set)

    @property
    def any_refreshed(self) -> bool:
        """``True`` if at least one artifact was actually downloaded.

        Used by the GUI to decide whether to log the friendly
        "Data refreshed: TLA, TMT" line vs. a quieter
        "No updates available" debug line.
        """
        return bool(self.refreshed_ratings or self.refreshed_parsed_cards or self.refreshed_models)


@dataclass
class UpdateRunner:
    """Holds the wiring for a recurring auto-update check.

    Built once at GUI startup, then ``runner.run()`` is invoked on a
    worker thread at startup and every N hours afterwards. The
    callbacks are the slice-2 reload hooks bound to the running
    service / coordinator.

    Attributes
    ----------
    manifest_url:
        Stable URL to the manifest JSON. For GitHub Releases this is
        the asset URL of ``manifest.json`` on a "current data"
        release; for tests, any URL the test's HTTP server serves.
    user_data_root:
        Where ratings + parsed_cards live on disk. The slice-1
        :func:`user_data.user_data_root()`.
    user_models_root:
        Where model bundles live on disk. The slice-1
        :func:`user_data.user_models_root()`.
    reload_ratings:
        Callback fired with the set of set_codes whose parquets
        were swapped. ``None`` to skip the reload (useful for
        the future "download-only" CLI mode).
    reload_parsed_cards:
        Callback fired with the set of set_codes whose JSON files
        were swapped.
    reload_model:
        Callback fired with the model name (e.g. ``"choice_v6"``)
        when a model artifact was swapped.
    timeout_seconds:
        Per-request socket timeout for the manifest fetch +
        downloads. The default (30 s on downloads, 15 s on the
        manifest) is fine on home networks; tests override it.
    """

    manifest_url: str
    user_data_root: Path
    user_models_root: Path
    reload_ratings: ReloadRatingsCb | None = None
    reload_parsed_cards: ReloadParsedCardsCb | None = None
    reload_model: ReloadModelCb | None = None
    download_timeout_seconds: float = 30.0
    manifest_timeout_seconds: float = _MANIFEST_TIMEOUT_SECONDS

    def run(self) -> UpdateReport:
        """One pass: fetch manifest, sync stale artifacts, fire reloads.

        Per-artifact failures are caught and recorded; one bad
        download (404, SHA mismatch, etc.) doesn't sink the rest.
        Manifest fetch failure does sink the whole pass — we have
        no idea what's current, so applying partial updates would
        be guessing.
        """
        report = UpdateReport()

        try:
            manifest = self._fetch_manifest()
        except _ManifestFetchError as exc:
            log.warning("auto-update: manifest fetch failed: %s", exc)
            report.status = "failed"
            report.manifest_error = str(exc)
            return report

        log.info(
            "auto-update: manifest schema=%d generated_at=%s lists %d artifact(s)",
            manifest.schema_version,
            manifest.generated_at,
            len(manifest.artifacts),
        )

        for artifact in manifest.artifacts:
            outcome = self._sync_one(artifact, report)
            report.outcomes.append(outcome)

        if any(o.status == "failed" for o in report.outcomes):
            report.status = "partial"
        self._fire_reloads(report)
        return report

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _fetch_manifest(self) -> Manifest:
        """Fetch + parse the manifest JSON into a :class:`Manifest`.

        Failures (HTTP error, JSON parse error, schema validation)
        all funnel through :class:`_ManifestFetchError`. The caller
        ((run)) treats any of them as "no update this pass."
        """
        request = urllib.request.Request(
            self.manifest_url,
            headers={"User-Agent": "mulligan-coach-overlay/auto-update"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.manifest_timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            raise _ManifestFetchError(f"fetch error: {exc}") from exc

        try:
            return parse_manifest(raw)
        except ManifestParseError as exc:
            raise _ManifestFetchError(f"parse error: {exc}") from exc

    def _sync_one(self, artifact: ManifestArtifact, report: UpdateReport) -> _ArtifactOutcome:
        """Sync a single artifact; record the outcome on *report*."""
        try:
            dest = self._artifact_destination(artifact)
        except ValueError as exc:
            log.warning("auto-update: skipping %s: %s", artifact.label(), exc)
            return _ArtifactOutcome(label=artifact.label(), status="failed", message=str(exc))

        # For zip-extracted kinds (model) the manifest's SHA is the
        # SHA of the zip payload, not of any single extracted file.
        # We track the last-installed zip SHA in a sidecar so we can
        # skip re-downloads on no-change.
        if artifact.kind == "model":
            install_marker = (
                self.user_models_root / (artifact.name or "_unknown") / ".installed_sha"
            )
            installed_sha = _read_marker(install_marker)
            if installed_sha == artifact.sha256:
                return _ArtifactOutcome(label=artifact.label(), status="up_to_date")
            return self._download_and_extract_model(artifact, dest, install_marker, report)

        local_sha = sha256_file(dest)
        if local_sha == artifact.sha256:
            return _ArtifactOutcome(label=artifact.label(), status="up_to_date")

        return self._download_single(artifact, dest, report)

    def _download_single(
        self, artifact: ManifestArtifact, dest: Path, report: UpdateReport
    ) -> _ArtifactOutcome:
        """Download a single file artifact (ratings parquet / parsed cards JSON)."""
        try:
            download_to_file(
                artifact.url,
                dest,
                expected_sha256=artifact.sha256,
                expected_size_bytes=artifact.size_bytes,
                timeout_seconds=self.download_timeout_seconds,
            )
        except DownloadError as exc:
            return _ArtifactOutcome(label=artifact.label(), status="failed", message=str(exc))

        if artifact.kind == "ratings" and artifact.set_code is not None:
            report.refreshed_ratings.add(artifact.set_code)
        elif artifact.kind == "parsed_cards" and artifact.set_code is not None:
            report.refreshed_parsed_cards.add(artifact.set_code)
        return _ArtifactOutcome(label=artifact.label(), status="refreshed")

    def _download_and_extract_model(
        self,
        artifact: ManifestArtifact,
        dest_dir: Path,
        install_marker: Path,
        report: UpdateReport,
    ) -> _ArtifactOutcome:
        """Download a model zip, extract into ``dest_dir``, stamp ``install_marker``.

        Strategy:

        1. Download zip into the user state dir's ``downloads/`` cache
           (so a partially-extracted state from a prior crash is
           recoverable: same zip, re-extract, no re-download).
        2. Stream-extract the zip into a sibling ``<model>.new/`` dir.
        3. Once extraction succeeds, rename the new dir into place:
           rotate any existing ``<model>/`` to ``<model>.old/``,
           rename ``<model>.new/`` to ``<model>/``, delete
           ``<model>.old/``.
        4. Stamp ``install_marker`` with the zip SHA so the next run
           short-circuits.

        Per-file rename inside step 3 is atomic on Windows for files.
        The directory rename used here works as long as the target
        doesn't exist — that's why we move the old dir aside first.
        A reader that opened a model file before step 3 keeps reading
        the old file (Windows holds the inode); the reload hook
        below picks up the new files on its next load.
        """
        # Hash-cached download lives next to user state so a re-run
        # picks up the bytes without re-downloading on each crash.
        downloads_dir = self.user_data_root.parent / "downloads"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        # Per-SHA filename means stale partial caches don't collide
        # with a newer artifact at the same path. The downloader
        # itself does atomic write + SHA verify into the zip path.
        zip_path = downloads_dir / f"{artifact.name}-{artifact.sha256[:12]}.zip"
        try:
            download_to_file(
                artifact.url,
                zip_path,
                expected_sha256=artifact.sha256,
                expected_size_bytes=artifact.size_bytes,
                timeout_seconds=self.download_timeout_seconds,
            )
        except DownloadError as exc:
            return _ArtifactOutcome(label=artifact.label(), status="failed", message=str(exc))

        # Extract to <model>.new/ first so a botched extraction
        # doesn't damage the live dir.
        staging = dest_dir.with_name(dest_dir.name + ".new")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        try:
            _extract_zip_safely(zip_path, staging)
            _swap_directory(dest_dir, staging)
            install_marker.write_text(artifact.sha256, encoding="utf-8")
        except OSError as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return _ArtifactOutcome(
                label=artifact.label(),
                status="failed",
                message=f"extract/swap failed: {exc}",
            )

        # Optional: clean up the cached zip once the live dir is
        # updated. We keep it around so a re-run on the same SHA
        # finds it instead of re-downloading; the downloads/ dir
        # can grow if the user keeps updating, but each zip is
        # small enough (~25 MB) that we don't worry yet. Garbage
        # collection of stale zips is a slice-3.5 follow-up.

        if artifact.name:
            report.refreshed_models.add(artifact.name)
        return _ArtifactOutcome(label=artifact.label(), status="refreshed")

    def _fire_reloads(self, report: UpdateReport) -> None:
        """Invoke the reload callbacks the caller provided.

        We collapse "several ratings sets refreshed" into one
        callback invocation (slice 2 accepts a set of set_codes).
        Each callback is wrapped in a try/except so a bug in one
        consumer doesn't cancel the others.
        """
        if report.refreshed_ratings and self.reload_ratings is not None:
            try:
                self.reload_ratings(report.refreshed_ratings)
            except Exception:
                log.exception("auto-update: reload_ratings callback raised")
        if report.refreshed_parsed_cards and self.reload_parsed_cards is not None:
            try:
                self.reload_parsed_cards(report.refreshed_parsed_cards)
            except Exception:
                log.exception("auto-update: reload_parsed_cards callback raised")
        if report.refreshed_models and self.reload_model is not None:
            for name in report.refreshed_models:
                try:
                    self.reload_model(name)
                except Exception:
                    log.exception("auto-update: reload_model(%s) callback raised", name)

    def _artifact_destination(self, artifact: ManifestArtifact) -> Path:
        """Map a manifest artifact to its on-disk destination.

        Path convention mirrors what slice 1 / the recommend service
        already read from:

        * ``ratings`` → ``<data>/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet``
        * ``parsed_cards`` → ``<data>/processed/parsed_cards/<SET>.json``
        * ``model`` → ``<models>/<name>/`` (zip extraction target).

        Defence in depth: every constructed path is asserted to resolve
        inside its expected root via :func:`_assert_within`. The manifest
        parser already rejects unsafe ``set_code`` / ``name`` values with
        a regex allowlist, but a containment check here means a future
        bug in the parser can't silently regress into a path-traversal
        write primitive against a compromised manifest publisher.
        """
        if artifact.kind == "ratings":
            if not artifact.set_code:
                raise ValueError("ratings artifact requires set_code")
            dest = (
                self.user_data_root
                / "processed"
                / "seventeenlands"
                / "ratings"
                / artifact.set_code
                / "PremierDraft.parquet"
            )
            _assert_within(dest, self.user_data_root)
            return dest
        if artifact.kind == "parsed_cards":
            if not artifact.set_code:
                raise ValueError("parsed_cards artifact requires set_code")
            dest = self.user_data_root / "processed" / "parsed_cards" / f"{artifact.set_code}.json"
            _assert_within(dest, self.user_data_root)
            return dest
        if artifact.kind == "model":
            if not artifact.name:
                raise ValueError("model artifact requires name")
            dest = self.user_models_root / artifact.name
            _assert_within(dest, self.user_models_root)
            return dest
        raise ValueError(f"unknown artifact kind: {artifact.kind!r}")


def _assert_within(candidate: Path, root: Path) -> None:
    """Raise :class:`ValueError` if *candidate* doesn't resolve inside *root*.

    Defence in depth for the manifest path-traversal class. Even with
    the parse-time regex allowlist on ``set_code`` / ``name``, this
    second check means we never write outside the expected user-data /
    user-models roots — a hostile manifest gets a clean "skipped" outcome
    rather than corrupting unrelated files.

    Uses :meth:`Path.resolve` to normalise ``..`` segments and to handle
    Windows' "joining with an absolute path discards the left operand"
    quirk. ``strict=False`` because the destination usually doesn't
    exist yet (we're about to write it); resolution is purely syntactic.
    """
    candidate_resolved = candidate.resolve(strict=False)
    root_resolved = root.resolve(strict=False)
    try:
        candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(
            f"artifact destination {candidate_resolved} escapes root {root_resolved}"
        ) from exc


def _extract_zip_safely(zip_path: Path, dest_dir: Path) -> None:
    """Extract *zip_path* into *dest_dir*, rejecting path-traversal entries.

    ``zipfile.ZipFile.extract`` resolves member names against the
    destination dir but doesn't refuse absolute / ``..`` paths in
    every Python version. We belt-and-braces here: any member whose
    resolved path escapes *dest_dir* raises and aborts the extract.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.namelist():
            target = (dest_dir / member).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError as exc:
                raise OSError(
                    f"zip {zip_path} contains entry {member!r} that escapes {dest_dir}"
                ) from exc
        archive.extractall(dest_dir)


def _swap_directory(live: Path, staging: Path) -> None:
    """Replace *live* with *staging* via the rotate-aside pattern.

    Windows dir-rename refuses to overwrite a non-empty destination,
    so we rotate the existing live dir to a ``.old`` sibling, move
    the staging dir into place, then delete the old one. Each rename
    is an atomic operation; the only inconsistency window is between
    the first rename (live → live.old) and the second (staging →
    live), during which the live name doesn't exist. Practically
    sub-millisecond.
    """
    parked = live.with_name(live.name + ".old")
    if parked.exists():
        shutil.rmtree(parked, ignore_errors=True)
    if live.exists():
        live.rename(parked)
    staging.rename(live)
    if parked.exists():
        shutil.rmtree(parked, ignore_errors=True)


def _read_marker(path: Path) -> str | None:
    """Read a small marker file's text, returning ``None`` if missing."""
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        log.warning("could not read install marker %s: %s", path, exc)
        return None


class _ManifestFetchError(RuntimeError):
    """Internal exception type for "couldn't get a valid manifest".

    Not exposed publicly because the runner catches it and folds
    it into the :class:`UpdateReport`. Distinct from
    :class:`DownloadError` so the run loop knows whether to abort
    the whole pass (manifest failed) or just record one artifact
    failure (download failed).
    """
