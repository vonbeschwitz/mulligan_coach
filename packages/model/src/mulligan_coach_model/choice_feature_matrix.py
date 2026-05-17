"""Materialise :class:`ChoiceRow` instances into a parquet feature cache.

Sister of :mod:`feature_matrix`, with two important shape differences:

* **Label is ``was_kept``** instead of ``won`` — we're modelling the
  player's keep/mull decision, not the eventual game outcome.
* **Kept-hand simulation reuse.** Every ``was_kept=True`` decision
  row's underlying ``(hand, deck, mulligan_number)`` is *also* the
  hand the player actually played, which the win-model's
  :func:`feature_matrix.materialize_feature_matrix` step has already
  simulated and turned into a 200-column feature row. Re-running
  the simulator on those hands is wasted compute; we look them up
  in the existing parquet cache instead. Mulled-away hands
  (``was_kept=False``) and cache misses fall back to the full
  ``simulate -> build_feature_row`` pipeline.

In TLA's dataset this is a ~10x sim-cost reduction: ~63k mulled hands
to simulate vs. ~626k total decision rows. The cached features
themselves are bit-identical to a fresh run because the underlying
hand+deck+mulligan_number are identical and the simulator seed is
deterministic in ``(draft_id, match_number, game_number)``.

Output schema (per row)
-----------------------

Same 200 features from :func:`build_feature_row`, plus:

* ``opp_mulligan_count_if_known`` — conditional feature (NULL on the
  play; opponent's mulligan count on the draw). Identical semantics
  to the win-model cache.
* ``opp_mulligan_number`` — raw value, always populated.
* ``mulligan_number`` — context feature for the choice (lower
  mulligan numbers carry more "I should keep" pressure).
* ``num_mulligans_in_game`` — total mulligans the player ended up
  doing. Useful for audit (verifies the kept-hand row matches its
  cached counterpart) but NOT a feature — would be label-leaking at
  inference time (it's only known after the decision).
* ``expansion``, ``event_type``, ``draft_id``, ``build_index``,
  ``match_number``, ``game_number`` — identity / split context.
* ``user_n_games_raw``, ``user_wr_raw`` — raw 17Lands per-player
  buckets for filtering audit (e.g. did the skill filter actually
  bite). Not features.
* ``was_kept`` — the label.

The on_the_play context feature is already a feature column, so it's
not duplicated.

Storage layout
--------------

``data/processed/choice_training/<EXPANSION>/<EVENT_TYPE>/`` — a
directory per ``(set, event_type)`` containing
``chunk_NNNNNNNN.parquet`` files. Atomic writes (``.chunk_*.tmp-<pid>``
+ rename) and chunk-level resume work identically to the win-model
materialiser, with one extra identity component:
``(draft_id, build_index, match_number, game_number, mulligan_number)``
is what uniquely identifies one decision (a single game has 1..7
decision rows, one per candidate hand).
"""

from __future__ import annotations

import hashlib
import logging
import multiprocessing
import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from mulligan_coach_features import build_feature_row
from mulligan_coach_simulation import simulate

from .choice_rows import (
    DEFAULT_MIN_N_GAMES_TO_JUDGE,
    DEFAULT_MIN_WIN_RATE,
    ChoiceRow,
    ChoiceRowStats,
    iter_choice_rows,
)
from .feature_matrix import (
    _build_format_stats,
    _FormatStats,
    _library_from_deck,
    feature_parquet_paths,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class ChoiceMaterializationStats:
    """Counters returned by :func:`materialize_choice_feature_matrix`."""

    rows_written: int = 0
    rows_via_cache: int = 0
    """``was_kept=True`` rows served from the win-model feature cache."""

    rows_via_simulation: int = 0
    """Rows whose features were freshly computed via ``simulate +
    build_feature_row``. Includes all mulled-away hands plus any
    kept-hand cache misses."""

    rows_failed_simulation: int = 0
    rows_failed_feature_build: int = 0
    chunks_written: int = 0
    rows_skipped_resume: int = 0
    choice_row_stats: ChoiceRowStats = field(default_factory=ChoiceRowStats)


# Non-feature columns we want carried in the cached output for
# splitting / auditing. Mirrors ``feature_matrix._NON_FEATURE_COLUMNS``
# with the choice-model-specific additions.
_OUTPUT_CONTEXT_COLUMNS: tuple[str, ...] = (
    "opp_mulligan_count_if_known",
    "opp_mulligan_number",
    "mulligan_number",
    "num_mulligans_in_game",
    "expansion",
    "event_type",
    "draft_id",
    "build_index",
    "match_number",
    "game_number",
    "user_n_games_raw",
    "user_wr_raw",
    "was_kept",
)


# ---------------------------------------------------------------------------
# Kept-hand cache: in-memory lookup over the win-model feature parquets
# ---------------------------------------------------------------------------


# Columns we DROP when loading the win-model cache — they're either
# the win-model's label or context we re-derive from the ChoiceRow.
_CACHE_COLUMNS_TO_DROP: frozenset[str] = frozenset(
    {
        "won",
        # User buckets in the win-model cache were derived for the
        # baseline residualisation; the choice model uses the raw
        # per-row values from the decisions parquet instead.
        "user_wr_bucket",
        "user_n_games_bucket",
    }
)


@dataclass
class KeptHandCache:
    """In-memory lookup of pre-computed kept-hand feature rows.

    The win-model cache stores one row per played game with the kept
    hand's 200 features + context. Indexed here on
    ``(draft_id, match_number, game_number)`` so the choice
    materialiser can serve ``was_kept=True`` rows without re-running
    the simulator. The cache holds the data as a pandas DataFrame —
    ~1 GB per format at the current data scale, which fits
    comfortably in main-process memory and gives O(1) lookups via
    a sorted MultiIndex.
    """

    frame: pd.DataFrame
    """DataFrame with the MultiIndex ``(draft_id, match_number,
    game_number)`` and one column per feature + cache-side context."""

    def lookup(
        self,
        draft_id: str,
        match_number: int,
        game_number: int,
    ) -> dict[str, Any] | None:
        """Return the cached feature dict, or ``None`` on miss.

        The returned dict is a fresh copy — callers can mutate it
        (e.g. swap label, override context) without affecting the
        cache. Cost: one pandas .loc + one to_dict — typically well
        under 100 µs.
        """
        key = (draft_id, match_number, game_number)
        try:
            row = self.frame.loc[key]
        except KeyError:
            return None
        return row.to_dict()  # type: ignore[no-any-return]

    def __len__(self) -> int:
        return len(self.frame)


def load_kept_hand_cache(cached_features_dir: Path) -> KeptHandCache:
    """Load every ``chunk_*.parquet`` in ``cached_features_dir`` into RAM.

    Parameters
    ----------
    cached_features_dir:
        Directory written by
        :func:`feature_matrix.materialize_feature_matrix` —
        ``data/processed/model_training/<SET>/<EVENT_TYPE>/``.

    Returns
    -------
    KeptHandCache
        Lookup indexed by ``(draft_id, match_number, game_number)``.

    Raises
    ------
    FileNotFoundError
        If the directory is missing or empty. The cache is optional
        for correctness (cache misses fall back to fresh sim) but
        missing it entirely means we lose the big speedup, so we
        surface the situation loudly rather than silently scaling
        down.
    """
    paths = feature_parquet_paths(cached_features_dir)
    if not paths:
        raise FileNotFoundError(
            f"No win-model feature chunks under {cached_features_dir}. "
            f"Run materialize_feature_matrix(set_code=...) first, or pass "
            f"cached_features_dir=None to skip the cache (slow)."
        )
    log.info("Loading kept-hand cache from %d chunk(s) under %s", len(paths), cached_features_dir)
    frames = []
    for p in paths:
        df = pd.read_parquet(p)
        drop_cols = [c for c in _CACHE_COLUMNS_TO_DROP if c in df.columns]
        if drop_cols:
            df = df.drop(columns=drop_cols)
        frames.append(df)
    frame = pd.concat(frames, ignore_index=True)
    required = ("draft_id", "match_number", "game_number")
    for col in required:
        if col not in frame.columns:
            raise ValueError(
                f"Win-model cache at {cached_features_dir} is missing required column {col!r}; "
                f"its schema may pre-date the choice-model pipeline."
            )
    frame = frame.set_index(list(required)).sort_index()
    log.info("Kept-hand cache loaded: %d rows, %d columns", len(frame), len(frame.columns))
    return KeptHandCache(frame=frame)


# ---------------------------------------------------------------------------
# Row assembly: cache-hit path and fresh-compute path
# ---------------------------------------------------------------------------


def _row_seed(
    draft_id: str,
    match_number: int,
    game_number: int,
    mulligan_number: int,
) -> int:
    """Deterministic per-decision simulation seed.

    Distinct from :func:`feature_matrix._row_seed` by the
    ``mulligan_number`` component — different candidate hands of the
    same game must get different simulator RNGs even though they
    share a ``(draft_id, match_number, game_number)``.
    """
    raw = f"{draft_id}\x00{match_number}\x00{game_number}\x00{mulligan_number}".encode()
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:4], "big")


def _decision_context(cr: ChoiceRow) -> dict[str, Any]:
    """Build the choice-model context-column dict for one decision row.

    Used by both the cache-hit and fresh-compute paths to ensure the
    output schema is identical regardless of how the features were
    obtained.
    """
    on_play = cr.on_the_play
    return {
        "opp_mulligan_count_if_known": (float(cr.opp_mulligan_number) if not on_play else None),
        "opp_mulligan_number": int(cr.opp_mulligan_number),
        "mulligan_number": int(cr.mulligan_number),
        "num_mulligans_in_game": int(cr.num_mulligans_in_game),
        "expansion": cr.expansion,
        "event_type": cr.event_type,
        "draft_id": cr.draft_id,
        "build_index": int(cr.build_index),
        "match_number": int(cr.match_number),
        "game_number": int(cr.game_number),
        "user_n_games_raw": cr.user_n_games_raw,
        "user_wr_raw": cr.user_wr_raw,
        "was_kept": bool(cr.was_kept),
    }


def build_choice_row_from_cache(
    cr: ChoiceRow,
    cached: dict[str, Any],
) -> dict[str, Any]:
    """Assemble a choice-model output row from a cache hit.

    ``cached`` is the dict returned by :meth:`KeptHandCache.lookup`
    — already stripped of the win-model's label / user-bucket
    columns. We start from the cached features (the 200 feature
    floats), then layer the choice-model context on top so the schema
    matches the fresh-compute path exactly.

    Pre-condition: the caller has already verified
    ``cached["mulligan_number"] == cr.mulligan_number`` (so the
    cached features really do correspond to a hand drawn at the same
    mulligan level — the simulator's outputs don't depend on
    mulligan_number but ``build_feature_row`` does, and we want
    bit-identical features to a fresh run).
    """
    row: dict[str, Any] = dict(cached)
    # Drop cache-side context columns we'll replace; keeping them would
    # silently shadow the ChoiceRow values if they happen to differ.
    for col in _OUTPUT_CONTEXT_COLUMNS:
        row.pop(col, None)
    row.update(_decision_context(cr))
    return row


def build_choice_row_fresh(
    cr: ChoiceRow,
    *,
    format_stats: _FormatStats,
    n_sims_per_row: int,
) -> dict[str, Any]:
    """Run the simulator + feature builder for one decision row.

    Same chain as :func:`feature_matrix.build_row`, but the seed
    additionally varies by ``mulligan_number`` and the output carries
    ``was_kept`` instead of ``won``.
    """
    library = _library_from_deck(cr.hand, cr.deck)
    seed = _row_seed(cr.draft_id, cr.match_number, cr.game_number, cr.mulligan_number)
    aggregate = simulate(
        list(cr.hand),
        library,
        on_the_play=cr.on_the_play,
        n_runs=n_sims_per_row,
        seed=seed,
    )
    row = build_feature_row(
        hand=list(cr.hand),
        deck=list(cr.deck),
        aggregate_stats=aggregate,
        shrunk=format_stats.shrunk,
        zscores=format_stats.zscores,
        on_the_play=cr.on_the_play,
        mulligan_number=cr.mulligan_number,
        event_type=cr.event_type,
        set_code=cr.expansion,
    )
    out: dict[str, Any] = dict(row)
    out.update(_decision_context(cr))
    return out


# ---------------------------------------------------------------------------
# Multiprocessing worker primitives (mirror feature_matrix's pattern)
# ---------------------------------------------------------------------------


_WORKER_FORMAT_STATS: _FormatStats | None = None
_WORKER_N_SIMS: int = 200


@dataclass(frozen=True)
class _WorkerResult:
    """Worker's per-row result — either a feature dict or a classified error."""

    success: bool
    row: dict[str, Any] | None
    error_kind: str | None
    error_repr: str | None
    draft_id: str
    match_number: int
    game_number: int
    mulligan_number: int


def _worker_init(format_stats: _FormatStats, n_sims_per_row: int) -> None:
    global _WORKER_FORMAT_STATS, _WORKER_N_SIMS
    _WORKER_FORMAT_STATS = format_stats
    _WORKER_N_SIMS = n_sims_per_row


def _worker_build(cr: ChoiceRow) -> _WorkerResult:
    """Run :func:`build_choice_row_fresh` inside a worker process.

    Same error-classification pattern as :func:`feature_matrix._worker_build`
    so the main loop's accounting matches across both materialisers.
    """
    if _WORKER_FORMAT_STATS is None:  # pragma: no cover — set by initializer
        raise RuntimeError("_worker_init was not called; worker state missing.")
    try:
        row = build_choice_row_fresh(
            cr,
            format_stats=_WORKER_FORMAT_STATS,
            n_sims_per_row=_WORKER_N_SIMS,
        )
        return _WorkerResult(
            success=True,
            row=row,
            error_kind=None,
            error_repr=None,
            draft_id=cr.draft_id,
            match_number=cr.match_number,
            game_number=cr.game_number,
            mulligan_number=cr.mulligan_number,
        )
    except Exception as exc:
        from mulligan_coach_simulation import DeckEncodingError

        kind = "simulation" if isinstance(exc, DeckEncodingError) else "feature_build"
        return _WorkerResult(
            success=False,
            row=None,
            error_kind=kind,
            error_repr=repr(exc),
            draft_id=cr.draft_id,
            match_number=cr.match_number,
            game_number=cr.game_number,
            mulligan_number=cr.mulligan_number,
        )


# ---------------------------------------------------------------------------
# Cache-aware iterator: serve cache hits inline, ship misses to workers
# ---------------------------------------------------------------------------


def _classify_and_iter_rows(
    choice_rows: Iterable[ChoiceRow],
    *,
    cache: KeptHandCache | None,
    format_stats: _FormatStats,
    n_sims_per_row: int,
    n_workers: int,
    chunksize: int,
    stats: ChoiceMaterializationStats,
) -> Iterator[dict[str, Any]]:
    """Single-threaded fast-path serving cache hits + delegating misses.

    Splits the input stream into two queues:

    * **Cache-hit** (``was_kept=True`` AND cached AND mulligan_number
      matches) — assembled inline via :func:`build_choice_row_from_cache`.
    * **Sim-required** — shipped to a worker pool (or run inline when
      ``n_workers == 1``) via :func:`build_choice_row_fresh`.

    Yields rows in arbitrary order — both feeds emit independently. The
    chunk writer is order-invariant. ``mulligan_number`` matters for
    classification so we always retain it.
    """
    sim_rows: list[ChoiceRow] = []
    cache_hit_rows: list[dict[str, Any]] = []

    for cr in choice_rows:
        if cache is not None and cr.was_kept:
            cached = cache.lookup(cr.draft_id, cr.match_number, cr.game_number)
            if cached is not None and int(cached.get("mulligan_number", -1)) == cr.mulligan_number:
                cache_hit_rows.append(build_choice_row_from_cache(cr, cached))
                stats.rows_via_cache += 1
                continue
        sim_rows.append(cr)

    log.info(
        "_classify_and_iter_rows: cache_hits=%d, sim_required=%d",
        len(cache_hit_rows),
        len(sim_rows),
    )

    yield from cache_hit_rows

    if not sim_rows:
        return

    if n_workers <= 1:
        for cr in sim_rows:
            try:
                row = build_choice_row_fresh(
                    cr,
                    format_stats=format_stats,
                    n_sims_per_row=n_sims_per_row,
                )
                stats.rows_via_simulation += 1
                yield row
            except Exception as exc:
                from mulligan_coach_simulation import DeckEncodingError

                if isinstance(exc, DeckEncodingError):
                    stats.rows_failed_simulation += 1
                else:
                    stats.rows_failed_feature_build += 1
                log.warning(
                    "choice row build failed for draft_id=%s match=%s game=%s mull=%s: %r",
                    cr.draft_id,
                    cr.match_number,
                    cr.game_number,
                    cr.mulligan_number,
                    exc,
                )
        return

    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(
        processes=n_workers,
        initializer=_worker_init,
        initargs=(format_stats, n_sims_per_row),
    ) as pool:
        for result in pool.imap_unordered(_worker_build, sim_rows, chunksize=chunksize):
            if result.success:
                assert result.row is not None
                stats.rows_via_simulation += 1
                yield result.row
            else:
                if result.error_kind == "simulation":
                    stats.rows_failed_simulation += 1
                else:
                    stats.rows_failed_feature_build += 1
                log.warning(
                    "choice row build failed for draft_id=%s match=%s game=%s mull=%s: %s",
                    result.draft_id,
                    result.match_number,
                    result.game_number,
                    result.mulligan_number,
                    result.error_repr,
                )


# ---------------------------------------------------------------------------
# Chunk writer + resume helpers
# ---------------------------------------------------------------------------


_CHUNK_FILE_PATTERN = "chunk_*.parquet"
_CHUNK_TMP_PATTERN = ".chunk_*.parquet.tmp-*"


# Unique identity of a decision row — distinct from feature_matrix's
# RowKey by the trailing mulligan_number component.
DecisionKey = tuple[str, int, int, int, int]
"""(draft_id, build_index, match_number, game_number, mulligan_number)."""


def choice_parquet_paths(output_dir: Path) -> list[Path]:
    """Sorted list of completed chunk parquets in ``output_dir``.

    Mirror of :func:`feature_matrix.feature_parquet_paths` for the
    choice-model output directory. Empty list if the directory is
    missing or empty.
    """
    if not output_dir.exists():
        return []
    return sorted(output_dir.glob(_CHUNK_FILE_PATTERN))


def _chunk_path(output_dir: Path, chunk_idx: int) -> Path:
    return output_dir / f"chunk_{chunk_idx:08d}.parquet"


def _chunk_tmp_path(output_dir: Path, chunk_idx: int) -> Path:
    return output_dir / f".chunk_{chunk_idx:08d}.parquet.tmp-{os.getpid()}"


def _write_chunk(rows: list[dict[str, Any]], output_dir: Path, chunk_idx: int) -> Path:
    final = _chunk_path(output_dir, chunk_idx)
    tmp = _chunk_tmp_path(output_dir, chunk_idx)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, tmp, compression="zstd")  # type: ignore[no-untyped-call]
    tmp.replace(final)
    return final


def _load_completed_keys(output_dir: Path) -> tuple[set[DecisionKey], int]:
    """Scan completed chunks and return (skip_set, next_chunk_idx)."""
    completed: set[DecisionKey] = set()
    max_idx = -1
    for chunk in sorted(output_dir.glob(_CHUNK_FILE_PATTERN)):
        table = pq.read_table(  # type: ignore[no-untyped-call]
            chunk,
            columns=[
                "draft_id",
                "build_index",
                "match_number",
                "game_number",
                "mulligan_number",
            ],
        )
        df = table.to_pandas()
        for d, b, m, g, mu in zip(
            df["draft_id"],
            df["build_index"],
            df["match_number"],
            df["game_number"],
            df["mulligan_number"],
            strict=True,
        ):
            completed.add((str(d), int(b), int(m), int(g), int(mu)))
        idx = int(chunk.stem.removeprefix("chunk_"))
        max_idx = max(max_idx, idx)
    return completed, max_idx + 1


def _sweep_stale_tmp_files(output_dir: Path) -> None:
    import contextlib

    for p in output_dir.glob(_CHUNK_TMP_PATTERN):
        with contextlib.suppress(OSError):
            p.unlink()


def _filter_completed(
    rows: Iterable[ChoiceRow],
    completed: set[DecisionKey],
    stats: ChoiceMaterializationStats,
) -> Iterator[ChoiceRow]:
    if not completed:
        yield from rows
        return
    for cr in rows:
        key = (cr.draft_id, cr.build_index, cr.match_number, cr.game_number, cr.mulligan_number)
        if key in completed:
            stats.rows_skipped_resume += 1
            continue
        yield cr


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def materialize_choice_feature_matrix(
    *,
    decisions_parquet: Path,
    cached_features_dir: Path | None,
    output_dir: Path,
    set_code: str,
    event_type: str = "PremierDraft",
    n_sims_per_row: int = 200,
    n_workers: int = 1,
    chunksize: int = 32,
    chunk_rows: int = 5000,
    resume: bool = True,
    limit: int | None = None,
    overwrite: bool = False,
    data_root: Path | None = None,
    min_n_games_to_judge: int = DEFAULT_MIN_N_GAMES_TO_JUDGE,
    min_win_rate: float = DEFAULT_MIN_WIN_RATE,
    log_every: int = 5000,
) -> ChoiceMaterializationStats:
    """Materialise the choice-model feature parquet shard for one ``(set, event)``.

    Parameters
    ----------
    decisions_parquet:
        Path to ``data/processed/seventeenlands/mulligan_decisions/
        {SET}.{EVENT}.parquet`` (or ``combined.{EVENT}.parquet`` —
        the ``set_code`` arg filters down).
    cached_features_dir:
        Path to ``data/processed/model_training/{SET}/{EVENT}/`` for
        the kept-hand sim cache. Pass ``None`` to skip the cache and
        re-simulate every row (slow; useful when the win-model cache
        is missing or stale).
    output_dir:
        Destination directory (created if missing). Output is a
        directory of ``chunk_NNNNNNNN.parquet`` files written
        atomically.
    set_code, event_type:
        Format identifiers. ``set_code`` filters the decisions parquet
        and selects the cached-features sub-directory.
    n_sims_per_row, n_workers, chunksize, chunk_rows:
        Same semantics as :func:`feature_matrix.materialize_feature_matrix`.
    resume, overwrite:
        Same three-way as the win-model materialiser.
    limit:
        Optional cap on decisions iterated (post-filter). Useful for
        smoke tests.
    data_root:
        Forwarded to cards / features helpers in tests.
    min_n_games_to_judge, min_win_rate:
        Player-skill filter thresholds. See
        :func:`choice_rows.should_keep_player`.
    log_every:
        Emit an INFO progress log every N rows written.

    Returns
    -------
    ChoiceMaterializationStats
        Counters of rows written / served-from-cache / freshly-simulated.
    """
    if n_workers < 1:
        raise ValueError(f"n_workers must be >= 1; got {n_workers}")
    if chunk_rows < 1:
        raise ValueError(f"chunk_rows must be >= 1; got {chunk_rows}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Resume / overwrite / fresh-start three-way (mirror feature_matrix).
    existing_chunks = list(output_dir.glob(_CHUNK_FILE_PATTERN))
    completed: set[DecisionKey]
    next_chunk_idx: int
    if existing_chunks:
        if overwrite:
            log.info(
                "materialize_choice_feature_matrix(%s): overwrite=True, deleting %d chunk(s)",
                set_code,
                len(existing_chunks),
            )
            for p in existing_chunks:
                p.unlink()
            completed, next_chunk_idx = set(), 0
        elif resume:
            completed, next_chunk_idx = _load_completed_keys(output_dir)
            log.info(
                "materialize_choice_feature_matrix(%s): resuming from %d chunk(s); "
                "skipping %d already-completed decisions",
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

    _sweep_stale_tmp_files(output_dir)

    cache: KeptHandCache | None = None
    if cached_features_dir is not None:
        cache = load_kept_hand_cache(cached_features_dir)
    else:
        log.warning(
            "materialize_choice_feature_matrix(%s): cached_features_dir=None; "
            "every kept-hand row will be re-simulated (slow path)",
            set_code,
        )

    format_stats = _build_format_stats(set_code, data_root=data_root)
    log.info(
        "materialize_choice_feature_matrix(%s): %d shrunk WRs, %d z-score rows",
        set_code,
        len(format_stats.shrunk),
        len(format_stats.zscores),
    )

    stats = ChoiceMaterializationStats()

    raw_iter = iter_choice_rows(
        decisions_parquet,
        set_code=set_code,
        event_type=event_type,
        data_root=data_root,
        stats=stats.choice_row_stats,
        min_n_games_to_judge=min_n_games_to_judge,
        min_win_rate=min_win_rate,
        limit=limit,
    )
    filtered_iter = _filter_completed(raw_iter, completed, stats)

    feature_iter = _classify_and_iter_rows(
        filtered_iter,
        cache=cache,
        format_stats=format_stats,
        n_sims_per_row=n_sims_per_row,
        n_workers=n_workers,
        chunksize=chunksize,
        stats=stats,
    )

    batch: list[dict[str, Any]] = []
    chunk_idx = next_chunk_idx
    for row in feature_iter:
        batch.append(row)
        if len(batch) >= chunk_rows:
            _write_chunk(batch, output_dir, chunk_idx)
            stats.rows_written += len(batch)
            stats.chunks_written += 1
            if stats.rows_written % log_every < chunk_rows or stats.chunks_written <= 5:
                log.info(
                    "materialize_choice_feature_matrix(%s): wrote chunk %d "
                    "(cumulative=%d, via_cache=%d, via_sim=%d)",
                    set_code,
                    chunk_idx,
                    stats.rows_written,
                    stats.rows_via_cache,
                    stats.rows_via_simulation,
                )
            chunk_idx += 1
            batch.clear()
    if batch:
        _write_chunk(batch, output_dir, chunk_idx)
        stats.rows_written += len(batch)
        stats.chunks_written += 1

    if stats.rows_written == 0 and not completed:
        log.warning(
            "materialize_choice_feature_matrix(%s): no rows emitted "
            "(choice_row_stats: emitted=%d, skipped_player_filter=%d, "
            "skipped_unknown_card=%d). Output directory not written.",
            set_code,
            stats.choice_row_stats.emitted,
            stats.choice_row_stats.skipped_player_filter,
            stats.choice_row_stats.skipped_unknown_card,
        )
        return stats

    log.info(
        "materialize_choice_feature_matrix(%s): wrote %d rows in %d chunk(s) to %s "
        "(via_cache=%d, via_sim=%d, skipped_resume=%d, failed_sim=%d, failed_build=%d)",
        set_code,
        stats.rows_written,
        stats.chunks_written,
        output_dir,
        stats.rows_via_cache,
        stats.rows_via_simulation,
        stats.rows_skipped_resume,
        stats.rows_failed_simulation,
        stats.rows_failed_feature_build,
    )
    return stats
