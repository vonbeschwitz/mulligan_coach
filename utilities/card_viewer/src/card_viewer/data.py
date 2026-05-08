"""In-memory card store used by the viewer.

The whole point of this utility is to look at one card at a time and check
the encoding looks right. At startup we collect every card in the
configured sets and pair its source Scryfall dict with the best available
``ParsedCard`` representation:

* If the persistent store at ``data/processed/parsed_cards/<SET>.json``
  contains an entry for the card, that entry is used verbatim. This is
  what makes the viewer surface ``llm_encoded`` and ``needs_human``
  cards correctly — re-parsing them would lose the hand-encoded fields.
* Otherwise the deterministic parser is invoked on the raw Scryfall
  dict, just like before.

Templates need both halves of each entry: the ``ParsedCard`` for the JSON
view and chip summary, the raw dict for ``image_uris`` (which the cards
package intentionally doesn't carry into ``ParsedCard``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mulligan_coach_cards import ParsedCard, parse_card
from mulligan_coach_cards.loader import filter_cards, load_all_cards
from mulligan_coach_cards.store import load_parsed_cards


@dataclass(frozen=True)
class CardEntry:
    """One card's parsed encoding plus its source Scryfall dict.

    The raw dict is kept so templates can read ``image_uris`` without an
    extra Scryfall API round-trip — the whole oracle_cards JSON already
    has the CDN URLs in it.
    """

    parsed: ParsedCard
    raw: dict[str, Any]


@dataclass(frozen=True)
class CardStore:
    """All loaded cards, plus an index for direct lookup."""

    entries: list[CardEntry]
    by_key: dict[tuple[str, str], CardEntry]

    @classmethod
    def build(cls, set_codes: Iterable[str]) -> CardStore:
        """Load every card in the given sets, preferring the persistent store.

        The Scryfall oracle dump is large (~37k entries / ~170 MiB) so we
        read it once, then filter per set. For each card we look up the
        persisted ``ParsedCard`` (written by ``mulligan-coach-cards
        run-detector``) by oracle id; if present, we use it as-is so
        ``llm_encoded`` and ``needs_human`` cards display correctly.
        Otherwise we fall back to a fresh deterministic parse — keeping
        the viewer useful on a clean checkout where the store hasn't
        been populated yet.
        """
        raw_all = load_all_cards()
        entries: list[CardEntry] = []
        for set_code in set_codes:
            raw_for_set = filter_cards(raw_all, set_code=set_code)
            stored_by_oracle = _stored_index(set_code)
            for raw in raw_for_set:
                oracle_id = str(raw.get("oracle_id", ""))
                parsed = stored_by_oracle.get(oracle_id) or parse_card(raw)
                entries.append(CardEntry(parsed=parsed, raw=raw))

        # Cards within a set are uniquely identified by their collector
        # number; we uppercase the set code so URL routes are case-insensitive.
        by_key = {
            (entry.parsed.set_code.upper(), entry.parsed.collector_number): entry
            for entry in entries
        }

        return cls(entries=entries, by_key=by_key)

    def get(self, set_code: str, collector_number: str) -> CardEntry | None:
        """Return the entry for one card, or ``None`` if it isn't loaded."""
        return self.by_key.get((set_code.upper(), collector_number))


def _stored_index(set_code: str) -> dict[str, ParsedCard]:
    """Index the persistent store for a set by oracle_id.

    Returns an empty dict if the per-set file is missing or malformed —
    the caller falls back to fresh parsing for those cards.
    """
    try:
        stored = load_parsed_cards(set_code)
    except Exception:
        return {}
    return {card.oracle_id: card for card in stored}


def image_url(entry: CardEntry, size: str = "small") -> str | None:
    """Best-effort image URL for a Scryfall card dict.

    Most cards expose ``image_uris`` directly. Double-faced and split
    cards put the URLs under ``card_faces[i].image_uris`` instead — even
    though ``filter_cards`` already drops the layouts the parser refuses
    to handle, we still guard for it so a future loosening of that filter
    doesn't crash the viewer.
    """
    raw = entry.raw
    direct = raw.get("image_uris")
    if direct and isinstance(direct, dict) and direct.get(size):
        return str(direct[size])

    faces = raw.get("card_faces")
    if faces and isinstance(faces, list):
        face = faces[0]
        if isinstance(face, dict):
            face_uris = face.get("image_uris")
            if face_uris and isinstance(face_uris, dict) and face_uris.get(size):
                return str(face_uris[size])

    return None
