# Elite first-mulligan agreement — results log

Persisted results from `scripts/elite_first_mull_agreement.py`. That script
asks how often the production **choice model**'s keep/mull verdict agrees with
the actual **first-mulligan** decisions (keep-or-mull on the opening 7) made by
*elite* players, per set and event type.

Keep this file updated whenever the eval is re-run (the script only prints to
stdout; nothing is saved unless you `tee` it). Reproduce any row with:

```
.venv/Scripts/python.exe scripts/elite_first_mull_agreement.py --set <SET>
```

## Definitions

- **Elite cohorts** are pinned in the root `CLAUDE.md` ("Elite player cohorts"):
  - *Premier*: `rank in {diamond, mythic}` AND `user_n_games_bucket >= 500` AND
    `user_game_win_rate_bucket >= 0.62`.
  - *Trad*: `user_n_games_bucket >= 500` AND `user_game_win_rate_bucket >= 0.68`
    (no rank filter).
- **Agreement** — model's keep/mull side matches the player's actual choice.
- **Always-keep baseline** — accuracy of trivially predicting "keep" every time
  (= the elite keep rate). The model only adds value above this line, since
  ~75–93% of opening 7s are kept.
- **Lift** — agreement minus the always-keep baseline (pp).
- **Not-complete-disagree** — agreement plus the cases where the model was only
  *marginal* on the wrong side (excludes confidently-wrong: `clear_keep` but
  player mulled, or `clear_mulligan` but player kept).
- **Mull recall / precision** — of the player's actual mulls, how many the model
  flagged (recall); of the model's predicted mulls, how many the player actually
  mulled (precision).
- **In-sample vs out-of-sample** — `choice_v6` was trained on **TLA + TMT**
  (Premier + Trad), confirmed in `logs/tune_choice_v6.log`. SOS is therefore a
  genuine **out-of-sample** test (neither its cards nor its games were seen in
  training); TLA and TMT rows are **in-sample**.

All runs below: model `models/choice_v6`, `n_sims=300`, first-mulligan
decisions only (`mulligan_number == 0`), 7-card hands, 40–42-card decks.
Runs dated 2026-06-27. Replay snapshots: SOS 2026-06-27; TLA/TMT ~2026-05.

## Summary (grouped by event type for in- vs out-of-sample comparison)

| Set | Event | Sample | n | Actual mull % | Pred mull % | **Agreement** | Always-keep | Lift | Not-complete-disagree |
|-----|-------|--------|---|---------------|-------------|---------------|-------------|------|-----------------------|
| TLA | Premier | in  | 4,375 | 9.17%  | 6.54%  | **95.09%** | 90.83% | +4.26  | 98.17% |
| TMT | Premier | in  | 296   | 7.09%  | 5.74%  | **95.27%** | 92.91% | +2.36  | 97.64% |
| SOS | Premier | **out** | 1,546 | 10.74% | 9.96% | **92.50%** | 89.26% | +3.24 | 96.57% |
| TLA | Trad | in  | 975 | 21.33% | 20.92% | **94.87%** | 78.67% | +16.20 | 98.56% |
| TMT | Trad | in  | 246 | 24.80% | 20.33% | **92.28%** | 75.20% | +17.08 | 97.15% |
| SOS | Trad | **out** | 777 | 15.83% | 20.21% | **91.25%** | 84.17% | +7.08 | 96.27% |

### Mull recall / precision

| Set | Event | Sample | Mull recall | Mull precision |
|-----|-------|--------|-------------|----------------|
| TLA | Premier | in  | 59% (236/401) | 83% (236/286) |
| TMT | Premier | in  | 57% (12/21)   | 71% (12/17)   |
| SOS | Premier | out | 61% (102/166) | 66% (102/154) |
| TLA | Trad | in  | 87% (181/208) | 89% (181/204) |
| TMT | Trad | in  | 75% (46/61)   | 92% (46/50)   |
| SOS | Trad | out | 86% (106/123) | 68% (106/157) |

## Headline read

- **Out-of-sample holds up, with modest degradation.** SOS agreement
  (Premier 92.50%, Trad 91.25%) sits ~2–3 pp below the in-sample TLA/TMT
  numbers (Premier ~95%, Trad ~93–95%). Not a collapse — the model still beats
  always-keep on a set whose cards and games it never saw.
- **The gap is concentrated in mulligan _precision_, not recall.** On SOS the
  model still *catches* mulligans about as well as in-sample (recall 61% / 86%),
  but its confident mulligans are less reliable (precision 66% / 68% vs.
  in-sample 71–92%). I.e. out-of-sample it over-calls some keeps as mulls.
- **Trad is consistently mull-happy.** Across all three sets the model predicts
  more mulligans than elite Bo3 players actually take (most visibly SOS Trad:
  20.2% predicted vs 15.8% actual) — plausibly because elites keep iffy 7s more
  aggressively when they can sideboard. In-sample TLA/TMT Trad calibration is
  close on rate but precision stays high, so it costs little there; on SOS the
  same tendency shows up as the lower precision above.
- **Lift over always-keep is much larger in Trad** (+7 to +17 pp) than Premier
  (+2 to +4 pp), simply because Trad mulligans far more often (15–25% vs
  7–11%), so a trivial always-keep is a weaker baseline and the model has more
  room to add value.

## Detail

### SOS — out-of-sample (run 2026-06-27, replay snapshot 2026-06-27)

Log: `logs/elite_sos.log`. SOS card coverage was complete (0 unresolved,
0 simulator-unsafe decks).

**PremierDraft / SOS** — n=1,546, agreement 92.50% (1,430/1,546)

```
verdict             player_kept  player_mulled  total
clear_keep                 1264             35   1299
marginal_keep                64             29     93
marginal_mulligan            34             41     75
clear_mulligan               18             61     79
total                      1380            166   1546
```

**TradDraft / SOS** — n=777, agreement 91.25% (709/777)

```
verdict             player_kept  player_mulled  total
clear_keep                  538              8    546
marginal_keep                65              9     74
marginal_mulligan            30             20     50
clear_mulligan               21             86    107
total                       654            123    777
```

### TLA — in-sample (run 2026-06-27)

Log: `logs/elite_insample_tla_tmt.log`.

**PremierDraft / TLA** — n=4,375, agreement 95.09% (4,160/4,375)

```
verdict             player_kept  player_mulled  total
clear_keep                 3772             71   3843
marginal_keep               152             94    246
marginal_mulligan            41             98    139
clear_mulligan                9            138    147
total                      3974            401   4375
```

**TradDraft / TLA** — n=975, agreement 94.87% (925/975)

```
verdict             player_kept  player_mulled  total
clear_keep                  699              9    708
marginal_keep                45             18     63
marginal_mulligan            18             39     57
clear_mulligan                5            142    147
total                       767            208    975
```

### TMT — in-sample (run 2026-06-27)

Log: `logs/elite_insample_tla_tmt.log`. Small samples (older set; some decks
had unresolvable cards — 6 dropped in Premier, 12 in Trad). Treat as
directional.

**PremierDraft / TMT** — n=296, agreement 95.27% (282/296)

```
verdict             player_kept  player_mulled  total
clear_keep                  263              5    268
marginal_keep                 7              4     11
marginal_mulligan             3              6      9
clear_mulligan                2              6      8
total                       275             21    296
```

**TradDraft / TMT** — n=246, agreement 92.28% (227/246)

```
verdict             player_kept  player_mulled  total
clear_keep                  172              7    179
marginal_keep                 9              8     17
marginal_mulligan             4              9     13
clear_mulligan                0             37     37
total                       185             61    246
```

---

# choice_v7 (TLA + TMT + SOS) — runs 2026-06-28

`models/choice_v7` = the choice_v6 methodology (same sweep, seed=0 split,
Premier-val selection; see `packages/model/scripts/tune_choice_v7.py`) with
**SOS folded into training**. Settings match the v6 runs above (`n_sims=300`,
mn=0, 7-card hands, 40–42-card decks). Replay snapshots: SOS 2026-06-27,
TLA/TMT ~2026-05.

**Sample changes vs v6:** SOS is now **in-sample** for v7 (its games were in
training). TLA/TMT remain in-sample. So the *full-set* SOS agreement below is
in-sample and not directly comparable to v6's out-of-sample SOS number — see
the held-out comparison further down for the fair test.

## Full elite sets — v7 vs v6

| Set | Event | n | v6 agree | v7 agree | v7 actual mull % | v7 pred mull % | v7 lift | v7 not-complete-disagree |
|-----|-------|---|----------|----------|------------------|----------------|---------|--------------------------|
| TLA | Premier | 4,375 | 95.09% | **94.93%** | 9.17%  | 6.61%  | +4.10  | 98.06% |
| TLA | Trad    | 975   | 94.87% | **94.77%** | 21.33% | 21.03% | +16.10 | 98.56% |
| TMT | Premier | 296   | 95.27% | **93.24%** | 7.09%  | 5.07%  | +0.33  | 97.30% |
| TMT | Trad    | 246   | 92.28% | **92.28%** | 24.80% | 20.33% | +17.08 | 96.34% |
| SOS | Premier | 1,546 | 92.50% *(out)* | **94.44%** *(in)* | 10.74% | 7.50% | +5.18 | 97.67% |
| SOS | Trad    | 777   | 91.25% *(out)* | **94.59%** *(in)* | 15.83% | 16.34% | +10.42 | 98.20% |

## Fair held-out SOS — v6 vs v7 on the SAME test-split drafts

Restricted to v7's held-out (test-split) SOS drafts via
`--draft-ids-file models/choice_v7/sos_heldout_draft_ids.txt`. On these rows
v7 never trained (held-out) and v6 never saw any SOS (out-of-sample), so the
two are judged on identical, fair-to-both hands.

| Event | n | v6 agree | v7 agree | v6 not-complete-disagree | v7 not-complete-disagree |
|-------|---|----------|----------|--------------------------|--------------------------|
| SOS Premier | 90 | 91.11% | 91.11% | 95.56% | 96.67% |
| SOS Trad    | 48 | 97.92% | 100.00% | 100.00% | 100.00% |

## Headline read (v7)

- **No regression on the production decision metric.** TLA is a tie (Premier
  94.93% vs 95.09%, Trad 94.77% vs 94.87% — sub-noise on n=4,375/975). TMT
  Premier dips to 93.24% (from 95.27%) but n=296 makes that ~1.6σ; TMT Trad is
  identical.
- **The fair held-out SOS comparison is a wash on this metric** — v6 and v7
  make *identical* keep/mull side-calls on the 90 held-out Premier decisions,
  and v7 is marginally better on "not-complete-disagree" (96.67% vs 95.56%) and
  on Trad (100% vs 97.92%, n=48). The held-out elite subset is tiny (138
  decisions) and the keep/mull-side metric is coarse — most elite 7s are obvious
  keeps — so it cannot resolve the calibration gain.
- **The real SOS win shows up in log-loss, not side-agreement.** On 62,079
  held-out SOS feature rows, v7 cuts log-loss ~9% (0.1935→0.1761 Premier) and
  lifts accuracy 0.919→0.927 vs v6. See
  `models/choice_v7/compare_v6_v7_heldout.log`
  (`packages/model/scripts/compare_choice_v6_v7_heldout.py`). The elite
  agreement check confirms v7 doesn't *break* anything; the log-loss eval is
  where the SOS improvement is measurable.
- **TMT is the one soft spot:** v7 is slightly worse on TMT (Premier lift +0.33
  vs v6's +2.36; held-out log-loss +0.014), consistent with TMT's training share
  shrinking once SOS (the largest set) joined. TMT is small and no longer the
  priority format, so this is an acceptable trade for the SOS gain.

---

# choice_v8 (TLA + TMT + SOS, fresh caches) — runs 2026-07-02

`models/choice_v8` = the choice_v7 methodology (same 6-config sweep, seed=0
grouped split, Premier-val selection; see
`packages/model/scripts/tune_choice_v8.py`) re-run on **freshly
re-materialised TLA/TMT choice caches**, so all three sets now share one
simulator version (current simulator + the 2026-06-30 TLA/TMT
alternative/additional casting-cost encoding fixes). This removes v7's
mixed-simulator caveat (v7's TLA/TMT caches predated sim changes #57/#58).
Settings match the v6/v7 runs (`n_sims=300`, mn=0, 7-card hands, 40–42-card
decks). Replay snapshots: SOS 2026-06-27; TLA/TMT re-simulated 2026-06-30/07-01.

**Simulator-version caveat for cross-model comparison.** These v8 elite runs
use the CURRENT simulator for every set. The v6/v7 numbers above used the OLD
simulator for TLA/TMT (their caches predated the re-materialisation), so the
TLA/TMT rows mix two simulator versions and are only roughly comparable. SOS is
directly comparable across v6/v7/v8 (its cache/sim were unchanged) — which is
why the SOS row is the clean cross-version read.

**Sample status:** TLA/TMT/SOS are all **in-sample** for v8. Same as v6 for
TLA/TMT; unlike v6, SOS is in-sample (as for v7).

## Full elite sets — v8 vs v6 vs v7

| Set | Event | n | v6 agree | v7 agree | v8 agree | v8 actual mull % | v8 pred mull % | v8 lift | v8 not-complete-disagree |
|-----|-------|---|----------|----------|----------|------------------|----------------|---------|--------------------------|
| TLA | Premier | 4,392 | 95.09% | 94.93% | **95.08%** | 9.15%  | 6.56%  | +4.23  | 98.18% |
| TLA | Trad    | 979   | 94.87% | 94.77% | **94.69%** | 21.25% | 20.63% | +15.94 | 97.96% |
| TMT | Premier | 296   | 95.27% | 93.24% | **94.26%** | 7.09%  | 5.41%  | +1.35  | 97.64% |
| TMT | Trad    | 253   | 92.28% | 92.28% | **92.89%** | 24.51% | 21.34% | +17.40 | 97.63% |
| SOS | Premier | 1,546 | 92.50% *(out)* | 94.44% *(in)* | **94.37%** *(in)* | 10.74% | 7.57% | +5.11 | 97.74% |
| SOS | Trad    | 777   | 91.25% *(out)* | 94.59% *(in)* | **94.21%** *(in)* | 15.83% | 16.73% | +10.04 | 97.81% |

(n differs slightly from the v6/v7 rows: the re-materialised replay snapshot has
a few more elite decisions — e.g. TLA Premier 4,392 vs 4,375.)

## Mull recall / precision (v8)

| Set | Event | Mull recall | Mull precision |
|-----|-------|-------------|----------------|
| TLA | Premier | 59% (237/402) | 82% (237/288) |
| TLA | Trad    | 86% (179/208) | 89% (179/202) |
| TMT | Premier | 48% (10/21)   | 63% (10/16)   |
| TMT | Trad    | 79% (49/62)   | 91% (49/54)   |
| SOS | Premier | 59% (98/166)  | 84% (98/117)  |
| SOS | Trad    | 85% (104/123) | 80% (104/130) |

## Held-out log-loss — v6 vs v7 vs v8 (and a split-reproducibility caveat)

`packages/model/scripts/compare_choice_v6_v7_v8_heldout.py` scores all three
boosters on v8's reproduced test split
(`models/choice_v8/compare_v6_v7_v8_heldout.log`). **The cross-model table in
that log is leakage-confounded and must NOT be read as a ranking.**
`_grouped_split` is permutation-index based, so re-materialising TLA/TMT
(changed row counts + non-deterministic `imap_unordered` order) produced a
*different* split than v6/v7 used. Much of v8's test set was in v6's/v7's
*training* set, and the old caches that defined their splits are overwritten
(unreconstructable). The tell: v7 scores ll=0.1579 on v8's test set but 0.1733
on its own held-out — a model can only beat its own held-out when it trained on
the rows.

**Honest comparison — each model on its OWN leakage-free held-out test
(`metadata.json`):**

| Model | held-out test log-loss | brier | acc | n |
|-------|------------------------|-------|-----|---|
| choice_v6 | 0.1702 | 0.0503 | 0.9324 | 76,501 *(no SOS; old caches)* |
| choice_v7 | 0.1733 | 0.0518 | 0.9296 | 138,557 |
| choice_v8 | 0.1736 | 0.0520 | 0.9286 | 139,458 |

v7 and v8 are a **dead heat** (0.1736 vs 0.1733, within noise). v8 matches v7's
quality while removing the mixed-simulator caveat. (Each row is that model's own
held-out population, so not identical rows — but each is leakage-free, unlike
the cross-scored table.)

## Headline read (v8)

- **No regression, and the mixed-simulator caveat is gone.** v8 ties v7 on
  honest held-out log-loss and matches v6/v7 on elite agreement (92–95%
  everywhere).
- **SOS clearly beats v6** on the one clean cross-version elite comparison (SOS
  sim unchanged): +1.9 pp Premier (94.37% vs 92.50%) and +3.0 pp Trad (94.21%
  vs 91.25%), matching v7's SOS gain. Folding SOS into training is the win.
- **TMT recovers slightly vs v7** (Premier 94.26% vs 93.24%; Trad 92.89% vs
  92.28%), though n=296/253 keeps this directional.
- **Still mildly mull-happy in Trad**, but v8's rate calibration is close (SOS
  Trad 16.73% predicted vs 15.83% actual).
- **Action item surfaced:** the split-reproducibility bug means cross-run
  held-out comparisons silently leak. Fix = materialisation-invariant split
  (hash `draft_id` -> bucket) + a retrain of both models under it. Related to
  the invisible-consistency risk in `docs_archive/design_review_2026-07-01.md`.

# choice_v9 (TLA + TMT + SOS, FEATURES-v3 caches) — runs 2026-07-05

`models/choice_v9` = the v7/v8 methodology (same 6-config sweep, seed=0,
Premier-val selection; see `packages/model/scripts/tune_choice_v9.py`) re-run
on the 2026-07-04/05 re-materialisation of all six choice caches
(`logs/remat_20260703/`). Three input changes vs v8 (details in the tune
script's docstring):

1. **FEATURES_SEMANTICS_VERSION 3** — folded-card-name 17Lands stats join
   (roadmap Step 5, PR #87); per-card WR / z-score features no longer zeroed
   on arena_id-less rows.
2. **2026-07-03 card-encoding fixes** — random-commons spot-check
   (triggered-draw clearances on Oroku Saki / April O'Neil Hacktivist, Stone
   Docent, Visionary's Dance) + modal-draw sim wiring (guide §18).
3. **Full set-code one-hots** — `set_onehots_v1` vocabulary (SOS/MSH included);
   203 features vs v8's 201.

v9 is the first tuned model with automatic lineage stamps: `metadata.json`
records `pipeline_versions={simulation:1, features:3}`,
`split_method=draftid_hash_v1`, and per-shard lineage; the sweep now enforces
`check_training_lineage` at load (the earlier tune scripts silently bypassed
it). Winning config unchanged from v6/v7/v8: max_depth=6, lr=0.02,
min_child_weight=5, subsample=0.8; best_iteration=4062.

**Comparison caveats.** (a) The elite evals below run the LIVE pipeline
(v3 features) for v9, while the v8 rows ran the pre-Step-5 pipeline — the
numbers compare deployable systems, not the boosters in isolation. (b) v9's
held-out split is `draftid_hash_v1`; v7/v8 used the permutation split, so
held-out log-losses are not row-comparable across models (each model's own
held-out remains leakage-free). **Sample status:** all three sets in-sample,
as for v8.

## Full elite sets — v9 vs v8

| Set | Event | n | v8 agree | v9 agree | v9 actual mull % | v9 pred mull % | v9 lift | v9 not-complete-disagree |
|-----|-------|---|----------|----------|------------------|----------------|---------|--------------------------|
| TLA | Premier | 4,392 | 95.08% | **95.15%** | 9.15%  | 6.49%  | +4.30  | 98.11% |
| TLA | Trad    | 979   | 94.69% | **95.10%** | 21.25% | 20.63% | +16.35 | 98.47% |
| TMT | Premier | 296   | 94.26% | **95.95%** | 7.09%  | 6.42%  | +3.04  | 97.97% |
| TMT | Trad    | 253   | 92.89% | **92.89%** | 24.51% | 19.76% | +17.40 | 97.63% |
| SOS | Premier | 1,546 | 94.37% | **94.37%** | 10.74% | 7.96%  | +5.11  | 97.87% |
| SOS | Trad    | 777   | 94.21% | **94.21%** | 15.83% | 16.99% | +10.04 | 98.07% |

(Logs: `logs/elite_v9_{TLA,TMT,SOS}.log`. Replay snapshot 2026-06-27 for all
sets, same as the v8 runs, so the n's match v8 exactly.)

## Mull recall / precision (v9)

| Set | Event | Mull recall | Mull precision |
|-----|-------|-------------|----------------|
| TLA | Premier | 59% (237/402)  | 83% (237/285)  |
| TLA | Trad    | 87% (181/208)  | 90% (181/202)  |
| TMT | Premier | 67% (14/21)    | 74% (14/19)    |
| TMT | Trad    | 76% (47/62)    | 94% (47/50)    |
| SOS | Premier | 61% (101/166)  | 82% (101/123)  |
| SOS | Trad    | 85% (105/123)  | 80% (105/132)  |

## Honest held-out (each model's own leakage-free test split)

| Model | held-out test log-loss | brier | acc | n | split |
|-------|------------------------|-------|-----|---|-------|
| choice_v7 | 0.1733 | 0.0518 | 0.9296 | 138,557 | permutation |
| choice_v8 | 0.1736 | 0.0520 | 0.9286 | 139,458 | permutation |
| choice_v9 | 0.1745 | 0.0524 | 0.9284 | 139,087 | draftid_hash_v1 |

Within-noise of v7/v8 (different split scheme + different feature semantics,
so the third decimal is not meaningful across rows).

## Headline read (v9)

- **No regression anywhere; small gains on three of six cells.** TLA Premier
  +0.07pp, TLA Trad +0.41pp, TMT Premier +1.69pp vs v8; TMT Trad and both SOS
  cells land on exactly the same agreement counts as v8 (near-identical
  boosters flip only a handful of verdicts, which netted to zero there).
- **TMT Premier mull recall improves** (67% vs v8's 48%) on an admittedly tiny
  n=21; TLA Trad recall 87% (v8 86%), SOS steady.
- **Still conservatively keep-leaning in Premier** (pred mull ~6.5–8% vs
  actual ~9–11%), unchanged in character from v6–v8; Trad calibration close
  (SOS Trad 16.99% pred vs 15.83% actual).
- **v9 is the promotion candidate**: identical quality to v8 with the Step-5
  stats join live at train AND serve time, single simulator version, full
  set-code vocabulary, and complete lineage stamps — promoting it to
  `models/choice_prod` clears the pipeline-version degradation on the website
  and overlay.

# Borderline verdict band adopted + choice_v9 promoted (2026-07-06)

Owner decision following the per-5%-bucket calibration study
(`scripts/elite_calibration_dump.py`, rows at
`logs/elite_calibration_rows.parquet`): the verdict gains a fifth,
no-judgement **borderline** band, rendered grey on both surfaces. New
thresholds on p_keep (asymmetric around 0.5 because the model's
probabilities are compressed toward the middle relative to elite
behaviour — elites mull the mull-leaning hands more than predicted):

| band | p_keep | observed elite mull rate |
|------|--------|--------------------------|
| clear_keep | > 0.85 | 1.1% |
| marginal_keep | 0.65–0.85 | 21% |
| borderline | 0.45–0.65 | **46% — a coin flip** |
| marginal_mulligan | 0.25–0.45 | 76% |
| clear_mulligan | ≤ 0.25 | 93% |

Effect on the elite deviation counts (all six set×event cells, n=8,243;
borderline excluded from the judged denominators):

| | old 4-band | new 5-band (judged) |
|---|---|---|
| agreement | 94.87% | 96.38% |
| soft keep-direction | 297 (3.6%) | 191 (2.41%) |
| soft mull-direction | 126 (1.5%) | 96 (1.21%) |
| hard clear_keep+mulled | 123 (1.5%) | 70 (0.88%) |
| hard clear_mulligan+kept | 35 (0.4%) | 35 (0.44%) |

Only 3.8% of hands land in the grey band. Caveat: thresholds were
picked and evaluated on the same elite sample — treat the cut points
as reasonable, not optimal. `elite_first_mull_agreement.py` now
reports borderline counts separately and scores agreement over judged
hands only, so post-change eval numbers are NOT directly comparable
to the v6–v9 tables above.

**choice_v9 was promoted to `models/choice_prod`** the same day (old
prod backed up to `models/choice_prod_pre_v9_backup/`). Verified: the
promoted bundle loads with no version warning and `recommend_choice`
returns zero degradations — the pipeline-version caveat both surfaces
showed since Step 5 is cleared.

# choice_v10 (simulation-semantics v2 caches) — runs 2026-07-10

`models/choice_v10` = the v9 methodology re-run on the 2026-07-09/10
re-materialisation of all six choice caches under
`SIMULATION_SEMANTICS_VERSION = 2` (the PR #112 mana-solver
double-tap fix) — see `packages/model/scripts/tune_choice_v10.py`
(PR #113) and `logs/retrain_v2_20260709_210433/`. Same winning config
as v9 (depth 6, lr 0.02, mcw 5, subsample 0.8); the draftid-hash
split reproduced v9's train/val/test rows exactly, so the held-out
comparison is apples-to-apples.

**These runs use the post-2026-07-06 eval semantics** (5-band verdict,
borderline excluded from judged denominators), so per-cell numbers are
NOT comparable to the v6–v9 tables above. The fair v9 reference is the
pooled judged agreement from the borderline-adoption study: **96.38%**
(n=8,243, borderline 3.8%).

## Full elite sets — v10 (judged-only semantics)

| Set | Event | n | judged | borderline % | v10 agree | actual mull % | pred mull % | not-complete-disagree |
|-----|-------|---|--------|--------------|-----------|---------------|-------------|-----------------------|
| TLA | Premier | 4,392 | 4,218 | 3.96% | **96.75%** | 9.15%  | 5.65%  | 98.79% |
| TLA | Trad    | 979   | 941   | 3.88% | **96.60%** | 21.25% | 19.61% | 98.62% |
| TMT | Premier | 296   | 286   | 3.38% | **96.50%** | 7.09%  | 5.41%  | 98.95% |
| TMT | Trad    | 253   | 243   | 3.95% | **95.47%** | 24.51% | 19.76% | 98.77% |
| SOS | Premier | 1,546 | 1,493 | 3.43% | **95.85%** | 10.74% | 7.12%  | 98.73% |
| SOS | Trad    | 777   | 742   | 4.50% | **95.55%** | 15.83% | 16.09% | 98.11% |

Pooled: **7,638 / 7,923 = 96.40%** judged agreement (borderline
320/8,243 = 3.88%) vs choice_v9's pooled 96.38% under identical
semantics — a tie. Hard disagreements: 67 clear_keep+mulled +
36 clear_mulligan+kept (v9: 70 + 35). (Logs:
`logs/elite_v10_{TLA,TMT,SOS}.log`. Replay snapshot 2026-06-27, same
as the v8/v9 runs, so the n's match exactly.)

## Mull recall / precision (v10, judged only)

| Set | Event | Mull recall | Mull precision |
|-----|-------|-------------|----------------|
| TLA | Premier | 68% (214/317) | 86% (214/248) |
| TLA | Trad    | 91% (177/194) | 92% (177/192) |
| TMT | Premier | 69% (11/16)   | 69% (11/16)   |
| TMT | Trad    | 85% (47/55)   | 94% (47/50)   |
| SOS | Premier | 68% (92/136)  | 84% (92/110)  |
| SOS | Trad    | 90% (103/114) | 82% (103/125) |

## Honest held-out (identical draftid_hash_v1 split, identical rows)

| Model | test log-loss | brier | acc | n |
|-------|---------------|-------|-----|---|
| choice_v9  | 0.1745 | 0.0524 | 0.9284 | 139,087 |
| choice_v10 | 0.1735 | 0.0523 | 0.9281 | 139,087 |

Unlike the cross-version rows above, this pair IS row-comparable —
same split scheme, same rows, only the simulation semantics differ.

## Headline read (v10)

- **Elite agreement ties v9** (96.40% vs 96.38% pooled) with slightly
  better held-out fit on identical rows. The sim fix (PR #112) mostly
  affects multi-ability-land decks, a small slice of hands — a wash on
  aggregate elite agreement is the expected outcome.
- **Zero simulator-unsafe decks** across all six cells (v7-era runs
  dropped a few per set) — the encoding fixes + semantics v2 have
  cleaned up the parse path.
- **Eval wall-clock roughly halved** vs the v9 runs (Step-6 simulator
  speedups) — ~6 min for TLA Premier's 4,392 rows at 10 workers.
- **v10 is the promotion candidate**: it carries the semantics-v2
  simulator (what the shipping code now runs — serving v9 on v2 sims
  is a train/serve mismatch), the MSH commons encoding fixes, and full
  lineage stamps ({simulation: 2, features: 3}; bundle loads with
  version_warning=None).

**choice_v10 was promoted to `models/choice_prod` 2026-07-10** (old
prod backed up to `models/choice_prod_pre_v10_backup/`).
