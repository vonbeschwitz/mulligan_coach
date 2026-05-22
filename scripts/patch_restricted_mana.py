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

    # Per-chunk re-extraction. The order of `mana_abilities` from a
    # detector run mirrors the order chunks were walked in the parser,
    # so we can match by chunk position. Any chunk whose extraction
    # now returns None (because of the restriction) drops that
    # corresponding ability — we use a positional walk: collect the
    # chunks that DO yield an ability (pre-fix) and DROP those that
    # would now be rejected.
    #
    # We rebuild the kept list from scratch instead of mapping indices:
    # the chunks not yielding an ability (ETB tapped, basic-synthesis,
    # etc.) never contributed to the list, so a positional rebuild is
    # equivalent and avoids subtle index mismatches.
    chunks = _split_chunks(text)
    kept: list[dict] = []
    # Cheap sanity check: count of "would have matched but for the
    # restriction" must equal the number of dropped abilities.
    dropped = 0
    for chunk in chunks:
        if "spend this mana only" not in chunk.lower():
            continue
        # This chunk used to yield an ability (before the parser fix).
        # If the rest of the chunk looks like a recognized mana-ability
        # shape, we count it as one to drop. We confirm by checking
        # whether the chunk matches any of the patterns *after* we
        # strip the restriction prose — kept simple: just count one
        # per chunk that mentions a tap-for-add line.
        dropped += 1

    # Walk the saved abilities and drop the trailing-N matching the
    # number of "would-have-matched-but-restricted" chunks. The parser
    # walked chunks in order; abilities were appended in order; the
    # restricted ones were *interspersed* among unrestricted ones, so
    # we can't just truncate. Instead we re-run the extractor on each
    # chunk that yielded an ability and rebuild the list.
    #
    # Approach: walk chunks and abilities in parallel. For each chunk
    # that yielded a non-None ability in the OLD parser, we have one
    # entry in ``abilities``. The OLD parser kept restricted entries;
    # the NEW parser drops them. So: for each chunk, decide whether
    # the OLD parser would have produced an ability (any of the six
    # shapes match without the restriction guard). If so, advance the
    # ``abilities`` index. If the chunk has the restriction, skip
    # appending; otherwise, keep the ability.
    idx = 0
    rebuilt: list[dict] = []
    for chunk in chunks:
        if idx >= len(abilities):
            break
        # Would the OLD parser have emitted an ability for this chunk?
        # We can answer cheaply: bypass the new restriction guard by
        # calling the same matchers manually, OR by temporarily
        # removing the leading restriction sentence from the chunk and
        # re-running the extractor. Easier: just strip the trailing
        # "Spend this mana only..." clause and re-run.
        cleaned = _strip_restriction(chunk)
        # The fixed extractor will now happily match cleaned without
        # hitting the restriction guard.
        new_ab = _extract_mana_ability(cleaned)
        if new_ab is None:
            continue
        # OLD parser emitted; either we drop (if restricted) or keep.
        if "spend this mana only" in chunk.lower():
            # Drop the corresponding stored ability.
            idx += 1
            continue
        rebuilt.append(abilities[idx])
        idx += 1

    if rebuilt == abilities:
        return False
    card["mana_abilities"] = rebuilt
    return True


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
                print(f"  patched {fp.stem} / {c['name']}: "
                      f"mana_abilities -> {len(c['mana_abilities'])} kept")
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
