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
* Model weights ship under hardcoded slot name `choice_v6`
  (`overlay/_frozen.py`, `_DEFAULT_CHOICE_MODEL_DIR`).
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

### Step 3 — doc sweep + slot rename (review #4)
* Mark `recommend_asymmetric` legacy everywhere (recommend/overlay
  CLAUDE.mds, README, coordinator docstring); fix recommend/CLAUDE.md test
  claim; fix root CLAUDE.md "vectorized numpy" claim.
* Rename model slot `choice_v6` → version-neutral (`choice_prod`) in
  `_frozen.py`, `_DEFAULT_CHOICE_MODEL_DIR`, publish scripts. Must land
  before the first public EXE.
* **Opus.**

### Step 4 — recommend-service tests (review #5)
* `packages/recommend/tests/test_service.py`: verdict-threshold boundaries,
  seed determinism, opp_mulligan NaN convention vs training, deck-size
  bounds. Align `coordinator.py` `_REQUIRED_DECK_SIZE` to service's 40–42.
* **Opus.**

### Step 5 — train/serve consistency (review #2b) + degradation surfacing
* Name-keyed `FormatStats` (reuse `StatsLookup.match` three-tier fallback);
  per-recommendation stats-coverage logging; `degradations: list[str]` on
  `ChoiceRecommendation` rendered in overlay footer + website.
* **Fable: consistency design (must reason about what TRAINING keyed on —
  fixing inference alone manufactures new skew). Opus: implementation.
  Fable: review.**

### Step 6 — simulator performance (review #3)
* cProfile one `simulate()` call (hotspot expected: castability snapshot);
  memoize castability per (mana-signature, mode-cost) within game; skip
  re-eval of already-castable cards; numba on mana solver only if needed.
  Use equivalence harness before/after; remember it only covers replayed
  cases — cache-key design needs real scrutiny.
* **Fable** (design + the caching-correctness reasoning + review; Opus can
  do mechanical parts under a tight spec).

### Step 7 — going-public Phase 0 (decisions; parallel with steps 1–6)
* Compliance note: read current MTGA ToS/CoC + Fan Content Policy + 17Lands
  usage guidelines + Scryfall guidelines; write one-page position
  (tool stays free; read-only line; takedown compliance). **Fable.**
* Decide: open-source main repo? (recommended yes). Pick signing route
  (Azure Trusted Signing ~$10/mo vs Certum OSS cert ~€69/yr — verify terms).
  **Owner decision, Fable/Opus assist research.**

### Step 8 — going-public Phase 1 (make it shippable, ~2–4 wks part-time)
* EXE update channel: `app_version` + download URL in auto-update manifest
  (additive, no schema bump) + "new version available" UI. **Opus.**
* CI build pipeline (GH Actions windows runner: sync → build_distribution →
  sign → Inno Setup installer → release + manifest). **Opus.**
* First-run wizard (Detailed Logs detection + guide; Arena-missing handling;
  Epic Games Store install path for Raw_CardDatabase; DPI/multi-monitor
  pass). **Opus.**
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
  Quick/Bo3); Sealed support decision. **Opus.**
* Full EXE self-update (swap-on-restart, file locking). **Fable** when
  it happens.

### Recurring (every ~2 months per new set) — the real ongoing commitment
* Encode new set (LLM sessions + audit) → **Fable** (top recurring
  Fable-value task; errors silently corrupt sim + training).
* Ratings refresh → automate (step 9).
* Retrain + eval judgment (leakage/calibration calls) → **Fable** for
  interpretation; scripts are Opus-tier.
* Per-set runbook to be written as part of step 8.

## Top 3 release gates (from going-public plan)
1. Written policy/compliance position.
2. Code signing + CI release pipeline.
3. EXE update channel (incl. slot rename) so problems are fixable
   after strangers install.
