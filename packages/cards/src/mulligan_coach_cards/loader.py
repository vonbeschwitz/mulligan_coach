"""Scryfall oracle-cards loader.

Reads the JSON snapshots that the ``data-download`` package writes to
``<data_root>/raw/scryfall/oracle_cards.<date>.json``. We always pick the
newest snapshot (highest filename suffix); old ones are kept on disk only
for the user's convenience.

The file is large (~170 MiB / ~37k cards) but loads fast because it's a
single JSON array. Loading once per CLI invocation is fine — we don't try
to stream or memoize at this stage.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Layouts the deterministic parser is willing to *consider*. Anything else
# (token, art_series, double_faced_token, …) gets filtered out before we
# even hand cards to the parser, so the demo report isn't padded with rows
# that aren't real game pieces.
RELEVANT_LAYOUTS: frozenset[str] = frozenset(
    {
        "normal",
        "split",
        "transform",
        "modal_dfc",
        "adventure",
        "saga",
        "leveler",
        "case",
        "class",
        "flip",
        "meld",
        "prototype",
        # SOS introduced "prepare" — a creature face stapled to a spell
        # face that you may cast as a copy while the creature is prepared.
        # The parser bails to NEEDS_LLM for this layout (handled by hand).
        "prepare",
    }
)


def _data_root() -> Path:
    """Resolve the project's ``data/`` directory.

    Mirrors the data-download package's ``paths.data_root`` without taking a
    runtime dependency on it (per the project layout guide, cross-package
    imports are minimised — both packages just agree on the on-disk layout).
    Honors ``MULLIGAN_COACH_DATA_ROOT`` for tests and ad-hoc overrides.
    """
    override = os.environ.get("MULLIGAN_COACH_DATA_ROOT")
    if override:
        return Path(override).resolve()
    # packages/cards/src/mulligan_coach_cards/loader.py
    #   -> mulligan_coach_cards -> src -> cards -> packages -> <repo>
    return Path(__file__).resolve().parents[4] / "data"


def latest_oracle_cards_path(data_root: Path | None = None) -> Path:
    """Return the path to the most recent Scryfall oracle-cards snapshot."""
    raw_dir = (data_root or _data_root()) / "raw" / "scryfall"
    if not raw_dir.exists():
        raise FileNotFoundError(
            f"Scryfall raw dir not found at {raw_dir}. Run 'mulligan-coach-data refresh-scryfall' first."
        )
    candidates = sorted(raw_dir.glob("oracle_cards.*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No oracle_cards JSON in {raw_dir}. Run 'mulligan-coach-data refresh-scryfall' first."
        )
    # Filenames embed an ISO-formatted date so lexical max == newest.
    return candidates[-1]


def load_all_cards(data_root: Path | None = None) -> list[dict[str, Any]]:
    """Load every card from the latest Scryfall snapshot."""
    path = latest_oracle_cards_path(data_root)
    with path.open(encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected list at top of {path}, got {type(data).__name__}")
    return data


def load_arena_id_index(
    data_root: Path | None = None,
) -> dict[tuple[str, str], int]:
    """Build a ``(scryfall_oracle_id, set_code) -> mtga_id`` index from MTGJSON.

    Reads ``<data_root>/raw/mtgjson/AllIdentifiers.json`` (downloaded by
    the data-download package) and joins ``identifiers.scryfallOracleId``
    to ``int(identifiers.mtgArenaId)``. Records without one of those two
    fields are skipped — typical reasons:

    * The card has never been printed on Arena (paper-only / digital-only
      products): no ``mtgArenaId``.
    * Tokens / art-series / non-game records: no ``scryfallOracleId``.
    * Very new sets where MTGJSON hasn't yet ingested Arena IDs (TLA today
      is in this state): all entries skipped, the index returns no rows
      for that ``set_code``.

    The (oracle_id, set_code) compound key avoids collisions when a card
    is reprinted across sets — Arena assigns a fresh ``mtga_id`` per
    printing, and we want the printing matching the format we're parsing.

    First-write-wins on collisions within a (oracle_id, set_code) pair
    (different art treatments of the same card share both keys but get
    distinct mtga_ids in Arena). 17Lands' aggregate stats collapse art
    treatments, and the loader's name-based fallback backstops cases
    where the chosen mtga_id doesn't match 17Lands' canonical pick.

    Loading is one-shot — call once per CLI invocation and pass the
    resulting dict to :func:`mulligan_coach_cards.parser.parse_card`.
    The file is large (~120 MiB) so building the index takes a few
    seconds; doing it per-card would be unworkable.
    """
    path = (data_root or _data_root()) / "raw" / "mtgjson" / "AllIdentifiers.json"
    if not path.exists():
        raise FileNotFoundError(
            f"MTGJSON AllIdentifiers not found at {path}. "
            f"Run 'mulligan-coach-data refresh-mtgjson' first."
        )
    with path.open(encoding="utf-8") as f:
        raw: Any = json.load(f)
    # Top-level shape is ``{"meta": ..., "data": {uuid: record, ...}}``.
    cards = raw.get("data", raw) if isinstance(raw, dict) else raw
    if not isinstance(cards, dict):
        raise RuntimeError(
            f"Unexpected MTGJSON shape at {path}: top-level 'data' is "
            f"{type(cards).__name__}, expected dict"
        )

    index: dict[tuple[str, str], int] = {}
    for record in cards.values():
        if not isinstance(record, dict):
            continue
        idents = record.get("identifiers") or {}
        oracle_id = idents.get("scryfallOracleId")
        mtga_id_raw = idents.get("mtgArenaId")
        set_code = record.get("setCode")
        if not (oracle_id and mtga_id_raw and set_code):
            continue
        try:
            mtga_id = int(mtga_id_raw)
        except (TypeError, ValueError):
            continue
        key = (str(oracle_id), str(set_code).upper())
        # setdefault preserves the first-write-wins semantic. The dict
        # iteration order is whatever MTGJSON wrote, but for our purposes
        # any consistent winner is fine — see docstring caveat.
        index.setdefault(key, mtga_id)
    return index


def filter_cards(
    cards: Iterable[dict[str, Any]],
    *,
    set_code: str | None = None,
    lang: str = "en",
) -> list[dict[str, Any]]:
    """Filter a card list by set code and language.

    Set code matching is case-insensitive. Layout filtering keeps only
    "real" gameplay layouts (see ``RELEVANT_LAYOUTS``).
    """
    code = set_code.upper() if set_code else None
    out: list[dict[str, Any]] = []
    for card in cards:
        if card.get("lang", "en") != lang:
            continue
        if str(card.get("layout", "normal")) not in RELEVANT_LAYOUTS:
            continue
        if code is not None and str(card.get("set", "")).upper() != code:
            continue
        out.append(card)
    return out
