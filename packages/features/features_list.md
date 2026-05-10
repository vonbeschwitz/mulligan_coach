# XGBoost feature list — total = 196

## Conventions

- "Spell" = any nonland card (creatures, instants, sorceries,
  enchantments, artifacts, planeswalkers). Lands are excluded.
- For cards with extra cast modes (cycling, evoke, flashback, channel,
  …), the *non-casting* mode counts as a castable spell but **not**
  as a castable creature, even when the printed card type is creature.
- For OH WR, GIH WR, GD WR everywhere below, use the **shrunken**
  versions from `seventeenlands_shrinkage.shrink_stats`. Z-scores are
  computed by `compute_format_zscores` against those shrunk WRs,
  per `(set, event_type)`.
- "Has an alternative mode" means `len(parsed_card.modes) > 1` —
  i.e. the card encodes cycling, land-cycling, channel, or an alt-cost
  cast (evoke / flashback / etc.). Adventure / MDFC / "choose one"
  cards are NOT counted as multi-modal in v1 (parser limitation).
- "Removal" everywhere = destroy / exile / burn / bounce / top-of-library /
  removal aura / counterspell. Once `is_counterspell` lands in the
  parser this will be a clean union of role flags.
- "Castable on turn T" comes from the simulator's per-card
  `p_castable_by_turn[T]`. "Avg count castable" sums the expected
  per-card castability across the matching subset of the hand.

---

## Context features — count = 3

1. `on_the_play` (bool).
2. `event_type` — one-hot: PremierDraft / Sealed / TradDraft.
3. `set_code` — one-hot: TMT / ECL / TLA / … (one column per known
   set; new sets at inference time get all-zeros).

(Counting the one-hot families as one feature each for budgeting; the
actual training matrix has more columns. `mulligan_number` is in the
hand-level basic section below.)

---

## Deck-level features — count = 16

Curve features (% of nonland cards):

1. `pct_lands_in_deck` — uses % rather than count to handle 41+ card
   decks.
2. `pct_creatures_in_deck`.
3. `pct_removal_in_deck` (destroy / exile / burn / bounce / top-of-library /
   removal aura / counterspell).
4. `pct_spells_mv_le_2`.
5. `pct_creatures_mv_le_2`.
6. `pct_spells_mv_eq_3`.
7. `pct_creatures_mv_eq_3`.
8. `pct_spells_mv_4_5`.
9. `pct_creatures_mv_4_5`.
10. `pct_spells_mv_ge_6`.
11. `pct_creatures_mv_ge_6` *(symmetry add)*.

Color shape:

12. `n_main_colors_in_deck` — colors required by more than 3 spells.
13. `n_total_colors_in_deck`.

Hand-quality summaries:

14. `avg_oh_wr_of_spells` (shrunk).
15. `avg_gd_wr_of_spells` (shrunk).
16. `avg_gih_wr_of_spells` (shrunk) *(symmetry add)*.

---

## Hand-level features

### Basic hand composition — count = 13

1. `mulligan_number` — number of mulligans previously taken (0 = kept
   the first 7).
2. `n_lands_in_hand`.
3. `n_nonbasic_lands_in_hand`.
4. `n_spells_in_hand`.
5. `n_ramp_spells_in_hand` — mana dork, mana rock, or any mode that
   fetches a land into play. Computed via a `is_ramp(card)` adapter
   over `parsed_card.modes` + `mana_abilities`.
6. `n_multi_modal_spells_in_hand` — `len(card.modes) > 1`.
7. `n_spells_mv_ge_6_no_alt_mode_in_hand`.
8. `n_spells_mv_ge_6_with_alt_mode_in_hand`.
9. `n_creatures_mv_le_2_in_hand`.
10. `n_creatures_mv_3_in_hand`.
11. `n_creatures_mv_4_in_hand`.
12. `n_creatures_mv_5_in_hand`.
13. `n_creatures_mv_ge_6_in_hand` *(symmetry add)*.

### Role-by-MV counts — count = 42

For each role below, three counts by mana value: `mv_0_2`, `mv_3`,
`mv_4_5`. (14 roles × 3 buckets.)

1. Destroy or exile removal (`removal_destroy_or_exile`).
2. Burn spell (`removal_burn_damage is not None`).
3. Bounce (`is_bounce`).
4. Top library (`is_top_library`).
5. Removal aura (`is_removal_aura`).
6. Punch / fight spell (`is_punch_fight`).
7. Pump spell (combat trick — `combat_trick_*` populated).
8. Pump aura (`is_pump_aura`).
9. Equipment (`is_equipment`).
10. Vehicle (`is_vehicle`).
11. Saga (`is_saga`).
12. Class (`is_class`).
13. Planeswalker (`is_planeswalker`).
14. Card draw or manipulation (`cards_drawn > 0 or cards_manipulated > 0`).

### Performance-based hand features — count = 11

Z-scores are computed within `(set, event_type)` against the
shrunken WR distribution. **Buckets are non-overlapping**: each card
lands in exactly one bucket per WR field.

OH WR Z-score bucket counts (4):

1. `n_spells_oh_z_gt_1_3` — Z > 1.3.
2. `n_spells_oh_z_0_4_to_1_3` — 0.4 ≤ Z ≤ 1.3.
3. `n_spells_oh_z_neg_0_7_to_0_4` — -0.7 ≤ Z < 0.4.
4. `n_spells_oh_z_lt_neg_0_7` — Z < -0.7.

GD WR Z-score bucket counts (4):

5. `n_spells_gd_z_gt_1_3`.
6. `n_spells_gd_z_0_4_to_1_3`.
7. `n_spells_gd_z_neg_0_7_to_0_4`.
8. `n_spells_gd_z_lt_neg_0_7`.

Other hand-quality summaries (3):

9. `avg_gih_wr_of_hand_spells` (shrunk).
10. `max_oh_wr_of_hand_spells` (shrunk).
11. `max_gd_wr_of_hand_spells` (shrunk).

(No `avg_oh` / `avg_gd` at hand level by design — those are already
covered by the Z-score bucket counts and the max features.)

### Mana-availability features — count = 7

Sourced from a new game-level aggregator over the simulator trace
(`packages/simulation` extension):

1. `p_land_drop_by_turn_2`.
2. `p_land_drop_by_turn_3`.
3. `p_land_drop_by_turn_4`.
4. `p_land_drop_by_turn_5`.
5. `expected_mana_count_turn_2`.
6. `expected_mana_count_turn_3`.
7. `expected_mana_count_turn_4`.

### Hand-quality additions — count = 4

1. `sum_gih_wr_of_hand_spells` (shrunk) — total hand quality. Differs
   meaningfully from the avg on small mulligan hands.
2. `avg_earliness_score_of_hand` — per-card `OH_WR - GD_WR`,
   averaged over hand spells. Captures how front-loaded the hand is.
3. `max_gih_wr_of_hand_spells` (shrunk) — symmetry with max OH / GD.
4. `avg_shrinkage_weight_of_hand_spells` — mean of
   `ShrunkWinRates.weight` across hand spells. Tells the model how
   much 17Lands data is behind the WR features (lower on new sets).

### Color-availability additions — count = 2

1. `n_double_or_triple_pip_cards_in_hand` — count of hand spells with
   any single color requiring 2+ pips (e.g. `{W}{W}` or `{B}{B}{B}`).
2. `n_distinct_colors_required_by_hand` — distinct colors named in
   the mana costs of hand spells.

---

## Castability features over turns — count = 98

For each turn 1–4, we ask:
- `p_any_castable[category]` — P(at least one card in the matching
  category is castable on this turn).
- `avg_count_castable[category]` — expected number of castable cards
  matching the category.

Both come from the simulator's per-card `p_castable_by_turn[T]`,
filtered down to the matching subset of the hand.

For three "broad" buckets — **any spell**, **creature**, **removal** —
each turn produces both an `all` version (over every card matching
the bucket) and a `high_oh` version (restricted to cards with
shrunken `OH_WR` Z-score > 0.5). The high-WR version is intentionally
omitted for narrower sub-categories where the typical hand has only
0 or 1 matching card.

### Turn 1 — 6 features

3 broad categories × {all, high_oh}, each with `p_any_castable` only
(`avg_count_castable` is uninformative for turn 1 — it equals the
P(any) when at most one card can be cast):

1. P(any spell castable T1) — all.
2. P(any spell castable T1) — high_oh.
3. P(any creature castable T1) — all.
4. P(any creature castable T1) — high_oh.
5. P(any removal castable T1) — all.
6. P(any removal castable T1) — high_oh.

### Turn 2 — 22 features

8 categories. Three are "broad" and get the high-WR split; five do not.

Broad (each: P(any) and avg(count), all + high_oh = 4 features per category):

1. Any spell.
2. Creature (includes anything with a creature on cast — token-makers, etc.).
3. Removal.

Narrow (each: P(any) and avg(count), all only = 2 features per category):

4. Pump (requires creature in play): `combat_trick_*` populated or
   `is_pump_aura`.
5. Equipment / Vehicle (requires creature but can be played without one).
6. Card manipulation (`cards_drawn` or `cards_manipulated` > 0).
7. Other (catch-all `is_other`).
8. Alternative mode (cycling / evoke / etc.).

3×4 + 5×2 = 12 + 10 = **22**.

### Turn 3 — 30 features

10 categories. Five broad, five narrow.

Broad (each: P(any) and avg(count), all + high_oh = 4 features):

1. Any spell, MV 0-2.
2. Any spell, MV 3+.
3. Small creature, MV 0-2.
4. Mid-sized creature, MV 3+.
5. Removal.

Narrow (each: P(any) and avg(count), all only = 2 features):

6. Pump (requires creature).
7. Equipment / Vehicle.
8. Card manipulation.
9. Other.
10. Alternative mode.

5×4 + 5×2 = 20 + 10 = **30**.

### Turn 4 — 38 features

12 categories. Seven broad, five narrow.

Broad (each: P(any) and avg(count), all + high_oh = 4 features):

1. Any spell, MV 0-2.
2. Any spell, MV 3.
3. Any spell, MV 4+.
4. Small creature, MV 0-2.
5. Mid-sized creature, MV 3.
6. Large creature, MV 4+.
7. Removal.

Narrow (each: P(any) and avg(count), all only = 2 features):

8. Pump (requires creature).
9. Equipment / Vehicle.
10. Card manipulation.
11. Other.
12. Alternative mode.

7×4 + 5×2 = 28 + 10 = **38**.

### Additional castability — count = 2

1. `avg_pct_deck_spells_with_colored_mana_by_turn_4` — averaged across
   simulation runs, the fraction of *all* nonland cards in the deck
   whose colored mana is producible by turn 4 in that run.
2. `avg_pct_hand_spells_with_colored_mana_by_turn_4` — same, but
   restricted to spells in the opening hand (counted whether or not
   they're already castable on an earlier turn).

---

## Feature count summary

| Section | Count |
|---|---|
| Context | 3 |
| Deck-level | 16 |
| Hand-level basic | 13 |
| Hand-level role-by-MV | 42 |
| Hand-level performance (Z-buckets + max + avg GIH) | 11 |
| Mana availability | 7 |
| Hand-quality additions | 4 |
| Color-availability additions | 2 |
| Castability turn 1–4 | 96 |
| Additional castability | 2 |
| **Total** | **196** |

196 features against ~1M observations is comfortable for XGBoost.
