"""Apply the MSH commons batch-3 (cards 31-45) audit fixes.

Findings from the 2026-07-07 review of MSH commons 31-45 (audit log:
``scripts/audit/MSH_commons_recheck.md``). Two hand-encodes; the third
batch-3 fix (Hydraulic Helper's restricted mana) is a parser fix
(`_RESTRICTED_MANA_RE` now catches the negative "this mana can't be
spent" phrasing) applied via detector rerun, not this script.

* **Hire a Crew (MSH #134)** — instant: 2/1 menace Villain token +
  "creatures you control get +1/+0 until end of turn". The mass anthem
  EOT on an instant is a combat trick per the Lorehold Charm precedent
  (guide §12: "+1/+1 anthem EOT -> combat_trick_power=1,
  combat_trick_toughness=1"); here +1/+0 -> power 1, toughness 0.
* **Hour of Defeat (MSH #99)** — "Destroy target creature. Surveil 1."
  The parser's destroy matcher consumed the chunk and dropped the
  mid-line surveil rider. Per §2 (surveil = scry; Hamato Guardian
  Stance's scry-1 rider precedent): ``cards_manipulated=1`` +
  ``ScryEffect(1)`` on the cast mode. The removal flag stays.

Patched cards become LLM_ENCODED so detector reruns preserve them.
Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_msh_batch3_fixes_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import ParsedCard, ParseStatus, ScryEffect

FIX_MARKER = "batch3 fix 2026-07-07"


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

    c = by_name["Hire a Crew"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 1
        c.role_features.combat_trick_toughness = 0
        _mark(
            c,
            "mass +1/+0 anthem EOT on an instant = combat trick per the "
            "Lorehold Charm precedent (guide §12); token body unchanged",
        )
        n += 1

    c = by_name["Hour of Defeat"]
    if not any(FIX_MARKER in r for r in c.reasons):
        mode = _cast_mode(c)
        if not any(e.kind == "scry" for e in mode.effects):
            mode.effects.append(ScryEffect(n=1))
        c.role_features.cards_manipulated = 1
        _mark(
            c,
            "surveil-1 rider after the destroy sentence credited per §2 "
            "(surveil = scry; mid-line rider missed by the chunk matcher)",
        )
        n += 1

    if n:
        save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {n} card(s)")


if __name__ == "__main__":
    main()
