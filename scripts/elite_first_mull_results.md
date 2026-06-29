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
