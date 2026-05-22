"""Strip restricted mana abilities from the committed parsed_cards JSONs.

The simulator has no way to model "Spend this mana only to cast an
instant or sorcery spell" / "...to cast Lesson spells" / etc., so
encoding these abilities as if they were unrestricted lets the policy
silently overcount mana for any spell type — see
``packages/simulation/CLAUDE.md`` "Things explicitly out of scope".

This script walks the committed ``data/processed/parsed_cards/*.json``,
and for every card it re-runs the parser's mana-ability extractor on
the oracle-text chunks. Any ability the extractor now rejects (because
the chunk also contains a "Spend this mana only" restriction phrase)
is removed from the persisted ``mana_abilities`` list. The other
fields are left exactly as-is.

The parser fix in ``packages/cards/src/mulligan_coach_cards/parser.py``
prevents these restricted abilities from being written on future
re-parses; this script is only needed once to clean the historical
JSONs without forcing a full re-detect-and-LLM cycle.

Run:
    .venv/Scripts/python.exe scripts/patch_restricted_mana.py
"""

from __future__ import annotations

import json
from pathlib import Path

from mulligan_coach_cards.parser import _extract_mana_ability, _split_chunks


def patch_card(card: dict) -> bool:
    """Return True if the card's ``mana_abilities`` list was changed."""
    abilities = card.get("mana_abilities") or []
    if not abilities:
        return False
    text = card.get("raw_oracle_text") or ""
    if "spend this mana only" not in text.lower():
        return False

    # Walk chunks and rebuild ``mana_abilities`` from the saved data.
    # Idempotent: if the file has already been patched, the saved
    # abilities are exactly the ones we'd keep — so we re-emit them
    # unchanged. The walk is keyed by chunk shape, not position, so a
    # mid-list previous-removal doesn't desync the index.
    chunks = _split_chunks(text)
    idx = 0
    rebuilt: list[dict] = []
    for chunk in chunks:
        cleaned = _strip_restriction(chunk)
        candidate = _extract_mana_ability(cleaned)
        if candidate is None:
            continue  # chunk never yielded an ability (no shape match)
        restricted = "spend this mana only" in chunk.lower()
        # Does the next saved ability match this chunk's shape? If yes,
        # it's the chunk's entry — keep it (unless restricted, in which
        # case drop). If no, the chunk's entry was removed by a prior
        # patch run — skip without advancing.
        next_matches = idx < len(abilities) and _matches(abilities[idx], candidate)
        if not next_matches:
            continue
        if restricted:
            idx += 1  # drop
            continue
        rebuilt.append(abilities[idx])
        idx += 1

    if rebuilt == abilities:
        return False
    card["mana_abilities"] = rebuilt
    return True


def _matches(saved: dict, candidate) -> bool:
    """Return True if ``saved`` (raw JSON dict) and ``candidate``
    (typed ``ManaAbility``) describe the same ability shape.

    Compares tap-cost, mana-cost-raw, and the produces matrix —
    enough to distinguish all six recognised mana-ability shapes
    that ``_extract_mana_ability`` emits. Conditions on
    ``ManaAbility.condition`` aren't checked because the extractor
    never emits a non-None condition for these chunks."""
    if saved["cost"]["tap"] != candidate.cost.tap:
        return False
    if saved["cost"]["mana"]["raw"] != candidate.cost.mana.raw:
        return False
    return saved["produces"] == [list(opts) for opts in candidate.produces]


def _strip_restriction(chunk: str) -> str:
    """Remove the trailing "Spend this mana only..." sentence from a chunk.

    Returns the chunk with everything from "Spend this mana only" (case-
    insensitive) onward removed, then trimmed."""
    lower = chunk.lower()
    pos = lower.find("spend this mana only")
    if pos < 0:
        return chunk
    return chunk[:pos].rstrip()


def main() -> None:
    root = Path("data/processed/parsed_cards")
    for fp in sorted(root.glob("*.json")):
        cards = json.loads(fp.read_text(encoding="utf-8"))
        changed = 0
        for c in cards:
            if patch_card(c):
                changed += 1
                print(
                    f"  patched {fp.stem} / {c['name']}: "
                    f"mana_abilities -> {len(c['mana_abilities'])} kept"
                )
        if changed:
            # ensure_ascii=True matches the committed file's encoding —
            # unicode characters like the em-dash stay as — escapes,
            # so the diff is narrowly scoped to the touched cards.
            fp.write_text(
                json.dumps(cards, indent=2, ensure_ascii=True) + "\n",
                encoding="utf-8",
            )
            print(f"{fp.stem}: {changed} card(s) patched, file rewritten")
        else:
            print(f"{fp.stem}: no changes")


if __name__ == "__main__":
    main()
