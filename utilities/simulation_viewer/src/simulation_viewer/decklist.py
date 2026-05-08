"""MTGA decklist parser and validator.

Pure function ``parse_mtga_decklist(text, store)`` produces a
:class:`ParseResult` carrying:

* The successfully resolved ``DeckCard`` entries (each with a count
  and the corresponding ``ParsedCard``).
* A list of :class:`ParseIssue` for lines we couldn't parse, couldn't
  resolve, or whose card lacks the encoding the simulator needs.

The viewer surfaces both halves so the user sees one coherent
report — "40 cards, 18 unique" plus, separately, "these 2 lines
couldn't be resolved". We deliberately don't raise
:class:`DeckEncodingError` from here: ``simulate`` itself will hard-fail
if a bad deck reaches it, but the validation panel needs to render
even when the deck has problems.

Format reference (an MTGA "Export to Clipboard" decklist):

```
Deck
4 Lightning Bolt (M21) 162
20 Mountain (M21) 268

Sideboard
1 Disenchant (M21) 18
```

Premier Draft Limited decks have no sideboard; Sealed pools may. We
always stop at ``Sideboard`` / ``Commander`` / ``Companion`` so those
zones don't end up shuffled into the goldfishing library.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from mulligan_coach_cards import ParsedCard, ParseStatus
from pydantic import BaseModel, Field

from .data import CardStore

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class DeckCard(BaseModel):
    """One line of the resolved deck.

    Stores ``count`` separately from the ``ParsedCard`` so the
    decklist round-trips cleanly even for multi-copy entries — we never
    duplicate ``ParsedCard`` instances at this layer (only when the
    caller calls :func:`expand_deck`).
    """

    count: int = Field(ge=1)
    card: ParsedCard
    raw_line: str


class ParseIssue(BaseModel):
    """Why one line of the pasted decklist didn't make it into the deck.

    ``reason`` is a short machine-friendly tag the template can group by;
    ``message`` is the user-facing explanation.
    """

    line_no: int
    raw_line: str
    reason: str
    message: str


class ParseResult(BaseModel):
    """Outcome of parsing a pasted decklist.

    The deck is "valid for simulation" iff ``unknown_lines`` is empty.
    The viewer's validation panel shows the success block when that's
    true, and the per-line issue list otherwise. Both halves are
    populated whenever they apply — a deck with one bad line still
    surfaces the 39 good ones in ``cards``.
    """

    cards: list[DeckCard] = Field(default_factory=list)
    unknown_lines: list[ParseIssue] = Field(default_factory=list)

    @property
    def total_cards(self) -> int:
        return sum(c.count for c in self.cards)

    @property
    def unique_cards(self) -> int:
        return len(self.cards)

    @property
    def is_valid(self) -> bool:
        return not self.unknown_lines and bool(self.cards)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Section headers we silently consume but otherwise ignore. ``About`` is
# MTGA's metadata block (deck name etc.); ``Deck`` / ``Maindeck`` mark
# the start of the main deck.
_SKIP_HEADERS = {"deck", "maindeck", "main deck", "about"}

# Section headers that mean "stop parsing": anything past these isn't
# part of the goldfishing library. Match exactly (case-insensitive) —
# we don't want to accidentally drop a card whose name happens to
# contain one of these words.
_STOP_HEADERS = {"sideboard", "commander", "companion"}

# ``<count> <name> [(<SET>) <COLLECTOR>]``. ``.+?`` non-greedy so the
# optional trailing group sticks to the suffix when present, but the
# whole match still works for plain ``<count> <name>`` lines (no
# parenthesised set/number) by letting the optional group be absent.
_LINE_RE = re.compile(r"^(\d{1,3})\s+(.+?)(?:\s+\(([A-Za-z0-9]+)\)\s+([\w-]+))?\s*$")

# Sanity cap so a pasted novel doesn't shuffle a million cards.
_MAX_TOTAL_COPIES = 250


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_mtga_decklist(text: str, store: CardStore) -> ParseResult:
    """Parse a pasted MTGA decklist against the loaded card store.

    Always returns a :class:`ParseResult`; never raises for user-facing
    errors (bad lines / missing cards / incomplete encodings). The
    caller's UI then decides how to render success vs. issues.

    Empty input returns an empty result. Total copies above
    :data:`_MAX_TOTAL_COPIES` get truncated with a single
    ``"deck too large"`` issue — we'd rather show *something* than
    block the user out of the page.
    """
    cards: list[DeckCard] = []
    issues: list[ParseIssue] = []
    total_copies = 0

    for line_no, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped:
            continue

        lower = stripped.lower()
        if lower in _SKIP_HEADERS:
            continue
        if lower in _STOP_HEADERS:
            break

        match = _LINE_RE.match(stripped)
        if not match:
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    raw_line=raw,
                    reason="bad_format",
                    message=(
                        "Couldn't parse line. Expected "
                        "'<count> <name>' or '<count> <name> (SET) NUMBER'."
                    ),
                )
            )
            continue

        count_str, name, set_code, collector = match.groups()
        count = int(count_str)
        if count < 1 or count > 99:
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    raw_line=raw,
                    reason="bad_count",
                    message=f"Quantity {count} is outside 1-99.",
                )
            )
            continue

        if total_copies + count > _MAX_TOTAL_COPIES:
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    raw_line=raw,
                    reason="too_large",
                    message=(
                        f"Stopping at {_MAX_TOTAL_COPIES} total copies. "
                        "Trim the decklist and try again."
                    ),
                )
            )
            break
        total_copies += count

        card = store.find(
            name=name.strip(),
            set_code=set_code,
            collector_number=collector,
        )
        if card is None:
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    raw_line=raw,
                    reason="not_found",
                    message=f"No encoded card named {name.strip()!r}.",
                )
            )
            continue

        encoding_problem = _encoding_problem(card)
        if encoding_problem is not None:
            issues.append(
                ParseIssue(
                    line_no=line_no,
                    raw_line=raw,
                    reason="encoding_incomplete",
                    message=encoding_problem,
                )
            )
            continue

        cards.append(DeckCard(count=count, card=card, raw_line=raw))

    return ParseResult(cards=cards, unknown_lines=issues)


def _encoding_problem(card: ParsedCard) -> str | None:
    """Re-implement ``check_deck_encodings``'s rules per-card.

    We can't call ``check_deck_encodings`` here because it raises a
    single combined exception over the whole deck — we want one issue
    per offending line so the user can fix them in batches. The two
    rules are intentionally kept in lock-step with
    ``packages/simulation/src/mulligan_coach_simulation/validate.py``;
    if that file grows a third rule, mirror it here.
    """
    if card.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
        return (
            f"Encoding incomplete (status={card.status.value}). "
            "Run mulligan-coach-cards run-detector or hand-encode this card."
        )
    if card.mana_cost is not None and not card.modes:
        return (
            f"Card has cost {card.mana_cost.raw!r} but no encoded play modes. "
            "Re-parse or hand-encode this card."
        )
    return None


# ---------------------------------------------------------------------------
# Helpers consumed by app.py / hand.py
# ---------------------------------------------------------------------------


def expand_deck(cards: Iterable[DeckCard]) -> list[ParsedCard]:
    """Flatten ``DeckCard(count=N, card=X)`` to ``[X, X, ..., X]`` (N times).

    The simulator works on lists of ``ParsedCard`` (one per copy in
    library / hand), so we expand only at the boundary where the
    decklist meets ``iter_traces``.
    """
    out: list[ParsedCard] = []
    for entry in cards:
        out.extend([entry.card] * entry.count)
    return out


def unique_card_names(result: ParseResult) -> list[str]:
    """Return the deck's card names in deck order, deduplicated.

    Used to populate the ``<datalist>`` for the manual-hand
    autocomplete.
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in result.cards:
        name = entry.card.name
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out
