# Roadmap — consolidated plan, order of work, and model allocation

Written 2026-07-02 (Claude Fable 5). This is the entry-point document; the
two detailed plans it sequences are:

* `docs/design_review_2026-07-01.md` — big-picture design review (top-5
  issues with concrete fixes, secondary findings).
* `docs/going_public_plan.md` — plan for public release of the overlay
  (policy/legal, signing, release engineering, ops).

Owner intent: fix the review items AND prepare a public release of the
overlay (website stays local). Both tracks converge: the slot rename,
train/serve skew fixes, and degradation surfacing are prerequisites for
both.

## Snapshot of state when this was written

* Production verdict = choice model via `RecommendationService.recommend_choice`
  (website `app.py` + overlay `coordinator.py`). Win model `all3_v2` is legacy.
* Model weights ship under the version-neutral slot name `choice_prod`
  (`overlay/_frozen.py`, `_DEFAULT_CHOICE_MODEL_DIR`) as of Step 3;
  the slot currently holds the `choice_v6` weights.
* choice_v8 (TLA+TMT+SOS) DONE 2026-07-02 (commit f4faf51): trained on
  fresh TLA/TMT caches, ties v7 on honest held-out eval, resolves the
  mixed-sim-version caveat; NOT promoted. It surfaced a `_grouped_split`
  reproducibility bug (cross-run held-out comparisons silently leak) —
  fold the fix into Step 1's versioning/reproducibility work. SOS still
  trains as all-zero set one-hot (reference category) — Step 2 unchanged.
  Step 0 ("wait for v8") is therefore complete.
* Main repo `vonbeschwitz/mulligan_coach` PRIVATE; public
  `vonbeschwitz/mulligan_coach_data` hosts auto-update artifacts
  (tag `data-current`). EXE = unsigned PyInstaller folder (~325 MB), built
  locally, shared as manual zip. Data auto-updates; EXE has no update channel.
* 2026-07-03: owner decisions recorded in the going-public plan ("Owner
  decisions 2026-07-03" section): no signing at launch (EXE updates
  notify-only until signed), new sets must ship data-only (needs the
  per-card-tolerant `load_parsed_cards` fix), feedback = Google Form +
  Issues on the data repo, usage counting = download-count snapshots
  before `--clobber`. Tray icon + manual-launch balloon shipped
  (`overlay/tray.py`, `--autostart` flag in the Run entry).
* MSH live on Arena since 2026-06-26; MSH decks currently get all-zero set
  one-hots at inference (indistinguishable from SOS to the model).

## Model-allocation principle

**Fable where mistakes are silent, cross-cutting, or one-way; Opus where
failure is loud and feedback fast** (failing tests, CI errors, visible UI).
Preferred workflow on the dangerous items: **Fable designs/specs → Opus
implements against the spec → Fable reviews the diff before merge.** The
dangerous parts are the first and last 10%; don't invert (Opus making
silent-failure design calls, Fable typing code).

Fable-tier work (ranked): (1) simulator perf work under bit-identical
equivalence; (2) anything touching feature semantics / train-serve
consistency; (3) card encoding for each new set (recurring; wrong calls
silently corrupt sim + training); (4) pipeline-versioning *design*;
(5) model training/eval judgment (leakage, calibration); (6) one-way doors:
WotC ToS/FCP compliance analysis, final review before open-sourcing repo
history; (7) later, full EXE self-update.

Opus-tier: doc sweeps, tests, asserts, logging, CI yaml, installer,
manifest version check, first-run wizard, canary, landing page, winget,
ratings automation.

## Ordered work plan

### Step 0 — unblock (now)
* Let choice_v8 finish; ask that agent to commit+push. Manually record v8's
  cache lineage in its metadata/notes (it predates version stamping).

### Step 1 — pipeline versioning (design review #1) — DONE
* `pipeline_versions()` stamps (`simulation` + `features` semantics ints)
  in per-shard `_meta.json` sidecars + model `metadata.json`; materialiser
  refuses resume on mismatch (offers `--overwrite`); training refuses to
  mix mismatched shards unless `--allow-version-mismatch`; bundle load
  warns (never fails). Legacy grace path stamps `unverified_legacy` on
  pre-existing unstamped shards. Also replaced the permutation split with a
  materialisation-invariant `sha256(draft_id)` hash split
  (`draftid_hash_v1`) in both trainers. See
  `docs/specs/step1_pipeline_versioning.md`.
* **Fable: design/spec + final review. Opus: implementation.**

### Step 2 — set-vocabulary fixes (review #2a) — DONE
* `DEFAULT_KNOWN_SETS` extended to TMT/ECL/TLA/SOS/MSH;
  `FEATURES_SEMANTICS_VERSION` bumped 1→2 (first real exercise of Step 1's
  machinery). Both trainers hard-fail on out-of-vocabulary `expansion`
  rows (no bypass flag). `cache_patch.py` +
  `scripts/patch_set_onehots.py` upgrade existing v1 caches' `set_code_*`
  one-hots in place from the stored `expansion` column (idempotent,
  atomic, validated, meta bumped 1→2 with `patch_history`). See
  `docs/specs/step2_set_vocabulary.md`.
* **Owner action after merge:** run the patch tool — dry-run first, then
  apply, tee both logs (see model CLAUDE.md). Production models predate
  v2 and will log a load-time version warning; that is expected and
  harmless (old models ignore the new columns).
* **Fable: spec + review. Opus: implementation.**

### Step 3 — doc sweep + slot rename (review #4) — DONE
* Marked `recommend_asymmetric` legacy everywhere it read as production:
  recommend/overlay CLAUDE.mds, recommend/README, website/CLAUDE.md
  (route claim + a legacy banner on the "Recommendation pipeline"
  section), coordinator.py + events.py docstrings. Fixed
  recommend/CLAUDE.md's false "no tests" claim (it has
  `test_recommend_reload.py`) and the root CLAUDE.md "vectorized numpy"
  claim (reality: per-game Python loop; numba is the fallback).
* Renamed the production model slot `choice_v6` → version-neutral
  `choice_prod` across the whole ship chain: `service.py`
  (`_DEFAULT_CHOICE_MODEL_DIR` + docstrings/errors), overlay `_frozen.py`
  (both env-var defaults), `mulligan_coach.spec` (bundle source + dest),
  `publish_data_release.py` (`_DEFAULT_MODEL_NAME` + rewritten
  version-neutral docstring), `user_data.py`, packaging README, and the
  auto-update docstring examples (manifest.py / runner.py / gui.py). The
  slot holds the current production weights (copied from `choice_v6`;
  `models/` is gitignored so the copy is local-only) and its actual
  training version lives in the dir's `metadata.json`. Promoting a new
  model = copy weights into `models/choice_prod`, no code change. Test
  fixtures that use `choice_v6` as an arbitrary model name were left
  as-is (they exercise the name-agnostic auto-update mechanism and pass
  names explicitly). Landed before the first public EXE.
* **Opus.**

### Step 4 — recommend-service tests (review #5) — DONE
* Added `packages/recommend/tests/test_service.py` (34 tests): verdict-
  threshold boundary table (inclusive-upper-edge semantics via
  `math.nextafter`), `_stable_seed` / `_deck_signature` determinism +
  order-independence + `recommend_choice`'s seed derivation, the
  `opp_mulligan_count_if_known` NaN convention (parametrised over
  play/draw × known/unknown, must match what training cached), and the
  deck-size / hand-size / mulligan-number / model-not-loaded guards.
  `recommend_choice` is exercised end-to-end against a tiny synthetic
  mono-G deck through the REAL `simulate` + `build_feature_row`, with
  only the final XGBoost predictor stubbed — so the captured feature row
  (hence the NaN assertion) is authentic and no trained bundle is needed.
* Aligned overlay `coordinator.py`: `_REQUIRED_DECK_SIZE = 40` → range
  `_MIN_DECK_SIZE = 40` / `_MAX_DECK_SIZE = 42`, matching the service's
  `40 <= len(deck) <= 42`. Added coordinator tests that a 41-card deck
  now reaches the service and a 43-card deck is still rejected
  (`deck_unresolved`, service never called).
* **Follow-up — DONE (standalone, after Step 4):** aligned `website/app.py`
  too. `_REQUIRED_DECK_SIZE = 40` → `_MIN_DECK_SIZE 40` / `_MAX_DECK_SIZE 42`
  (range check + updated error message), stale "expects exactly 40" comment
  fixed, `_validation.html` + `index.html` now show "40-42" via
  `min_deck_size`/`max_deck_size` template vars. Added `/validate` boundary
  tests (41 → no warning, 43 → out-of-range warning).
* **Opus.**

### Step 5 — train/serve consistency (review #2b) + degradation surfacing — DONE
* Re-keyed the 17Lands stats join from `arena_id` (MTGJSON-dependent) to
  **folded card name** — a pure function of `(card name, ratings parquet)`,
  identical across training materialisation, website, and overlay. New
  `packages/features/stats_join.py` (`fold_card_name` + generic
  `stats_for_card` with folded-name match + DFC front-face fallback);
  `shrink_stats` / `zscore_stats` now return `dict[str, …]` keyed by folded
  name (the `dict[int, …]` change rippled through features, model, recommend,
  scripts, and tests). Bumped `FEATURES_SEMANTICS_VERSION` 2 → 3 (values shift;
  column set unchanged). This removes both the training-time zeroing (caches
  materialised with mostly-`None` arena_ids) and the website↔overlay serving
  skew (the overlay backfilled arena_id from Arena's DB; the website didn't).
* Added `degradations: tuple[str, …]` + `stats_coverage: tuple[int,int] | None`
  to `ChoiceRecommendation`, built in `recommend_choice` by `_choice_degradations`
  (four producers: no ratings / partial coverage / set-unknown-to-model /
  pipeline-version-mismatch) with one `log.info` per recommendation (review
  #2c). Rendered as warn-lines + a "17Lands data: k/n spells" summary on the
  website, an amber word-wrapped footer + compact-pill `⚠` on the overlay, and
  one-per-line under the verdict in headless.
* Tests: `fold_card_name` / `stats_for_card` unit tests; folded-name-keyed
  shrink/zscore dicts; a `build_feature_row` regression proving `arena_id=None`
  cards now populate stats features; parametrised degradation tests in
  `recommend`; website `/recommend` render tests. Full suite + ruff + mypy green.
* **Validation:** name-join coverage 1514/1515 (99.93%) across TMT/ECL/TLA/SOS/MSH
  (was ~3–80% via arena_id); the lone miss is TMT "Bespoke Bō", whose ratings row
  stores a literal `?` where the `ō` should be — a parquet data artifact, not a
  fold-fixable case (the spec's 1515/1515 assumed a macron-stripped "o"). Interim
  skew A/B (empty vs name-keyed stats through `choice_prod`): median |Δp_keep| =
  0.09 pp (TLA) / 0.46 pp (SOS), mean 1.0 / 1.5 pp, max ~11–12 pp — well under the
  5 pp flag threshold, so no urgent action beyond the planned retrain. Logs at
  `logs/step5_name_join_coverage.log` + `logs/step5_interim_skew_ab.log`.
* **OWNER ACTION after merge:** re-materialise the win + choice feature caches
  with `--overwrite` (v2 caches refuse resume under v3), retrain both models,
  and promote the new choice model to `models/choice_prod`. Until promotion,
  both surfaces show the pipeline-version-mismatch degradation (`choice_prod`
  is pre-Step-1 unstamped, so it always warned; the new join makes the caveat
  visible rather than silent).
* **Fable: consistency design. Opus: implementation. Fable: review.**

### Step 6 — simulator performance (review #3) — DONE
* ~2.0× on the pure `simulate()` workload (2.25 → 1.15 ms/game on real
  TLA rows), bit-identical: all three equivalence baselines (TLA 50×20,
  TLA 100×40, SOS 50×20 for Prepare) `--check` clean, so
  `SIMULATION_SEMANTICS_VERSION` stays 1 and existing caches remain
  valid. Profile showed the mana CSP at 56%, not the snapshot per se.
  Landed: (1) CSP cache re-keyed from instance-ids to the *ability
  identity sequence* with payments stored as positions and rebound to
  live AbilityRefs on hit — hit rate 62% → 93% and the cache now
  survives across the games of a run (id-reuse ruled out by pinning
  the keyed objects in the value); (2) requirement lists pre-sorted
  once per cost (kills the per-DFS-node sort); (3)
  `available_mana_abilities` hoisted out of the snapshot/L1/picker
  inner loops; (4) duplicate lands share one L1 lookahead; (5) raw
  int-list pools in the DFS. Numba NOT needed. "Skip re-eval of
  already-castable cards" deliberately dropped: it changes traces
  (witness_land_choice) → semantics bump → cache invalidation, a bad
  trade right before the Step-5 retrain. Spec + correctness argument +
  measurements: `docs/specs/step6_simulator_performance.md`; new
  profiling companion `packages/simulation/scripts/profile_hotspots.py`.
* **Fable** (design + the caching-correctness reasoning + review; Opus can
  do mechanical parts under a tight spec).

### Step 7 — going-public Phase 0 (decisions; parallel with steps 1–6)
* Compliance note — DONE 2026-07-03: read current MTGA ToS/CoC + Fan
  Content Policy + 17Lands usage guidelines + Scryfall guidelines; position
  written at `docs/compliance_position.md` (tolerated-class argument, red
  lines, per-source obligations incl. 17Lands day-12 new-set ratings
  embargo, takedown protocol, pre-launch checklist that feeds Step 8).
  **Fable.**
* Decide: open-source main repo? (recommended yes). **Owner decision,
  Fable/Opus assist research.**
* ~~Pick signing route~~ — DECIDED 2026-07-03: skip signing at launch,
  revisit if the tool gets traction. Consequence: EXE update channel is
  notify-only until signed (unsigned self-update is an AV magnet).

### Step 8 — going-public Phase 1 (make it shippable, ~2–4 wks part-time)
* EXE update channel (notify-only): overlay polls the `exe_version.json`
  sidecar `publish_exe_release.py` already uploads to `exe-latest`;
  "new version available" UI + button opening the download/installer.
  No silent self-replacement until signed. **Opus.**
* Per-card-tolerant `load_parsed_cards` (cards/store.py): skip + log
  cards that fail validation, surface the skip count as a degradation —
  makes "new set = data-only push" safe when encodings use new enum
  values on old EXEs. Small but load-bearing for the data channel.
  **Opus implementation; Fable spec'd it (2026-07-03 discussion).**
* CI build pipeline (GH Actions windows runner: sync → build_distribution →
  Inno Setup installer → release + manifest; unsigned for now, signing
  slots in here later). **Opus.**
* First-run wizard (Detailed Logs detection + guide; Arena-missing handling;
  Epic Games Store install path for Raw_CardDatabase; DPI/multi-monitor
  pass). *Tray icon + manual-launch balloon DONE 2026-07-03
  (`overlay/tray.py`; autostart launches stay silent via `--autostart`
  Run-entry flag; tray menu = future home for Check-for-updates /
  Send-feedback / diagnostics / About).* **Opus.**
* Feedback channel: in-app "Send feedback" → pre-filled Google Form
  (versions in URL params); Issues + templates on the public data repo.
  **Opus.**
* Download-count snapshotting in both publish scripts (`gh api` asset
  counts → append-only log, BEFORE `--clobber` which resets them);
  GoatCounter on the landing page. **Opus.**
* Repo hygiene for open-sourcing: full-history secrets scan, anonymize log
  fixtures (screen names/clientMetadata), personal paths. **Opus does the
  sweep with tools; Fable does the final pre-flip review (one-way door).**
* Landing page (GH Pages: GIF, download, SmartScreen note, Detailed Logs
  setup, FAQ, FCP disclaimer, 17Lands/Scryfall attribution); user-facing
  README rewrite. **Opus.**
* Soft-launch as open beta in ONE Limited community (Draft Discord /
  r/lrcast) before broad posting.

### Step 9 — going-public Phase 2 (post-launch iteration)
* "Copy diagnostics" button + rotating log; Arena-update canary ("no events
  parsed for N min while Arena foreground"); AV false-positive submissions;
  winget manifest; scheduled ratings-refresh + publish Action; event-type
  detection + caveat (model is Premier-trained; users will run Sealed/
  Quick/Bo3); Sealed support decision; Discord if a community forms.
  **Opus.**
* Code signing (if the tool gets traction — Azure Trusted Signing vs
  Certum OSS cert), THEN full EXE self-update (swap-on-restart, file
  locking) — self-update is gated on signing per the 2026-07-03
  decision. **Fable** when it happens.

### Recurring (every ~2 months per new set) — the real ongoing commitment
* Encode new set (LLM sessions + audit) → **Fable** (top recurring
  Fable-value task; errors silently corrupt sim + training).
* Ratings refresh → automate (step 9).
* Retrain + eval judgment (leakage/calibration calls) → **Fable** for
  interpretation; scripts are Opus-tier.
* Per-set runbook to be written as part of step 8.

## Top 3 release gates (from going-public plan)
1. Written policy/compliance position.
2. CI release pipeline (signing deferred per 2026-07-03 decision).
3. EXE update channel — notify-only — (incl. slot rename, done) so
   problems are fixable after strangers install.
