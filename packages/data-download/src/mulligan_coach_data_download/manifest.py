"""Per-source download bookkeeping for incremental refresh.

The manifest is a single JSON file at ``data/processed/manifest.json``
that records, for every URL we've downloaded:

* ``etag`` and ``last_modified`` — used for HTTP conditional requests so we
  can skip unchanged files cheaply (17Lands republishes daily during a
  format).
* ``sha256``, ``size`` — sanity check that the file on disk matches what we
  recorded last time.
* ``downloaded_at`` — ISO 8601 timestamp.
* ``row_count`` — set after we convert raw → parquet, used by the ``status``
  CLI command and by tests.

We use Pydantic v2 models so missing/extra fields are explicit and we get
typed access throughout the codebase.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from . import paths


class SourceEntry(BaseModel):
    """One row in the manifest — one downloaded URL."""

    model_config = ConfigDict(extra="forbid")

    url: str
    local_path: str
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    size: int | None = None
    downloaded_at: str | None = None
    row_count: int | None = None


class Manifest(BaseModel):
    """The whole manifest file. Keyed by URL for stable lookups."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    sources: dict[str, SourceEntry] = Field(default_factory=dict)

    # ---- Lookup / mutation helpers --------------------------------------

    def get(self, url: str) -> SourceEntry | None:
        return self.sources.get(url)

    def upsert(self, entry: SourceEntry) -> SourceEntry:
        """Insert or update an entry, stamping ``downloaded_at`` if missing.

        Returns the stored entry — useful when the caller wants to surface
        the post-upsert state (with timestamp) rather than the pre-upsert
        argument.
        """
        if not entry.downloaded_at:
            entry = entry.model_copy(update={"downloaded_at": datetime.now(UTC).isoformat()})
        self.sources[entry.url] = entry
        return entry

    # ---- I/O ------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | None = None) -> Manifest:
        """Load the manifest from disk, returning an empty one if missing."""
        manifest_file = path or paths.manifest_path()
        if not manifest_file.exists():
            return cls()
        return cls.model_validate_json(manifest_file.read_text(encoding="utf-8"))

    def save(self, path: Path | None = None) -> None:
        """Atomically write the manifest to disk.

        We write to a temp file in the same directory and then ``os.replace``
        so an interrupted save can never leave a corrupt file at the
        canonical path.
        """
        manifest_file = path or paths.manifest_path()
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        payload = self.model_dump_json(indent=2) + "\n"
        # NamedTemporaryFile keeps the file in the same directory so the
        # final ``os.replace`` is a true atomic rename on the same filesystem.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=manifest_file.parent,
            prefix=".manifest-",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp.write(payload)
            tmp_path = Path(tmp.name)
        tmp_path.replace(manifest_file)
