"""Tests for the pipeline-version lineage helpers.

Covers the stdlib-only primitives in
:mod:`mulligan_coach_model.versioning`:

* :class:`ShardMeta` JSON round-trip + forward-compat (unknown keys) +
  missing-file behaviour.
* :func:`shard_meta_mismatches` / :func:`stamp_or_check_shard_meta`
  reconcile logic (fresh, resume-match, mismatch, legacy, overwrite).
* :func:`gather_shard_lineage` / :func:`check_training_lineage`.
* :func:`draftid_hash_unit` determinism.
* :func:`compute_version_warning`.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from mulligan_coach_model.versioning import (
    SHARD_META_FILENAME,
    ShardLineageEntry,
    ShardMeta,
    ShardVersionError,
    check_training_lineage,
    compute_version_warning,
    draftid_hash_unit,
    gather_shard_lineage,
    now_iso,
    pipeline_versions,
    read_shard_meta,
    shard_meta_mismatches,
    stamp_or_check_shard_meta,
    write_shard_meta,
)


def _meta(
    *,
    versions: dict[str, int] | None = None,
    set_code: str = "TLA",
    event_type: str = "PremierDraft",
    n_sims: int = 200,
    legacy: bool = False,
) -> ShardMeta:
    return ShardMeta(
        pipeline_versions=versions if versions is not None else {"simulation": 1, "features": 1},
        set_code=set_code,
        event_type=event_type,
        n_sims_per_row=n_sims,
        created_at=now_iso(),
        unverified_legacy=legacy,
    )


# ---------------------------------------------------------------------------
# ShardMeta round-trip / forward-compat / missing file
# ---------------------------------------------------------------------------


def test_shard_meta_round_trips_through_disk(tmp_path: Path) -> None:
    meta = _meta(n_sims=123)
    write_shard_meta(tmp_path, meta)
    loaded = read_shard_meta(tmp_path)
    assert loaded == meta
    # The sidecar uses the underscore-prefixed filename so it stays out of
    # the chunk_*.parquet globs.
    assert (tmp_path / SHARD_META_FILENAME).exists()


def test_read_shard_meta_tolerates_unknown_keys(tmp_path: Path) -> None:
    """A newer writer may add fields; an older reader must ignore them."""
    payload = {
        "pipeline_versions": {"simulation": 1, "features": 1},
        "set_code": "TLA",
        "event_type": "PremierDraft",
        "n_sims_per_row": 200,
        "created_at": now_iso(),
        "unverified_legacy": False,
        "some_future_field": {"nested": [1, 2, 3]},
        "another_future_scalar": 42,
    }
    (tmp_path / SHARD_META_FILENAME).write_text(json.dumps(payload))
    loaded = read_shard_meta(tmp_path)
    assert loaded is not None
    assert loaded.set_code == "TLA"
    assert loaded.n_sims_per_row == 200


def test_read_shard_meta_missing_file_returns_none(tmp_path: Path) -> None:
    assert read_shard_meta(tmp_path / "does_not_exist") is None
    # A directory with no sidecar also reads as None.
    assert read_shard_meta(tmp_path) is None


# ---------------------------------------------------------------------------
# shard_meta_mismatches
# ---------------------------------------------------------------------------


def test_shard_meta_mismatches_detects_each_field() -> None:
    base = _meta()
    assert shard_meta_mismatches(base, base) == []

    versions = shard_meta_mismatches(base, _meta(versions={"simulation": 2, "features": 1}))
    assert any("pipeline_versions" in m for m in versions)

    nsims = shard_meta_mismatches(base, _meta(n_sims=100))
    assert any("n_sims_per_row" in m for m in nsims)

    setc = shard_meta_mismatches(base, _meta(set_code="TMT"))
    assert any("set_code" in m for m in setc)

    evt = shard_meta_mismatches(base, _meta(event_type="TradDraft"))
    assert any("event_type" in m for m in evt)

    # unverified_legacy is provenance, not compatibility.
    assert shard_meta_mismatches(base, _meta(legacy=True)) == []


# ---------------------------------------------------------------------------
# stamp_or_check_shard_meta
# ---------------------------------------------------------------------------


def test_stamp_fresh_dir_writes_meta(tmp_path: Path) -> None:
    current = _meta()
    stamp_or_check_shard_meta(tmp_path, current=current, had_existing_chunks=False, overwrite=False)
    loaded = read_shard_meta(tmp_path)
    assert loaded == current
    assert loaded is not None and not loaded.unverified_legacy


def test_stamp_resume_on_match_leaves_meta(tmp_path: Path) -> None:
    current = _meta()
    write_shard_meta(tmp_path, current)
    # Resume onto existing chunks with a matching meta: no error, meta kept.
    stamp_or_check_shard_meta(tmp_path, current=_meta(), had_existing_chunks=True, overwrite=False)
    loaded = read_shard_meta(tmp_path)
    assert loaded == current


def test_stamp_resume_version_mismatch_raises(tmp_path: Path) -> None:
    write_shard_meta(tmp_path, _meta(versions={"simulation": 1, "features": 1}))
    with pytest.raises(ShardVersionError, match="pipeline_versions"):
        stamp_or_check_shard_meta(
            tmp_path,
            current=_meta(versions={"simulation": 2, "features": 1}),
            had_existing_chunks=True,
            overwrite=False,
        )


def test_stamp_resume_n_sims_mismatch_raises(tmp_path: Path) -> None:
    write_shard_meta(tmp_path, _meta(n_sims=200))
    with pytest.raises(ShardVersionError, match="n_sims_per_row"):
        stamp_or_check_shard_meta(
            tmp_path, current=_meta(n_sims=50), had_existing_chunks=True, overwrite=False
        )


def test_stamp_legacy_dir_warns_and_stamps(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Chunks exist but no sidecar: warn + stamp unverified_legacy=True."""
    with caplog.at_level(logging.WARNING, logger="mulligan_coach_model.versioning"):
        stamp_or_check_shard_meta(
            tmp_path, current=_meta(), had_existing_chunks=True, overwrite=False
        )
    loaded = read_shard_meta(tmp_path)
    assert loaded is not None
    assert loaded.unverified_legacy is True
    assert any("legacy" in rec.message.lower() for rec in caplog.records)


def test_stamp_overwrite_resets_meta(tmp_path: Path) -> None:
    """overwrite=True rewrites the sidecar even when one already exists,
    clearing any legacy flag."""
    write_shard_meta(tmp_path, _meta(n_sims=50, legacy=True))
    stamp_or_check_shard_meta(
        tmp_path, current=_meta(n_sims=200), had_existing_chunks=True, overwrite=True
    )
    loaded = read_shard_meta(tmp_path)
    assert loaded is not None
    assert loaded.n_sims_per_row == 200
    assert loaded.unverified_legacy is False


# ---------------------------------------------------------------------------
# Lineage gather + training check
# ---------------------------------------------------------------------------


def test_gather_shard_lineage_reads_meta_and_marks_legacy(tmp_path: Path) -> None:
    with_meta = tmp_path / "a"
    without_meta = tmp_path / "b"
    with_meta.mkdir()
    without_meta.mkdir()
    write_shard_meta(with_meta, _meta(n_sims=200))

    paths = [
        with_meta / "chunk_00000000.parquet",
        with_meta / "chunk_00000001.parquet",  # same dir -> deduped
        without_meta / "chunk_00000000.parquet",
    ]
    lineage = gather_shard_lineage(paths)
    assert len(lineage) == 2  # deduped to two dirs
    by_dir = {Path(e.dir).name: e for e in lineage}
    assert by_dir["a"].pipeline_versions == {"simulation": 1, "features": 1}
    assert by_dir["a"].n_sims_per_row == 200
    assert by_dir["b"].pipeline_versions is None  # legacy -> null lineage


def test_check_training_lineage_legacy_only_warns(caplog: pytest.LogCaptureFixture) -> None:
    lineage = [
        ShardLineageEntry(
            dir="/x/b", pipeline_versions=None, unverified_legacy=False, n_sims_per_row=None
        )
    ]
    with caplog.at_level(logging.WARNING, logger="mulligan_coach_model.versioning"):
        check_training_lineage(lineage, allow_version_mismatch=False)  # no raise
    assert any("no version metadata" in rec.message for rec in caplog.records)


def test_check_training_lineage_mismatch_raises_unless_allowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    live = pipeline_versions()
    stale = {k: v + 1 for k, v in live.items()}
    lineage = [
        ShardLineageEntry(
            dir="/x/a", pipeline_versions=stale, unverified_legacy=False, n_sims_per_row=200
        ),
    ]
    with pytest.raises(ShardVersionError, match="differ from the live"):
        check_training_lineage(lineage, allow_version_mismatch=False)
    # With the override it proceeds (logs a warning instead).
    with caplog.at_level(logging.WARNING, logger="mulligan_coach_model.versioning"):
        check_training_lineage(lineage, allow_version_mismatch=True)
    assert any("Proceeding anyway" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# draftid_hash_unit + compute_version_warning
# ---------------------------------------------------------------------------


def test_draftid_hash_unit_deterministic_and_in_range() -> None:
    a = draftid_hash_unit(0, "draft-1")
    b = draftid_hash_unit(0, "draft-1")
    assert a == b
    assert 0.0 <= a < 1.0
    # Different seed / id changes the value.
    assert draftid_hash_unit(1, "draft-1") != a
    assert draftid_hash_unit(0, "draft-2") != a


def test_compute_version_warning() -> None:
    live = pipeline_versions()
    # Matching versions -> no warning.
    assert compute_version_warning(dict(live)) is None
    # Absent (old model) -> warning.
    absent = compute_version_warning(None)
    assert absent is not None and "before pipeline-version stamping" in absent
    # Mismatched -> warning.
    stale = {k: v + 1 for k, v in live.items()}
    warn = compute_version_warning(stale)
    assert warn is not None and "differ" in warn
