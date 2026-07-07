"""Apply the MSH uncommons batch-3 (cards 61-107 alphabetically) audit fixes.

Final batch of the MSH uncommons encoding recheck (#61 Path to Exile
through #107 Yellowjacket): 44 clean, 3 fixed. Audit log:
``scripts/audit/MSH_commons_recheck.md``.

1. **Political Triumph (MSH #31)** — "Whenever a creature you control
   enters, scry 1 …; When the FOURTH plan counter is put on this
   enchantment, sacrifice it, draw a card, and put a +1/+1 counter on
   each creature." The parser credited ``cards_drawn=1`` from the
   4th-plan-counter payoff, but that draw is gated behind accumulating
   four plan counters (four creature-ETBs while the enchantment
   survives) — far outside the turn 1-4 window (§16/§19). Its three
   sibling "Plan" enchantments all correctly leave the payoff
   uncredited (Death to Our Enemies, Rewrite History, Robot Domination
   → ``is_other``). Cleared to match: ``cards_drawn=0`` → ``is_other``.

2. **Punishing Punch (MSH #180)** — "Target creature you control deals
   damage equal to twice its power to target creature an opponent
   controls." A one-sided fight (punch, §1), but the "twice its power"
   variable amount tripped the variable-damage matcher, landing it at
   ``is_other`` instead of ``is_punch_fight``. Set
   ``is_punch_fight=True`` (the Tenderize-style punch template, §1).

3. **Thirst for Knowledge (MSH #79)** — "Draw three cards. Then discard
   two cards unless you discard an artifact card." A loot, but the
   discard sits in a separate sentence with an "unless" clause, so the
   loot matcher missed it and recorded GROSS ``cards_drawn=3``. Per §2
   (the Thirst for Identity precedent: draw 3 / discard 2 → net 1),
   corrected to NET: ``cards_drawn=1``, ``cards_manipulated=3`` (gross),
   and ``DiscardCardEffect(n=2)`` wired onto the cast mode after the
   ``DrawCardsEffect(n=3)``. The "unless artifact" upside (net 2) needs
   an artifact in hand and isn't reliably available (§9), so the
   conservative discard-2 floor is used.

Idempotent via per-card fix markers.

Usage::

    uv run python scripts/audit/apply_msh_unc_batch3_fixes_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import DiscardCardEffect, DrawCardsEffect, ParseStatus

FIX_MARKER = "unc-batch3 fix 2026-07-07"


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}
    patched = 0

    # 1. Political Triumph — clear the plan-counter-payoff draw.
    c = by_name["Political Triumph"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.cards_drawn = 0
        c.role_features.is_other = True
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: cleared cards_drawn (the 'draw a card' fires only on the FOURTH "
            "plan counter — gated behind 4 creature-ETBs, outside the mulligan window per "
            "§16/§19); matches sibling Plans (Death to Our Enemies / Rewrite History / Robot "
            "Domination → is_other)."
        )
        patched += 1

    # 2. Punishing Punch — punch (one-sided fight), not is_other.
    c = by_name["Punishing Punch"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.is_punch_fight = True
        c.role_features.is_other = False
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: 'target creature you control deals damage equal to twice its power "
            "to target creature an opponent controls' is a punch (§1); the 'twice its power' "
            "variable amount tripped the variable-damage matcher into is_other."
        )
        patched += 1

    # 3. Thirst for Knowledge — net the loot (draw 3, discard 2).
    c = by_name["Thirst for Knowledge"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.cards_drawn = 1
        c.role_features.cards_manipulated = 3
        cast = next((m for m in c.modes if m.kind == "cast"), None)
        if cast is not None and not any(isinstance(e, DiscardCardEffect) for e in cast.effects):
            # Keep the existing DrawCardsEffect(3); append the discard so the
            # simulator sees the net-1 hand change (§2 loot encoding).
            assert any(isinstance(e, DrawCardsEffect) and e.n == 3 for e in cast.effects)
            cast.effects.append(DiscardCardEffect(n=2))
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: loot (draw 3, then discard 2 unless artifact) → NET cards_drawn=1, "
            "cards_manipulated=3 per §2 (Thirst for Identity precedent); DiscardCardEffect(2) "
            "wired onto the cast mode. Gross cards_drawn=3 was wrong (the discard sentence's "
            "'unless' clause slipped past the loot matcher)."
        )
        patched += 1

    save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {patched} card(s)")


if __name__ == "__main__":
    main()
