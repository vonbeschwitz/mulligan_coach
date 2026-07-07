"""Apply the MSH commons batch-4/5 (cards 46-75) audit fixes.

Findings from the 2026-07-07 review of MSH commons 46-75 (audit log:
``scripts/audit/MSH_commons_recheck.md``). Three hand-encodes:

* **I Am Iron Man (MSH #58)** — instant: "target artifact or creature
  becomes a 4/4 with flying until end of turn. Draw a card." The draw
  was already encoded; the base-P/T mode is a combat trick per the
  Quandrix Charm precedent (guide §12: 5/5 base ~ +3/+3 over a typical
  2/2 — use the differential): 4/4 -> combat_trick 2/2, granted
  keywords ['flying'].
* **K'un-Lun Warrior (MSH #140)** — ETB "you may sacrifice an artifact
  or discard a card. If you do, draw a card." The discard half is
  reliably payable (§9), so this is a real ETB loot; the parser
  credited ``cards_manipulated=1`` but never wired the sim effects.
  §2's loot rule wires ``DrawCardsEffect(1) + DiscardCardEffect(1)``
  onto the cast mode.
* **Stolen Stark Tech (MSH #114)** — {1}{B} FLASH Equipment whose ETB
  attaches it to a creature and grants indestructible until end of
  turn. §3's flash rule ("flash creatures with ETB pump effects set
  combat-trick fields") extends naturally: the play pattern is exactly
  "flash in to save a blocker". combat_trick_power=1 (the +1/+0
  static applies immediately on attach), toughness=0,
  granted_keywords=['indestructible']. is_equipment stays.

Patched cards become LLM_ENCODED so detector reruns preserve them.
Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_msh_batch45_fixes_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import (
    DiscardCardEffect,
    DrawCardsEffect,
    ParsedCard,
    ParseStatus,
)

FIX_MARKER = "batch45 fix 2026-07-07"


def _mark(card: ParsedCard, note: str) -> None:
    card.status = ParseStatus.LLM_ENCODED
    card.reasons.append(f"{FIX_MARKER}: {note}")


def _cast_mode(card: ParsedCard):
    for mode in card.modes:
        if mode.kind == "cast":
            return mode
    raise AssertionError(f"{card.name}: no cast mode")


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}
    n = 0

    c = by_name["I Am Iron Man"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 2
        c.role_features.combat_trick_toughness = 2
        c.role_features.combat_trick_granted_keywords = ["flying"]
        _mark(
            c,
            "4/4 base P/T until EOT = combat trick +2/+2 over a typical 2/2 "
            "(Quandrix Charm differential precedent, guide §12) + flying grant; "
            "cards_drawn=1 unchanged",
        )
        n += 1

    c = by_name["K'un-Lun Warrior"]
    if not any(FIX_MARKER in r for r in c.reasons):
        mode = _cast_mode(c)
        if not any(e.kind == "draw_cards" for e in mode.effects):
            mode.effects.append(DrawCardsEffect(n=1))
            mode.effects.append(DiscardCardEffect(n=1))
        c.role_features.cards_manipulated = 1
        c.role_features.cards_drawn = 0
        _mark(
            c,
            "ETB 'discard a card: draw a card' loot wired per §2 (discard is "
            "reliably payable, §9; the sac-an-artifact alternative is ignored)",
        )
        n += 1

    c = by_name["Stolen Stark Tech"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 1
        c.role_features.combat_trick_toughness = 0
        c.role_features.combat_trick_granted_keywords = ["indestructible"]
        _mark(
            c,
            "flash Equipment with ETB auto-attach + indestructible-EOT grant = "
            "combat trick per §3's flash rule (flash in to save a blocker); "
            "+1/+0 static applies on attach",
        )
        n += 1

    if n:
        save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {n} card(s)")


if __name__ == "__main__":
    main()
