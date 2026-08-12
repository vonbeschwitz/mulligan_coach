"""In-place upgrade of feature caches after a set-vocabulary bump.

When a new set is appended to ``mulligan_coach_features.DEFAULT_KNOWN_SETS``
(with the matching ``FEATURES_SEMANTICS_VERSION`` bump), a freshly-
materialised cache would carry the new ``set_code_*`` column — but
re-materialising a format costs many hours of simulation. This module
upgrades an *existing* cache to the new one-hot vocabulary **without any
re-simulation**: only the ``set_code_*`` columns are rewritten, purely
from each chunk's already-stored ``expansion`` column. Every other
column (the ~200 simulation/feature columns, the label, the context
columns) is carried over physically untouched.

Scope: the win-model caches under
``data/processed/model_training/<SET>/<EVENT>/`` and the choice-model
caches under ``data/processed/choice_training/<SET>/<EVENT>/`` — both
share the ``build_feature_row`` schema, so both carry the same
``set_code_*`` + ``expansion`` columns.

Each vocabulary bump is a pinned :class:`Migration` record naming exactly
which source pipeline versions it upgrades, which vocabulary it writes,
and which features version it stamps. Migrations are deliberately NOT
derived live from ``DEFAULT_KNOWN_SETS`` at call sites: a later
vocabulary bump must add a NEW pinned migration rather than silently
reusing an old one to stamp the wrong version (:func:`patch_roots`
asserts the active migration still matches the live builder vocabulary).

Design (see docs/specs/step2_set_vocabulary.md §C):

* **Idempotent + self-correcting.** Every run rewrites ALL ``set_code_{S}``
  columns from ``expansion`` — not just the new ones — so a partial or
  wrong prior state converges to correct. A row whose ``expansion`` is
  outside the vocabulary gets all columns ``0.0`` (matching the feature
  builder's all-zero reference-category behaviour for unknown sets); such
  rows are counted and reported, never a failure.
* **pyarrow table ops, no pandas round-trip.** A pandas round-trip can
  perturb dtypes / float precision on the untouched columns; we replace
  only the one-hot columns and leave the rest as their original Arrow
  buffers.
* **Atomic per chunk.** Write a tmp sibling, validate, then
  ``os.replace`` — a crash never leaves a half-written chunk.
* **Meta bump last, as raw JSON.** Only after every chunk in a shard dir
  succeeds do we edit ``_meta.json`` (features bump + a ``patch_history``
  entry). Editing the raw dict — not via :class:`ShardMeta` — preserves
  any unknown keys. A crash between chunk-patching and the meta bump is
  safe: the re-run re-patches idempotently, then bumps.
* **Meta-less (legacy) shards are skipped, not guessed at.** The original
  v1 migration patched them as a grace path (stamping didn't exist yet);
  every shard we still train on carries a sidecar now, so a missing meta
  means "unknown provenance" and the shard is left alone with a warning
  (e.g. the retired ECL win cache).

Stdlib + pyarrow only (both already workspace dependencies).
"""

from __future__ import annotations

import enum
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from mulligan_coach_features import DEFAULT_KNOWN_SETS

from .versioning import SHARD_META_FILENAME, now_iso

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pinned migrations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """One pinned vocabulary migration.

    Rewrites every ``set_code_*`` one-hot to :attr:`known_sets` and bumps
    ``pipeline_versions.features`` to :attr:`target_features_version`,
    for shards whose ``_meta.json`` carries exactly
    :attr:`source_pipeline_versions`. Any other version combination is
    refused — we don't guess how a shard was built.
    """

    name: str
    """Recorded in each patched shard's ``_meta.json`` ``patch_history``."""

    known_sets: tuple[str, ...]
    """The one-hot vocabulary this migration writes, pinned as a literal."""

    source_pipeline_versions: tuple[tuple[str, int], ...]
    """The (whole) pipeline-version dict a shard must carry to be
    eligible, as sorted ``(key, value)`` pairs (tuples keep the dataclass
    hashable/frozen). Use :meth:`source_versions_dict` to compare."""

    target_features_version: int
    """The features version stamped after a successful patch."""

    def source_versions_dict(self) -> dict[str, int]:
        return dict(self.source_pipeline_versions)

    @property
    def source_features_version(self) -> int:
        return self.source_versions_dict()["features"]


SET_ONEHOTS_V1 = Migration(
    name="set_onehots_v1",
    known_sets=("TMT", "ECL", "TLA", "SOS", "MSH"),
    source_pipeline_versions=(("features", 1), ("simulation", 1)),
    target_features_version=2,
)
"""Roadmap Step 2 (2026-07): appended SOS + MSH, features 1 -> 2.

Retired — kept as the historical record of what the v1 caches were
patched with. Note the original tool also patched meta-less legacy dirs;
that grace path no longer exists (see module docstring)."""

SET_ONEHOTS_V2 = Migration(
    name="set_onehots_v2",
    known_sets=("TMT", "ECL", "TLA", "SOS", "MSH", "HOB"),
    source_pipeline_versions=(("features", 3), ("simulation", 2)),
    target_features_version=4,
)
"""HOB rotation (2026-08): appended HOB, features 3 -> 4.

Every existing cache row's ``expansion`` is a pre-HOB set, so the new
``set_code_HOB`` column is all-zero everywhere — the patch's value is
that the *column exists*, matching what ``build_feature_row`` now emits,
so v4 training and inference agree on the row shape."""

ACTIVE_MIGRATION = SET_ONEHOTS_V2
"""The migration the CLI applies. Re-point when the vocabulary bumps."""


class CachePatchError(RuntimeError):
    """Raised on a genuinely corrupt cache (missing ``expansion`` column,
    post-patch validation failure) — as opposed to the *refused* path,
    which is a recorded outcome, not an exception."""


# Chunk-file glob shared with the materialiser (kept in sync manually —
# the two modules are the only writers of this layout).
_CHUNK_GLOB: str = "chunk_*.parquet"

_SET_CODE_PREFIX: str = "set_code_"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Disposition(enum.Enum):
    """What was done (or would be done, in a dry run) with one shard dir."""

    PATCHED = "patched"
    """Meta present at the migration's source versions — chunks patched,
    meta bumped."""
    SKIPPED_LEGACY = "skipped_legacy"
    """No ``_meta.json`` — unknown provenance, left alone with a warning."""
    SKIPPED_AT_TARGET = "skipped_at_target"
    """Meta already at the target features version — nothing to do
    (re-run no-op)."""
    REFUSED = "refused"
    """Meta present with an unrecognised version combination — not touched."""


@dataclass
class ChunkReport:
    """Per-chunk accounting for one ``chunk_*.parquet``."""

    path: Path
    n_rows: int
    expansion_counts: dict[str, int]
    """Row count per distinct ``expansion`` value in the chunk (includes
    out-of-vocabulary values)."""
    out_of_vocab_rows: int
    set_columns_replaced: list[str]
    """Existing ``set_code_*`` columns overwritten in place."""
    set_columns_added: list[str]
    """New ``set_code_*`` columns appended (absent before the patch)."""
    written: bool
    """False in a dry run (analysis only)."""


@dataclass
class DirResult:
    """Per-shard-directory outcome."""

    shard_dir: Path
    disposition: Disposition
    chunk_reports: list[ChunkReport] = field(default_factory=list)
    meta_bumped: bool = False
    reason: str | None = None
    """Populated for :attr:`Disposition.REFUSED` — why the dir was refused."""

    @property
    def n_chunks(self) -> int:
        return len(self.chunk_reports)

    @property
    def expansion_counts(self) -> dict[str, int]:
        """Row counts per expansion, aggregated across the dir's chunks."""
        totals: dict[str, int] = {}
        for cr in self.chunk_reports:
            for exp, n in cr.expansion_counts.items():
                totals[exp] = totals.get(exp, 0) + n
        return totals


@dataclass
class PatchReport:
    """Aggregate result of a :func:`patch_roots` / multi-dir run."""

    dir_results: list[DirResult]
    dry_run: bool
    migration_name: str = ACTIVE_MIGRATION.name

    def by_disposition(self, disposition: Disposition) -> list[DirResult]:
        return [d for d in self.dir_results if d.disposition is disposition]

    @property
    def any_refused(self) -> bool:
        return any(d.disposition is Disposition.REFUSED for d in self.dir_results)

    @property
    def total_expansion_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for d in self.dir_results:
            for exp, n in d.expansion_counts.items():
                totals[exp] = totals.get(exp, 0) + n
        return totals


# ---------------------------------------------------------------------------
# Chunk-level patch (the atomic unit)
# ---------------------------------------------------------------------------


def _one_hot_array(expansions: list[Any], set_code: str) -> pa.Array:
    """Build a float64 one-hot column: 1.0 where ``expansion == set_code``.

    Rows whose expansion differs (including ``None`` / out-of-vocabulary)
    get 0.0 — the same all-zero encoding the feature builder emits for an
    unknown set.
    """
    return pa.array([1.0 if e == set_code else 0.0 for e in expansions], type=pa.float64())


def _expansion_counts(expansions: list[Any]) -> dict[str, int]:
    """Tally rows per distinct expansion value (``None`` -> ``"<null>"``)."""
    counts: dict[str, int] = {}
    for e in expansions:
        key = "<null>" if e is None else str(e)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _validate_patched_table(
    original: pa.Table,
    patched: pa.Table,
    expansions: list[Any],
    known_sets: tuple[str, ...],
) -> None:
    """Assert the invariants that make an in-place rewrite safe.

    Raises :class:`CachePatchError` on any violation (caller writes only
    after this passes):

    * row count unchanged;
    * every non-``set_code_*`` column NAME preserved, in order (their data
      is carried over by reference, but we still guard the schema);
    * one-hot correctness per row: ``set_code_{expansion} == 1.0`` and
      exactly one 1.0 across the vocabulary's columns when the expansion
      is in vocabulary; all columns 0.0 when it isn't.
    """
    if patched.num_rows != original.num_rows:
        raise CachePatchError(
            f"row count changed during patch: {original.num_rows} -> {patched.num_rows}"
        )

    def _non_set_names(table: pa.Table) -> list[str]:
        return [n for n in table.schema.names if not n.startswith(_SET_CODE_PREFIX)]

    if _non_set_names(original) != _non_set_names(patched):
        raise CachePatchError(
            "non-set_code column names/order changed during patch: "
            f"{_non_set_names(original)} -> {_non_set_names(patched)}"
        )

    set_columns = {s: patched.column(f"{_SET_CODE_PREFIX}{s}").to_pylist() for s in known_sets}
    vocab = set(known_sets)
    for i, exp in enumerate(expansions):
        row = [set_columns[s][i] for s in known_sets]
        total = sum(row)
        if exp in vocab:
            if set_columns[exp][i] != 1.0 or total != 1.0:
                raise CachePatchError(
                    f"row {i} expansion={exp!r}: expected exactly set_code_{exp}==1.0, "
                    f"got {dict(zip(known_sets, row, strict=True))}"
                )
        elif total != 0.0:
            raise CachePatchError(
                f"row {i} expansion={exp!r} is out of vocabulary but has a non-zero "
                f"one-hot: {dict(zip(known_sets, row, strict=True))}"
            )


def _build_patched_table(
    table: pa.Table,
    expansions: list[Any],
    known_sets: tuple[str, ...],
) -> tuple[pa.Table, list[str], list[str]]:
    """Return ``(patched_table, replaced_names, added_names)``.

    Existing ``set_code_{S}`` columns are replaced in place (position
    preserved); missing ones are appended in vocabulary order. No other
    column is touched.
    """
    present = set(table.schema.names)
    replaced: list[str] = []
    added: list[str] = []
    result = table
    # Replace existing one-hots in position first (keeps human-facing
    # column order stable for the sets that already exist in the source).
    for s in known_sets:
        name = f"{_SET_CODE_PREFIX}{s}"
        if name in present:
            idx = result.schema.get_field_index(name)
            result = result.set_column(
                idx, pa.field(name, pa.float64()), _one_hot_array(expansions, s)
            )
            replaced.append(name)
    # New columns are appended so they land at the end.
    for s in known_sets:
        name = f"{_SET_CODE_PREFIX}{s}"
        if name not in present:
            result = result.append_column(
                pa.field(name, pa.float64()), _one_hot_array(expansions, s)
            )
            added.append(name)
    return result, replaced, added


def _chunk_tmp_path(chunk: Path) -> Path:
    """Per-pid tmp sibling for an atomic chunk rewrite."""
    return chunk.parent / f".{chunk.name}.tmp-{os.getpid()}"


def analyze_chunk(
    chunk: Path, known_sets: tuple[str, ...] = ACTIVE_MIGRATION.known_sets
) -> ChunkReport:
    """Read a chunk's ``expansion`` column and report what a patch *would*
    change — no write. Used by the dry-run path.
    """
    table = pq.read_table(chunk, columns=["expansion"])  # type: ignore[no-untyped-call]
    if "expansion" not in table.schema.names:
        raise CachePatchError(f"{chunk} has no 'expansion' column; cannot patch.")
    expansions = table.column("expansion").to_pylist()
    counts = _expansion_counts(expansions)
    vocab = set(known_sets)
    out_of_vocab = sum(n for e, n in counts.items() if e not in vocab)

    # Determine which one-hots exist by reading just the schema of the full file.
    schema = pq.read_schema(chunk)  # type: ignore[no-untyped-call]
    present = set(schema.names)
    replaced = [f"{_SET_CODE_PREFIX}{s}" for s in known_sets if f"{_SET_CODE_PREFIX}{s}" in present]
    added = [
        f"{_SET_CODE_PREFIX}{s}" for s in known_sets if f"{_SET_CODE_PREFIX}{s}" not in present
    ]
    return ChunkReport(
        path=chunk,
        n_rows=len(expansions),
        expansion_counts=counts,
        out_of_vocab_rows=out_of_vocab,
        set_columns_replaced=replaced,
        set_columns_added=added,
        written=False,
    )


def patch_chunk(
    chunk: Path, known_sets: tuple[str, ...] = ACTIVE_MIGRATION.known_sets
) -> ChunkReport:
    """Rewrite one chunk's ``set_code_*`` columns in place, atomically.

    Reads the whole table, rebuilds the one-hots from ``expansion``,
    validates, then writes a tmp sibling and ``os.replace``s it over the
    original. Idempotent: running it on an already-patched chunk produces
    the identical result.

    Deliberately does NOT touch ``_meta.json`` — the dir-level driver
    bumps meta only after every chunk succeeds, so a crash mid-dir is
    recoverable by re-running.
    """
    table = pq.read_table(chunk)  # type: ignore[no-untyped-call]
    if "expansion" not in table.schema.names:
        raise CachePatchError(f"{chunk} has no 'expansion' column; cannot patch.")
    expansions = table.column("expansion").to_pylist()

    patched, replaced, added = _build_patched_table(table, expansions, known_sets)
    _validate_patched_table(table, patched, expansions, known_sets)

    tmp = _chunk_tmp_path(chunk)
    try:
        pq.write_table(patched, tmp, compression="zstd")  # type: ignore[no-untyped-call]
        tmp.replace(chunk)
    finally:
        # Sweep the tmp file if the replace never happened (write failed).
        if tmp.exists():
            tmp.unlink()

    counts = _expansion_counts(expansions)
    vocab = set(known_sets)
    out_of_vocab = sum(n for e, n in counts.items() if e not in vocab)
    return ChunkReport(
        path=chunk,
        n_rows=len(expansions),
        expansion_counts=counts,
        out_of_vocab_rows=out_of_vocab,
        set_columns_replaced=replaced,
        set_columns_added=added,
        written=True,
    )


# ---------------------------------------------------------------------------
# Meta eligibility + raw-JSON bump
# ---------------------------------------------------------------------------


def _classify_dir(shard_dir: Path, migration: Migration) -> tuple[Disposition, str | None]:
    """Decide what to do with a shard dir from its ``_meta.json``.

    Order matters: an already-at-target shard is a no-op (so re-runs are
    safe) *before* we consider the strict source match, and any other
    present combination is refused rather than guessed at. A missing
    sidecar is skipped with a warning, not patched (see module docstring).
    """
    meta_path = shard_dir / SHARD_META_FILENAME
    if not meta_path.exists():
        return Disposition.SKIPPED_LEGACY, None

    data = json.loads(meta_path.read_text())
    versions = data.get("pipeline_versions")
    if not isinstance(versions, dict):
        return (
            Disposition.REFUSED,
            f"{SHARD_META_FILENAME} has no valid 'pipeline_versions' dict: {versions!r}",
        )
    versions_int = {str(k): int(v) for k, v in versions.items()}

    if versions_int.get("features") == migration.target_features_version:
        return Disposition.SKIPPED_AT_TARGET, None
    if versions_int == migration.source_versions_dict():
        return Disposition.PATCHED, None
    return (
        Disposition.REFUSED,
        f"unrecognised pipeline_versions {versions_int}; migration {migration.name} "
        f"only upgrades {migration.source_versions_dict()} (unknown provenance — "
        "refusing to guess).",
    )


def _bump_meta_features(shard_dir: Path, migration: Migration) -> None:
    """Raw-JSON bump of a shard's ``_meta.json`` to the migration's target.

    Preserves every existing key (including unknown ones a newer writer
    may have added): we read the dict, set ``pipeline_versions.features``
    to the target, append a ``patch_history`` entry, and atomically write
    it back.
    """
    meta_path = shard_dir / SHARD_META_FILENAME
    data = json.loads(meta_path.read_text())
    versions = data["pipeline_versions"]
    versions["features"] = migration.target_features_version
    history = data.setdefault("patch_history", [])
    if not isinstance(history, list):
        raise CachePatchError(
            f"{meta_path}: existing 'patch_history' is not a list ({type(history)!r})"
        )
    history.append(
        {
            "patch": migration.name,
            "at": now_iso(),
            "from_features": migration.source_features_version,
            "to_features": migration.target_features_version,
        }
    )
    tmp = shard_dir / f"{SHARD_META_FILENAME}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(meta_path)


# ---------------------------------------------------------------------------
# Directory-level driver
# ---------------------------------------------------------------------------


def find_shard_dirs(root: Path) -> list[Path]:
    """Return every directory under ``root`` holding ``chunk_*.parquet``.

    Robust to the exact ``<SET>/<EVENT>`` nesting: a shard dir is defined
    by containing chunk files, so we take the parents of all chunks. Sorted
    for a stable, human-diffable processing order.
    """
    if not root.exists():
        return []
    return sorted({p.parent for p in root.rglob(_CHUNK_GLOB)})


def patch_shard_dir(
    shard_dir: Path,
    *,
    dry_run: bool,
    migration: Migration = ACTIVE_MIGRATION,
) -> DirResult:
    """Patch one shard directory end to end (eligibility -> chunks -> meta).

    * SKIP / REFUSE dispositions do no chunk work.
    * PATCHED: every ``chunk_*.parquet`` is (dry-run: analysed; real:
      rewritten). The meta bump happens only after all chunks succeed.
    """
    disposition, reason = _classify_dir(shard_dir, migration)

    if disposition is Disposition.REFUSED:
        log.error("REFUSED %s: %s", shard_dir, reason)
        return DirResult(shard_dir=shard_dir, disposition=disposition, reason=reason)
    if disposition is Disposition.SKIPPED_LEGACY:
        log.warning(
            "SKIP %s: no %s sidecar (unknown provenance); not patched. "
            "Stamp or re-materialise it if this shard is still wanted.",
            shard_dir,
            SHARD_META_FILENAME,
        )
        return DirResult(shard_dir=shard_dir, disposition=disposition)
    if disposition is Disposition.SKIPPED_AT_TARGET:
        log.info(
            "SKIP %s: already at features %d (patched previously).",
            shard_dir,
            migration.target_features_version,
        )
        return DirResult(shard_dir=shard_dir, disposition=disposition)

    chunks = sorted(shard_dir.glob(_CHUNK_GLOB))
    if not chunks:
        # A meta-only dir with no chunks: nothing to patch; treat as a
        # no-op skip rather than inventing an outcome.
        log.warning("%s has a meta but no chunk files; nothing to patch.", shard_dir)
        return DirResult(shard_dir=shard_dir, disposition=Disposition.SKIPPED_AT_TARGET)

    reports: list[ChunkReport] = []
    for chunk in chunks:
        report = (
            analyze_chunk(chunk, migration.known_sets)
            if dry_run
            else patch_chunk(chunk, migration.known_sets)
        )
        reports.append(report)

    meta_bumped = False
    if not dry_run:
        _bump_meta_features(shard_dir, migration)
        meta_bumped = True

    label = "DRY-RUN" if dry_run else disposition.value.upper()
    log.info(
        "%s %s: %d chunk(s), %d rows%s",
        label,
        shard_dir,
        len(reports),
        sum(r.n_rows for r in reports),
        (
            f" (meta bumped {migration.source_features_version}"
            f"->{migration.target_features_version})"
            if meta_bumped
            else ""
        ),
    )
    return DirResult(
        shard_dir=shard_dir,
        disposition=disposition,
        chunk_reports=reports,
        meta_bumped=meta_bumped,
    )


def patch_roots(
    roots: list[Path],
    *,
    dry_run: bool,
    migration: Migration = ACTIVE_MIGRATION,
) -> PatchReport:
    """Patch every shard dir found under each root.

    Asserts the live builder vocabulary still matches the migration's
    pinned vocabulary — a mismatch means the vocabulary was bumped again
    and this migration must not be reused blindly.
    """
    if tuple(DEFAULT_KNOWN_SETS) != migration.known_sets:
        raise CachePatchError(
            "live DEFAULT_KNOWN_SETS "
            f"{tuple(DEFAULT_KNOWN_SETS)} != migration {migration.name}'s pinned "
            f"vocabulary {migration.known_sets}. The feature vocabulary changed "
            "again; add a NEW pinned Migration (targeting the new "
            "FEATURES_SEMANTICS_VERSION) rather than reusing an old one."
        )

    dir_results: list[DirResult] = []
    for root in roots:
        shard_dirs = find_shard_dirs(root)
        if not shard_dirs:
            log.info("No shard dirs under %s", root)
            continue
        log.info("Scanning %d shard dir(s) under %s", len(shard_dirs), root)
        for shard_dir in shard_dirs:
            dir_results.append(patch_shard_dir(shard_dir, dry_run=dry_run, migration=migration))
    return PatchReport(dir_results=dir_results, dry_run=dry_run, migration_name=migration.name)


# ---------------------------------------------------------------------------
# Human-readable report
# ---------------------------------------------------------------------------


def format_report(report: PatchReport) -> str:
    """Render a :class:`PatchReport` as a per-dir + summary text block.

    Used by the CLI for both dry-run and real runs so the operator (who
    tees stdout) sees the same shape either way.
    """
    lines: list[str] = []
    mode = "DRY RUN (no writes)" if report.dry_run else "APPLIED"
    lines.append(f"=== set-code one-hot patch ({report.migration_name}) — {mode} ===")

    for d in report.dir_results:
        lines.append("")
        lines.append(f"{d.disposition.value.upper():<20} {d.shard_dir}")
        if d.disposition is Disposition.REFUSED:
            lines.append(f"    reason: {d.reason}")
            continue
        if d.disposition in (Disposition.SKIPPED_AT_TARGET, Disposition.SKIPPED_LEGACY):
            continue
        # PATCHED: show chunk + per-expansion detail.
        counts = d.expansion_counts
        exp_detail = ", ".join(f"{exp}={n}" for exp, n in sorted(counts.items()))
        lines.append(f"    chunks: {d.n_chunks}    rows: {sum(counts.values())}")
        lines.append(f"    rows per expansion: {exp_detail}")
        if d.chunk_reports:
            first = d.chunk_reports[0]
            if first.set_columns_replaced:
                lines.append(f"    columns replaced: {', '.join(first.set_columns_replaced)}")
            if first.set_columns_added:
                lines.append(f"    columns added:    {', '.join(first.set_columns_added)}")
        out_of_vocab = sum(cr.out_of_vocab_rows for cr in d.chunk_reports)
        if out_of_vocab:
            lines.append(f"    out-of-vocabulary rows (all-zero one-hot): {out_of_vocab}")
        lines.append(f"    meta bumped: {d.meta_bumped}")

    # Summary table.
    lines.append("")
    lines.append("--- summary ---")
    for disp in Disposition:
        dirs = report.by_disposition(disp)
        lines.append(f"  {disp.value:<20} dirs: {len(dirs)}")
    total_chunks = sum(
        d.n_chunks for d in report.dir_results if d.disposition is Disposition.PATCHED
    )
    verb = "chunks to write" if report.dry_run else "chunks written"
    lines.append(f"  {verb}: {total_chunks}")
    totals = report.total_expansion_counts
    if totals:
        lines.append("  rows per expansion (all patched dirs):")
        for exp, n in sorted(totals.items()):
            lines.append(f"      {exp:<10} {n}")
    return "\n".join(lines)
