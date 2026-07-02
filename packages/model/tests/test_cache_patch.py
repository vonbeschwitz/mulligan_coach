"""Tests for the v1 -> v2 set-code one-hot cache patch.

Everything runs on synthetic chunk parquets built directly with pyarrow
(a "3-column old vocab + expansion" shard, including SOS / MSH / an
out-of-vocabulary row) under ``tmp_path`` — the real ``data/`` cache is
never touched. The tests assert the invariants the spec calls out:

* the five one-hots are rewritten correctly and every other column is
  bit-identical after the patch;
* the ``_meta.json`` bump preserves unknown keys and records
  ``patch_history``;
* re-runs are dir-level no-ops (byte-identical chunks);
* legacy (no-meta) dirs are patched but stay meta-less;
* an unrecognised version combination is refused, not guessed;
* dry runs write nothing;
* a crash between chunk-patching and the meta bump recovers idempotently.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mulligan_coach_model.cache_patch import (
    V2_KNOWN_SETS,
    CachePatchError,
    Disposition,
    format_report,
    patch_chunk,
    patch_roots,
    patch_shard_dir,
)
from mulligan_coach_model.versioning import SHARD_META_FILENAME, now_iso

# ---------------------------------------------------------------------------
# Synthetic fixture builders
# ---------------------------------------------------------------------------

# A representative mix: each known set once, plus an out-of-vocabulary row.
_EXPANSIONS = ["TMT", "ECL", "TLA", "SOS", "MSH", "ZZZ"]


def _write_v1_chunk(path: Path, expansions: list[str]) -> pa.Table:
    """Write a synthetic v1 chunk: the old 3-set one-hots + expansion + two
    'untouched' columns whose bytes we later assert are preserved."""
    n = len(expansions)
    table = pa.table(
        {
            # Untouched non-set columns (distinct dtypes + values).
            "on_the_play": pa.array([1.0 if i % 2 else 0.0 for i in range(n)], pa.float64()),
            "some_feature": pa.array([float(i) * 0.25 for i in range(n)], pa.float64()),
            # Old (v1) vocabulary one-hots — float64, exactly as the builder wrote.
            "set_code_TMT": pa.array(
                [1.0 if e == "TMT" else 0.0 for e in expansions], pa.float64()
            ),
            "set_code_ECL": pa.array(
                [1.0 if e == "ECL" else 0.0 for e in expansions], pa.float64()
            ),
            "set_code_TLA": pa.array(
                [1.0 if e == "TLA" else 0.0 for e in expansions], pa.float64()
            ),
            "expansion": pa.array(expansions, pa.string()),
            "draft_id": pa.array([f"d-{i:03d}" for i in range(n)], pa.string()),
        }
    )
    pq.write_table(table, path, compression="zstd")  # type: ignore[no-untyped-call]
    return table


def _write_meta(
    shard_dir: Path,
    versions: dict[str, int],
    *,
    extra: dict[str, object] | None = None,
) -> Path:
    """Write a raw ``_meta.json`` (optionally with unknown keys) at the
    given pipeline versions."""
    data: dict[str, object] = {
        "pipeline_versions": versions,
        "set_code": "TLA",
        "event_type": "PremierDraft",
        "n_sims_per_row": 200,
        "created_at": now_iso(),
        "unverified_legacy": False,
    }
    if extra:
        data.update(extra)
    path = shard_dir / SHARD_META_FILENAME
    path.write_text(json.dumps(data, indent=2))
    return path


# Default meta versions for a "standard v1 shard" fixture. Module-level so
# the factory's default isn't a mutable literal (B006); no test mutates it.
_V1_META_VERSIONS: dict[str, int] = {"simulation": 1, "features": 1}


def _make_shard(
    tmp_path: Path,
    *,
    versions: dict[str, int] | None = _V1_META_VERSIONS,
    expansions: list[str] | None = None,
    extra_meta: dict[str, object] | None = None,
) -> tuple[Path, pa.Table]:
    """Build a one-chunk shard dir. ``versions=None`` => legacy (no meta)."""
    shard_dir = tmp_path / "TLA" / "PremierDraft"
    shard_dir.mkdir(parents=True)
    original = _write_v1_chunk(shard_dir / "chunk_00000000.parquet", expansions or _EXPANSIONS)
    if versions is not None:
        _write_meta(shard_dir, versions, extra=extra_meta)
    return shard_dir, original


def _read(shard_dir: Path) -> pa.Table:
    return pq.read_table(shard_dir / "chunk_00000000.parquet")  # type: ignore[no-untyped-call]


# ---------------------------------------------------------------------------
# Happy path: five columns, untouched columns bit-identical
# ---------------------------------------------------------------------------


def test_patch_produces_five_columns_and_preserves_untouched(tmp_path: Path) -> None:
    shard_dir, original = _make_shard(tmp_path)

    result = patch_shard_dir(shard_dir, dry_run=False)
    assert result.disposition is Disposition.PATCHED
    assert result.meta_bumped is True

    patched = _read(shard_dir)

    # Row count + order preserved.
    assert patched.num_rows == original.num_rows
    assert patched.column("draft_id").to_pylist() == original.column("draft_id").to_pylist()

    # All five one-hots present and float64.
    for s in V2_KNOWN_SETS:
        assert patched.schema.field(f"set_code_{s}").type == pa.float64()

    # One-hot correctness, including the out-of-vocab (ZZZ) all-zero row.
    for i, exp in enumerate(_EXPANSIONS):
        row = {s: patched.column(f"set_code_{s}")[i].as_py() for s in V2_KNOWN_SETS}
        if exp in V2_KNOWN_SETS:
            assert row[exp] == 1.0
            assert sum(row.values()) == 1.0
        else:
            assert sum(row.values()) == 0.0

    # Untouched columns bit-identical (compare Arrow column data directly).
    for name in ("on_the_play", "some_feature", "expansion", "draft_id"):
        assert patched.column(name).equals(original.column(name)), name


# ---------------------------------------------------------------------------
# Meta bump: features 1 -> 2, patch_history, unknown keys preserved
# ---------------------------------------------------------------------------


def test_meta_bumped_and_unknown_keys_preserved(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path, extra_meta={"custom_note": "keep-me", "answer": 42})

    patch_shard_dir(shard_dir, dry_run=False)

    meta = json.loads((shard_dir / SHARD_META_FILENAME).read_text())
    assert meta["pipeline_versions"] == {"simulation": 1, "features": 2}
    # Unknown keys survived the raw-JSON edit.
    assert meta["custom_note"] == "keep-me"
    assert meta["answer"] == 42
    # patch_history entry recorded.
    history = meta["patch_history"]
    assert isinstance(history, list) and len(history) == 1
    entry = history[0]
    assert entry["patch"] == "set_onehots_v1"
    assert entry["from_features"] == 1
    assert entry["to_features"] == 2
    assert "at" in entry


# ---------------------------------------------------------------------------
# Second run: skipped, chunks byte-identical
# ---------------------------------------------------------------------------


def test_second_run_skips_and_chunk_bytes_identical(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path)
    chunk = shard_dir / "chunk_00000000.parquet"

    patch_shard_dir(shard_dir, dry_run=False)
    bytes_after_first = chunk.read_bytes()
    meta_after_first = (shard_dir / SHARD_META_FILENAME).read_bytes()

    result = patch_shard_dir(shard_dir, dry_run=False)
    assert result.disposition is Disposition.SKIPPED_ALREADY_V2
    assert result.meta_bumped is False
    # Nothing rewritten on the no-op re-run.
    assert chunk.read_bytes() == bytes_after_first
    assert (shard_dir / SHARD_META_FILENAME).read_bytes() == meta_after_first


# ---------------------------------------------------------------------------
# Legacy dir (no meta): chunks patched, meta stays absent
# ---------------------------------------------------------------------------


def test_legacy_dir_patched_without_creating_meta(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path, versions=None)  # no _meta.json

    result = patch_shard_dir(shard_dir, dry_run=False)
    assert result.disposition is Disposition.LEGACY
    assert result.meta_bumped is False
    assert not (shard_dir / SHARD_META_FILENAME).exists()

    # Chunks still got the correct five one-hots.
    patched = _read(shard_dir)
    for s in V2_KNOWN_SETS:
        assert f"set_code_{s}" in patched.schema.names
    assert patched.column("set_code_SOS").to_pylist()[3] == 1.0  # SOS row


# ---------------------------------------------------------------------------
# Refused: unrecognised version combination
# ---------------------------------------------------------------------------


def test_unexpected_versions_refused_and_chunk_untouched(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path, versions={"simulation": 2, "features": 1})
    chunk = shard_dir / "chunk_00000000.parquet"
    bytes_before = chunk.read_bytes()

    result = patch_shard_dir(shard_dir, dry_run=False)
    assert result.disposition is Disposition.REFUSED
    assert result.reason is not None
    # Not patched: no SOS/MSH columns, bytes untouched.
    assert chunk.read_bytes() == bytes_before
    assert "set_code_SOS" not in _read(shard_dir).schema.names


# ---------------------------------------------------------------------------
# Dry run: nothing on disk changes
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path)
    chunk = shard_dir / "chunk_00000000.parquet"
    meta = shard_dir / SHARD_META_FILENAME

    chunk_bytes = chunk.read_bytes()
    meta_bytes = meta.read_bytes()

    result = patch_shard_dir(shard_dir, dry_run=True)
    assert result.disposition is Disposition.PATCHED  # WOULD patch
    assert result.meta_bumped is False
    assert all(not cr.written for cr in result.chunk_reports)
    # Per-expansion counts still reported for the dry-run report.
    assert result.expansion_counts["SOS"] == 1
    assert result.expansion_counts["ZZZ"] == 1

    # Disk unchanged.
    assert chunk.read_bytes() == chunk_bytes
    assert meta.read_bytes() == meta_bytes


# ---------------------------------------------------------------------------
# Crash recovery: chunks patched but meta left at 1 -> re-run finishes
# ---------------------------------------------------------------------------


def test_crash_between_chunk_and_meta_recovers_idempotently(tmp_path: Path) -> None:
    shard_dir, _ = _make_shard(tmp_path)
    chunk = shard_dir / "chunk_00000000.parquet"

    # Simulate a crash: chunk-level patch ran, but the meta bump didn't.
    first = patch_chunk(chunk)
    assert first.written is True
    assert first.set_columns_added == ["set_code_SOS", "set_code_MSH"]
    # Meta is still at features 1 (never bumped).
    meta = json.loads((shard_dir / SHARD_META_FILENAME).read_text())
    assert meta["pipeline_versions"]["features"] == 1

    # Patching the same chunk again is idempotent (same values).
    values_once = _read(shard_dir).column("set_code_SOS").to_pylist()
    patch_chunk(chunk)
    assert _read(shard_dir).column("set_code_SOS").to_pylist() == values_once

    # The full dir-level re-run still sees v1 meta, re-patches, and bumps.
    result = patch_shard_dir(shard_dir, dry_run=False)
    assert result.disposition is Disposition.PATCHED
    assert result.meta_bumped is True
    meta2 = json.loads((shard_dir / SHARD_META_FILENAME).read_text())
    assert meta2["pipeline_versions"]["features"] == 2


# ---------------------------------------------------------------------------
# patch_chunk guards + patch_roots scan
# ---------------------------------------------------------------------------


def test_patch_chunk_missing_expansion_raises(tmp_path: Path) -> None:
    path = tmp_path / "chunk_00000000.parquet"
    pq.write_table(  # type: ignore[no-untyped-call]
        pa.table({"some_feature": pa.array([1.0, 2.0], pa.float64())}), path
    )
    with pytest.raises(CachePatchError, match="expansion"):
        patch_chunk(path)


def test_patch_roots_scans_nested_dirs_and_reports(tmp_path: Path) -> None:
    # Two shard dirs under one root, each set/event nested.
    root = tmp_path / "choice_training"
    for set_code in ("TLA", "SOS"):
        d = root / set_code / "PremierDraft"
        d.mkdir(parents=True)
        _write_v1_chunk(d / "chunk_00000000.parquet", _EXPANSIONS)
        _write_meta(d, {"simulation": 1, "features": 1})

    report = patch_roots([root], dry_run=True)
    assert len(report.dir_results) == 2
    assert all(d.disposition is Disposition.PATCHED for d in report.dir_results)
    # Aggregate counts + rendered report are sane.
    assert report.total_expansion_counts["MSH"] == 2  # one per dir
    text = format_report(report)
    assert "DRY RUN" in text
    assert "set_onehots_v1" in text
