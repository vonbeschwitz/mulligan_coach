"""Pre-simulation deck validation.

Runs once before any Monte Carlo iteration. The simulator's policies
assume that every card has its mechanical encoding (modes / costs /
effects) populated by the cards package — silently treating an
incompletely-encoded card as "vanilla" would corrupt downstream
training data in ways that are hard to spot.

So we hard-fail at the boundary instead.
"""

from __future__ import annotations

from collections.abc import Iterable

from mulligan_coach_cards import ParsedCard, ParseStatus


class DeckEncodingError(Exception):
    """Raised by :func:`check_deck_encodings` when one or more cards in the
    supplied deck lack the encoding the simulator needs."""


def check_deck_encodings(cards: Iterable[ParsedCard]) -> None:
    """Verify every card in *cards* is safe to simulate.

    Hard-fails (raises :class:`DeckEncodingError`) if any card:

    * has ``status`` of ``NEEDS_LLM`` or ``NEEDS_HUMAN`` — the parser
      bailed and no LLM has hand-encoded the modes yet, so we don't
      know what the card actually does;
    * has a non-``None`` ``mana_cost`` but an empty ``modes`` list —
      a castable card with no encoded way to play it.

    Cards with ``status`` of ``AUTO`` or ``LLM_ENCODED`` pass.

    The error message lists every offending card so the caller can fix
    them in one batch rather than discovering them one-at-a-time.
    """
    issues: list[str] = []
    for c in cards:
        if c.status in (ParseStatus.NEEDS_LLM, ParseStatus.NEEDS_HUMAN):
            issues.append(
                f"  - {c.name!r} ({c.set_code} #{c.collector_number}): "
                f"status={c.status.value}; reasons={c.reasons!r}"
            )
            continue
        if c.mana_cost is not None and not c.modes:
            issues.append(
                f"  - {c.name!r} ({c.set_code} #{c.collector_number}): "
                f"has mana_cost {c.mana_cost.raw!r} but modes is empty"
            )

    if issues:
        raise DeckEncodingError(
            f"Cannot simulate this deck — {len(issues)} card(s) lack the "
            "mechanical encoding the simulator needs:\n"
            + "\n".join(issues)
            + "\n\nFix by re-running `mulligan-coach-cards run-detector` "
            "after either extending the parser or hand-encoding the "
            "affected cards."
        )
