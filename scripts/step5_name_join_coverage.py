"""Validation: 17Lands name-join coverage across all five current sets.

Roadmap Step 5 replaced the arena_id-keyed 17Lands stats join with a
folded-card-name join (see ``packages/features/stats_join.py``). This
script quantifies how many parsed cards now resolve to a ratings row
through :func:`stats_for_card` — the number that used to crater to
arena_id coverage (TMT 20/210, ECL 21/288, …) whenever MTGJSON lagged a
freshly-rotated format.

For each set it builds the folded-name → ratings table (the key
:func:`shrink_stats` / :func:`zscore_stats` now emit) and counts how
many parsed cards join. Diacritic folding is what lets TMT "Bespoke Bō"
match 17Lands' macron-free "Bespoke Bo".

Run (tee the output to the gitignored logs/):

    .venv/Scripts/python.exe scripts/step5_name_join_coverage.py \\
        | tee logs/step5_name_join_coverage.log
"""

from __future__ import annotations

import sys

from mulligan_coach_cards import (
    SeventeenLandsStats,
    load_parsed_cards,
    load_premier_draft_stats,
)
from mulligan_coach_features import fold_card_name, stats_for_card

SETS = ("TMT", "ECL", "TLA", "SOS", "MSH")


def main() -> int:
    # Card names carry diacritics (e.g. "Bespoke Bō"); force UTF-8 so the
    # Windows cp1252 console doesn't choke when we print misses.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    total_cards = 0
    total_matched = 0
    print("Step 5 name-join coverage (parsed cards vs PremierDraft ratings)\n")
    print(f"{'set':>5}  {'matched':>8}  {'total':>6}  misses")
    print("-" * 60)
    for set_code in SETS:
        cards = load_parsed_cards(set_code)
        lookup = load_premier_draft_stats(set_code)
        # Folded-name -> ratings row, the shape the join key uses.
        table: dict[str, SeventeenLandsStats] = {
            fold_card_name(s.name): s for s in lookup.by_name.values()
        }
        matched = 0
        misses: list[str] = []
        for card in cards:
            if stats_for_card(card, table) is not None:
                matched += 1
            else:
                misses.append(card.name)
        total_cards += len(cards)
        total_matched += matched
        miss_str = ", ".join(misses[:8]) + (" ..." if len(misses) > 8 else "")
        print(f"{set_code:>5}  {matched:>8}  {len(cards):>6}  {miss_str}")

    print("-" * 60)
    pct = 100.0 * total_matched / total_cards if total_cards else 0.0
    print(f"{'ALL':>5}  {total_matched:>8}  {total_cards:>6}  ({pct:.2f}% joined)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
