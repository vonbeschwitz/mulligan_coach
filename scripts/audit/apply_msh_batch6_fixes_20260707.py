"""Apply the MSH commons batch-6 (cards 76-94, final batch) audit fixes.

Findings from the 2026-07-07 review of the last 19 MSH commons (audit
log: ``scripts/audit/MSH_commons_recheck.md``). Four commons plus one
out-of-batch rider:

* **Take Up the Shield (MSH #39)** — "+1/+1 counter + lifelink and
  indestructible until end of turn" on an instant is the *exact*
  Saved by the Shell precedent (guide §3): combat_trick 1/1 +
  ['lifelink', 'indestructible'].
* **Super Suit (MSH #78)** — flash Equipment with ETB auto-attach
  (+1/+2 static, untap rider) = combat trick 1/2 per the
  flash-equipment ruling the owner confirmed on Stolen Stark Tech
  (2026-07-07, now codified in §3).
* **Super Speed (MSH #154)** — flash pump Aura. Two fixes: the aura's
  STATIC grant is haste (the parser mis-captured the ETB line's
  'first strike' instead), and per the same flash ruling the card
  also gets combat-trick fields (+1/+0, first strike EOT on arrival).
* **Super Strength (MSH #189)** — the "has trample and ward {1}" tail
  after the +4/+4 was dropped; aura_pump_granted_keywords =
  ['trample', 'ward'].
* **Rancor (MSH #bonus-2x2-156, uncommon — out-of-batch rider)** —
  same dropped-grant shape as Super Strength ('trample'); fixed here
  because it surfaced in the same scan and the fix is one line.

Patched cards become LLM_ENCODED so detector reruns preserve them.
Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_msh_batch6_fixes_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import ParsedCard, ParseStatus

FIX_MARKER = "batch6 fix 2026-07-07"


def _mark(card: ParsedCard, note: str) -> None:
    card.status = ParseStatus.LLM_ENCODED
    card.reasons.append(f"{FIX_MARKER}: {note}")


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}
    n = 0

    c = by_name["Take Up the Shield"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 1
        c.role_features.combat_trick_toughness = 1
        c.role_features.combat_trick_granted_keywords = ["lifelink", "indestructible"]
        c.role_features.is_other = False
        _mark(
            c,
            "counter + keyword grant on an instant = combat trick — the exact "
            "Saved by the Shell precedent (guide §3)",
        )
        n += 1

    c = by_name["Super Suit"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 1
        c.role_features.combat_trick_toughness = 2
        _mark(
            c,
            "flash Equipment with ETB auto-attach (+1/+2, untap) = combat trick "
            "per the flash ruling (§3, owner-confirmed 2026-07-07)",
        )
        n += 1

    c = by_name["Super Speed"]
    if not any(FIX_MARKER in r for r in c.reasons):
        # Static grant is haste; the parser had captured the ETB line's
        # temporary first-strike grant into the static field instead.
        c.role_features.aura_pump_granted_keywords = ["haste"]
        c.role_features.combat_trick_power = 1
        c.role_features.combat_trick_toughness = 0
        c.role_features.combat_trick_granted_keywords = ["first strike"]
        _mark(
            c,
            "static grant corrected to haste; flash pump Aura also gets "
            "combat-trick fields (+1/+0, first strike EOT) per the §3 flash ruling",
        )
        n += 1

    c = by_name["Super Strength"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.aura_pump_granted_keywords = ["trample", "ward"]
        _mark(c, "dropped 'has trample and ward {1}' tail restored to the aura grants")
        n += 1

    c = by_name["Rancor"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.aura_pump_granted_keywords = ["trample"]
        _mark(
            c,
            "dropped 'and has trample' tail restored (out-of-batch rider — "
            "same scan as Super Strength)",
        )
        n += 1

    if n:
        save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {n} card(s)")


if __name__ == "__main__":
    main()
