"""Apply the owner's other-permanent-trigger token ruling (2026-07-07).

Owner ruling (overruling the Black Panther fix in
``apply_msh_unc_batch1_fix_20260707.py``): a recurring trigger that the
card CANNOT generate by itself — "whenever ANOTHER permanent you
control enters/leaves …" — is too conditional to credit token bodies.
Contrast with self-generated triggers, which keep their tokens:
Sokka (his own attack), Madame Masque (her own ETB connive supplies
the second draw), Ant-Man Colony Commander (his own attack ability
places the counter that feeds his token trigger), Crescent Island
Temple (its own ETB counts itself as a Shrine, so the "for each
Shrine" body is a guaranteed minimum of one).

Cleared by this script (full-set scan; these are the only three):

* **Black Panther, Vanguard (MSH #207)** — "whenever another nontoken
  Hero you control enters, choose one — create a 1/1 Soldier / …"
  (reverts the unc-batch1 fix).
* **Simulacrum Synthesizer (MSH #bonus-big-6)** — "whenever another
  artifact with MV 3+ enters, create a 0/0 Construct". ETB scry 2
  stays.
* **Suki, Courageous Rescuer (TLA #37)** — "whenever another permanent
  you control leaves the battlefield during your turn, create a 1/1
  Ally". Note: TLA is in choice_v9's training data, so this adds one
  card to the known train/serve encoding drift absorbed at the next
  retrain.

Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_other_trigger_token_ruling_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import ParsedCard, ParseStatus

FIX_MARKER = "other-trigger token ruling 2026-07-07"

TARGETS: dict[str, list[str]] = {
    "MSH": ["Black Panther, Vanguard", "Simulacrum Synthesizer"],
    "TLA": ["Suki, Courageous Rescuer"],
}


def _clear(card: ParsedCard) -> bool:
    if any(FIX_MARKER in r for r in card.reasons):
        return False
    card.role_features.creates_creatures = []
    card.status = ParseStatus.LLM_ENCODED
    card.reasons.append(
        f"{FIX_MARKER}: token trigger requires ANOTHER permanent to "
        "enter/leave — the card can't generate it by itself, so the body "
        "isn't credited (guide §4)"
    )
    return True


def main() -> None:
    for set_code, names in TARGETS.items():
        cards = load_parsed_cards(set_code)
        by_name = {c.name: c for c in cards}
        n = sum(1 for name in names if _clear(by_name[name]))
        if n:
            save_parsed_cards(set_code, cards)
        print(f"[{set_code}] patched {n} card(s)")


if __name__ == "__main__":
    main()
