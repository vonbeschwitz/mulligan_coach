"""17Lands per-card aggregate stats — parquet → typed lookup.

The XGBoost model (``packages/model``, future) joins against per-card
17Lands data — GIH WR (game-in-hand), OH WR (opening-hand), ALSA
(average last seen at), play rate, etc. — to compute hand-level
features. This module reads the parquet that the ``data-download``
package writes to
``data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet`` and
returns one ``SeventeenLandsStats`` per card.

Design choices:

* **Raw fields only.** Field names match the 17Lands JSON schema
  one-for-one. Derived features (earliness, sample-shrunk WRs, z-scores)
  belong in the model package, not here — keeps the cards package's
  scope on "represent the cards", not "engineer the model's features".
* **Premier Draft only (v1).** Other event types (Sealed / TradDraft /
  TradSealed) are downloaded and on disk, but the project's primary
  scope is Premier Draft. Adding a Sealed loader later is a one-line
  parameter change.
* **``StatsLookup`` with three-tier fallback.** The loader returns a
  ``StatsLookup`` that exposes both ``by_arena_id`` and ``by_name`` and
  a ``match(card)`` helper. ``match`` tries ``arena_id`` first, then
  exact name, then the front-face name (split on ``" // "``). DFCs
  whose ``ParsedCard.name`` is the joint ``Front // Back`` form still
  join via the front-face fallback even before MTGJSON has populated
  ``arena_id`` for the new set.
* **No memoization.** Each parquet is small (~340 rows / ~100 KB) and
  loads in single-digit milliseconds. Callers that want caching can
  wrap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from .models import ParsedCard


def _data_root() -> Path:
    """Resolve the project's ``data/`` directory.

    Mirrors ``loader.py:_data_root`` (deliberately duplicated rather than
    importing a private — keeps this module standalone, in line with the
    "packages agree on on-disk layout, don't import each other" convention
    used between cards and data-download).
    """
    override = os.environ.get("MULLIGAN_COACH_DATA_ROOT")
    if override:
        return Path(override).resolve()
    # packages/cards/src/mulligan_coach_cards/seventeenlands_stats.py
    #   -> mulligan_coach_cards -> src -> cards -> packages -> <repo>
    return Path(__file__).resolve().parents[4] / "data"


def ratings_parquet_path(
    set_code: str,
    event_type: str = "PremierDraft",
    data_root: Path | None = None,
) -> Path:
    """Return the canonical path for a 17Lands ratings parquet.

    The data-download package writes one parquet per (set, event_type)
    pair at ``data/processed/seventeenlands/ratings/<SET>/<EVENT>.parquet``.
    Set codes are case-normalised to upper to match how data-download
    writes them.
    """
    root = data_root or _data_root()
    return (
        root
        / "processed"
        / "seventeenlands"
        / "ratings"
        / set_code.upper()
        / f"{event_type}.parquet"
    )


class SeventeenLandsStats(BaseModel):
    """One row of 17Lands aggregate per-card statistics for a single format.

    Field names mirror the 17Lands JSON schema directly — no renaming —
    so reading raw output (curl, parquet inspectors, the 17Lands website)
    lines up with what you see here. The two format-marking columns
    (``expansion`` and ``event_type``) are injected by the data-download
    package's parquet conversion step; they identify which (set,
    event-type) this row came from.

    Win-rate, ALSA, ATA, and play-rate fields are ``float | None``
    because 17Lands returns ``null`` for cards with too few observations
    to publish a meaningful number (the threshold varies by metric).
    Game-count fields are always populated (counts default to 0).

    Pydantic ignores any extra columns in the parquet by default, so a
    future 17Lands schema addition won't break loading. If a new field
    becomes important, add it explicitly here.
    """

    # ---- Identity ---------------------------------------------------------
    name: str
    mtga_id: int = Field(description="MTGA card ID — useful for log-parsing joins later.")
    expansion: str = Field(
        description="Set code (e.g. 'TLA'). Injected by data-download's conversion step."
    )
    event_type: str = Field(
        description="Event type (e.g. 'PremierDraft'). Injected by data-download."
    )
    color: str = Field(description="WUBRG combination as a string ('', 'W', 'WU', etc.).")
    rarity: str
    types: list[str] = Field(
        default_factory=list, description="Card type lines as published by 17Lands."
    )
    layout: str

    # ---- Pick-rate / draft signal ----------------------------------------
    seen_count: int
    avg_seen: float | None = Field(
        default=None, description="ALSA — average position when last seen in pack."
    )
    pick_count: int
    avg_pick: float | None = Field(
        default=None, description="ATA — average pick number when taken."
    )

    # ---- Inclusion / play-rate ------------------------------------------
    # Both rates can be null in real data — TLA, the newest set in the
    # current rotation, has ~60-85 nulls per float column out of 342 rows.
    game_count: int
    pool_count: int
    play_rate: float | None = Field(
        default=None,
        description="%GP — fraction of decks (with this card in pool) that include it.",
    )
    win_rate: float | None = Field(
        default=None, description="GP WR — win rate when the card is in the deck."
    )

    # ---- Per-state win rates (None for low-N cards) ---------------------
    opening_hand_game_count: int
    opening_hand_win_rate: float | None = Field(
        default=None, description="OH WR — win rate when the card is in the opening hand."
    )
    drawn_game_count: int
    drawn_win_rate: float | None = Field(
        default=None,
        description="GD WR — win rate when drawn after turn 1 (excludes the opening hand).",
    )
    ever_drawn_game_count: int
    ever_drawn_win_rate: float | None = Field(
        default=None,
        description=(
            "GIH WR — win rate in any game where the card was in hand at any point. "
            "The headline per-card 17Lands metric."
        ),
    )
    never_drawn_game_count: int
    never_drawn_win_rate: float | None = Field(
        default=None,
        description="GND WR — win rate in games where the card was in deck but never drawn.",
    )
    drawn_improvement_win_rate: float | None = Field(
        default=None, description="IWD = GIH WR - GND WR; how much drawing the card helps."
    )


@dataclass(frozen=True)
class StatsLookup:
    """Per-card 17Lands stats with multiple lookup paths.

    Built by :func:`load_premier_draft_stats` for one (set, event_type)
    pair. Exposes both arena_id and name indices so consumers can pick
    the right one for their context, and a ``match(card)`` helper that
    runs a three-tier fallback against a ``ParsedCard``:

    1. ``by_arena_id[card.arena_id]`` if both sides have it.
    2. ``by_name[card.name]`` for exact name match.
    3. ``by_name[front_face]`` where ``front_face`` is
       ``card.name.split(" // ", 1)[0]``. Catches DFCs whose
       ``ParsedCard.name`` is the joint ``Front // Back`` form while
       17Lands uses the front face only.

    The dataclass is frozen so the lookup itself is read-only, but the
    underlying dicts aren't deep-copied — don't mutate them.
    """

    by_arena_id: dict[int, SeventeenLandsStats]
    by_name: dict[str, SeventeenLandsStats]

    def match(self, card: ParsedCard) -> SeventeenLandsStats | None:
        """Return the stats row that best matches ``card``, or None."""
        # Tier 1: arena_id (the canonical join when MTGJSON has it).
        if card.arena_id is not None:
            hit = self.by_arena_id.get(card.arena_id)
            if hit is not None:
                return hit
        # Tier 2: exact name. Catches everything not covered by tier 1
        # for older sets where MTGJSON has arena_id, and everything in
        # newer sets too — except DFCs.
        hit = self.by_name.get(card.name)
        if hit is not None:
            return hit
        # Tier 3: front-face name. ParsedCard preserves the joint
        # "Front // Back" name for transform DFCs; 17Lands uses the
        # front face only. This fallback retires automatically once
        # MTGJSON populates arena_id for the new set.
        front_face = card.name.split(" // ", 1)[0]
        if front_face != card.name:
            return self.by_name.get(front_face)
        return None


def load_premier_draft_stats(set_code: str, *, data_root: Path | None = None) -> StatsLookup:
    """Load all PremierDraft 17Lands stats for ``set_code``.

    Returns a :class:`StatsLookup` indexed by both ``mtga_id`` and card
    name. Use ``.match(card)`` for the three-tier join semantics, or
    ``.by_arena_id`` / ``.by_name`` directly for ad-hoc inspection.

    Raises ``FileNotFoundError`` if the parquet doesn't exist, with a
    message pointing at the data-download CLI.
    """
    path = ratings_parquet_path(set_code, "PremierDraft", data_root)
    if not path.exists():
        raise FileNotFoundError(
            f"17Lands ratings parquet not found at {path}. "
            f"Run 'mulligan-coach-data refresh-17lands --sets {set_code.upper()}' first."
        )

    # DuckDB reads the parquet, normalises list / nullable columns, and
    # tolerates schema drift across sets in case 17Lands adds a column
    # for a later set. We materialise to a list of dicts and let pydantic
    # validate / coerce. The path is a repository-managed file, not user
    # input, but we still single-quote-escape it to mirror data-download's
    # _sql_str pattern and stay safe against unusual install paths.
    con = duckdb.connect(":memory:")
    try:
        path_literal = "'" + path.as_posix().replace("'", "''") + "'"
        rows: list[dict[str, Any]] = (
            con.execute(f"SELECT * FROM read_parquet({path_literal})").to_arrow_table().to_pylist()
        )
    finally:
        con.close()

    by_arena_id: dict[int, SeventeenLandsStats] = {}
    by_name: dict[str, SeventeenLandsStats] = {}
    for row in rows:
        stats = SeventeenLandsStats.model_validate(row)
        # First-write-wins on duplicates within a set — shouldn't happen
        # in practice but we don't want a surprise here.
        by_arena_id.setdefault(stats.mtga_id, stats)
        by_name.setdefault(stats.name, stats)
    return StatsLookup(by_arena_id=by_arena_id, by_name=by_name)
