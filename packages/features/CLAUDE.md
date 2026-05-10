# features — Claude instructions

## Purpose

Derived per-card and hand-level features for the Mulligan Coach
pipeline. Sits between `packages/cards/` (raw card representation +
raw 17Lands stats) and the future `packages/model/` (XGBoost training
+ inference). The cards package is deliberately scoped to "represent
the cards"; anything derived — sample-shrunk win rates, earliness
scores, hand-level mana / castability features — lives here instead.

## Layout

```
src/mulligan_coach_features/
├── __init__.py                       # Re-exports the public surface
├── seventeenlands_shrinkage.py       # PlayRateBins / FormatPriors / ShrunkWinRates
│                                     # + compute_format_priors + shrink_stats
└── seventeenlands_zscores.py         # FormatWRDistribution / CardZScores
                                      # + compute_format_wr_distribution + zscore_stats
scripts/
└── inspect_shrinkage.py              # Eyeball the shrinkage on real (set, format) data
tests/
├── test_seventeenlands_shrinkage.py
└── test_seventeenlands_zscores.py
```

## seventeenlands_shrinkage

The 17Lands per-card win rates (OH WR, GD WR, GIH WR) are noisy for
cards with few observations. Naively averaging them with the format
mean fixes the variance but flatters genuinely-bad cards: a low sample
size can also reflect that the card is bad and gets sideboarded out,
in which case the low WR is real and shouldn't be pulled toward the
average.

Two design choices resolve this together:

1. **Use `pick_count` as the sample-size N in the shrinkage weight.**
   Selection is informative — a card picked 2000 times but played in
   only 20 games has 1980 picks' worth of evidence that "people don't
   want to play this." Using `pick_count` (not `*_game_count`) keeps
   the weight on the raw WR high in that case, because pick_count is
   unaffected when a card gets sideboarded.
2. **Use a play-rate-conditional mean as the prior.** Cards with
   similar `play_rate` have similar typical quality. Sideboard-tier
   cards (low play_rate) have a low conditional mean, so even when
   shrinkage is applied the result is pulled toward "typical sideboard
   WR," not toward the format-wide ~0.53.

Formula, applied independently for each WR field:

```
prior   = conditional_mean(card.play_rate)   if card.play_rate is not None
        = overall_set_mean(WR)               otherwise
w       = card.pick_count / (card.pick_count + K_BASE)   # K_BASE default = 500
shrunk  = w * raw_WR + (1 - w) * prior       if raw_WR is not None
        = prior                              if raw_WR is None
```

The conditional mean is a 10-decile lookup over `play_rate`: cards in
the (set, event_type) are sorted by play_rate, split into 10 equal-count
bins, and each bin's mean WR is the lookup value for any card whose
play_rate falls into that bin. Decile binning (vs. isotonic regression
or kernel smoothing) is deliberate — transparent, no extra dependencies,
and ~25 cards per bin in a Premier Draft set is enough for stable bin
means.

`K_BASE = 500` is a starting default. With pick_count typically in the
thousands for non-mythic cards in a Premier Draft format on 17Lands,
this gives `w ≈ 0.85+` on commons and `w ≈ 0.3–0.5` on mythics. Tune
after looking at real distributions via `scripts/inspect_shrinkage.py`.

The single `weight` field on `ShrunkWinRates` covers all three WRs
because `pick_count` is per-card, not per-WR-field — so the same `w`
applies to OH / GD / GIH.

### Edge cases

| Case | Behaviour |
|---|---|
| `raw_WR is None`, `play_rate is not None` | Return `prior` (the conditional mean) — the no-stats imputation case. |
| `raw_WR is None`, `play_rate is None` | Return overall set mean. |
| `pick_count` is 0 | `w = 0`, return `prior`. |
| `play_rate is None` (raw_WR present) | Use overall set mean as prior; otherwise normal formula. |
| All cards in (set, event_type) have `WR=None` for some field | `overall_mean` and the conditional bins are `None` for that field; per-card `shrunk` is `None`; warning logged. |
| Mixed `(expansion, event_type)` in input | Raise `ValueError`. The shrinkage is per-format. |

### Known limitation: imputation slightly flatters fringe cards

17Lands only publishes a per-card WR once an internal sample threshold
is cleared. The truly fringe sideboard cards (and any card from a
brand-new set without enough games) get `*_win_rate=None` in the raw
data. Our no-stats path imputes them at the lowest-decile conditional
mean — empirically ~0.530 in TLA Premier Draft.

That number is computed from the cards that *did* clear the threshold
(in TLA, 262 of 342 stats rows have a published GIH WR), so it
slightly overestimates the true win rate of cards too rarely played
to be published in the first place. We accept this — going to 20-bin
vigintiles or directly modelling the unobserved tail moves the imputed
prior by less than one win rate point on TLA, not worth the extra
complexity. Re-evaluate if a future format shows a much steeper bottom
tail.

## seventeenlands_zscores

The XGBoost feature stage (per `features_list.md`) buckets each hand
spell by its OH / GD WR z-score relative to the format — e.g.
"number of hand spells with OH WR z > 1.3". This module produces
those z-scores, normalising each card's *shrunk* WR (not raw) against
the format's distribution.

Standardising the shrunk values is deliberate. Raw 17Lands WRs are
heavy on noise for low-N cards; the shrinkage pass already pulled
those toward a play-rate-conditional reference. So z-scoring atop
the shrunk values means "z > 1.3" cleanly corresponds to a card
that's genuinely above-average for the format, not one that's seen
30 games and rolled a few wins.

Formula, applied independently per WR field, per `(set, event_type)`:

```
mean = mean(shrunk_WR over cards with shrunk_WR is not None)
std  = std (shrunk_WR over cards with shrunk_WR is not None)      # ddof=0
z    = (shrunk_WR - mean) / std    if shrunk_WR, mean, std are non-None and std > 0
     = None                        otherwise
```

`std` uses `ddof=0` (population std) — we're describing the format
itself, not estimating a wider distribution from a sample. The
numerical difference vs. `ddof=1` is ~0.2% at ~250 cards per format,
but ddof=0 is the correct semantic.

### Edge cases

| Case | Behaviour |
|---|---|
| `shrunk_WR is None` for a card | That card's z for the field is `None`. |
| Every card has `shrunk_WR=None` for some field | The field's `mean` / `std` are `None`; per-card z for the field is `None`. |
| `std == 0.0` (degenerate test fixtures, every card same WR) | z for the field is `None` — no spread to normalise against, avoids NaN. |
| Empty input | Raise `ValueError`. |

The module **does not** verify the input shares one `(expansion, event_type)` —
the upstream :func:`shrink_stats` already enforced that, and the
shrunk values it produces have no expansion/event_type attached for
us to re-check. Mixing formats degrades the distribution but doesn't
error out; it's the caller's bug to avoid.

## Tests

```
uv run pytest packages/features
```

Tests construct `SeventeenLandsStats` (for the shrinkage chain) or
`ShrunkWinRates` (for the z-score chain) instances directly; no
parquet I/O.

## Scope

This package contains derived per-card features (above) and, as the
project grows, hand-level features over a `ParsedCard` opening hand
(mana, castability, role mix). Anything that takes raw 17Lands rows
or raw Scryfall dicts as input belongs in `cards` or `data-download`,
not here.

## Known limitations

* **"Alternative mode" features count cycling / evoke / flashback only.**
  The features list uses `len(card.modes) > 1` as the operational
  definition of "has an alternative mode" — that captures cycling,
  land-cycling, channel, and the alt-cost-cast family
  (evoke / flashback / madness / jump-start / aftermath). It **does not**
  capture Adventure / MDFC / Split / "choose one" modal cards, because
  those bail to NEEDS_LLM in the parser today and never get multiple
  Modes attached. Re-evaluate when the parser gains support for those
  layouts.
