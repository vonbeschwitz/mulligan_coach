"""Apply a batch of patches to the per-set ParsedCard store in one pass.

Each patch describes one card update. Format:

    [
      {
        "set_code": "TLA",
        "collector_number": "5",
        "status": "llm_encoded",
        "patch": {                   # any subset of ParsedCard fields
          "role_features": {...},
          "modes": [...],
          "reasons": [...]
        }
      },
      ...
    ]

Reads patches from a JSON file (path passed as the only argument) or
stdin (if no path is given) and writes the merged data back via the
store helpers. Significantly faster than calling ``mark`` 20 times in a
row because each invocation re-loads / re-saves the per-set file once.

Usage:
    python -m packages.cards.scripts.apply_patches patches.json
    cat patches.json | python -m packages.cards.scripts.apply_patches
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Resolve the cards package without installing as editable (this script
# lives outside the src/ tree).
_HERE = Path(__file__).resolve().parent
_CARDS_SRC = _HERE.parent / "src"
if str(_CARDS_SRC) not in sys.path:
    sys.path.insert(0, str(_CARDS_SRC))

from mulligan_coach_cards.models import ParsedCard, ParseStatus  # noqa: E402
from mulligan_coach_cards.store import (  # noqa: E402
    load_parsed_cards,
    save_parsed_cards,
)


def main() -> None:
    if len(sys.argv) > 1:
        patches: list[dict[str, Any]] = json.loads(
            Path(sys.argv[1]).read_text(encoding="utf-8-sig")
        )
    else:
        patches = json.loads(sys.stdin.read())

    if not isinstance(patches, list):
        print("Patches file must contain a JSON list.", file=sys.stderr)
        sys.exit(1)

    by_set: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entry in patches:
        by_set[entry["set_code"].upper()].append(entry)

    for set_code, entries in by_set.items():
        cards = load_parsed_cards(set_code)
        index_by_collector: dict[str, int] = {c.collector_number: i for i, c in enumerate(cards)}
        applied = 0
        skipped: list[str] = []
        for entry in entries:
            collector = str(entry["collector_number"])
            idx = index_by_collector.get(collector)
            if idx is None:
                skipped.append(collector)
                continue
            target = cards[idx]
            patch_dict: dict[str, Any] = entry.get("patch") or {}
            merged_dict = target.model_dump(mode="json")
            for k, v in patch_dict.items():
                # Shallow-merge nested dicts (role_features) so partial
                # patches don't blow away unrelated fields.
                if isinstance(v, dict) and isinstance(merged_dict.get(k), dict):
                    nested = dict(merged_dict[k])
                    nested.update(v)
                    merged_dict[k] = nested
                else:
                    merged_dict[k] = v
            merged_dict["status"] = ParseStatus(entry["status"]).value
            cards[idx] = ParsedCard.model_validate(merged_dict)
            applied += 1
        save_parsed_cards(set_code, cards)
        print(
            f"[{set_code}] applied {applied}/{len(entries)} patches "
            + (f"(skipped: {', '.join(skipped)})" if skipped else "")
        )


if __name__ == "__main__":
    main()
