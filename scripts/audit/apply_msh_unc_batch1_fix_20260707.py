"""Apply the MSH uncommons batch-1 (cards 1-20) audit fix.

Single finding from the 2026-07-07 review of the first 20 MSH
uncommons (audit log: ``scripts/audit/MSH_commons_recheck.md``):

* **Black Panther, Vanguard (MSH #207)** — "Whenever another nontoken
  Hero you control enters, choose one — • Create a 1/1 white Soldier
  creature token. / • Creatures you control get +1/+1 until end of
  turn." The modal bullets inside the trigger broke the deterministic
  parser and the MV>=4 fast-path promoted the card with no token
  body. Per the recurring-trigger token precedent (Sokka / Madame
  Masque, guide §4) aggregated across modes (§12), the token mode is
  recorded: ``creates_creatures=[1/1 W Soldier]``. The anthem mode is
  a trigger, not a castable choice — no combat-trick fields.

Only card in the set with a "• Create …" bullet and no body (scan in
the audit log). Idempotent via the fix marker.

Usage::

    uv run python scripts/audit/apply_msh_unc_batch1_fix_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import CreatureBody, ParseStatus

FIX_MARKER = "unc-batch1 fix 2026-07-07"


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}

    c = by_name["Black Panther, Vanguard"]
    if any(FIX_MARKER in r for r in c.reasons):
        print("[MSH] patched 0 card(s)")
        return
    c.role_features.creates_creatures = [
        CreatureBody(power="1", toughness="1", colors=["W"], subtypes=["Soldier"], keywords=[])
    ]
    c.status = ParseStatus.LLM_ENCODED
    c.reasons.append(
        f"{FIX_MARKER}: recurring-trigger modal token recorded per the Sokka / "
        "Madame Masque precedent (§4) aggregated per §12; the +1/+1-EOT anthem "
        "mode is trigger-gated, not a combat trick"
    )
    save_parsed_cards("MSH", cards)
    print("[MSH] patched 1 card(s)")


if __name__ == "__main__":
    main()
