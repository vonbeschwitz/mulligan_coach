"""Bulk-mark all `needs_llm` cards with non-normal-layout reasons as
`needs_human` — these are Sagas / DFCs / Adventures / Classes / Split
cards which are out-of-scope per the project's v1 plan and won't be
auto-classifiable until layout-aware parsing lands."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_CARDS_SRC = _HERE.parent / "src"
if str(_CARDS_SRC) not in sys.path:
    sys.path.insert(0, str(_CARDS_SRC))

from mulligan_coach_cards.models import ParseStatus  # noqa: E402
from mulligan_coach_cards.store import (  # noqa: E402
    load_parsed_cards,
    save_parsed_cards,
)


def main() -> None:
    set_codes = sys.argv[1:] or ["TMT", "ECL", "TLA"]
    total_marked = 0
    for code in set_codes:
        cards = load_parsed_cards(code)
        n_marked = 0
        for card in cards:
            if card.status != ParseStatus.NEEDS_LLM:
                continue
            if any("non-normal layout" in r for r in card.reasons):
                card.status = ParseStatus.NEEDS_HUMAN
                card.reasons.append(
                    "llm: layout out of scope for v1 parser; needs human encoding when "
                    "layout-aware parsing lands (Saga / DFC / Adventure / Class / etc.)"
                )
                n_marked += 1
        save_parsed_cards(code, cards)
        print(f"[{code}] marked {n_marked} non-normal-layout cards as needs_human")
        total_marked += n_marked
    print(f"Total: {total_marked} cards marked as needs_human")


if __name__ == "__main__":
    main()
