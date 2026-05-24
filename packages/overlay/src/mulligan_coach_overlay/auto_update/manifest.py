"""Auto-update manifest schema + JSON parsing.

The manifest is one JSON file the overlay fetches from a stable URL
(GitHub Releases asset, in practice). It lists every artifact the
overlay can hot-refresh and where to get each one. The schema is
intentionally flat — one dataclass, one ``artifacts`` array — so
adding a new artifact kind (say, future tournament metadata) means
one new discriminator value and one client-side mapping rule, no
schema rewrites.

On-disk shape (current ``schema_version=1``)
--------------------------------------------

::

    {
      "schema_version": 1,
      "generated_at": "2026-05-23T12:00:00Z",
      "artifacts": [
        {"kind": "ratings",       "set_code": "TLA",
         "url": "...", "sha256": "abc...", "version": "2026-05-22"},
        {"kind": "parsed_cards",  "set_code": "TLA",
         "url": "...", "sha256": "def...", "version": "2026-05-15"},
        {"kind": "model",         "name": "choice_v6",
         "url": "...", "sha256": "ghi...", "version": "2026-04-30"}
      ]
    }

Forward-compat rule: parsing tolerates unknown top-level fields,
unknown artifact ``kind`` values (logged + skipped), and unknown
fields inside an artifact. A future overlay won't blow up loading a
manifest that mentions, e.g., a ``"opponent_cache"`` kind we
haven't taught it about.

Why ``sha256`` is mandatory
---------------------------

It's the only field the downloader uses to decide "do I already
have this?" — comparing the manifest hash to the local file's hash
means we can skip downloads even when the user manually drops a
file in, and we never trust a download until the hash matches. The
``version`` field is for humans (UI display, log lines); the
downloader never reads it.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

log = logging.getLogger(__name__)


# These two patterns are the primary defence against a hostile manifest
# planting attacker-controlled bytes at attacker-controlled paths. The
# `set_code` and `name` fields are joined into local file paths inside
# `auto_update.runner._artifact_destination`; without a strict allowlist
# at parse time, a manifest entry like ``{"name": "../../AppData/.../Startup"}``
# would let a compromised publisher write outside the overlay's data dirs
# (e.g. drop an autostart payload). Regex anchors + character allowlist
# + length cap reject path separators, drive letters, traversal tokens,
# and anything Windows would refuse as a filename component.
_SAFE_SET_CODE_PATTERN = re.compile(r"^[A-Z0-9]{2,8}$")
"""Magic set codes in current use are 2-5 uppercase alphanumeric chars
(TLA, TMT, ECL, SOS, P22, …). The 8-char ceiling is generous future-
proofing without admitting separators or punctuation."""

_SAFE_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]{0,63}$")
"""Model bundles ship under names like ``choice_v6`` / ``win_v1``.
Allowing letters, digits, underscores, and hyphens covers any reasonable
future naming without admitting dots (NTFS edge cases on trailing dots),
path separators, drive letters, or traversal tokens."""


AUTO_UPDATE_SCHEMA_VERSION = 1
"""Bumped only on incompatible JSON shape changes.

Adding new optional fields or new artifact ``kind`` values does not
bump the schema — those are handled by the forward-compat rules in
:func:`parse_manifest`.
"""


ArtifactKind = Literal["ratings", "parsed_cards", "model"]
"""Closed set of known artifact discriminators in this schema version.

Future kinds get a new literal here and a new mapping entry in
:func:`auto_update.runner._artifact_destination`. Manifests
listing an unknown kind are logged and the offending entries
skipped, never raised — so an old client + new manifest doesn't
crash on launch.
"""

_KNOWN_KINDS: frozenset[str] = frozenset({"ratings", "parsed_cards", "model"})


class ManifestParseError(ValueError):
    """The manifest JSON couldn't be decoded into a :class:`Manifest`.

    Raised on structurally invalid input (missing required fields,
    wrong top-level shape, malformed sha256). Unknown ``kind`` values
    don't raise — they're logged and dropped, so the overlay keeps
    working as long as *some* artifacts parsed.
    """


@dataclass(frozen=True)
class ManifestArtifact:
    """One downloadable artifact described by the manifest.

    Mapped to a local file path by
    :func:`auto_update.runner._artifact_destination` based on
    ``kind`` + optional ``set_code`` / ``name``. The mapping lives
    on the client side (not in the manifest) so the on-disk layout
    can change without rev'ing the manifest schema.

    Fields
    ------
    kind:
        Closed-set discriminator (``ratings``, ``parsed_cards``,
        ``model``).
    set_code:
        Set identifier for per-set artifacts (``TLA`` / ``TMT`` /
        ``ECL`` / ``SOS``). ``None`` for ``kind="model"``.
    name:
        Model name (e.g. ``choice_v6``) for ``kind="model"``.
        ``None`` for the per-set kinds.
    url:
        Direct download URL. GitHub Releases asset URLs are stable
        — that's the recommended host — but any HTTP(S) URL that
        serves the file bytes works.
    sha256:
        Lowercase hex digest of the file the URL serves. Used for
        both "do I need this?" (compare to local file's sha256) and
        post-download verification.
    version:
        Opaque human-readable identifier (date, semver, build id).
        Never interpreted programmatically — used for log lines and
        eventual UI surfacing only.
    size_bytes:
        Expected payload size. Optional; when present, the downloader
        can short-circuit if the response body exceeds it.
    """

    kind: ArtifactKind
    url: str
    sha256: str
    version: str
    set_code: str | None = None
    name: str | None = None
    size_bytes: int | None = None

    def label(self) -> str:
        """Short human-readable identifier used in log lines + reports."""
        if self.kind == "model":
            return f"model:{self.name}"
        return f"{self.kind}:{self.set_code}"


@dataclass(frozen=True)
class Manifest:
    """Parsed auto-update manifest.

    The ``artifacts`` list is in publication order (not necessarily
    a stable order across rebuilds of the publisher tool). Callers
    should iterate and rely on the artifact's own identity fields.
    """

    schema_version: int
    generated_at: str
    artifacts: tuple[ManifestArtifact, ...] = field(default_factory=tuple)


def parse_manifest(raw: str | bytes) -> Manifest:
    """Decode JSON bytes/text into a :class:`Manifest`.

    Forward-compat:

    * Unknown top-level keys are ignored.
    * Unknown artifact ``kind`` values are logged + dropped.
    * Required fields per kind (set_code / name / url / sha256 /
      version) are validated; missing ones raise
      :class:`ManifestParseError`.

    Raises
    ------
    ManifestParseError:
        Top-level shape is wrong, schema version is too new, or a
        known-kind artifact is missing a required field.
    """
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestParseError(f"manifest is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ManifestParseError(
            f"manifest top-level must be an object; got {type(payload).__name__}"
        )

    schema_version = payload.get("schema_version")
    if not isinstance(schema_version, int):
        raise ManifestParseError(
            f"manifest missing or invalid 'schema_version' (got {schema_version!r})"
        )
    if schema_version > AUTO_UPDATE_SCHEMA_VERSION:
        # Future-schema manifest: refuse rather than misparse fields
        # we don't understand. The publisher should bump only when
        # the change is genuinely incompatible — additive changes
        # ship under the same version.
        raise ManifestParseError(
            f"manifest schema_version={schema_version} is newer than this client "
            f"supports (max={AUTO_UPDATE_SCHEMA_VERSION}). Update the overlay."
        )

    generated_at = payload.get("generated_at")
    if not isinstance(generated_at, str):
        raise ManifestParseError(f"manifest missing 'generated_at' string (got {generated_at!r})")

    raw_artifacts = payload.get("artifacts", [])
    if not isinstance(raw_artifacts, list):
        raise ManifestParseError(
            f"manifest 'artifacts' must be a list (got {type(raw_artifacts).__name__})"
        )

    parsed: list[ManifestArtifact] = []
    for index, entry in enumerate(raw_artifacts):
        artifact = _parse_one_artifact(entry, index)
        if artifact is not None:
            parsed.append(artifact)

    return Manifest(
        schema_version=schema_version,
        generated_at=generated_at,
        artifacts=tuple(parsed),
    )


def _parse_one_artifact(entry: Any, index: int) -> ManifestArtifact | None:
    """Validate one artifact dict; return ``None`` for unknown kinds.

    Hard-fails (raises :class:`ManifestParseError`) when a *known*
    artifact kind is missing required fields. Soft-fails (logs +
    returns ``None``) when the discriminator itself is unknown, so
    the overlay can still process artifacts it understands when a
    newer manifest lists kinds it doesn't.
    """
    if not isinstance(entry, dict):
        raise ManifestParseError(
            f"manifest artifact[{index}] must be an object; got {type(entry).__name__}"
        )

    kind = entry.get("kind")
    if kind not in _KNOWN_KINDS:
        log.info("auto-update manifest: skipping artifact[%d] with unknown kind=%r", index, kind)
        return None

    url = entry.get("url")
    sha256 = entry.get("sha256")
    version = entry.get("version")
    if not isinstance(url, str) or not url:
        raise ManifestParseError(f"artifact[{index}] ({kind!r}) missing 'url' string")
    if not isinstance(sha256, str) or not _is_valid_sha256(sha256):
        raise ManifestParseError(
            f"artifact[{index}] ({kind!r}) has invalid 'sha256' "
            f"(expected 64 lowercase hex chars, got {sha256!r})"
        )
    if not isinstance(version, str) or not version:
        raise ManifestParseError(f"artifact[{index}] ({kind!r}) missing 'version' string")

    set_code: str | None = None
    name: str | None = None
    if kind in ("ratings", "parsed_cards"):
        set_code = entry.get("set_code")
        if not isinstance(set_code, str) or not set_code:
            raise ManifestParseError(f"artifact[{index}] kind={kind!r} requires 'set_code' string")
        set_code = set_code.upper()
        if not _SAFE_SET_CODE_PATTERN.match(set_code):
            # Rejects path separators, traversal tokens, drive letters,
            # whitespace, and any Unicode — see _SAFE_SET_CODE_PATTERN.
            raise ManifestParseError(
                f"artifact[{index}] kind={kind!r} has unsafe 'set_code' "
                f"(must match {_SAFE_SET_CODE_PATTERN.pattern}; got {set_code!r})"
            )
    elif kind == "model":
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise ManifestParseError(f"artifact[{index}] kind='model' requires 'name' string")
        if not _SAFE_MODEL_NAME_PATTERN.match(name):
            raise ManifestParseError(
                f"artifact[{index}] kind='model' has unsafe 'name' "
                f"(must match {_SAFE_MODEL_NAME_PATTERN.pattern}; got {name!r})"
            )

    size_bytes_raw = entry.get("size_bytes")
    size_bytes: int | None
    if size_bytes_raw is None:
        size_bytes = None
    elif (
        isinstance(size_bytes_raw, int)
        and size_bytes_raw >= 0
        and not isinstance(size_bytes_raw, bool)
    ):
        size_bytes = size_bytes_raw
    else:
        raise ManifestParseError(
            f"artifact[{index}] 'size_bytes' must be a non-negative int (got {size_bytes_raw!r})"
        )

    return ManifestArtifact(
        kind=kind,  # type: ignore[arg-type]  # guarded by _KNOWN_KINDS
        url=url,
        sha256=sha256.lower(),
        version=version,
        set_code=set_code,
        name=name,
        size_bytes=size_bytes,
    )


def _is_valid_sha256(value: str) -> bool:
    """SHA256 hex digest sanity check.

    64 lowercase hex chars. We accept uppercase by lowercasing later
    but require the *length* to be right — anything else is almost
    certainly a typo or wrong-algorithm digest.
    """
    return len(value) == 64 and all(c in "0123456789abcdefABCDEF" for c in value)
