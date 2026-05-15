"""Hand-state helpers — pure functions over ``hand_ids``.

No server-side session: every HTMX request POSTs the entire form,
including a ``hand_ids`` list of opaque strings of the form
``"<SET>:<COLLECTOR>"`` (or ``"BASIC:<basic name>"`` for synthesised
basics). These helpers turn that flat list into the ``ParsedCard``
lists the simulator wants, and produce updates for the random / add /
remove / clear buttons.

All functions are pure: they take the parsed decklist + current
hand ids and return a new hand-id list (plus any user-facing error).
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass

from mulligan_coach_cards import ParsedCard

from .decklist import DeckCard, ParseResult, expand_deck

HAND_SIZE = 7


def card_to_id(card: ParsedCard) -> str:
    """Stable identifier for one ``ParsedCard`` printing.

    ``set_code:collector_number`` — that's what MTGA exports embed,
    and it survives reorders within the form (hand entries are plain
    hidden inputs, not addressed by index).
    """
    return f"{card.set_code.upper()}:{card.collector_number}"


@dataclass(frozen=True)
class HandEntry:
    """One slot of the current hand, in form order.

    ``card`` may be ``None`` when an id no longer matches the deck
    (e.g. the user edited the decklist after building the hand). The
    template renders such slots as broken chips so the user sees a
    concrete reason rather than a silent crash.
    """

    index: int
    id: str
    card: ParsedCard | None


# ---------------------------------------------------------------------------
# Resolving hand_ids -> (hand, library)
# ---------------------------------------------------------------------------


def resolve_hand(
    result: ParseResult,
    hand_ids: list[str],
) -> tuple[list[HandEntry], list[ParsedCard], list[str]]:
    """Map ``hand_ids`` onto deck copies; build the library from what's left.

    Returns a triple:

    1. ``entries`` — one :class:`HandEntry` per slot, in form order,
       so the template can render the grid without re-sorting and
       the remove buttons stay aligned with their indices.
    2. ``library`` — every deck copy not consumed by the hand. This
       is what feeds the simulator.
    3. ``errors`` — user-facing strings for any hand id that didn't
       match a deck copy (typo, decklist edited after hand was built,
       etc.). The caller decides whether to surface them inline or
       block ``/recommend``.
    """
    deck_copies: list[ParsedCard] = expand_deck(result.cards)

    # Group remaining copies by id; pop from the end as we resolve
    # each hand slot.
    remaining: dict[str, list[ParsedCard]] = {}
    for copy in deck_copies:
        remaining.setdefault(card_to_id(copy), []).append(copy)

    entries: list[HandEntry] = []
    errors: list[str] = []
    for index, hand_id in enumerate(hand_ids):
        bucket = remaining.get(hand_id)
        if bucket:
            card = bucket.pop()
            entries.append(HandEntry(index=index, id=hand_id, card=card))
        else:
            entries.append(HandEntry(index=index, id=hand_id, card=None))
            errors.append(
                f"Hand slot {index + 1} ({hand_id}) has no matching copy left "
                "in the deck. Re-paste the decklist or rebuild the hand."
            )

    library: list[ParsedCard] = []
    for bucket in remaining.values():
        library.extend(bucket)

    return entries, library, errors


# ---------------------------------------------------------------------------
# Hand mutations (random / add / remove / clear)
# ---------------------------------------------------------------------------


def random_hand(
    result: ParseResult,
    *,
    rng: random.Random | None = None,
    size: int = HAND_SIZE,
) -> list[str]:
    """Return ``size`` hand_ids sampled without replacement from the deck.

    Raises :class:`ValueError` if the deck has fewer than ``size``
    copies — caller surfaces this to the user inline.
    """
    rng = rng or random.Random()
    deck = expand_deck(result.cards)
    if len(deck) < size:
        raise ValueError(f"Deck has only {len(deck)} cards; need at least {size} for a hand.")
    sample = rng.sample(deck, size)
    return [card_to_id(c) for c in sample]


def add_card_by_name(
    result: ParseResult,
    hand_ids: list[str],
    name: str,
) -> tuple[list[str], str | None]:
    """Append one matching copy of *name* to the hand.

    Returns ``(new_hand_ids, error)``. ``error`` is ``None`` on
    success, a user-facing message on failure (name not in deck, or
    no copies remain after the existing hand is subtracted). Matching
    is case-insensitive on the printed card name; the first deck
    entry with a matching name wins on collision.
    """
    if len(hand_ids) >= HAND_SIZE:
        return hand_ids, f"Hand already has {HAND_SIZE} cards."

    target = _find_deck_card_by_name(result.cards, name)
    if target is None:
        return hand_ids, f"No card named {name!r} in the deck."

    target_id = card_to_id(target.card)
    in_deck = _expanded_count(result.cards, target_id)
    in_hand = sum(1 for h in hand_ids if h == target_id)
    if in_hand >= in_deck:
        return hand_ids, (f"All {in_deck} copies of {target.card.name!r} are already in the hand.")

    return [*hand_ids, target_id], None


def remove_at(hand_ids: list[str], index: int) -> list[str]:
    """Drop the entry at *index*; out-of-range indices are silently ignored.

    Silently because an out-of-range index almost always means a
    stale button click (the user clicked twice / hit back), not a
    bug worth surfacing.
    """
    if index < 0 or index >= len(hand_ids):
        return hand_ids
    return [h for i, h in enumerate(hand_ids) if i != index]


# ---------------------------------------------------------------------------
# Internal lookups
# ---------------------------------------------------------------------------


def _find_deck_card_by_name(cards: list[DeckCard], name: str) -> DeckCard | None:
    """First ``DeckCard`` whose printed name matches *name* (case-insensitive)."""
    needle = name.strip().lower()
    if not needle:
        return None
    for entry in cards:
        if entry.card.name.lower() == needle:
            return entry
        # Front-face fallback for DFCs, mirroring data.CardStore._name_keys.
        if " // " in entry.card.name:
            front = entry.card.name.partition(" // ")[0].strip()
            if front.lower() == needle:
                return entry
    return None


def _expanded_count(cards: list[DeckCard], target_id: str) -> int:
    """How many copies of *target_id* the deck contains in total."""
    counts = Counter(card_to_id(c.card) for c in cards for _ in range(c.count))
    return counts.get(target_id, 0)
