"""Mulligan-decisions parquet -> typed :class:`ChoiceRow` instances.

Parallel to :mod:`training_rows`: where that module emits one row
per played game (with ``won`` as the label), this module emits one
row per *mulligan decision* the player made (with ``was_kept`` as
the label).

Source data
-----------

``scripts/mulligan_decisions/build_dataset.py`` writes per-set
parquets to
``data/processed/seventeenlands/mulligan_decisions/{SET}.{EVENT}.parquet``.
Each row carries:

* ``hand`` — pipe-delimited card names (always 7 under London mulligan).
* ``deck`` — ``"Card A xN | Card B xM | ..."`` sorted alphabetically.
* ``was_kept`` — True iff the player kept this candidate hand.
* ``mulligan_number`` — 0-indexed; how many mulligans had been taken
  *before* this candidate was drawn.
* ``num_mulligans_in_game`` — total mulligans the player ended up
  doing. ``was_kept`` is exactly ``mulligan_number == num_mulligans_in_game``.
* Context: ``draft_id``, ``match_number``, ``game_number``,
  ``build_index``, ``on_play``, ``opp_num_mulligans``, ``won``.
* Player-skill anchors: ``user_n_games_bucket`` (int — lower edge of
  the 17Lands bucket: 1, 5, 10, 50, 100, 500, 1000) and
  ``user_game_win_rate_bucket`` (float — bucket center value, 0.00,
  0.02, ..., 0.92, with NaN possible for brand-new accounts).

Player-skill filter
-------------------

The owner's intent is to train on *good* players' decisions, treating
the keep/mull choice as expert behaviour. We drop a row when **all** of:

* The player's lifetime game count is known and at least
  ``min_n_games_to_judge`` (so we have a meaningful sample).
* The player's lifetime win rate is known and strictly less than
  ``min_win_rate`` (so they're demonstrably below average).

When either signal is unknown (NaN n_games — rare; NaN WR — ~110/626K
rows in TLA), we keep the row: we can't judge them as bad. This is a
"remove the known-bad" filter, not a "keep only the known-good" filter.
Defaults are ``min_n_games_to_judge=50`` and ``min_win_rate=0.50``.

Card-name reconstruction reuses :func:`training_rows.build_name_lookup`
so the same per-set ParsedCard catalogue + DFC front-face fallback +
synthesised basics work here without duplication.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import pyarrow.parquet as pq
from mulligan_coach_cards import ParsedCard

from .training_rows import (
    EXPECTED_HAND_SIZE,
    MAX_DECK_SIZE,
    MAX_MULLIGAN_NUMBER,
    MIN_DECK_SIZE,
    build_name_lookup,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceRow:
    """One mulligan-decision row, reconstructed for choice-model training.

    Mirrors :class:`TrainingRow` in spirit but with a different label
    (``was_kept`` rather than ``won``) and a slightly different identity
    key (``build_index`` joins ``(draft_id, match_number, game_number)``
    because a single draft-game can theoretically have multiple builds).
    """

    hand: tuple[ParsedCard, ...]
    """The 7-card candidate hand the player saw at this decision point
    (pre-bottom under London mulligan)."""

    deck: tuple[ParsedCard, ...]
    """The 40-card deck (includes the hand cards). Identical across all
    decision rows from the same game."""

    on_the_play: bool
    mulligan_number: int
    """0-indexed: how many mulligans had been taken *before* this
    candidate was drawn. ``0`` is the very first 7-card draw."""

    was_kept: bool
    """**Label.** True iff the player kept this candidate hand."""

    num_mulligans_in_game: int
    """Total mulligans the player ended up doing. ``was_kept`` is
    exactly ``mulligan_number == num_mulligans_in_game``. Carried so
    downstream code can match against the game_data feature cache,
    which is keyed by the kept-hand's mulligan_number."""

    opp_mulligan_number: int

    user_n_games_raw: int | None
    """Raw 17Lands bucket lower edge (``1, 5, 10, 50, 100, 500, 1000``)
    or ``None`` when missing. Kept verbatim for audit; the player
    filter consumes this directly rather than going through a coarsened
    bucket."""

    user_wr_raw: float | None
    """Raw 17Lands bucket value (e.g. ``0.56``) or ``None`` when
    missing. Kept verbatim for the same audit reason."""

    expansion: str
    event_type: str
    won: bool
    draft_id: str
    build_index: int
    match_number: int
    game_number: int


@dataclass
class ChoiceRowStats:
    """Counters returned alongside the row stream for diagnostics."""

    emitted: int = 0
    skipped_bad_hand_size: int = 0
    skipped_bad_deck_size: int = 0
    skipped_unknown_card: int = 0
    skipped_missing_context: int = 0
    skipped_bad_mulligan: int = 0
    skipped_player_filter: int = 0
    unknown_card_names: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Player-skill filter
# ---------------------------------------------------------------------------


DEFAULT_MIN_N_GAMES_TO_JUDGE: Final[int] = 50
"""Default ``min_n_games_to_judge`` for :func:`should_keep_player`.

Matches the lower edge of the 17Lands bucket above ``"10-49"`` — i.e.
we only judge a player as below-average once they've played at least
50 games in the format. Below that the WR estimate is too noisy to act
on."""

DEFAULT_MIN_WIN_RATE: Final[float] = 0.50
"""Default ``min_win_rate`` for :func:`should_keep_player`.

The owner's request: "remove ... players we know are bad, meaning
those that over a fair amount of game have win rates of under 50%."
"""


def should_keep_player(
    user_n_games_raw: int | None,
    user_wr_raw: float | None,
    *,
    min_n_games_to_judge: int = DEFAULT_MIN_N_GAMES_TO_JUDGE,
    min_win_rate: float = DEFAULT_MIN_WIN_RATE,
) -> bool:
    """Decide whether to include a player's decision in the training set.

    Returns ``True`` (keep the row) unless **both** of:

    * The player has a known sample size at or above
      ``min_n_games_to_judge``, AND
    * The player has a known win rate strictly below ``min_win_rate``.

    Unknown values on either side default to "keep" — we can't
    confidently call a player bad without both signals.
    """
    if user_n_games_raw is None:
        return True
    if user_wr_raw is None or (isinstance(user_wr_raw, float) and math.isnan(user_wr_raw)):
        return True
    if int(user_n_games_raw) < min_n_games_to_judge:
        return True
    return float(user_wr_raw) >= min_win_rate


# ---------------------------------------------------------------------------
# Deck/hand string parsing
# ---------------------------------------------------------------------------


# Matches "Card Name xN" — name may contain spaces, commas, apostrophes,
# colons, slashes, parentheses, hyphens, etc. We anchor on the trailing
# " xN" suffix which the build_dataset.py emits in a fixed format.
_DECK_ENTRY_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<name>.+) x(?P<count>\d+)$")


def parse_hand_names(hand_str: str) -> list[str]:
    """Split the pipe-delimited hand string into a list of card names.

    The builder emits exactly 7 names per row (London mulligan); we
    don't enforce that here so callers can detect oddities via the
    hand-size check downstream.
    """
    if not hand_str:
        return []
    return hand_str.split("|")


def parse_deck_string(deck_str: str) -> list[tuple[str, int]]:
    """Parse ``"Card A x4 | Card B x2 | ..."`` into ``[(name, count), ...]``.

    Each entry is space-pipe-space separated. The count suffix is
    ``" xN"`` where N is a positive integer. Empty input returns an
    empty list. Malformed entries raise :class:`ValueError` — the
    builder is deterministic so corruption indicates an upstream bug
    rather than data we should tolerate silently.
    """
    if not deck_str:
        return []
    out: list[tuple[str, int]] = []
    for raw in deck_str.split(" | "):
        m = _DECK_ENTRY_RE.match(raw)
        if m is None:
            raise ValueError(f"Malformed deck entry {raw!r} in deck string")
        out.append((m.group("name"), int(m.group("count"))))
    return out


# ---------------------------------------------------------------------------
# Streaming reader
# ---------------------------------------------------------------------------


_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "expansion",
    "event_type",
    "draft_id",
    "build_index",
    "match_number",
    "game_number",
    "on_play",
    "user_n_games_bucket",
    "user_game_win_rate_bucket",
    "num_mulligans_in_game",
    "opp_num_mulligans",
    "won",
    "mulligan_number",
    "was_kept",
    "hand_size",
    "hand",
    "deck",
)


def _materialise_cards_from_counts(
    counts: list[tuple[str, int]],
    *,
    name_lookup: dict[str, ParsedCard],
) -> tuple[list[ParsedCard], list[str]]:
    """Expand ``[(name, count), ...]`` into a list of ParsedCards.

    Returns ``(cards, missing_names)`` so the caller can decide whether
    to skip the row. Pure mirror of
    :func:`training_rows._materialise_cards` but operating on the
    (name, count) list shape the choice-row parquet uses.
    """
    cards: list[ParsedCard] = []
    missing: list[str] = []
    for name, count in counts:
        if count <= 0:
            continue
        card = name_lookup.get(name)
        if card is None:
            missing.append(name)
            continue
        cards.extend([card] * count)
    return cards, missing


def _materialise_hand(
    hand_names: list[str],
    *,
    name_lookup: dict[str, ParsedCard],
) -> tuple[list[ParsedCard], list[str]]:
    """Resolve a list of pipe-delimited hand card names to ParsedCards.

    Mirrors :func:`_materialise_cards_from_counts` but for the hand,
    where each list entry is one copy (not a name+count). Returns
    ``(cards, missing_names)``.
    """
    cards: list[ParsedCard] = []
    missing: list[str] = []
    for name in hand_names:
        card = name_lookup.get(name)
        if card is None:
            missing.append(name)
            continue
        cards.append(card)
    return cards, missing


def _coerce_optional_int(value: object) -> int | None:
    """Return ``int(value)`` or ``None`` for NULL / NaN inputs.

    The pyarrow / pandas round-trip preserves NaN for nullable float
    columns and ``None`` for nullable int columns; we accept both.
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return int(value)


def _coerce_optional_float(value: object) -> float | None:
    """Return ``float(value)`` or ``None`` for NULL / NaN inputs."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return float(value)


def iter_choice_rows(
    parquet_path: Path,
    *,
    set_code: str,
    event_type: str = "PremierDraft",
    name_lookup: dict[str, ParsedCard] | None = None,
    data_root: Path | None = None,
    stats: ChoiceRowStats | None = None,
    batch_size: int = 5000,
    min_n_games_to_judge: int = DEFAULT_MIN_N_GAMES_TO_JUDGE,
    min_win_rate: float = DEFAULT_MIN_WIN_RATE,
    limit: int | None = None,
) -> Iterator[ChoiceRow]:
    """Stream :class:`ChoiceRow` instances from a mulligan-decisions parquet.

    Parameters
    ----------
    parquet_path:
        Path to ``{SET}.{EVENT}.parquet`` (or ``combined.{EVENT}.parquet``)
        from the mulligan-decisions builder.
    set_code:
        Three-letter set code to filter on. The parquet may carry
        multiple sets (e.g. ``combined.PremierDraft.parquet``); we
        select only the matching rows.
    event_type:
        Defaults to ``"PremierDraft"``.
    name_lookup:
        Optional pre-built name -> :class:`ParsedCard` map. When
        ``None``, built from :func:`build_name_lookup(set_code, ...)`.
    data_root:
        Forwarded to :func:`build_name_lookup` when constructing the
        default lookup.
    stats:
        Optional :class:`ChoiceRowStats` accumulator. Created
        internally when ``None`` and dumped at INFO level on exhaustion.
    batch_size:
        Number of parquet rows to materialise per RecordBatch. The
        parquet is narrow (~25 columns) so we can afford larger batches
        than the wide-games-view iterator uses.
    min_n_games_to_judge, min_win_rate:
        Player-skill filter thresholds. See :func:`should_keep_player`.
    limit:
        Optional cap on the number of rows yielded (post-filter).
        Useful for smoke tests.

    Yields
    ------
    ChoiceRow
        One per surviving decision, in parquet read order.
    """
    set_code_upper = set_code.upper()
    if name_lookup is None:
        name_lookup = build_name_lookup(set_code_upper, data_root=data_root)

    own_stats = stats is None
    if stats is None:
        stats = ChoiceRowStats()

    pf = pq.ParquetFile(parquet_path)  # type: ignore[no-untyped-call]
    available_cols = set(pf.schema_arrow.names)
    missing_cols = [c for c in _REQUIRED_COLUMNS if c not in available_cols]
    if missing_cols:
        raise ValueError(
            f"{parquet_path} is missing required columns: {missing_cols}. "
            f"Re-run scripts/mulligan_decisions/build_dataset.py?"
        )

    # Cache parsed decks per game-id so repeated decision rows for one
    # game (up to 7 candidate hands) only pay the parsing cost once.
    deck_cache: dict[tuple[str, int, int, int], tuple[ParsedCard, ...] | None] = {}

    emitted = 0
    for batch in pf.iter_batches(batch_size=batch_size, columns=list(_REQUIRED_COLUMNS)):
        records = batch.to_pylist()
        for record in records:
            if limit is not None and emitted >= limit:
                if own_stats:
                    _log_summary(stats, set_code_upper, event_type)
                return
            row = _record_to_choice_row(
                record,
                set_code_upper=set_code_upper,
                event_type=event_type,
                name_lookup=name_lookup,
                deck_cache=deck_cache,
                stats=stats,
                min_n_games_to_judge=min_n_games_to_judge,
                min_win_rate=min_win_rate,
            )
            if row is not None:
                stats.emitted += 1
                emitted += 1
                yield row

    if own_stats:
        _log_summary(stats, set_code_upper, event_type)


def _log_summary(stats: ChoiceRowStats, set_code: str, event_type: str) -> None:
    log.info(
        "iter_choice_rows(%s, %s): emitted=%d, skipped_bad_hand=%d, "
        "skipped_bad_deck=%d, skipped_unknown_card=%d, "
        "skipped_missing_context=%d, skipped_bad_mulligan=%d, "
        "skipped_player_filter=%d",
        set_code,
        event_type,
        stats.emitted,
        stats.skipped_bad_hand_size,
        stats.skipped_bad_deck_size,
        stats.skipped_unknown_card,
        stats.skipped_missing_context,
        stats.skipped_bad_mulligan,
        stats.skipped_player_filter,
    )


def _record_to_choice_row(
    record: dict[str, object],
    *,
    set_code_upper: str,
    event_type: str,
    name_lookup: dict[str, ParsedCard],
    deck_cache: dict[tuple[str, int, int, int], tuple[ParsedCard, ...] | None],
    stats: ChoiceRowStats,
    min_n_games_to_judge: int,
    min_win_rate: float,
) -> ChoiceRow | None:
    """Validate + decode one parquet record. Returns None when skipped.

    Skips for: format mismatch, missing context columns, out-of-range
    mulligan count, bad hand size, bad deck size, unknown card name,
    or player filtered out by skill. Counters on ``stats`` are bumped
    accordingly.
    """
    # Format filter first — cheapest signal.
    expansion = record.get("expansion")
    record_event_type = record.get("event_type")
    if expansion != set_code_upper or record_event_type != event_type:
        return None

    # ---- Required context columns -------------------------------------
    try:
        draft_id = record["draft_id"]
        build_index = record["build_index"]
        match_number = record["match_number"]
        game_number = record["game_number"]
        on_play = record["on_play"]
        num_mulligans_in_game = record["num_mulligans_in_game"]
        opp_num_mulligans = record["opp_num_mulligans"]
        won = record["won"]
        mulligan_number = record["mulligan_number"]
        was_kept = record["was_kept"]
        hand_size = record["hand_size"]
        hand_str = record["hand"]
        deck_str = record["deck"]
    except KeyError:  # pragma: no cover — guarded above
        stats.skipped_missing_context += 1
        return None

    if (
        draft_id is None
        or build_index is None
        or match_number is None
        or game_number is None
        or on_play is None
        or num_mulligans_in_game is None
        or opp_num_mulligans is None
        or won is None
        or mulligan_number is None
        or was_kept is None
        or hand_size is None
        or hand_str is None
        or deck_str is None
    ):
        stats.skipped_missing_context += 1
        return None

    mulligan_number_int = int(mulligan_number)
    if mulligan_number_int < 0 or mulligan_number_int > MAX_MULLIGAN_NUMBER:
        stats.skipped_bad_mulligan += 1
        return None
    num_mulligans_in_game_int = int(num_mulligans_in_game)
    if num_mulligans_in_game_int < 0 or num_mulligans_in_game_int > MAX_MULLIGAN_NUMBER:
        stats.skipped_bad_mulligan += 1
        return None

    if int(hand_size) != EXPECTED_HAND_SIZE:
        stats.skipped_bad_hand_size += 1
        return None

    # ---- Player-skill filter -------------------------------------------
    user_n_games_raw = _coerce_optional_int(record.get("user_n_games_bucket"))
    user_wr_raw = _coerce_optional_float(record.get("user_game_win_rate_bucket"))
    if not should_keep_player(
        user_n_games_raw,
        user_wr_raw,
        min_n_games_to_judge=min_n_games_to_judge,
        min_win_rate=min_win_rate,
    ):
        stats.skipped_player_filter += 1
        return None

    # ---- Hand reconstruction -------------------------------------------
    hand_names = parse_hand_names(str(hand_str))
    if len(hand_names) != EXPECTED_HAND_SIZE:
        stats.skipped_bad_hand_size += 1
        return None
    hand_cards, missing_hand = _materialise_hand(hand_names, name_lookup=name_lookup)
    if missing_hand:
        stats.skipped_unknown_card += 1
        for name in missing_hand:
            stats.unknown_card_names[name] = stats.unknown_card_names.get(name, 0) + 1
        return None

    # ---- Deck reconstruction (cache by game id) ------------------------
    game_key = (str(draft_id), int(build_index), int(match_number), int(game_number))
    deck_cards_cached = deck_cache.get(game_key)
    if deck_cards_cached is None and game_key not in deck_cache:
        # First time we see this game — parse the deck string once.
        try:
            deck_entries = parse_deck_string(str(deck_str))
        except ValueError:
            stats.skipped_bad_deck_size += 1
            deck_cache[game_key] = None
            return None
        deck_cards, missing_deck = _materialise_cards_from_counts(
            deck_entries, name_lookup=name_lookup
        )
        if missing_deck:
            stats.skipped_unknown_card += 1
            for name in missing_deck:
                stats.unknown_card_names[name] = stats.unknown_card_names.get(name, 0) + 1
            deck_cache[game_key] = None
            return None
        deck_size = len(deck_cards)
        if deck_size < MIN_DECK_SIZE or deck_size > MAX_DECK_SIZE:
            stats.skipped_bad_deck_size += 1
            deck_cache[game_key] = None
            return None
        deck_cards_cached = tuple(deck_cards)
        deck_cache[game_key] = deck_cards_cached
    elif deck_cards_cached is None:
        # Previously failed for this game — skip without re-bumping counters.
        return None

    return ChoiceRow(
        hand=tuple(hand_cards),
        deck=deck_cards_cached,
        on_the_play=bool(on_play),
        mulligan_number=mulligan_number_int,
        was_kept=bool(was_kept),
        num_mulligans_in_game=num_mulligans_in_game_int,
        opp_mulligan_number=int(opp_num_mulligans),
        user_n_games_raw=user_n_games_raw,
        user_wr_raw=user_wr_raw,
        expansion=str(expansion),
        event_type=str(record_event_type),
        won=bool(won),
        draft_id=str(draft_id),
        build_index=int(build_index),
        match_number=int(match_number),
        game_number=int(game_number),
    )
