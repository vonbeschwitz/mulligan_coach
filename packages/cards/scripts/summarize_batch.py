"""Summarize a batch of NEEDS_LLM cards for human/LLM review.

Reads the JSON output of `mulligan-coach-cards list-needs-llm --json`
and prints a compact one-block-per-card summary so the encoder can scan
the batch quickly without wading through model dumps.

Usage:
    python -m mulligan_coach_cards.scripts.summarize_batch <batch.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: summarize_batch.py <batch.json>")
        sys.exit(1)
    path = Path(sys.argv[1])
    # PowerShell's Out-File -Encoding utf8 prepends a BOM; tolerate it.
    cards = json.loads(path.read_text(encoding="utf-8-sig"))
    print(f"=== {len(cards)} cards ===\n")
    for c in cards:
        print(
            f"--- #{c['collector_number']:>4} {c['name']!r}  [{c['type_line']}]  ({c['rarity']}) ---"
        )
        cost_raw = (c.get("mana_cost") or {}).get("raw") if c.get("mana_cost") else None
        cmc = (c.get("mana_cost") or {}).get("cmc") if c.get("mana_cost") else None
        if cost_raw is not None:
            print(f"  cost: {cost_raw}  cmc={cmc}")
        if c.get("power") is not None or c.get("toughness") is not None:
            print(f"  P/T: {c['power']}/{c['toughness']}")
        if c.get("evergreen_keywords"):
            print(f"  evergreens: {', '.join(c['evergreen_keywords'])}")
        rf = c.get("role_features") or {}
        rf_summary = []
        for k in (
            "is_creature",
            "is_planeswalker",
            "is_equipment",
            "is_vehicle",
            "is_punch_fight",
            "is_other",
            "removal_destroy_or_exile",
            "is_bounce",
            "is_top_library",
            "is_removal_aura",
            "is_pump_aura",
        ):
            if rf.get(k):
                rf_summary.append(k)
        if rf.get("cards_drawn"):
            rf_summary.append(f"cards_drawn={rf['cards_drawn']}")
        if rf.get("cards_manipulated"):
            rf_summary.append(f"cards_manipulated={rf['cards_manipulated']}")
        if rf.get("creates_creatures"):
            bodies = rf["creates_creatures"]
            rf_summary.append(f"creates={len(bodies)}")
        if rf_summary:
            print(f"  role_features: {', '.join(rf_summary)}")
        oracle = c.get("raw_oracle_text", "")
        if oracle:
            for line in oracle.splitlines():
                print(f"    | {line}")
        if c.get("reasons"):
            print("  parser reasons:")
            for r in c["reasons"]:
                print(f"    - {r}")
        print()


if __name__ == "__main__":
    main()
