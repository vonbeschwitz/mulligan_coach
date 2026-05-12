"""DuckDB ``games`` view -> typed :class:`TrainingRow` instances.

The 17Lands public game-data parquets (downloaded and unified by
:mod:`mulligan_coach_data_download.seventeenlands`) carry one row per
game with a wide schema:

* Context columns: ``expansion``, ``event_type``, ``draft_id``,
  ``on_play``, ``num_mulligans``, ``opp_num_mulligans``, ``won``,
  ``user_n_games_bucket``, ``user_game_win_rate_bucket``,
  ``game_number``.
* For every card that appeared in any tracked set:
  ``deck_<CARD_NAME>`` (count in the 40-card deck) and
  ``opening_hand_<CARD_NAME>`` (count in the post-mulligan opening
  hand). The 17Lands London mulligan convention is that
  ``opening_hand_*`` always sums to **7** — players who mulled to N
  drew 7 fresh cards and put ``num_mulligans`` on the bottom; the
  recorded 7 is the pre-bottom hand. We surface that 7-card draw
  exactly as recorded and let downstream code account for
  ``mulligan_number`` (e.g. the model treats it as a context
  feature).

This module hides the schema width and produces typed
:class:`TrainingRow` instances ready for the feature-matrix step.

Card-name -> ``ParsedCard`` reconstruction uses
:func:`mulligan_coach_cards.load_parsed_cards` for the per-set
catalogue, plus synthesised basic-land entries (basic lands live in
Scryfall's main bulk, not the per-set parsed-card JSON store).
Double-faced cards from Scryfall use the joint ``Front // Back``
name while 17Lands columns use the front-face name only; the
front-face fallback in :func:`build_name_lookup` handles this
without touching the cards package's existing
:meth:`mulligan_coach_cards.StatsLookup.match` logic.

Rows that fail data-quality checks (wrong event type, hand size
not 7, deck size not 40, unmapped card name, missing context
column) are skipped; counts of each skip reason are returned via
:class:`TrainingRowStats` so callers can audit dump quality.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import duckdb
from mulligan_coach_cards import (
    Cost,
    ManaAbility,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    load_parsed_cards,
)
from mulligan_coach_cards.models import ManaOption

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


UNKNOWN_BUCKET: Final[str] = "unknown"
"""Sentinel bucket label used when 17Lands didn't report a user-stats value.

The baseline regression treats ``"unknown"`` as just another cell;
the cell will absorb whatever residual variance the missing-data
rows carry without contaminating the well-populated cells.
"""


@dataclass(frozen=True)
class TrainingRow:
    """One 17Lands game row, reconstructed for model training.

    All fields are eagerly resolved at construction time — no
    promises of lazy decoding, no shared mutable state with the
    DuckDB cursor. The hand/deck lists are tuples to keep the
    dataclass hashable and so downstream code can't accidentally
    mutate them.
    """

    hand: tuple[ParsedCard, ...]
    """7 cards drawn for the player's opening hand (pre-bottom; see
    module docstring for the London-mulligan convention)."""

    deck: tuple[ParsedCard, ...]
    """40-card deck list — includes the hand cards. Order is not
    meaningful (17Lands records counts, not order)."""

    on_the_play: bool
    mulligan_number: int
    """How many times the player took a mulligan (0..2 typical; the
    upstream raw data occasionally has higher values which are
    filtered out by :func:`iter_training_rows`)."""

    opp_mulligan_number: int

    user_wr_bucket: str
    """Coarsened bucket over ``user_game_win_rate_bucket``; one of
    ``"<45%"``, ``"45-50%"``, ``"50-55%"``, ``"55-60%"``,
    ``">=60%"``, or :data:`UNKNOWN_BUCKET` when the raw value is
    missing."""

    user_n_games_bucket: str
    """Coarsened bucket over ``user_n_games_bucket``; one of
    ``"<10"``, ``"10-49"``, ``"50-99"``, ``"100-499"``, ``"500+"``,
    or :data:`UNKNOWN_BUCKET`."""

    expansion: str
    event_type: str
    won: bool
    draft_id: str
    match_number: int
    """Match index within the draft (1, 2, ... in best-of-N matches).
    Required to uniquely identify a game: ``(draft_id, game_number)``
    alone is NOT unique because each draft plays multiple matches."""

    game_number: int
    """Game index within the *match* (1..3 typically). Together with
    ``draft_id`` and ``match_number`` this identifies the row uniquely
    and seeds the simulator deterministically."""


@dataclass
class TrainingRowStats:
    """Counters returned alongside the row stream for diagnostics."""

    emitted: int = 0
    skipped_bad_hand_size: int = 0
    skipped_bad_deck_size: int = 0
    skipped_unknown_card: int = 0
    skipped_missing_context: int = 0
    skipped_bad_mulligan: int = 0
    unknown_card_names: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bucketing helpers
# ---------------------------------------------------------------------------


# Upper edges (exclusive) for the 5 WR buckets + label.
_WR_BUCKETS: Final[tuple[tuple[float, str], ...]] = (
    (0.45, "<45%"),
    (0.50, "45-50%"),
    (0.55, "50-55%"),
    (0.60, "55-60%"),
    (math.inf, ">=60%"),
)


def bucket_user_wr(value: float | None) -> str:
    """Map a raw ``user_game_win_rate_bucket`` value to a coarse bucket label.

    17Lands publishes the user's win-rate in 2% bins (0.00, 0.02,
    0.04, ...). Five wide buckets is a good trade-off for the
    saturated-cell baseline (PR 3): few enough cells that
    most are well-populated, fine enough that the bucket-level
    residual variance is small. ``None`` -> :data:`UNKNOWN_BUCKET`.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return UNKNOWN_BUCKET
    for upper, label in _WR_BUCKETS:
        if value < upper:
            return label
    # value == inf path; unreachable in practice because _WR_BUCKETS ends with inf.
    return _WR_BUCKETS[-1][1]


_N_GAMES_LABELS: Final[dict[int, str]] = {
    1: "<10",
    5: "<10",
    10: "10-49",
    50: "50-99",
    100: "100-499",
    500: "500+",
    1000: "500+",
}


def bucket_user_n_games(value: int | None) -> str:
    """Map a raw ``user_n_games_bucket`` edge to a coarse bucket label.

    17Lands stores the bucket's lower edge — one of
    ``{1, 5, 10, 50, 100, 500, 1000}``. We collapse to five labels;
    any value outside the known edge set returns
    :data:`UNKNOWN_BUCKET` rather than raising, so a future 17Lands
    schema change doesn't crash the materialisation pass.
    """
    if value is None:
        return UNKNOWN_BUCKET
    return _N_GAMES_LABELS.get(int(value), UNKNOWN_BUCKET)


# ---------------------------------------------------------------------------
# Per-set name -> ParsedCard lookup
# ---------------------------------------------------------------------------


# Basic land specs — synthesised by :func:`_make_basic_land` since
# load_parsed_cards doesn't carry basics (they live in Scryfall's main
# bulk, not the per-set JSON store).
_BASIC_LANDS: Final[tuple[tuple[str, ManaOption, str], ...]] = (
    ("Plains", "W", "Plains"),
    ("Island", "U", "Island"),
    ("Swamp", "B", "Swamp"),
    ("Mountain", "R", "Mountain"),
    ("Forest", "G", "Forest"),
)


def _make_basic_land(name: str, color: ManaOption, subtype: str) -> ParsedCard:
    """Synthesise a basic-land ParsedCard.

    Shape matches the test factory at
    ``packages/features/tests/_factories.py:basic`` and the smoke
    script in ``packages/features/scripts/smoke_feature_builder.py``
    so behaviour is consistent across the feature pipeline.
    """
    return ParsedCard(
        name=name,
        set_code="BASIC",
        collector_number=name.lower(),
        oracle_id=f"basic-land-{name.lower()}",
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land — {subtype}",
        types=["Land"],
        subtypes=[subtype],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )


def build_name_lookup(
    set_code: str,
    *,
    data_root: Path | None = None,
) -> dict[str, ParsedCard]:
    """Build a name -> ParsedCard map for one set.

    Indexes both the canonical ``ParsedCard.name`` and the
    front-face name (split on ``" // "``) so 17Lands columns —
    which use the front-face name for DFCs — find their card.
    Adds synthesised entries for the five WUBRG basic lands.

    The lookup is not the place to enforce per-set isolation
    (game rows may include basics or copies of off-set staples
    only at the column level); name collisions are resolved by
    "first one wins", with the parsed-card entry preferred over
    the synthesised basic.
    """
    lookup: dict[str, ParsedCard] = {}
    for card in load_parsed_cards(set_code, data_root=data_root):
        lookup[card.name] = card
        if " // " in card.name:
            front = card.name.split(" // ", 1)[0]
            lookup.setdefault(front, card)
    for name, color, subtype in _BASIC_LANDS:
        lookup.setdefault(name, _make_basic_land(name, color, subtype))
    return lookup


# ---------------------------------------------------------------------------
# Streaming reader
# ---------------------------------------------------------------------------


# Constants used in row-level validation. Centralised so PR 2 / 4 can
# reuse them if they ever need to mirror the same filters.
EXPECTED_HAND_SIZE: Final[int] = 7
MIN_DECK_SIZE: Final[int] = 40
MAX_DECK_SIZE: Final[int] = 42
"""17Lands records the pre-bottom 7-card draw regardless of
``num_mulligans`` (London mulligan). Limited decks are at least 40
cards; many players run 41 or 42 when they're torn between two
strong cards. We accept up to 42 — beyond that the deck is unusual
enough to suggest data corruption or an off-meta strategy not worth
training on."""

# Back-compat alias: the original constant.  Existing call sites used
# ``EXPECTED_DECK_SIZE == 40`` as the canonical "minimum legal deck"
# value; the variable name now refers to the lower bound of the
# accepted range. Kept for use by tests and downstream consumers.
EXPECTED_DECK_SIZE: Final[int] = MIN_DECK_SIZE

MAX_MULLIGAN_NUMBER: Final[int] = 6
"""Cap on plausible mulligan counts. Premier Draft hands ``> 6``
mulligans are data quirks (we saw exactly 2 rows with
``num_mulligans=7`` across ~1.1M games) and get dropped."""

# Required non-card columns. The materialisation pass needs every
# one of these to be non-NULL; rows missing any are skipped.
_REQUIRED_CONTEXT_COLS: Final[tuple[str, ...]] = (
    "expansion",
    "event_type",
    "draft_id",
    "on_play",
    "num_mulligans",
    "opp_num_mulligans",
    "won",
    "match_number",
    "game_number",
)


def _column_partition(
    column_names: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Split a column-name iterable into (deck_, opening_hand_) lists.

    Order is preserved within each prefix family so downstream code
    can rely on a stable iteration order (useful for hashing and
    for human-readable logs).
    """
    deck_cols: list[str] = []
    oh_cols: list[str] = []
    for col in column_names:
        if col.startswith("deck_"):
            deck_cols.append(col)
        elif col.startswith("opening_hand_"):
            oh_cols.append(col)
    return deck_cols, oh_cols


def _safe_int(value: Any) -> int:
    """Coerce a DuckDB cell to int, treating NULL / NaN as 0.

    The wide ``games`` view (built with ``union_by_name=True``)
    null-pads card columns for rows from other sets, so the
    summation must tolerate NULL gracefully.
    """
    if value is None:
        return 0
    # Avoid importing pandas just for isna; handle the float-NaN case
    # explicitly. DuckDB returns Python ints for BIGINT columns.
    if isinstance(value, float) and math.isnan(value):
        return 0
    return int(value)


def _materialise_cards(
    counts_by_col: dict[str, int],
    *,
    prefix: str,
    name_lookup: dict[str, ParsedCard],
) -> tuple[list[ParsedCard], list[str]]:
    """Expand a ``{column_name: count}`` dict into a list of ParsedCards.

    Returns ``(cards, missing_names)``. ``missing_names`` contains
    every card name whose count was > 0 but is absent from
    ``name_lookup``; the caller decides whether to skip the row.
    """
    cards: list[ParsedCard] = []
    missing: list[str] = []
    for col, count in counts_by_col.items():
        if count <= 0:
            continue
        name = col[len(prefix) :]
        card = name_lookup.get(name)
        if card is None:
            missing.append(name)
            continue
        cards.extend([card] * count)
    return cards, missing


def iter_training_rows(
    *,
    connection: duckdb.DuckDBPyConnection,
    set_code: str,
    event_type: str = "PremierDraft",
    view_name: str = "games",
    name_lookup: dict[str, ParsedCard] | None = None,
    limit: int | None = None,
    data_root: Path | None = None,
    stats: TrainingRowStats | None = None,
    batch_size: int = 1000,
) -> Iterator[TrainingRow]:
    """Stream :class:`TrainingRow` instances for one ``(set, event_type)``.

    The function pulls rows in batches via ``cursor.fetchmany`` so
    memory usage stays bounded even on the largest formats.
    Filter logic in SQL: ``WHERE expansion = ? AND event_type = ?``.
    All other quality checks are applied row-by-row in Python because
    they touch the wide card columns.

    Parameters
    ----------
    connection:
        Active DuckDB connection. The caller is responsible for
        opening / closing it — typically against
        ``data/processed/games.duckdb``.
    set_code:
        Three-letter set code to filter on (case-insensitive; the
        underlying ``expansion`` column stores upper-case).
    event_type:
        Defaults to ``"PremierDraft"`` (the v1 scope per
        ``packages/CLAUDE.md``).
    view_name:
        Name of the games view in the connection's schema.
        Default ``"games"`` matches
        :mod:`mulligan_coach_data_download.seventeenlands.duckdb_views`.
    name_lookup:
        Optional pre-built name -> :class:`ParsedCard` map. When
        ``None``, the lookup is built from
        :func:`build_name_lookup(set_code, data_root=...)`. Injecting
        a custom lookup is the recommended testing path (avoids
        depending on the data root layout).
    limit:
        Optional cap on the number of SQL rows pulled. Useful for
        smoke tests; ``None`` reads the entire shard.
    data_root:
        Forwarded to :func:`build_name_lookup`. Ignored when
        ``name_lookup`` is provided.
    stats:
        Optional :class:`TrainingRowStats` accumulator. If passed,
        per-skip counters are incremented in place so the caller can
        audit dump quality. When ``None``, the counters are still
        tracked internally and dumped at INFO level on exhaustion.
    batch_size:
        DuckDB fetch chunk size. 1000 rows x 1700 columns x 8 bytes
        is ~14 MB per batch — bounded enough to stream comfortably.

    Yields
    ------
    TrainingRow
        One per valid game row, in ``cursor`` iteration order.
    """
    set_code_upper = set_code.upper()
    if name_lookup is None:
        name_lookup = build_name_lookup(set_code_upper, data_root=data_root)

    own_stats = stats is None
    if stats is None:
        stats = TrainingRowStats()

    sql = f"SELECT * FROM {view_name} WHERE expansion = ? AND event_type = ?"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"

    cursor = connection.execute(sql, [set_code_upper, event_type])
    column_names = [d[0] for d in cursor.description]
    deck_cols, oh_cols = _column_partition(column_names)
    context_idx = {col: idx for idx, col in enumerate(column_names)}
    user_wr_idx = context_idx.get("user_game_win_rate_bucket")
    user_n_idx = context_idx.get("user_n_games_bucket")
    # ``opp_num_mulligans`` is required; the others (user buckets) are
    # nice-to-have — missing values bucket to UNKNOWN_BUCKET rather
    # than triggering a skip.
    missing_required = [col for col in _REQUIRED_CONTEXT_COLS if col not in context_idx]
    if missing_required:
        raise ValueError(
            f"`{view_name}` view is missing required columns: {missing_required}. "
            f"Did the 17Lands schema change?"
        )

    deck_idx = [context_idx[c] for c in deck_cols]
    oh_idx = [context_idx[c] for c in oh_cols]

    while True:
        batch = cursor.fetchmany(batch_size)
        if not batch:
            break
        for row in batch:
            tr = _row_to_training_row(
                row,
                column_names=column_names,
                context_idx=context_idx,
                deck_cols=deck_cols,
                oh_cols=oh_cols,
                deck_idx=deck_idx,
                oh_idx=oh_idx,
                user_wr_idx=user_wr_idx,
                user_n_idx=user_n_idx,
                name_lookup=name_lookup,
                stats=stats,
            )
            if tr is not None:
                stats.emitted += 1
                yield tr

    if own_stats:
        log.info(
            "iter_training_rows(%s, %s): emitted=%d, "
            "skipped_bad_hand=%d, skipped_bad_deck=%d, "
            "skipped_unknown_card=%d, skipped_missing_context=%d, "
            "skipped_bad_mulligan=%d",
            set_code_upper,
            event_type,
            stats.emitted,
            stats.skipped_bad_hand_size,
            stats.skipped_bad_deck_size,
            stats.skipped_unknown_card,
            stats.skipped_missing_context,
            stats.skipped_bad_mulligan,
        )


def _row_to_training_row(
    row: tuple[Any, ...],
    *,
    column_names: list[str],
    context_idx: dict[str, int],
    deck_cols: list[str],
    oh_cols: list[str],
    deck_idx: list[int],
    oh_idx: list[int],
    user_wr_idx: int | None,
    user_n_idx: int | None,
    name_lookup: dict[str, ParsedCard],
    stats: TrainingRowStats,
) -> TrainingRow | None:
    """Validate + decode one SQL row. Returns None when the row is skipped.

    The hot path: zip the deck/oh index lists with the row values
    to avoid 1700 dict lookups per row. The rest is field-by-field
    extraction of the small set of context columns.
    """
    # ---- Context columns ----------------------------------------------
    try:
        expansion = row[context_idx["expansion"]]
        event_type = row[context_idx["event_type"]]
        draft_id = row[context_idx["draft_id"]]
        on_play = row[context_idx["on_play"]]
        num_mulligans = row[context_idx["num_mulligans"]]
        opp_num_mulligans = row[context_idx["opp_num_mulligans"]]
        won = row[context_idx["won"]]
        match_number = row[context_idx["match_number"]]
        game_number = row[context_idx["game_number"]]
    except (KeyError, IndexError):  # pragma: no cover — guarded by missing-col check above.
        stats.skipped_missing_context += 1
        return None

    if (
        expansion is None
        or event_type is None
        or draft_id is None
        or on_play is None
        or num_mulligans is None
        or opp_num_mulligans is None
        or won is None
        or match_number is None
        or game_number is None
    ):
        stats.skipped_missing_context += 1
        return None

    num_mulligans_int = int(num_mulligans)
    if num_mulligans_int < 0 or num_mulligans_int > MAX_MULLIGAN_NUMBER:
        stats.skipped_bad_mulligan += 1
        return None

    # ---- Deck + hand columns ------------------------------------------
    deck_counts = {col: _safe_int(row[idx]) for col, idx in zip(deck_cols, deck_idx, strict=True)}
    oh_counts = {col: _safe_int(row[idx]) for col, idx in zip(oh_cols, oh_idx, strict=True)}

    hand_size = sum(oh_counts.values())
    deck_size = sum(deck_counts.values())
    if hand_size != EXPECTED_HAND_SIZE:
        stats.skipped_bad_hand_size += 1
        return None
    if deck_size < MIN_DECK_SIZE or deck_size > MAX_DECK_SIZE:
        stats.skipped_bad_deck_size += 1
        return None

    hand_cards, missing_hand = _materialise_cards(
        oh_counts, prefix="opening_hand_", name_lookup=name_lookup
    )
    deck_cards, missing_deck = _materialise_cards(
        deck_counts, prefix="deck_", name_lookup=name_lookup
    )
    if missing_hand or missing_deck:
        stats.skipped_unknown_card += 1
        for name in missing_hand:
            stats.unknown_card_names[name] = stats.unknown_card_names.get(name, 0) + 1
        for name in missing_deck:
            stats.unknown_card_names[name] = stats.unknown_card_names.get(name, 0) + 1
        return None

    # ---- User-skill buckets -------------------------------------------
    user_wr_raw = row[user_wr_idx] if user_wr_idx is not None else None
    user_n_raw = row[user_n_idx] if user_n_idx is not None else None
    user_wr_bucket = bucket_user_wr(float(user_wr_raw) if user_wr_raw is not None else None)
    user_n_bucket = bucket_user_n_games(int(user_n_raw) if user_n_raw is not None else None)

    return TrainingRow(
        hand=tuple(hand_cards),
        deck=tuple(deck_cards),
        on_the_play=bool(on_play),
        mulligan_number=num_mulligans_int,
        opp_mulligan_number=int(opp_num_mulligans),
        user_wr_bucket=user_wr_bucket,
        user_n_games_bucket=user_n_bucket,
        expansion=str(expansion),
        event_type=str(event_type),
        won=bool(won),
        draft_id=str(draft_id),
        match_number=int(match_number),
        game_number=int(game_number),
    )
