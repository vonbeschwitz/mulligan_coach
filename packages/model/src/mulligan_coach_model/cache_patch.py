"""In-place upgrade of v1 feature caches to v2 set-code one-hot semantics.

Roadmap Step 2 appended ``SOS`` and ``MSH`` to
``mulligan_coach_features.DEFAULT_KNOWN_SETS`` and bumped
``FEATURES_SEMANTICS_VERSION`` 1 -> 2. A freshly-materialised cache would
carry the two new ``set_code_*`` columns and the corrected SOS one-hot,
but re-materialising a format costs ~17 h of simulation. This module
upgrades an *existing* v1 cache to the v2 one-hot encoding **without any
re-simulation**: only the ``set_code_*`` columns are rewritten, purely
from each chunk's already-stored ``expansion`` column. Every other
column (the ~200 simulation/feature columns, the label, the context
columns) is carried over physically untouched.

Scope: the win-model caches under
``data/processed/model_training/<SET>/<EVENT>/`` and the choice-model
caches under ``data/processed/choice_training/<SET>/<EVENT>/`` — both
share the ``build_feature_row`` schema, so both carry the same
``set_code_*`` + ``expansion`` columns.

Design (see docs/specs/step2_set_vocabulary.md §C):

* **Idempotent + self-correcting.** Every run rewrites ALL five
  ``set_code_{S}`` columns from ``expansion`` — not just the two new
  ones — so a partial or wrong prior state converges to correct. A row
  whose ``expansion`` is outside the vocabulary gets all five columns
  ``0.0`` (matching the feature builder's all-zero reference-category
  behaviour for unknown sets); such rows are counted and reported, never
  a failure.
* **pyarrow table ops, no pandas round-trip.** A pandas round-trip can
  perturb dtypes / float precision on the untouched columns; we replace
  only the five one-hot columns and leave the rest as their original
  Arrow buffers.
* **Atomic per chunk.** Write a tmp sibling, validate, then
  ``os.replace`` — a crash never leaves a half-written chunk.
* **Meta bump last, as raw JSON.** Only after every chunk in a shard dir
  succeeds do we edit ``_meta.json`` (features 1 -> 2 + a ``patch_history``
  entry). Editing the raw dict — not via :class:`ShardMeta` — preserves
  any unknown keys. A crash between chunk-patching and the meta bump is
  safe: the re-run re-patches idempotently, then bumps.

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
# Constants pinning the v1 -> v2 migration
# ---------------------------------------------------------------------------

# The one-hot vocabulary this patch writes, pinned as a literal. It is
# deliberately NOT taken live from ``DEFAULT_KNOWN_SETS`` at call sites:
# this tool is the *v1 -> v2* migration, and a LATER vocabulary bump (a 6th
# set, features -> 3) must not silently reuse it to stamp ``features: 2``
# while writing a v3 column set. :func:`patch_roots` asserts that the live
# builder vocabulary still equals this literal; if a future bump changes
# it, write a NEW patch (``set_onehots_v2``, target features 3), don't
# reuse this one.
V2_KNOWN_SETS: tuple[str, ...] = ("TMT", "ECL", "TLA", "SOS", "MSH")

# The (whole) pipeline-version dict a shard must carry to be eligible for
# this patch. Any other present combination is refused (unknown
# provenance — we don't guess how it was built).
SOURCE_PIPELINE_VERSIONS: dict[str, int] = {"simulation": 1, "features": 1}

SOURCE_FEATURES_VERSION: int = 1
TARGET_FEATURES_VERSION: int = 2

# Recorded in each patched shard's ``_meta.json`` ``patch_history``.
PATCH_NAME: str = "set_onehots_v1"

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
    """Meta present at the v1 source version — chunks patched, meta bumped."""
    LEGACY = "legacy"
    """No ``_meta.json`` — chunks patched, meta intentionally left absent
    (stays visibly legacy; training keeps warning on it)."""
    SKIPPED_ALREADY_V2 = "skipped_already_v2"
    """Meta already at features 2 — nothing to do (re-run no-op)."""
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


class CachePatchError(RuntimeError):
    """Raised on a genuinely corrupt cache (missing ``expansion`` column,
    post-patch validation failure) — as opposed to the *refused* path,
    which is a recorded outcome, not an exception."""


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
      exactly one 1.0 across the five columns when the expansion is in
      vocabulary; all five 0.0 when it isn't.
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
    # column order stable for TMT/ECL/TLA, which already exist in v1).
    for s in known_sets:
        name = f"{_SET_CODE_PREFIX}{s}"
        if name in present:
            idx = result.schema.get_field_index(name)
            result = result.set_column(
                idx, pa.field(name, pa.float64()), _one_hot_array(expansions, s)
            )
            replaced.append(name)
    # New columns (SOS/MSH) are appended so they land at the end.
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


def analyze_chunk(chunk: Path, known_sets: tuple[str, ...] = V2_KNOWN_SETS) -> ChunkReport:
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


def patch_chunk(chunk: Path, known_sets: tuple[str, ...] = V2_KNOWN_SETS) -> ChunkReport:
    """Rewrite one chunk's ``set_code_*`` columns in place, atomically.

    Reads the whole table, rebuilds the five one-hots from ``expansion``,
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


def _classify_dir(shard_dir: Path) -> tuple[Disposition, str | None]:
    """Decide what to do with a shard dir from its ``_meta.json``.

    Order matters: an already-v2 shard is a no-op (so re-runs are safe)
    *before* we consider the strict source match, and any other present
    combination is refused rather than guessed at.
    """
    meta_path = shard_dir / SHARD_META_FILENAME
    if not meta_path.exists():
        return Disposition.LEGACY, None

    data = json.loads(meta_path.read_text())
    versions = data.get("pipeline_versions")
    if not isinstance(versions, dict):
        return (
            Disposition.REFUSED,
            f"{SHARD_META_FILENAME} has no valid 'pipeline_versions' dict: {versions!r}",
        )
    versions_int = {str(k): int(v) for k, v in versions.items()}

    if versions_int.get("features") == TARGET_FEATURES_VERSION:
        return Disposition.SKIPPED_ALREADY_V2, None
    if versions_int == SOURCE_PIPELINE_VERSIONS:
        return Disposition.PATCHED, None
    return (
        Disposition.REFUSED,
        f"unrecognised pipeline_versions {versions_int}; this patch only upgrades "
        f"{SOURCE_PIPELINE_VERSIONS} (unknown provenance — refusing to guess).",
    )


def _bump_meta_features(shard_dir: Path) -> None:
    """Raw-JSON bump of a shard's ``_meta.json`` from features 1 to 2.

    Preserves every existing key (including unknown ones a newer writer
    may have added): we read the dict, set ``pipeline_versions.features``
    to 2, append a ``patch_history`` entry, and atomically write it back.
    """
    meta_path = shard_dir / SHARD_META_FILENAME
    data = json.loads(meta_path.read_text())
    versions = data["pipeline_versions"]
    versions["features"] = TARGET_FEATURES_VERSION
    history = data.setdefault("patch_history", [])
    if not isinstance(history, list):
        raise CachePatchError(
            f"{meta_path}: existing 'patch_history' is not a list ({type(history)!r})"
        )
    history.append(
        {
            "patch": PATCH_NAME,
            "at": now_iso(),
            "from_features": SOURCE_FEATURES_VERSION,
            "to_features": TARGET_FEATURES_VERSION,
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
    known_sets: tuple[str, ...] = V2_KNOWN_SETS,
) -> DirResult:
    """Patch one shard directory end to end (eligibility -> chunks -> meta).

    * SKIP / REFUSE dispositions do no chunk work.
    * PATCH / LEGACY: every ``chunk_*.parquet`` is (dry-run: analysed;
      real: rewritten). The meta bump happens only after all chunks
      succeed, and only for the PATCH (meta-present) case — a LEGACY dir
      keeps no sidecar so it stays visibly unverified.
    """
    disposition, reason = _classify_dir(shard_dir)

    if disposition is Disposition.REFUSED:
        log.error("REFUSED %s: %s", shard_dir, reason)
        return DirResult(shard_dir=shard_dir, disposition=disposition, reason=reason)
    if disposition is Disposition.SKIPPED_ALREADY_V2:
        log.info(
            "SKIP %s: already at features %d (patched previously).",
            shard_dir,
            TARGET_FEATURES_VERSION,
        )
        return DirResult(shard_dir=shard_dir, disposition=disposition)

    chunks = sorted(shard_dir.glob(_CHUNK_GLOB))
    if not chunks:
        # A meta-only dir with no chunks: nothing to patch; treat as a
        # no-op skip rather than inventing an outcome.
        log.warning("%s has a meta but no chunk files; nothing to patch.", shard_dir)
        return DirResult(shard_dir=shard_dir, disposition=Disposition.SKIPPED_ALREADY_V2)

    reports: list[ChunkReport] = []
    for chunk in chunks:
        report = analyze_chunk(chunk, known_sets) if dry_run else patch_chunk(chunk, known_sets)
        reports.append(report)

    meta_bumped = False
    if not dry_run and disposition is Disposition.PATCHED:
        _bump_meta_features(shard_dir)
        meta_bumped = True

    label = "DRY-RUN" if dry_run else disposition.value.upper()
    log.info(
        "%s %s: %d chunk(s), %d rows%s",
        label,
        shard_dir,
        len(reports),
        sum(r.n_rows for r in reports),
        " (meta bumped 1->2)" if meta_bumped else "",
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
    known_sets: tuple[str, ...] = V2_KNOWN_SETS,
) -> PatchReport:
    """Patch every shard dir found under each root.

    Asserts the live builder vocabulary still matches the pinned v2
    vocabulary this patch writes — a mismatch means the vocabulary was
    bumped again and this v1->v2 tool must not be reused blindly.
    """
    if tuple(DEFAULT_KNOWN_SETS) != known_sets:
        raise CachePatchError(
            "live DEFAULT_KNOWN_SETS "
            f"{tuple(DEFAULT_KNOWN_SETS)} != this patch's pinned vocabulary {known_sets}. "
            "The feature vocabulary changed again; write a NEW patch (target the new "
            "FEATURES_SEMANTICS_VERSION) rather than reusing the v1->v2 patch."
        )

    dir_results: list[DirResult] = []
    for root in roots:
        shard_dirs = find_shard_dirs(root)
        if not shard_dirs:
            log.info("No shard dirs under %s", root)
            continue
        log.info("Scanning %d shard dir(s) under %s", len(shard_dirs), root)
        for shard_dir in shard_dirs:
            dir_results.append(patch_shard_dir(shard_dir, dry_run=dry_run, known_sets=known_sets))
    return PatchReport(dir_results=dir_results, dry_run=dry_run)


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
    lines.append(f"=== set-code one-hot patch ({PATCH_NAME}) — {mode} ===")

    for d in report.dir_results:
        lines.append("")
        lines.append(f"{d.disposition.value.upper():<20} {d.shard_dir}")
        if d.disposition is Disposition.REFUSED:
            lines.append(f"    reason: {d.reason}")
            continue
        if d.disposition is Disposition.SKIPPED_ALREADY_V2:
            continue
        # PATCHED / LEGACY: show chunk + per-expansion detail.
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
        lines.append(f"    meta bumped 1->2: {d.meta_bumped}")

    # Summary table.
    lines.append("")
    lines.append("--- summary ---")
    for disp in Disposition:
        dirs = report.by_disposition(disp)
        lines.append(f"  {disp.value:<20} dirs: {len(dirs)}")
    total_chunks = sum(
        d.n_chunks
        for d in report.dir_results
        if d.disposition in (Disposition.PATCHED, Disposition.LEGACY)
    )
    verb = "chunks to write" if report.dry_run else "chunks written"
    lines.append(f"  {verb}: {total_chunks}")
    totals = report.total_expansion_counts
    if totals:
        lines.append("  rows per expansion (all patched/legacy dirs):")
        for exp, n in sorted(totals.items()):
            lines.append(f"      {exp:<10} {n}")
    return "\n".join(lines)
