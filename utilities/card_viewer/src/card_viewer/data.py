"""In-memory card store used by the viewer.

The whole point of this utility is to look at one card at a time and check
the encoding looks right, so we keep it dead simple: at server startup we
parse every card in the configured sets once, hold them in a list, and
build a small lookup index by (set_code, collector_number).

Each entry pairs the parsed encoding with the original Scryfall dict —
templates need both: the ``ParsedCard`` for the JSON view and the chip
summary, the raw dict for ``image_uris`` (which the cards package
intentionally doesn't carry into ``ParsedCard``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from mulligan_coach_cards import ParsedCard, parse_card
from mulligan_coach_cards.loader import filter_cards, load_all_cards


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
        """Load and parse every card in the given sets.

        The Scryfall oracle dump is large (~37k entries / ~170 MiB) so we
        read it once, then filter per set. Parsing is deterministic and
        cheap — well under a second for the ~740 cards across the three
        current Premier-Draft sets.
        """
        raw_all = load_all_cards()
        wanted: list[dict[str, Any]] = []
        for set_code in set_codes:
            wanted.extend(filter_cards(raw_all, set_code=set_code))

        entries = [CardEntry(parsed=parse_card(d), raw=d) for d in wanted]

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
