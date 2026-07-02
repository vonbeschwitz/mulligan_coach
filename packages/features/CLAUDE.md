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
├── seventeenlands_zscores.py         # FormatWRDistribution / CardZScores
│                                     # + compute_format_wr_distribution + zscore_stats
├── categories.py                     # Card-classification predicates
│                                     # (is_ramp / is_removal / has_alt_mode / mv_* / …)
└── feature_builder.py                # 196-feature XGBoost row assembler
                                      # (build_deck_features / build_hand_features /
                                      # build_simulation_features / build_feature_row)
scripts/
├── inspect_shrinkage.py              # Eyeball the shrinkage on real (set, format) data
└── smoke_feature_builder.py          # End-to-end smoke: builds a 200-column row
                                      # on real TLA data + a hand-built 40-card deck
tests/
├── _factories.py                     # Hand-built ParsedCard fixtures
│                                     # (basic/dual lands, creatures, cantrips, etc.)
├── test_categories.py
├── test_feature_builder.py
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

## feature_builder

The XGBoost feature row assembler. The catalogue of features (and the
naming conventions) lives in `features_list.md`; this module
implements it.

`build_feature_row(hand, deck, aggregate_stats, shrunk, zscores,
on_the_play, mulligan_number, event_type, set_code)` returns a flat
`dict[str, float]` of 202 columns (the three context one-hot families
counted as one feature each):

* **Context (3 / 9 columns)** — `on_the_play` + one-hot event_type
  + one-hot set_code. Default vocabularies cover the five current
  Premier Draft sets (`DEFAULT_KNOWN_SETS = ("TMT", "ECL", "TLA",
  "SOS", "MSH")`) and three event types; new sets at inference time
  produce all-zero columns rather than blowing up the row's shape.
  SOS + MSH were appended in the `FEATURES_SEMANTICS_VERSION` 1 -> 2
  bump (roadmap Step 2), which is why the row grew from 200 to 202
  columns; before it, SOS trained as the all-zero reference category
  and MSH was indistinguishable from it at inference.
* **Deck-level (16)** — curve percentages, removal %, color counts,
  avg WR of spells (shrunk).
* **Hand-level (72)** — 13 basic counts + 42 role-by-MV grid
  + 11 Z-bucket / max / avg performance summaries + 4 hand-quality
  additions + 2 color-availability additions.
* **Simulation-sourced (105)** — 7 game-level mana-availability
  features + 96 per-turn castability features (broad buckets carry
  both an `all` and a `high_oh` variant; narrow buckets are `all`
  only) + 2 additional castability summaries.

Per-turn castability iterates over **deck spells** (not hand
spells) keyed by name, reading
`aggregate_stats.by_card_name[name].p_castable_in_snapshot_by_turn`
— the simulator's per-game marginal that already counts both
opening-hand and drawn instances. Two copies of the same name in
the deck collapse to a single per-name marginal because the
underlying simulator aggregate is per-name; the `p_any` aggregator
treats different names as independent (`1 - prod(1 - p)`), the
same natural approximation the prior hand-only path used.

The previous semantics — iterating only over opening-hand cards
using `p_castable_by_turn` (the per-instance conditional-on-in-hand
probability) — surfaced a misleading 0 for "P(any creature
castable on T2)" whenever the opening hand happened to lack a
matching card, even when the deck was full of them. The simulator
already knew the answer was nonzero (drawn cards are evaluated in
the snapshot); the feature builder just wasn't reading the right
aggregate. The shift to deck-wide marginal answers the user-visible
question "did the simulation have a 2-drop to cast on T2?" honestly.

**Note on model retraining:** the keys (`p_any_creature_t2`,
`avg_count_creature_t2`, etc.) didn't change, but their
*distribution* did — deck-wide values are typically much higher
than the old hand-only values. A model trained on the old feature
distribution will produce subtly miscalibrated predictions on
these features until retrained. Retraining is a separate step;
the simulator and feature row are correct as of this change.

The `high_oh` filter restricts a broad-bucket category to hand cards
with shrunken OH WR z-score > 0.5, using the `zscores` map keyed on
arena_id (mtga_id).

Category predicates live in `categories.py` — `is_ramp`,
`is_removal`, `is_pump_broad`, `is_card_manipulation`, `has_alt_mode`,
and the MV-bucket helpers. They're broken out so the builder stays
focused on the bookkeeping; new categories land there first.

### Run the smoke test

```
.venv/Scripts/python.exe packages/features/scripts/smoke_feature_builder.py
```

Builds a 200-column row on real TLA data and a hand-built 40-card
deck. Useful for eyeballing feature values after pipeline changes.

### Known limitations

* MTGJSON's `arena_id` lag affects the current shipping sets (TLA,
  ECL, TMT all have `arena_id=None` on their `ParsedCard` JSON
  today). The shrunk-WR / z-score dicts are keyed by `arena_id`, so
  every hand card's lookup returns None and the
  `avg_*_wr_of_*` / Z-bucket-count features fall to 0. Once MTGJSON
  refreshes (or a name-based join lands), the values will populate
  naturally — no code change needed.
* The `is_other` castability bucket on T2–T4 reads
  `role_features.is_other` directly. Cards the parser couldn't fully
  classify and which left `is_other` False (typically those with a
  populated non-creature-removal effect) won't be counted.

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

## Feature semantics version — when to bump `FEATURES_SEMANTICS_VERSION`

`FEATURES_SEMANTICS_VERSION` (an `int` in
`src/mulligan_coach_features/__init__.py`, currently `1`) identifies the
*meaning* of a `build_feature_row` output. Like the simulator's version,
it's stamped into feature-cache `_meta.json` sidecars and model
`metadata.json` so stale caches and train/serve skew are caught rather
than silently corrupting predictions.

**Bump it — in the SAME PR as the change — on any change to the values
or the column set `build_feature_row` emits for fixed inputs.** That
includes:

* adding / removing / renaming a feature column;
* changing how any existing feature is computed (the deck-wide
  castability change is the canonical trap — the *column names* were
  unchanged but the *distribution* shifted, so a model trained on the old
  values was subtly miscalibrated);
* changing `DEFAULT_KNOWN_SETS` / `DEFAULT_KNOWN_EVENT_TYPES` — the
  one-hot context vocabulary is part of the row's meaning, so a new set
  added there changes the encoding of every row.

Pure refactors that leave every emitted value identical do not bump.
