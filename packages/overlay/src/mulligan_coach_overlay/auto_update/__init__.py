"""Auto-update for the overlay's data + model artifacts.

What this is for
----------------

The shipped overlay's bundle is mostly read-only code. The artifacts
the user actually wants refreshed — 17Lands ratings (weekly+),
parsed-cards encodings (when sets get fixed or new cards land), and
the choice model (occasional) — live in a user-writable directory
(see :mod:`mulligan_coach_overlay.user_data`). This subpackage adds
the small in-process updater that:

1. Fetches a remote *manifest* describing the canonical version of
   each artifact.
2. Compares each manifest entry's SHA256 against the local file's
   SHA256. Mismatches trigger a download.
3. Downloads atomically (.tmp + ``os.replace``) and verifies the
   SHA256 before swap-in. A corrupt or truncated transfer never
   replaces a known-good file.
4. Calls the service-level reload hooks added in slice 2
   (:meth:`RecommendationService.reload_ratings`, etc.) so the
   running overlay picks up the new artifacts without a restart.

Module layout
-------------

* :mod:`.manifest` — the JSON schema (one dataclass per artifact)
  and its parser. Strict — unknown fields are tolerated for forward
  compatibility but missing/typo'd required fields raise.
* :mod:`.downloader` — pure-stdlib HTTP fetch, streaming SHA256,
  atomic write. No new dependencies.
* :mod:`.runner` — orchestrates manifest → downloader → reload
  hooks. The single function the GUI thread invokes on a worker.

The GUI integration (a :class:`QTimer` on the overlay window) lives
in :mod:`mulligan_coach_overlay.gui` so this subpackage stays
Qt-free and testable in isolation.
"""

from __future__ import annotations

from .downloader import DownloadError, download_to_file, sha256_file
from .exe_update import (
    DEFAULT_EXE_VERSION_URL,
    ExeUpdateChecker,
    ExeUpdateResult,
    ExeVersionInfo,
    ExeVersionParseError,
    is_newer_bundle_version,
    manual_check_message,
    parse_exe_version,
    update_available_message,
)
from .manifest import (
    AUTO_UPDATE_SCHEMA_VERSION,
    Manifest,
    ManifestArtifact,
    ManifestParseError,
    parse_manifest,
)
from .runner import UpdateReport, UpdateRunner

__all__ = [
    "AUTO_UPDATE_SCHEMA_VERSION",
    "DEFAULT_EXE_VERSION_URL",
    "DownloadError",
    "ExeUpdateChecker",
    "ExeUpdateResult",
    "ExeVersionInfo",
    "ExeVersionParseError",
    "Manifest",
    "ManifestArtifact",
    "ManifestParseError",
    "UpdateReport",
    "UpdateRunner",
    "download_to_file",
    "is_newer_bundle_version",
    "manual_check_message",
    "parse_exe_version",
    "parse_manifest",
    "sha256_file",
    "update_available_message",
]
