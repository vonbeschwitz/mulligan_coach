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
* Context columns needed by the residualization baseline (PR 3)
  and the grouped train/val split (PR 4): ``user_wr_bucket``,
  ``user_n_games_bucket``, ``opp_mulligan_number`` (the raw value,
  always populated — distinct from the conditional feature),
  ``mulligan_number``, ``expansion``, ``event_type``, ``set_code``,
  ``draft_id``, ``game_number``.
* Label: ``won``.

The on_the_play context feature is intentionally **not** duplicated
into a separate context column — it's already a feature.

Storage layout
--------------

``data/processed/model_training/<EXPANSION>/<EVENT_TYPE>.parquet``,
one shard per ``(set, event_type)``. The whole directory is
gitignored (per ``.gitignore`` rules for ``data/processed/*``);
trained-model artifacts go in ``models/`` instead.

Resumability is per-shard: if the output file already exists, the
function refuses unless ``overwrite=True``. Mid-shard interruptions
are tolerated via the staged-write-then-rename pattern — we write
to a ``.tmp`` neighbour and ``os.replace`` into place atomically
when done, so partial files never appear at the canonical path.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
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


def _row_seed(draft_id: str, game_number: int) -> int:
    """Deterministic per-row simulation seed.

    Stable across runs so the materialisation step is
    reproducible bit-for-bit. We hash ``(draft_id, game_number)``
    into a 32-bit positive int (Python's built-in :func:`hash` is
    randomised per process; use a stable digest instead).
    """
    import hashlib

    raw = f"{draft_id}\x00{game_number}".encode()
    digest = hashlib.sha256(raw).digest()
    # First 4 bytes -> unsigned 32-bit int. Numpy's PCG accepts
    # arbitrary uint64; this fits within the simulator's seed range.
    return int.from_bytes(digest[:4], "big")


@dataclass(frozen=True)
class _FormatStats:
    """Pre-computed shrunk / zscore lookups for one ``(set, event_type)``.

    Same shape :func:`build_feature_row` consumes; we compute it
    once per format rather than once per row.
    """

    shrunk: dict[int, ShrunkWinRates]
    zscores: dict[int, CardZScores]


def _build_format_stats(set_code: str, data_root: Path | None = None) -> _FormatStats:
    """Run the shrinkage + z-score chain end-to-end for one set.

    Mirrors ``packages/features/scripts/smoke_feature_builder.py``
    so behaviour stays in lockstep with the smoke surface that's
    used to eyeball the feature builder during development.
    """
    stats_lookup = load_premier_draft_stats(set_code, data_root=data_root)
    all_stats = list(stats_lookup.by_arena_id.values())
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
    seed = _row_seed(tr.draft_id, tr.game_number)
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
    carry ``draft_id`` / ``game_number`` on the failure path so the
    main process can log which row blew up.
    """

    success: bool
    row: dict[str, Any] | None
    error_kind: str | None  # "simulation" or "feature_build" when success=False
    error_repr: str | None
    draft_id: str
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
                    "feature row build failed for draft_id=%s game_number=%s: %s",
                    result.draft_id,
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
# Parquet writer
# ---------------------------------------------------------------------------


def materialize_feature_matrix(
    *,
    set_code: str,
    duckdb_path: Path,
    output_path: Path,
    event_type: str = "PremierDraft",
    n_sims_per_row: int = 200,
    n_workers: int = 1,
    chunksize: int = 32,
    limit: int | None = None,
    overwrite: bool = False,
    data_root: Path | None = None,
    batch_size: int = 1000,
    log_every: int = 1000,
) -> MaterializationStats:
    """Materialise the feature parquet shard for one (set, event_type).

    Pipeline (single-process):

    1. Open the DuckDB games view in read-only mode.
    2. Build the per-format shrunk / zscore lookups once.
    3. Stream :class:`TrainingRow` instances via
       :func:`iter_training_rows`.
    4. For each row: run the Monte Carlo simulator, then the
       200-column feature builder, then emit the feature dict +
       label + context columns.
    5. Buffer ``batch_size`` rows, convert to a pyarrow Table,
       append to a streaming :class:`pyarrow.parquet.ParquetWriter`.
    6. ``os.replace`` the temp parquet to the canonical path
       once the shard finishes — partial files never appear at
       ``output_path``.

    Parameters
    ----------
    set_code:
        Three-letter set code (e.g. ``"TLA"``). Used both to filter
        the games view and to load the per-format ratings parquet.
    duckdb_path:
        Path to ``data/processed/games.duckdb``.
    output_path:
        Destination parquet path. Refuses to overwrite an existing
        file unless ``overwrite=True``.
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
        ``n_workers > 1`` the parquet row order is **not stable** —
        :func:`pool.imap_unordered` yields rows as workers complete
        them. The model and the baseline are order-invariant so this
        doesn't affect training; only relevant if a downstream
        consumer depends on the row order in the parquet shard.
    chunksize:
        ``multiprocessing.Pool.imap_unordered`` chunksize. 32 is a
        reasonable default for ~700ms-per-row tasks: small enough
        that a slow row doesn't stall, large enough to amortise IPC.
        Ignored when ``n_workers == 1``.
    limit:
        Optional cap on rows pulled from the SQL view (passes
        through to :func:`iter_training_rows`). Useful for smoke
        tests.
    overwrite:
        When ``False`` (default) and ``output_path`` exists, raise
        :class:`FileExistsError`. When ``True``, the existing file
        is replaced atomically once the new shard is fully written.
    data_root:
        Forwarded to the cards / features helpers for tests; ignored
        in production where the default repo-root resolution
        applies.
    batch_size:
        Rows per parquet row-group. 1000 is small enough to keep
        per-batch arrow conversion cheap and large enough to keep
        the row-group count manageable.
    log_every:
        Emit an INFO progress log every N rows.

    Returns
    -------
    MaterializationStats
        Counters of rows written / failed / skipped (the embedded
        :class:`TrainingRowStats` tracks the SQL-side filters).
    """
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"{output_path} already exists; pass overwrite=True to replace.")
    if n_workers < 1:
        raise ValueError(f"n_workers must be >= 1; got {n_workers}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    if tmp_path.exists():
        tmp_path.unlink()

    format_stats = _build_format_stats(set_code, data_root=data_root)
    log.info(
        "Loaded format stats for %s: %d shrunk WRs, %d z-score rows",
        set_code,
        len(format_stats.shrunk),
        len(format_stats.zscores),
    )

    materialization_stats = MaterializationStats()

    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        row_iter = iter_training_rows(
            connection=con,
            set_code=set_code,
            event_type=event_type,
            limit=limit,
            data_root=data_root,
            stats=materialization_stats.training_row_stats,
        )
        feature_iter: Iterator[dict[str, Any]]
        if n_workers > 1:
            # Materialise the training rows up front so the DuckDB
            # cursor isn't held open during the Pool's lifetime. ~1M
            # TrainingRow instances at ~a few KB each is ~hundreds of
            # MB — acceptable for the duration of a single shard
            # materialisation, and far smaller than the parquet output
            # we're about to write.
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

        writer: pq.ParquetWriter | None = None
        batch: list[dict[str, Any]] = []
        for row in feature_iter:
            batch.append(row)
            if len(batch) >= batch_size:
                table = pa.Table.from_pylist(batch)
                if writer is None:
                    writer = pq.ParquetWriter(  # type: ignore[no-untyped-call]
                        tmp_path, table.schema, compression="zstd"
                    )
                writer.write_table(table)  # type: ignore[no-untyped-call]
                materialization_stats.rows_written += len(batch)
                if log_every and materialization_stats.rows_written % log_every == 0:
                    log.info(
                        "materialize_feature_matrix(%s): %d rows written",
                        set_code,
                        materialization_stats.rows_written,
                    )
                batch.clear()

        # Flush trailing partial batch.
        if batch:
            table = pa.Table.from_pylist(batch)
            if writer is None:
                writer = pq.ParquetWriter(  # type: ignore[no-untyped-call]
                    tmp_path, table.schema, compression="zstd"
                )
            writer.write_table(table)  # type: ignore[no-untyped-call]
            materialization_stats.rows_written += len(batch)

        if writer is not None:
            writer.close()  # type: ignore[no-untyped-call]
    finally:
        con.close()

    if materialization_stats.rows_written == 0:
        # Don't leave a zero-row parquet sitting at the canonical
        # path; clean up the tmp file too.
        if tmp_path.exists():
            tmp_path.unlink()
        log.warning(
            "materialize_feature_matrix(%s): no rows emitted (training "
            "row stats: emitted=%d, skipped_bad_hand=%d, skipped_bad_deck=%d, "
            "skipped_unknown_card=%d). Output path not written.",
            set_code,
            materialization_stats.training_row_stats.emitted,
            materialization_stats.training_row_stats.skipped_bad_hand_size,
            materialization_stats.training_row_stats.skipped_bad_deck_size,
            materialization_stats.training_row_stats.skipped_unknown_card,
        )
        return materialization_stats

    # Atomic move — partial files never appear at output_path.
    tmp_path.replace(output_path)
    log.info(
        "materialize_feature_matrix(%s): wrote %d rows to %s (failed_sim=%d, failed_build=%d)",
        set_code,
        materialization_stats.rows_written,
        output_path,
        materialization_stats.rows_failed_simulation,
        materialization_stats.rows_failed_feature_build,
    )
    return materialization_stats
