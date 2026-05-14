"""Emit a compact per-card summary for classification audit.

Reads `data/processed/parsed_cards/<SET>.json` and produces a Markdown
file with one block per card containing:

* name, set, collector number, rarity
* type line
* mana cost (raw)
* full oracle text
* status (auto / llm_encoded / needs_human / needs_llm)
* the role_features values that are "interesting" (i.e. set, or relevant)

The output is one big markdown file per set. The point is to make it
fast to scan all cards in one pass and judge whether the classification
looks right.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def fmt_role_features(rf: dict) -> list[str]:
    """Return a list of short strings describing the populated role features."""
    out: list[str] = []
    flags = [
        "is_creature",
        "is_planeswalker",
        "is_equipment",
        "is_vehicle",
        "is_land",
        "is_mana_rock",
        "is_saga",
        "is_class",
        "is_punch_fight",
        "is_other",
        "removal_destroy_or_exile",
        "is_mass_removal",
        "is_counterspell",
        "is_bounce",
        "is_top_library",
        "is_removal_aura",
        "is_pump_aura",
    ]
    for f in flags:
        if rf.get(f):
            out.append(f)
    if rf.get("removal_burn_damage") is not None:
        out.append(f"removal_burn_damage={rf['removal_burn_damage']}")
    if rf.get("cards_drawn"):
        out.append(f"cards_drawn={rf['cards_drawn']}")
    if rf.get("cards_manipulated"):
        out.append(f"cards_manipulated={rf['cards_manipulated']}")
    if rf.get("combat_trick_power") is not None or rf.get("combat_trick_toughness") is not None:
        p = rf.get("combat_trick_power")
        t = rf.get("combat_trick_toughness")
        kws = rf.get("combat_trick_granted_keywords", [])
        if kws:
            out.append(f"combat_trick: +{p}/+{t} grants {kws}")
        else:
            out.append(f"combat_trick: +{p}/+{t}")
    elif rf.get("combat_trick_granted_keywords"):
        out.append(f"combat_trick: grants {rf['combat_trick_granted_keywords']}")
    if rf.get("aura_pump_power") is not None or rf.get("aura_pump_toughness") is not None:
        p = rf.get("aura_pump_power")
        t = rf.get("aura_pump_toughness")
        kws = rf.get("aura_pump_granted_keywords", [])
        if kws:
            out.append(f"aura_pump: +{p}/+{t} grants {kws}")
        else:
            out.append(f"aura_pump: +{p}/+{t}")
    if rf.get("creates_creatures"):
        bodies = []
        for body in rf["creates_creatures"]:
            colors = "".join(body.get("colors", []) or [])
            subtypes = "/".join(body.get("subtypes", []) or [])
            keywords = ",".join(body.get("keywords", []) or [])
            tail = f" {keywords}" if keywords else ""
            bodies.append(f"{body['power']}/{body['toughness']} {colors} {subtypes}{tail}".strip())
        out.append("creates_creatures: " + " | ".join(bodies))
    return out


def card_summary(card: dict) -> str:
    name = card["name"]
    cn = card["collector_number"]
    rarity = card["rarity"]
    type_line = card["type_line"]
    mc_raw = (card.get("mana_cost") or {}).get("raw", "") or "—"
    status = card.get("status", "?")
    oracle = card.get("raw_oracle_text", "") or "(no text)"
    rf_flags = fmt_role_features(card.get("role_features", {}))
    flags_str = ", ".join(rf_flags) if rf_flags else "(none)"

    return (
        f"### #{cn} {name}  [{rarity}, status={status}]\n"
        f"- type: {type_line}\n"
        f"- cost: {mc_raw}\n"
        f"- oracle: {oracle}\n"
        f"- role_features: {flags_str}\n"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    inp = REPO_ROOT / "data" / "processed" / "parsed_cards" / f"{args.set.upper()}.json"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cards = json.loads(inp.read_text(encoding="utf-8"))
    lines = [f"# Classification audit dump — {args.set.upper()} ({len(cards)} cards)\n"]
    for card in cards:
        lines.append(card_summary(card))
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(cards)} cards to {out}")


if __name__ == "__main__":
    main()
