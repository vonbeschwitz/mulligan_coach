"""Hand-encodes for the owner's ETB-only trigger ruling (2026-07-07).

General rule (guide §4): triggered abilities credit role_features ONLY
when the trigger is the permanent's own entry. Attack / cast / upkeep /
combat-start / counter-placement / landfall triggers never credit;
death triggers only via a self-sac outlet (§19). The parser enforces
this for auto cards; this script covers the preserved ``llm_encoded``
cards and the judgment cases the deterministic matchers can't see.

Token clears (non-self triggers):

* Madame Masque (MSH #104) — draw-second-card trigger token; her ETB
  connive loot stays.
* Namor the Sub-Mariner (MSH #69) — cast-trigger Merfolk.
* Dark Leo & Shredder (TMT #142) — combat-damage trigger Ninja.
* Fire Navy Trebuchet (TLA #100) — attack-trigger Boulder.
* Ambitious Augmenter (SOS #140) — death trigger, no self-sac outlet.
* Bitterblossom (ECL #bonus-2x2-69) — upkeep engine. NOTE: cleared
  under the strict reading (an upkeep trigger is not an ETB); flagged
  in the audit log in case the owner wants a carve-out for
  unconditional time-based engines.

Draw corrections (self-ETB triggers whose draw text is gated — the
inner matchers can't judge the conditions):

* Sygg (ECL #7) — the "draw" lives inside an ability GRANTED to
  another creature, gated on combat damage (§2 April precedent) → 0.
* Blighted Blackthorn (ECL #34) — draw gated on "you may blight 2"
  (a cost we can't assume payable, §2/§9) → 0.
* South Pole Voyager (TLA #35) — draw gated on the trigger resolving
  twice in one turn → 0.
* Stadium Tidalmage (SOS #33) — "you may draw a card. If you do,
  discard a card" on its own ETB = a real net-0 loot →
  cards_manipulated=1, cards_drawn=0, loot wired on the cast mode
  (§2, A.I.M. Scientists shape).

Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_etb_only_trigger_ruling_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import (
    DiscardCardEffect,
    DrawCardsEffect,
    ParsedCard,
    ParseStatus,
)

FIX_MARKER = "etb-only ruling 2026-07-07"

TOKEN_CLEARS: dict[str, list[tuple[str, str]]] = {
    "MSH": [
        ("Madame Masque", "draw-second-card trigger token cleared; ETB connive loot stays"),
        ("Namor the Sub-Mariner", "cast-trigger Merfolk tokens cleared"),
    ],
    "TMT": [
        ("Dark Leo & Shredder", "combat-damage trigger Ninja token cleared"),
    ],
    "TLA": [
        ("Fire Navy Trebuchet", "attack-trigger Boulder token cleared"),
    ],
    "SOS": [
        ("Ambitious Augmenter", "death-trigger token cleared (no self-sac outlet, §19)"),
    ],
    "ECL": [
        (
            "Bitterblossom",
            "upkeep-engine token cleared under the strict ETB-only reading — "
            "flagged for a possible time-based-engine carve-out",
        ),
    ],
}

DRAW_ZEROES: dict[str, list[tuple[str, str]]] = {
    "ECL": [
        (
            "Sygg, Wanderwine Wisdom // Sygg, Wanderbrine Shield",
            "the 'draw' is inside a granted combat-damage ability (§2 April precedent)",
        ),
        (
            "Blighted Blackthorn",
            "draw gated on 'you may blight 2' — cost not assumed payable (§2/§9)",
        ),
    ],
    "TLA": [
        (
            "South Pole Voyager",
            "draw gated on the trigger resolving twice in a turn — too conditional",
        ),
    ],
}


def _mark(card: ParsedCard, note: str) -> None:
    card.status = ParseStatus.LLM_ENCODED
    card.reasons.append(f"{FIX_MARKER}: {note}")


def main() -> None:
    for set_code in ["MSH", "TMT", "TLA", "SOS", "ECL"]:
        cards = load_parsed_cards(set_code)
        by_name = {c.name: c for c in cards}
        n = 0

        for name, note in TOKEN_CLEARS.get(set_code, []):
            c = by_name[name]
            if any(FIX_MARKER in r for r in c.reasons):
                continue
            c.role_features.creates_creatures = []
            _mark(c, note)
            n += 1

        for name, note in DRAW_ZEROES.get(set_code, []):
            c = by_name[name]
            if any(FIX_MARKER in r for r in c.reasons):
                continue
            c.role_features.cards_drawn = 0
            _mark(c, note)
            n += 1

        if set_code == "SOS":
            c = by_name["Stadium Tidalmage"]
            if not any(FIX_MARKER in r for r in c.reasons):
                c.role_features.cards_drawn = 0
                c.role_features.cards_manipulated = 1
                mode = next(m for m in c.modes if m.kind == "cast")
                if not any(e.kind == "draw_cards" for e in mode.effects):
                    mode.effects.append(DrawCardsEffect(n=1))
                    mode.effects.append(DiscardCardEffect(n=1))
                _mark(
                    c,
                    "self-ETB 'may draw, if you do discard' = net-0 loot; wired per §2 "
                    "(the attack half of the trigger adds nothing at mulligan time)",
                )
                n += 1

        if n:
            save_parsed_cards(set_code, cards)
        print(f"[{set_code}] patched {n} card(s)")


if __name__ == "__main__":
    main()
