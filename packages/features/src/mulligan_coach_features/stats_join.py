"""Card-name join for 17Lands per-card stats.

The 17Lands stats join used to key on ``ParsedCard.arena_id`` (the
MTGA/mtga_id). That key is populated from MTGJSON, which lags a
freshly-rotated format by weeks — so every main-set card of a new set
had ``arena_id=None`` and the per-card WR / z-score features silently
fell to zero. Worse, the two inference surfaces disagreed: the overlay
backfills ``arena_id`` from Arena's own SQLite (so it fed a fully
populated distribution to a model trained on the mostly-zero one),
while the website matched the training distribution.

Keying the join on the **card name** instead makes the stats lookup a
pure function of ``(card name, ratings parquet)`` — independent of
MTGJSON lag, independent of the overlay's Arena-DB backfill, and
identical across training materialisation, website, and overlay.

Two helpers:

* :func:`fold_card_name` — NFKD-normalise and drop combining marks, so
  17Lands' diacritic-free spelling ("Bespoke Bo") matches the parsed
  card's ("Bespoke Bō"). Pure-ASCII names are returned unchanged.
* :func:`stats_for_card` — folded-name lookup into a name-keyed table
  with a DFC front-face fallback (``ParsedCard.name`` for a DFC is the
  joint ``"Front // Back"`` form, but 17Lands uses the front face).
  Generic over the value type so both the ``ShrunkWinRates`` and the
  ``CardZScores`` tables use the same lookup.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from mulligan_coach_cards import ParsedCard


def fold_card_name(name: str) -> str:
    """Return *name* with diacritics folded away.

    NFKD-normalises the string and drops every combining character
    (``unicodedata.combining(ch) != 0``). Pure-ASCII names are
    unchanged; "Bespoke Bō" folds to "Bespoke Bo", matching 17Lands'
    macron-free spelling.
    """
    normalized = unicodedata.normalize("NFKD", name)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def stats_for_card[T](card: ParsedCard, table: Mapping[str, T]) -> T | None:
    """Folded-name lookup with a DFC front-face fallback.

    Looks *card* up in a name-keyed *table* (keyed by
    :func:`fold_card_name` of the 17Lands display name). Returns the
    matching value, or ``None`` when the card has no ratings row.

    The fallback handles transform DFCs: ``ParsedCard.name`` is the
    joint ``"Front // Back"`` form, whereas 17Lands names the front
    face only — so a miss on the joint name retries on the front face.
    """
    hit = table.get(fold_card_name(card.name))
    if hit is not None:
        return hit
    front = card.name.split(" // ", 1)[0]
    if front != card.name:
        return table.get(fold_card_name(front))
    return None
