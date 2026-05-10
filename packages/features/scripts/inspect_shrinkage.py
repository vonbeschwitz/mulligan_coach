"""Eyeball-test the 17Lands win-rate shrinkage on a real (set, format).

Usage:

::

    uv run python packages/features/scripts/inspect_shrinkage.py [SET]

Defaults to ``TLA`` (the newest Premier Draft set as of 2026-05). Loads
the ratings parquet via ``cards.load_premier_draft_stats``, builds the
priors, runs ``shrink_stats``, and prints:

1. The decile bin means for GIH WR (sanity-checks the conditional prior).
2. A table of 12 cards spread across the play_rate spectrum, showing
   ``raw_GIH``, ``conditional_prior``, ``shrunk_GIH``, and the weight.
   Sideboard-tier rows should NOT show shrunk values jumping to ~0.53.
"""

from __future__ import annotations

import sys

from mulligan_coach_cards import load_premier_draft_stats
from mulligan_coach_features import compute_format_priors, shrink_stats
from mulligan_coach_features.seventeenlands_shrinkage import _resolve_prior


def main() -> int:
    set_code = sys.argv[1] if len(sys.argv) > 1 else "TLA"
    lookup = load_premier_draft_stats(set_code)
    stats = list(lookup.by_arena_id.values())
    priors = compute_format_priors(stats)
    shrunk = shrink_stats(stats, priors=priors)

    overall = priors.overall_mean_gih_wr
    overall_str = f"{overall:.3f}" if overall is not None else "n/a"
    print(f"=== {set_code} PremierDraft -- n={priors.n_cards} cards ===")
    print(f"Overall GIH WR mean: {overall_str}")
    print("Conditional GIH WR by play_rate decile (lo -> hi):")
    bins = priors.conditional_mean_gih_wr
    print(f"  bin means: {[round(m, 3) for m in bins.means]}")
    print(f"  edges:     {[round(e, 3) for e in bins.edges]}")
    print()

    # Pick 12 cards spread across the play_rate spectrum: 4 low, 4 mid, 4 high.
    have_play_rate = [s for s in stats if s.play_rate is not None]
    have_play_rate.sort(key=lambda s: s.play_rate or 0.0)
    n = len(have_play_rate)
    if n < 12:
        sample = have_play_rate
    else:
        # Grab evenly-spaced indices spanning the sorted range.
        sample = [have_play_rate[round(i * (n - 1) / 11)] for i in range(12)]

    cols = ("name", "pick", "play_rate", "raw_GIH", "prior", "shrunk_GIH", "w")
    widths = (28, 6, 9, 8, 8, 10, 6)
    print(" ".join(c.ljust(w) for c, w in zip(cols, widths, strict=True)))
    print("-" * (sum(widths) + len(widths) - 1))
    for s in sample:
        sw = shrunk[s.mtga_id]
        prior = _resolve_prior(
            s.play_rate, priors.conditional_mean_gih_wr, priors.overall_mean_gih_wr
        )
        cells = (
            s.name[:27],
            str(s.pick_count),
            f"{s.play_rate:.3f}" if s.play_rate is not None else "n/a",
            f"{s.ever_drawn_win_rate:.3f}" if s.ever_drawn_win_rate is not None else "n/a",
            f"{prior:.3f}" if prior is not None else "n/a",
            f"{sw.shrunk_ever_drawn_win_rate:.3f}"
            if sw.shrunk_ever_drawn_win_rate is not None
            else "n/a",
            f"{sw.weight:.2f}",
        )
        print(" ".join(cell.ljust(w) for cell, w in zip(cells, widths, strict=True)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
