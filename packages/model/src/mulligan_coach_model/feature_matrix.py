"""Materialise :class:`TrainingRow` instances into a parquet feature cache.

The slow step of model training is the per-row Monte Carlo
simulation (`simulate()`). On a typical Premier-Draft format with
~1M rows we'd be re-running it on every fit experiment, so we
materialise the simulator + feature builder output to a parquet
cache once and let later XGBoost / baseline fits read from disk.

Output schema (per row):

* All 200 columns from
  :func:`mulligan_coach_features.build_feature_row`.
* ``opp_mulligan_count_if_known`` — the opponent's mulligan count
  when the player was on the draw; NULL when on the play. XGBoost's
  native missing-value handling lets the model use this only in the
  info set where it's actually available.
* Context columns needed by the residualization baseline and the
  grouped train/val split: ``user_wr_bucket``,
  ``user_n_games_bucket``, ``opp_mulligan_number`` (the raw value,
  always populated — distinct from the conditional feature),
  ``mulligan_number``, ``expansion``, ``event_type``, ``draft_id``,
  ``match_number``, ``game_number``. The triple
  ``(draft_id, match_number, game_number)`` uniquely identifies a
  17Lands game row; it's used as the per-row simulator seed and
  as the resume skip-key.
* Label: ``won``.

The on_the_play context feature is intentionally **not** duplicated
into a separate context column — it's already a feature.

Storage layout
--------------

``data/processed/model_training/<EXPANSION>/<EVENT_TYPE>/`` — a
directory per ``(set, event_type)`` containing
``chunk_NNNNNNNN.parquet`` files. Each chunk is written atomically
(``.chunk_*.tmp-<pid>`` then ``os.replace``); a crashed mid-write
leaves only the tmp file, never a partial canonical chunk.

The whole directory is gitignored (per ``.gitignore`` rules for
``data/processed/*``); trained-model artifacts go in ``models/``
instead.

Resumability is per-chunk: on restart, the function scans the
existing chunks, reads their identity columns into a skip-set,
and filters the input row stream so workers only see rows that
haven't been completed yet. Stale ``.chunk_*.tmp-*`` files from a
prior crashed run are swept on startup.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

# DuckDB is only used inside ``materialize_feature_matrix`` to read
# the training games view. Importing it lazily there keeps the
# overlay's inference-only import graph from pulling in DuckDB's
# ~36 MB native lib (the recommend service imports this module
# transitively via ``mulligan_coach_model`` → ``BaselineModel``).
from mulligan_coach_cards import (
    ParsedCard,
    load_premier_draft_stats,
)
from mulligan_coach_features import (
    CardZScores,
    ShrunkWinRates,
    build_feature_row,
    compute_format_priors,
    compute_format_wr_distribution,
    shrink_stats,
    zscore_stats,
)
from mulligan_coach_simulation import simulate

from .training_rows import (
    TrainingRow,
    TrainingRowStats,
    iter_training_rows,
)
from .versioning import (
    ShardMeta,
    now_iso,
    pipeline_versions,
    stamp_or_check_shard_meta,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class MaterializationStats:
    """Counters returned by :func:`materialize_feature_matrix`.

    Includes the embedded :class:`TrainingRowStats` so the caller
    can audit both the SQL-side filtering and the feature-build
    pass in one go.
    """

    rows_written: int = 0
    rows_failed_simulation: int = 0
    rows_failed_feature_build: int = 0
    chunks_written: int = 0
    rows_skipped_resume: int = 0
    training_row_stats: TrainingRowStats = field(default_factory=TrainingRowStats)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _library_from_deck(
    hand: tuple[ParsedCard, ...],
    deck: tuple[ParsedCard, ...],
) -> list[ParsedCard]:
    """Return ``deck - hand`` (multiset subtraction by card name).

    17Lands records integer counts, not specific card instances.
    Within a ``TrainingRow`` the hand and deck lists both reference
    the same shared ParsedCard objects from the per-set lookup, so
    subtracting by name (decrementing a Counter as we iterate the
    deck) leaves a library of the correct shape: every hand copy is
    removed exactly once.
    """
    from collections import Counter

    hand_counts: Counter[str] = Counter(c.name for c in hand)
    library: list[ParsedCard] = []
    for card in deck:
        if hand_counts.get(card.name, 0) > 0:
            hand_counts[card.name] -= 1
        else:
            library.append(card)
    return library


def _row_seed(draft_id: str, match_number: int, game_number: int) -> int:
    """Deterministic per-row simulation seed.

    Stable across runs so the materialisation step is
    reproducible bit-for-bit. We hash
    ``(draft_id, match_number, game_number)`` into a 32-bit
    positive int (Python's built-in :func:`hash` is randomised
    per process; use a stable digest instead).

    The match_number is included because
    ``(draft_id, game_number)`` alone is **not** unique in
    17Lands data — one draft plays multiple matches, each with
    multiple games.
    """
    import hashlib

    raw = f"{draft_id}\x00{match_number}\x00{game_number}".encode()
    digest = hashlib.sha256(raw).digest()
    # First 4 bytes -> unsigned 32-bit int. Numpy's PCG accepts
    # arbitrary uint64; this fits within the simulator's seed range.
    return int.from_bytes(digest[:4], "big")


@dataclass(frozen=True)
class _FormatStats:
    """Pre-computed shrunk / zscore lookups for one ``(set, event_type)``.

    Same shape :func:`build_feature_row` consumes; we compute it
    once per format rather than once per row. Both dicts are keyed by
    folded card name (``mulligan_coach_features.fold_card_name``).
    """

    shrunk: dict[str, ShrunkWinRates]
    zscores: dict[str, CardZScores]


def _build_format_stats(set_code: str, data_root: Path | None = None) -> _FormatStats:
    """Run the shrinkage + z-score chain end-to-end for one set.

    Mirrors ``packages/features/scripts/smoke_feature_builder.py``
    so behaviour stays in lockstep with the smoke surface that's
    used to eyeball the feature builder during development.
    """
    stats_lookup = load_premier_draft_stats(set_code, data_root=data_root)
    all_stats = list(stats_lookup.by_name.values())
    priors = compute_format_priors(all_stats)
    shrunk = shrink_stats(all_stats, priors=priors)
    distribution = compute_format_wr_distribution(shrunk.values())
    zscores = zscore_stats(shrunk.values(), distribution=distribution)
    return _FormatStats(shrunk=shrunk, zscores=zscores)


# ---------------------------------------------------------------------------
# Feature row + context row assembly
# ---------------------------------------------------------------------------


def build_row(
    tr: TrainingRow,
    *,
    format_stats: _FormatStats,
    n_sims_per_row: int,
) -> dict[str, Any]:
    """Run the simulator + feature builder + context glue for one row.

    Pure-ish — the only side effect is simulator-internal RNG
    consumption seeded from :func:`_row_seed`. The output dict
    carries every column the parquet shard needs (features + label
    + context).

    Raises whatever :func:`simulate` or :func:`build_feature_row`
    raise — callers in :func:`materialize_feature_matrix` catch
    these and update :class:`MaterializationStats` counters
    instead of aborting the whole shard.
    """
    library = _library_from_deck(tr.hand, tr.deck)
    seed = _row_seed(tr.draft_id, tr.match_number, tr.game_number)
    aggregate = simulate(
        list(tr.hand),
        library,
        on_the_play=tr.on_the_play,
        n_runs=n_sims_per_row,
        seed=seed,
    )

    row = build_feature_row(
        hand=list(tr.hand),
        deck=list(tr.deck),
        aggregate_stats=aggregate,
        shrunk=format_stats.shrunk,
        zscores=format_stats.zscores,
        on_the_play=tr.on_the_play,
        mulligan_number=tr.mulligan_number,
        event_type=tr.event_type,
        set_code=tr.expansion,
    )

    # opp_mulligan: feature only on the draw (XGBoost handles None
    # natively via the missing-value path). Always include the raw
    # value as a context column for the baseline.
    row_out: dict[str, Any] = dict(row)
    row_out["opp_mulligan_count_if_known"] = (
        float(tr.opp_mulligan_number) if not tr.on_the_play else None
    )
    row_out["user_wr_bucket"] = tr.user_wr_bucket
    row_out["user_n_games_bucket"] = tr.user_n_games_bucket
    row_out["opp_mulligan_number"] = int(tr.opp_mulligan_number)
    row_out["mulligan_number"] = int(tr.mulligan_number)
    row_out["expansion"] = tr.expansion
    row_out["event_type"] = tr.event_type
    row_out["draft_id"] = tr.draft_id
    row_out["match_number"] = int(tr.match_number)
    row_out["game_number"] = int(tr.game_number)
    row_out["won"] = bool(tr.won)
    return row_out


# ---------------------------------------------------------------------------
# Multiprocessing worker primitives
# ---------------------------------------------------------------------------
#
# Two-process layout: the main process drives DuckDB + parquet writing; a
# Pool of worker processes runs the simulator + feature builder for each
# TrainingRow. ``format_stats`` and ``n_sims_per_row`` are set once per
# worker via the Pool initializer; rows ship as pickled TrainingRow
# instances (a few KB each — fast enough to amortise across a chunksize
# of dozens of rows per task).
#
# Workers never raise out — they classify any exception into a
# ``_WorkerResult(success=False, ...)`` so the main loop can bump the
# right counter on :class:`MaterializationStats` without aborting the
# whole shard. (A worker-process crash, e.g. segfault, still bubbles up;
# we want those visible.)

_WORKER_FORMAT_STATS: _FormatStats | None = None
"""Per-worker format-stats cache. Set by :func:`_worker_init`; lives as a
module-level global so subsequent tasks reuse it without re-pickling."""

_WORKER_N_SIMS: int = 200
"""Per-worker Monte Carlo replicate count. Set by :func:`_worker_init`."""


@dataclass(frozen=True)
class _WorkerResult:
    """Result of one worker task: either a successful feature row or a
    classified error.

    The dataclass is picklable; ``multiprocessing.Pool`` serialises it
    back to the main process across the imap_unordered channel. We
    carry the row's identity tuple (``draft_id, match_number,
    game_number``) on the failure path so the main process can log
    which row blew up.
    """

    success: bool
    row: dict[str, Any] | None
    error_kind: str | None  # "simulation" or "feature_build" when success=False
    error_repr: str | None
    draft_id: str
    match_number: int
    game_number: int


def _worker_init(format_stats: _FormatStats, n_sims_per_row: int) -> None:
    """Pool initializer: stash the per-worker constants once.

    Avoids pickling ``format_stats`` (~hundreds of KB of WRs + zscores)
    on every task. The main process passes them via ``initargs``;
    every subsequent task reuses the same in-process copy.
    """
    global _WORKER_FORMAT_STATS, _WORKER_N_SIMS
    _WORKER_FORMAT_STATS = format_stats
    _WORKER_N_SIMS = n_sims_per_row


def _worker_build(tr: TrainingRow) -> _WorkerResult:
    """Run :func:`build_row` for one training row inside a worker process.

    Never raises out — classifies exceptions into a ``_WorkerResult``
    so the main loop's accounting matches the single-threaded path.
    The classification logic mirrors :func:`iter_feature_rows`:
    :class:`DeckEncodingError` -> simulation; everything else ->
    feature_build.
    """
    if _WORKER_FORMAT_STATS is None:  # pragma: no cover — set by initializer
        raise RuntimeError("_worker_init was not called; worker state missing.")
    try:
        row = build_row(
            tr,
            format_stats=_WORKER_FORMAT_STATS,
            n_sims_per_row=_WORKER_N_SIMS,
        )
        return _WorkerResult(
            success=True,
            row=row,
            error_kind=None,
            error_repr=None,
            draft_id=tr.draft_id,
            match_number=tr.match_number,
            game_number=tr.game_number,
        )
    except Exception as exc:
        from mulligan_coach_simulation import DeckEncodingError

        kind = "simulation" if isinstance(exc, DeckEncodingError) else "feature_build"
        return _WorkerResult(
            success=False,
            row=None,
            error_kind=kind,
            error_repr=repr(exc),
            draft_id=tr.draft_id,
            match_number=tr.match_number,
            game_number=tr.game_number,
        )


def _iter_feature_rows_parallel(
    training_rows: Iterable[TrainingRow],
    *,
    format_stats: _FormatStats,
    n_sims_per_row: int,
    n_workers: int,
    chunksize: int,
    stats: MaterializationStats,
) -> Iterator[dict[str, Any]]:
    """Multi-process counterpart to :func:`iter_feature_rows`.

    Order is **not preserved** — uses ``imap_unordered`` so a slow
    row doesn't stall the whole shard. The model is order-invariant
    and per-row determinism is preserved by :func:`_row_seed`, so this
    is fine for training. Tests that compare parallel vs serial output
    sort by ``(draft_id, game_number)`` first.
    """
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(
        processes=n_workers,
        initializer=_worker_init,
        initargs=(format_stats, n_sims_per_row),
    ) as pool:
        for result in pool.imap_unordered(
            _worker_build,
            training_rows,
            chunksize=chunksize,
        ):
            if result.success:
                assert result.row is not None  # narrowed by success
                yield result.row
            else:
                if result.error_kind == "simulation":
                    stats.rows_failed_simulation += 1
                else:
                    stats.rows_failed_feature_build += 1
                log.warning(
                    "feature row build failed for draft_id=%s match=%s game_number=%s: %s",
                    result.draft_id,
                    result.match_number,
                    result.game_number,
                    result.error_repr,
                )


def iter_feature_rows(
    training_rows: Iterable[TrainingRow],
    *,
    format_stats: _FormatStats,
    n_sims_per_row: int = 200,
    on_error: str = "skip",
    stats: MaterializationStats | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream feature rows from a training-row iterable.

    ``on_error="skip"`` swallows per-row simulator / feature-builder
    exceptions and increments the matching counter on ``stats``.
    ``"raise"`` re-raises after logging — useful for tests.

    The downside of ``skip`` is that systematic errors (e.g. an
    unencoded card in the deck) silently truncate the shard rather
    than failing loudly; rely on the counter values to detect that
    after the run.
    """
    if on_error not in ("skip", "raise"):
        raise ValueError(f"on_error must be 'skip' or 'raise', got {on_error!r}")
    if stats is None:
        stats = MaterializationStats()

    for tr in training_rows:
        try:
            yield build_row(tr, format_stats=format_stats, n_sims_per_row=n_sims_per_row)
        except Exception as exc:
            # Simulation errors (e.g. DeckEncodingError from
            # check_deck_encodings) are distinguishable from feature
            # builder errors by their origin in the simulation
            # package — but they bubble up as plain exceptions.
            # We classify heuristically by the simulator's known
            # error type and fall back to "feature build" for the rest.
            from mulligan_coach_simulation import DeckEncodingError

            if isinstance(exc, DeckEncodingError):
                stats.rows_failed_simulation += 1
            else:
                stats.rows_failed_feature_build += 1
            log.warning(
                "feature row build failed for draft_id=%s game_number=%s: %r",
                tr.draft_id,
                tr.game_number,
                exc,
            )
            if on_error == "raise":
                raise


# ---------------------------------------------------------------------------
# Chunked parquet output + resume helpers
# ---------------------------------------------------------------------------
#
# Output is a directory of ``chunk_NNNNNNNN.parquet`` files written
# atomically (one tmp neighbour per chunk + rename). On restart, the
# materialiser scans the directory, reads the ``(draft_id,
# game_number)`` columns from every completed chunk to build a
# skip-set, and filters the input stream so workers don't redo
# already-done rows. Chunks left as ``.chunk_*.tmp-<pid>`` from a
# previous crashed run are deleted on startup.


_CHUNK_FILE_PATTERN = "chunk_*.parquet"
_CHUNK_TMP_PATTERN = ".chunk_*.parquet.tmp-*"


def feature_parquet_paths(output_dir: Path) -> list[Path]:
    """Return the sorted list of completed chunk parquets in ``output_dir``.

    Downstream callers (``BaselineModel.fit``, ``train_model``) accept
    ``Sequence[Path]``; this helper exists so they don't have to know
    the ``chunk_*.parquet`` naming convention. Returns an empty list
    if the directory doesn't exist or is empty.
    """
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob(_CHUNK_FILE_PATTERN))


def _chunk_path(output_dir: Path, chunk_idx: int) -> Path:
    """Canonical path for a chunk's final parquet file."""
    return output_dir / f"chunk_{chunk_idx:08d}.parquet"


def _chunk_tmp_path(output_dir: Path, chunk_idx: int) -> Path:
    """Path for a chunk's tmp file during write (per-pid for safety)."""
    return output_dir / f".chunk_{chunk_idx:08d}.parquet.tmp-{os.getpid()}"


def _write_chunk(rows: list[dict[str, Any]], output_dir: Path, chunk_idx: int) -> Path:
    """Write one chunk parquet atomically.

    Writes to a per-pid tmp neighbour, then ``os.replace`` to the
    canonical name. Partial / crashed chunks always sit at the tmp
    path, never at the canonical path — so the resume scan only ever
    sees fully-written chunks.
    """
    final = _chunk_path(output_dir, chunk_idx)
    tmp = _chunk_tmp_path(output_dir, chunk_idx)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, tmp, compression="zstd")  # type: ignore[no-untyped-call]
    tmp.replace(final)
    return final


RowKey = tuple[str, int, int]
"""(draft_id, match_number, game_number) — the unique identity of a
17Lands game row. Used for resume bookkeeping and the per-row seed."""


def _load_completed_keys(output_dir: Path) -> tuple[set[RowKey], int]:
    """Scan completed chunks and return ``(skip_set, next_chunk_idx)``.

    The skip_set holds every ``(draft_id, match_number, game_number)``
    triple that's already been materialised — the full unique row
    identity (``(draft_id, game_number)`` alone is NOT unique in
    17Lands data because one draft has many matches).
    ``next_chunk_idx`` is one past the highest existing chunk index,
    so newly-written chunks don't collide with the old ones.

    Reads only the three key columns from each chunk — fast even for
    a fully-completed format (~1M tuples = a few MB of arrow data).
    """
    completed: set[RowKey] = set()
    max_idx = -1
    for chunk in sorted(output_dir.glob(_CHUNK_FILE_PATTERN)):
        table = pq.read_table(  # type: ignore[no-untyped-call]
            chunk, columns=["draft_id", "match_number", "game_number"]
        )
        df = table.to_pandas()
        completed.update(
            (str(d), int(m), int(g))
            for d, m, g in zip(df["draft_id"], df["match_number"], df["game_number"], strict=True)
        )
        idx = int(chunk.stem.removeprefix("chunk_"))
        max_idx = max(max_idx, idx)
    return completed, max_idx + 1


def _sweep_stale_tmp_files(output_dir: Path) -> None:
    """Delete any leftover ``.chunk_*.tmp-*`` files from prior runs.

    These are mid-write parquet files orphaned by a crashed process.
    They're invalid (no footer) and would otherwise accumulate
    across re-runs. We never honour them on the resume path.
    """
    import contextlib

    for p in output_dir.glob(_CHUNK_TMP_PATTERN):
        with contextlib.suppress(OSError):  # file gone between glob + unlink
            p.unlink()


def _filter_completed(
    rows: Iterable[TrainingRow],
    completed: set[RowKey],
    stats: MaterializationStats,
) -> Iterator[TrainingRow]:
    """Yield only rows whose ``(draft_id, match_number, game_number)``
    isn't in completed.

    Increments ``stats.rows_skipped_resume`` for each filtered row so
    the resume audit log carries the count.
    """
    if not completed:
        yield from rows
        return
    for tr in rows:
        if (tr.draft_id, tr.match_number, tr.game_number) in completed:
            stats.rows_skipped_resume += 1
            continue
        yield tr


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------


def materialize_feature_matrix(
    *,
    set_code: str,
    duckdb_path: Path,
    output_dir: Path,
    event_type: str = "PremierDraft",
    n_sims_per_row: int = 200,
    n_workers: int = 1,
    chunksize: int = 32,
    chunk_rows: int = 5000,
    resume: bool = True,
    limit: int | None = None,
    overwrite: bool = False,
    data_root: Path | None = None,
    log_every: int = 1000,
) -> MaterializationStats:
    """Materialise the feature parquet shard for one (set, event_type).

    Output is a **directory** of chunk parquet files
    (``chunk_NNNNNNNN.parquet``), not a single file. Each chunk is
    written atomically (tmp neighbour + ``os.replace``), so a
    crashed run leaves only completed chunks on disk — the next
    invocation reads those to build a skip-set of completed
    ``(draft_id, game_number)`` pairs and resumes on only the
    remaining rows.

    Pipeline:

    1. Open the DuckDB games view in read-only mode.
    2. Build the per-format shrunk / zscore lookups once.
    3. Resume bookkeeping: scan ``output_dir`` for existing
       ``chunk_*.parquet`` files; build the skip-set; sweep any
       orphaned ``.chunk_*.tmp-*`` files from prior crashes.
    4. Stream :class:`TrainingRow` instances via
       :func:`iter_training_rows`, filtered through
       :func:`_filter_completed`.
    5. For each row: run the Monte Carlo simulator, then the
       200-column feature builder, then emit the feature dict +
       label + context columns.
    6. Every ``chunk_rows`` rows, write one chunk parquet
       atomically and increment ``chunks_written``.

    Parameters
    ----------
    set_code:
        Three-letter set code (e.g. ``"TLA"``). Used both to filter
        the games view and to load the per-format ratings parquet.
    duckdb_path:
        Path to ``data/processed/games.duckdb``.
    output_dir:
        Destination directory. Created if missing. Contains
        ``chunk_NNNNNNNN.parquet`` files after a successful run.
    event_type:
        v1 scope is ``"PremierDraft"`` per ``packages/CLAUDE.md``;
        the parameter exists so a later sealed run can reuse the
        same code path.
    n_sims_per_row:
        How many goldfish games to run per training row. 200 is a
        balance between simulator wall-clock and aggregate variance;
        higher values reduce per-row noise but at linear runtime
        cost. Tune after looking at a first training pass.
    n_workers:
        When ``> 1``, fan per-row work out across a
        :class:`multiprocessing.Pool` of this many worker processes.
        Default ``1`` keeps the existing single-threaded path. The
        per-row simulator is mostly Python-level so multiprocessing
        gives roughly an N-x speedup with N cores. With
        ``n_workers > 1`` row order is **not preserved** —
        :func:`pool.imap_unordered` yields rows as workers complete
        them. The model and the baseline are order-invariant so this
        doesn't affect training.
    chunksize:
        ``multiprocessing.Pool.imap_unordered`` chunksize. 32 is a
        reasonable default for ~700ms-per-row tasks: small enough
        that a slow row doesn't stall, large enough to amortise IPC.
        Ignored when ``n_workers == 1``.
    chunk_rows:
        Number of rows per output chunk parquet file. 5000 is small
        enough that a crash never loses more than ~30 minutes of
        work at the production simulator speed (~3-9 rows/sec
        single-threaded), and large enough that the chunk count
        stays manageable (~200 chunks for a 1M-row format).
    resume:
        When ``True`` (default), existing ``chunk_*.parquet`` files
        in ``output_dir`` are honoured and their rows are skipped.
        When ``False`` and chunks exist, ``materialize_feature_matrix``
        raises :class:`FileExistsError` (unless ``overwrite=True``).
    limit:
        Optional cap on rows pulled from the SQL view (passes
        through to :func:`iter_training_rows`). Useful for smoke
        tests.
    overwrite:
        When ``True``, deletes every existing ``chunk_*.parquet``
        in ``output_dir`` before starting. Overrides ``resume``.
        When ``False`` (default) + ``resume=False`` + chunks exist,
        raises :class:`FileExistsError`.
    data_root:
        Forwarded to the cards / features helpers for tests; ignored
        in production where the default repo-root resolution
        applies.
    log_every:
        Emit an INFO progress log every N rows.

    Returns
    -------
    MaterializationStats
        Counters of rows written / failed / skipped (the embedded
        :class:`TrainingRowStats` tracks the SQL-side filters).
    """
    if n_workers < 1:
        raise ValueError(f"n_workers must be >= 1; got {n_workers}")
    if chunk_rows < 1:
        raise ValueError(f"chunk_rows must be >= 1; got {chunk_rows}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the resume / overwrite / fresh-start three-way.
    existing_chunks = list(output_dir.glob(_CHUNK_FILE_PATTERN))
    completed: set[RowKey]
    next_chunk_idx: int
    if existing_chunks:
        if overwrite:
            log.info(
                "materialize_feature_matrix(%s): overwrite=True, deleting %d existing chunk(s)",
                set_code,
                len(existing_chunks),
            )
            for p in existing_chunks:
                p.unlink()
            completed, next_chunk_idx = set(), 0
        elif resume:
            completed, next_chunk_idx = _load_completed_keys(output_dir)
            log.info(
                "materialize_feature_matrix(%s): resuming from %d existing chunk(s); "
                "skipping %d already-completed rows",
                set_code,
                len(existing_chunks),
                len(completed),
            )
        else:
            raise FileExistsError(
                f"{output_dir} contains existing chunks; pass overwrite=True "
                f"to clear them or resume=True (default) to skip completed rows."
            )
    else:
        completed, next_chunk_idx = set(), 0

    # Always sweep orphan tmp files (left by a prior crashed write).
    _sweep_stale_tmp_files(output_dir)

    # Stamp / check the pipeline-version sidecar before any expensive work.
    # Fresh dir or overwrite -> write _meta.json; resume -> verify the
    # existing chunks were built by the same simulator + feature code
    # (raises ShardVersionError on drift) or grace-stamp a legacy shard.
    stamp_or_check_shard_meta(
        output_dir,
        current=ShardMeta(
            pipeline_versions=pipeline_versions(),
            set_code=set_code,
            event_type=event_type,
            n_sims_per_row=n_sims_per_row,
            created_at=now_iso(),
        ),
        had_existing_chunks=bool(existing_chunks),
        overwrite=overwrite,
    )

    format_stats = _build_format_stats(set_code, data_root=data_root)
    log.info(
        "Loaded format stats for %s: %d shrunk WRs, %d z-score rows",
        set_code,
        len(format_stats.shrunk),
        len(format_stats.zscores),
    )

    materialization_stats = MaterializationStats()

    import duckdb  # local — see note at top of module

    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        raw_row_iter = iter_training_rows(
            connection=con,
            set_code=set_code,
            event_type=event_type,
            limit=limit,
            data_root=data_root,
            stats=materialization_stats.training_row_stats,
        )
        # Apply the resume skip-set before workers see the row, so
        # we don't pay simulator time on rows we're going to drop.
        row_iter = _filter_completed(raw_row_iter, completed, materialization_stats)

        feature_iter: Iterator[dict[str, Any]]
        if n_workers > 1:
            # Materialise the (filtered) training rows up front so
            # the DuckDB cursor isn't held open during the Pool's
            # lifetime. The skip-set was applied above, so the list
            # holds only rows we'll actually process.
            row_list = list(row_iter)
            log.info(
                "materialize_feature_matrix(%s): fanning %d rows across %d workers",
                set_code,
                len(row_list),
                n_workers,
            )
            feature_iter = _iter_feature_rows_parallel(
                row_list,
                format_stats=format_stats,
                n_sims_per_row=n_sims_per_row,
                n_workers=n_workers,
                chunksize=chunksize,
                stats=materialization_stats,
            )
        else:
            feature_iter = iter_feature_rows(
                row_iter,
                format_stats=format_stats,
                n_sims_per_row=n_sims_per_row,
                stats=materialization_stats,
            )

        # Buffer rows into chunks; write each chunk atomically.
        batch: list[dict[str, Any]] = []
        chunk_idx = next_chunk_idx
        for row in feature_iter:
            batch.append(row)
            if len(batch) >= chunk_rows:
                _write_chunk(batch, output_dir, chunk_idx)
                materialization_stats.rows_written += len(batch)
                materialization_stats.chunks_written += 1
                log.info(
                    "materialize_feature_matrix(%s): wrote chunk %d "
                    "(cumulative rows in this run=%d)",
                    set_code,
                    chunk_idx,
                    materialization_stats.rows_written,
                )
                chunk_idx += 1
                batch.clear()

        # Flush the trailing partial chunk.
        if batch:
            _write_chunk(batch, output_dir, chunk_idx)
            materialization_stats.rows_written += len(batch)
            materialization_stats.chunks_written += 1
    finally:
        con.close()

    if materialization_stats.rows_written == 0 and not completed:
        log.warning(
            "materialize_feature_matrix(%s): no rows emitted (training "
            "row stats: emitted=%d, skipped_bad_hand=%d, skipped_bad_deck=%d, "
            "skipped_unknown_card=%d). Output directory not written.",
            set_code,
            materialization_stats.training_row_stats.emitted,
            materialization_stats.training_row_stats.skipped_bad_hand_size,
            materialization_stats.training_row_stats.skipped_bad_deck_size,
            materialization_stats.training_row_stats.skipped_unknown_card,
        )
        return materialization_stats

    log.info(
        "materialize_feature_matrix(%s): wrote %d new rows in %d chunk(s) to %s "
        "(skipped_resume=%d, failed_sim=%d, failed_build=%d)",
        set_code,
        materialization_stats.rows_written,
        materialization_stats.chunks_written,
        output_dir,
        materialization_stats.rows_skipped_resume,
        materialization_stats.rows_failed_simulation,
        materialization_stats.rows_failed_feature_build,
    )
    return materialization_stats
