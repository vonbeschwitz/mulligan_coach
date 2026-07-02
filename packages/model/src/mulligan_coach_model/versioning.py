"""Pipeline version lineage: stamp, check, and split reproducibly.

Training features are outputs of code — the simulator and the feature
builder — cached in parquet for ~17h per set. Nothing in the cache
recorded *which* code version produced it, so a resumed run after a
simulator change could stitch two semantics into one shard, and a model
could be trained on a cache older than the live code (the choice_v7
incident). This module makes that lineage a first-class artifact:

* Two integer semantics versions (imported from the simulation and
  features packages) describe the current pipeline. See
  :func:`pipeline_versions`.
* Each feature-cache shard directory carries a ``_meta.json`` sidecar
  (:class:`ShardMeta`) stamping the versions, the ``(set, event_type)``
  it was built for, and the per-row sim budget. The materialiser writes
  it on a fresh build and *checks* it on resume; the trainer reads every
  shard's meta and refuses to mix mismatched semantics by default.
* Model ``metadata.json`` records the live versions at train time plus a
  per-shard lineage, so a loaded model can be compared against the
  running code (:func:`compute_version_warning`).

Split reproducibility lives here too: :func:`draftid_hash_unit` maps a
``draft_id`` to a deterministic float in ``[0, 1)`` that depends only on
``(seed, draft_id)`` — invariant to how many drafts are present or the
order they appear, so re-materialising a cache no longer reshuffles the
train/val/test assignment. See ``SPLIT_METHOD``.

Stdlib only (hashlib / json / dataclasses / datetime) — plus the two
version constants from sibling workspace packages. No new dependencies.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from mulligan_coach_features import FEATURES_SEMANTICS_VERSION
from mulligan_coach_simulation import SIMULATION_SEMANTICS_VERSION

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Version identity
# ---------------------------------------------------------------------------


def pipeline_versions() -> dict[str, int]:
    """Return the live pipeline semantics versions.

    ``{"simulation": SIMULATION_SEMANTICS_VERSION,
       "features": FEATURES_SEMANTICS_VERSION}`` — the two integers that,
    together, identify the code that turns a ``(hand, deck, context)`` row
    into a feature vector. Bumping either constant (in the same PR as the
    behaviour change) is what invalidates existing caches / models.
    """
    return {
        "simulation": SIMULATION_SEMANTICS_VERSION,
        "features": FEATURES_SEMANTICS_VERSION,
    }


# The name recorded in a model's ``metadata.json`` under ``"split_method"``.
# Bump the suffix if the hashing scheme in :func:`draftid_hash_unit` ever
# changes, so models remain self-describing about how their held-out split
# was drawn. Models trained under the *old* permutation split predate this
# and carry no ``split_method`` key at all.
SPLIT_METHOD: str = "draftid_hash_v1"


def draftid_hash_unit(seed: int, draft_id: object) -> float:
    """Map ``(seed, draft_id)`` to a deterministic float in ``[0, 1)``.

    The value is ``sha256(f"{seed}:{draft_id}")``'s first 8 bytes read as a
    big-endian unsigned integer, divided by ``2**64``. Because it depends
    only on the pair — not on the number of unique draft_ids, their order,
    or which other draft_ids are present — a draft always lands in the same
    train/val/test band regardless of how the cache was materialised. This
    is the materialisation-invariance property the permutation-index split
    lacked.
    """
    digest = hashlib.sha256(f"{seed}:{draft_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


# ---------------------------------------------------------------------------
# Shard metadata sidecar
# ---------------------------------------------------------------------------


# Underscore prefix keeps this out of the ``chunk_*.parquet`` globs the
# materialisers and loaders use to enumerate a shard's data files.
SHARD_META_FILENAME: str = "_meta.json"


def _as_int(value: object) -> int:
    """Coerce a JSON-decoded scalar to ``int`` (dict values type as ``object``).

    JSON numbers decode to ``int`` / ``float``; we also accept numeric
    strings defensively. Anything else is a corrupt sidecar and raises.
    """
    if isinstance(value, bool):  # bool is an int subclass — normalise explicitly
        return int(value)
    if isinstance(value, int | float | str):
        return int(value)
    raise TypeError(f"expected an int-like value, got {type(value)!r}")


class ShardVersionError(ValueError):
    """Raised when a shard / model's recorded versions are incompatible.

    Subclasses :class:`ValueError` so existing ``except ValueError`` config
    handlers still catch it, while callers that care can match the specific
    type. Surfaced on materialiser resume (cache built by different code)
    and at training time (shards disagree with each other or with the live
    pipeline).
    """


@dataclass(frozen=True)
class ShardMeta:
    """Provenance sidecar for one feature-cache shard directory.

    Serialised to ``_meta.json`` as a flat dict. Unknown keys are tolerated
    on read (forward compatibility): a newer writer can add fields without
    breaking an older reader.
    """

    pipeline_versions: dict[str, int]
    set_code: str
    event_type: str
    n_sims_per_row: int
    created_at: str
    """ISO-8601 timestamp of when the shard was first stamped."""

    unverified_legacy: bool = False
    """True when the meta was back-filled onto a pre-existing (unstamped)
    shard whose actual build versions are unknown. Such a shard is trusted
    on the grace path but the flag records that we never verified it."""

    def to_json_dict(self) -> dict[str, object]:
        """Flat JSON-ready dict. Kept explicit so the on-disk shape is
        obvious and stable rather than however ``asdict`` happens to order
        things."""
        return {
            "pipeline_versions": dict(self.pipeline_versions),
            "set_code": self.set_code,
            "event_type": self.event_type,
            "n_sims_per_row": self.n_sims_per_row,
            "created_at": self.created_at,
            "unverified_legacy": self.unverified_legacy,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> ShardMeta:
        """Build from a parsed JSON dict, ignoring unknown keys.

        Raises :class:`KeyError` / :class:`TypeError` only if a *required*
        field is missing or malformed — a genuinely corrupt sidecar should
        surface loudly rather than silently degrade.
        """
        raw_versions = data["pipeline_versions"]
        if not isinstance(raw_versions, dict):
            raise TypeError(f"pipeline_versions must be a dict; got {type(raw_versions)!r}")
        versions = {str(k): int(v) for k, v in raw_versions.items()}
        return cls(
            pipeline_versions=versions,
            set_code=str(data["set_code"]),
            event_type=str(data["event_type"]),
            n_sims_per_row=_as_int(data["n_sims_per_row"]),
            created_at=str(data["created_at"]),
            unverified_legacy=bool(data.get("unverified_legacy", False)),
        )


def _meta_path(shard_dir: Path) -> Path:
    return shard_dir / SHARD_META_FILENAME


def now_iso() -> str:
    """UTC timestamp in ISO-8601 form (for :attr:`ShardMeta.created_at`)."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def write_shard_meta(shard_dir: Path, meta: ShardMeta) -> None:
    """Write ``meta`` to ``shard_dir/_meta.json`` atomically.

    Uses the same tmp-sibling + ``os.replace`` pattern the chunk writer
    uses, so a crash mid-write never leaves a half-written sidecar that a
    later resume would misparse.
    """
    shard_dir.mkdir(parents=True, exist_ok=True)
    final = _meta_path(shard_dir)
    tmp = shard_dir / f"{SHARD_META_FILENAME}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(meta.to_json_dict(), indent=2))
    tmp.replace(final)  # atomic rename (Path.replace); os.replace equivalent


def read_shard_meta(shard_dir: Path) -> ShardMeta | None:
    """Load ``shard_dir/_meta.json``; return ``None`` when it doesn't exist.

    A missing sidecar means "legacy / unstamped shard" — the caller decides
    whether that's a warn-and-continue (materialiser resume) or a
    lineage-``null`` (training). A *present but corrupt* sidecar raises
    through :meth:`ShardMeta.from_json_dict`.
    """
    path = _meta_path(shard_dir)
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return ShardMeta.from_json_dict(data)


def delete_shard_meta(shard_dir: Path) -> None:
    """Remove the sidecar if present (used on ``overwrite=True``). No-op if
    absent."""
    path = _meta_path(shard_dir)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Compatibility checks
# ---------------------------------------------------------------------------


def shard_meta_mismatches(existing: ShardMeta, current: ShardMeta) -> list[str]:
    """Return human-readable descriptions of every incompatibility.

    Empty list ⇒ the shard was built by the same pipeline the current run
    uses and can be safely appended to on resume. Compares the four fields
    that change the *meaning* of the cached rows: pipeline versions, the
    per-row sim budget (mixing budgets mixes feature noise levels), and the
    format identity. ``created_at`` / ``unverified_legacy`` are provenance,
    not compatibility, so they're excluded.
    """
    problems: list[str] = []
    if existing.pipeline_versions != current.pipeline_versions:
        problems.append(
            f"pipeline_versions differ: shard={existing.pipeline_versions} "
            f"vs current={current.pipeline_versions}"
        )
    if existing.n_sims_per_row != current.n_sims_per_row:
        problems.append(
            f"n_sims_per_row differ: shard={existing.n_sims_per_row} "
            f"vs current={current.n_sims_per_row}"
        )
    if existing.set_code != current.set_code:
        problems.append(f"set_code differ: shard={existing.set_code} vs current={current.set_code}")
    if existing.event_type != current.event_type:
        problems.append(
            f"event_type differ: shard={existing.event_type} vs current={current.event_type}"
        )
    return problems


def stamp_or_check_shard_meta(
    shard_dir: Path,
    *,
    current: ShardMeta,
    had_existing_chunks: bool,
    overwrite: bool,
) -> None:
    """Reconcile a shard's ``_meta.json`` for a materialiser run.

    Call once, after the materialiser has resolved its
    overwrite / resume / fresh three-way (chunks already deleted when
    ``overwrite``) and only on a path that will proceed to write rows — the
    "refuse and raise ``FileExistsError``" path must not reach here.

    * **Fresh dir OR overwrite** — (re)write ``current`` from scratch.
    * **Resume onto existing chunks, meta present** — raise
      :class:`ShardVersionError` on any incompatibility (naming exactly what
      differs and advising ``--overwrite``); otherwise leave the existing
      meta untouched so its original ``created_at`` is preserved.
    * **Resume onto existing chunks, no meta (legacy shard)** — log a
      prominent WARNING, then stamp ``current`` with
      ``unverified_legacy=True`` so subsequent runs are checked. This is the
      grace path for every shard that pre-dates version stamping.
    """
    if overwrite or not had_existing_chunks:
        delete_shard_meta(shard_dir)  # harmless if absent
        write_shard_meta(shard_dir, current)
        return

    existing = read_shard_meta(shard_dir)
    if existing is None:
        log.warning(
            "Shard %s has chunks but no %s (legacy / unverified). Stamping "
            "current pipeline versions %s with unverified_legacy=True; a "
            "future resume will be checked against them. If this shard was "
            "actually built by a DIFFERENT simulator/feature version, "
            "re-materialise it with --overwrite instead.",
            shard_dir,
            SHARD_META_FILENAME,
            current.pipeline_versions,
        )
        write_shard_meta(shard_dir, replace(current, unverified_legacy=True))
        return

    problems = shard_meta_mismatches(existing, current)
    if problems:
        raise ShardVersionError(
            f"Refusing to resume {shard_dir}: it was materialised under "
            f"different pipeline semantics than this run.\n  - "
            + "\n  - ".join(problems)
            + "\nRe-materialise the shard with --overwrite to rebuild it "
            "cleanly, or investigate the version drift before resuming."
        )


# ---------------------------------------------------------------------------
# Training-time lineage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShardLineageEntry:
    """One shard directory's provenance, recorded in a model's metadata.

    ``pipeline_versions`` / ``n_sims_per_row`` are ``None`` for a legacy
    shard that carried no ``_meta.json`` at train time (recorded as JSON
    ``null`` — the model stays honest that its lineage there is unknown).
    """

    dir: str
    pipeline_versions: dict[str, int] | None
    unverified_legacy: bool
    n_sims_per_row: int | None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "dir": self.dir,
            "pipeline_versions": self.pipeline_versions,
            "unverified_legacy": self.unverified_legacy,
            "n_sims_per_row": self.n_sims_per_row,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, object]) -> ShardLineageEntry:
        raw_versions = data.get("pipeline_versions")
        versions: dict[str, int] | None
        if raw_versions is None:
            versions = None
        elif isinstance(raw_versions, dict):
            versions = {str(k): int(v) for k, v in raw_versions.items()}
        else:
            raise TypeError(f"pipeline_versions must be a dict or null; got {type(raw_versions)!r}")
        raw_n_sims = data.get("n_sims_per_row")
        return cls(
            dir=str(data["dir"]),
            pipeline_versions=versions,
            unverified_legacy=bool(data.get("unverified_legacy", False)),
            n_sims_per_row=None if raw_n_sims is None else _as_int(raw_n_sims),
        )


def gather_shard_lineage(parquet_paths: Sequence[Path]) -> list[ShardLineageEntry]:
    """Read the ``_meta.json`` of every shard dir the parquet paths span.

    Shard dirs are ``{p.parent for p in parquet_paths}`` — the training
    entry points hand us the flat list of chunk files, and multiple chunks
    share one shard dir. Returns one entry per unique dir, sorted for a
    stable metadata ordering. A dir with no sidecar yields an all-``None``
    entry (legacy).
    """
    dirs = sorted({p.parent for p in parquet_paths})
    entries: list[ShardLineageEntry] = []
    for d in dirs:
        meta = read_shard_meta(d)
        if meta is None:
            entries.append(
                ShardLineageEntry(
                    dir=str(d),
                    pipeline_versions=None,
                    unverified_legacy=False,
                    n_sims_per_row=None,
                )
            )
        else:
            entries.append(
                ShardLineageEntry(
                    dir=str(d),
                    pipeline_versions=meta.pipeline_versions,
                    unverified_legacy=meta.unverified_legacy,
                    n_sims_per_row=meta.n_sims_per_row,
                )
            )
    return entries


def check_training_lineage(
    lineage: Sequence[ShardLineageEntry],
    *,
    allow_version_mismatch: bool,
) -> None:
    """Enforce version consistency across the shards feeding a training run.

    Rules (per the Step 1 spec):

    * A shard with no meta (legacy, ``pipeline_versions is None``) → WARNING
      only; its lineage entry already records ``null``.
    * Any shard whose versions disagree with the live
      :func:`pipeline_versions` → raise :class:`ShardVersionError` unless
      ``allow_version_mismatch=True``. Comparing each present shard against
      the live versions also catches shards disagreeing *with each other*:
      if two differ, at least one differs from live.

    The v7 incident is exactly "cache older than live code", so this blocks
    by default; ``--allow-version-mismatch`` is the deliberate override.
    """
    live = pipeline_versions()
    conflicts: list[str] = []
    for entry in lineage:
        if entry.pipeline_versions is None:
            log.warning(
                "Training shard %s has no version metadata (legacy shard). "
                "Recording its lineage as null; it will not be verified.",
                entry.dir,
            )
            continue
        if entry.pipeline_versions != live:
            conflicts.append(f"{entry.dir}: {entry.pipeline_versions} != live {live}")

    if conflicts:
        message = (
            "Training shards were built under pipeline versions that differ "
            f"from the live code (live={live}):\n  - " + "\n  - ".join(conflicts) + "\n"
        )
        if allow_version_mismatch:
            log.warning(
                "%sProceeding anyway because allow_version_mismatch=True. "
                "The resulting model's held-out metrics mix feature semantics "
                "— interpret them accordingly.",
                message,
            )
            return
        raise ShardVersionError(
            message + "Re-materialise the stale shards with --overwrite so every "
            "shard matches the live simulator + feature code, or pass "
            "--allow-version-mismatch to train on the mix anyway (recorded "
            "in metadata.json)."
        )


# ---------------------------------------------------------------------------
# Load-time warning
# ---------------------------------------------------------------------------


def compute_version_warning(recorded: dict[str, int] | None) -> str | None:
    """Return a one-line warning if a model's versions don't match live.

    ``recorded`` is the ``pipeline_versions`` a model wrote into its
    ``metadata.json`` at train time (``None`` for a model trained before
    version stamping). Returns ``None`` when they match the running code, a
    descriptive string otherwise. Never raises — frozen-EXE users can
    legitimately run skewed versions; surfacing it in the UI is a later
    roadmap step, this just records the string on the bundle.
    """
    live = pipeline_versions()
    if recorded is None:
        return (
            f"model was trained before pipeline-version stamping (no recorded "
            f"versions); live pipeline is {live}. Predictions may reflect "
            f"different simulator/feature semantics than this model expects."
        )
    if recorded != live:
        return (
            f"model pipeline versions {recorded} differ from the live pipeline "
            f"{live}; feature semantics may have shifted since training "
            f"(train/serve skew)."
        )
    return None
