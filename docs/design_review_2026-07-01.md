# Design review — 2026-07-01 (Claude Fable 5)

Big-picture review of architecture, abstractions, error handling, testing,
performance, and security. Style/formatting deliberately excluded.
Saved so the findings survive context compaction; see also the memory entry
`project_design_review_2026-07` in Claude's memory directory.

**Context at review time:** production verdict path is
`RecommendationService.recommend_choice` (choice model, weights published under
the slot name `choice_v6`) called by both `website/app.py` and
`overlay/coordinator.py`. Win model (`all3_v2`) is legacy/best-effort.
choice_v8 (TLA+TMT+SOS) was being trained by a separate agent
(`packages/model/scripts/tune_choice_v8.py` — training only, reads existing
`data/processed/choice_training/` caches, does not run the simulator).

**Overall verdict:** well-architected. Clean unidirectional package
boundaries, strong empirical-validation culture, deliberate edge error
handling, unusually hardened auto-updater. The dominant systemic risk:
**pipeline correctness depends on invisible consistency between simulator
semantics, feature semantics, cached parquet, trained models, and card
encodings — and nothing records or enforces that consistency.**

---

## Top 5 (ranked)

### 1. No version lineage across simulator → feature cache → model
- **Risk:** silent training corruption. Already happened: choice_v7 (and v8)
  trained on TLA/TMT caches predating sim changes #57/#58 mixed with fresh SOS
  caches; the deck-wide castability change in `feature_builder.py` changed
  feature distributions under unchanged column names. Resume logic in
  `feature_matrix.py` treats existing chunks as authoritative, so a resumed
  run after a sim change stitches two semantics into one shard.
- **Fix:** `PIPELINE_VERSION` dict (e.g. `{"simulation": N, "features": M}`,
  manually bumped on semantics changes; habit enforced via CLAUDE.md note).
  Write into chunk parquet metadata (or `_meta.json` sidecar per shard dir) in
  `feature_matrix.py` / `choice_feature_matrix.py`; refuse resume on mismatch
  (offer `--overwrite`); write into model `metadata.json` in `train.py` /
  `choice_train.py`; warn loudly at bundle load / service startup on mismatch.
  Grace path needed: treat missing stamp as "legacy, warn" (all existing
  shards are unstamped) or do a one-time sidecar backfill.
- **Effort:** ~1 day. **Impact: highest.**

### 2. Train/serve skew: set one-hot vocabulary + arena_id-keyed stats
- **Facts:** `DEFAULT_KNOWN_SETS = ("TMT", "ECL", "TLA")` in
  `feature_builder.py:83`. SOS trains as the all-zero reference category
  (v7 and v8). MSH (live since 2026-06-26) also encodes all-zero at
  inference → model applies SOS's learned format offset to MSH decks; all
  unknown sets collide. Separately, `FormatStats` (`recommend/service.py:163`)
  keys shrunk WR / z-scores by `arena_id`; when MTGJSON lags, ~15 per-card
  features silently zero out — and skew is surface-dependent: overlay
  backfills arena_ids from Arena's SQLite (`card_index.py`), website
  ParsedCard JSONs don't, so the same hand can produce different feature rows
  on different surfaces (and vs training).
- **Fix:** (a) add new sets to `DEFAULT_KNOWN_SETS` at encoding time; assert
  in `choice_train.py` that every training row's `expansion` is in the
  vocabulary. (b) key `FormatStats` by card name using the existing
  `StatsLookup.match` three-tier fallback instead of arena_id-only —
  removes the MTGJSON dependency from inference. (c) log per-recommendation
  stats coverage ("38/40 deck cards have WR data").
- **Effort:** small (a: 1h; b: ~1 day). **Impact: high** (quality during the
  early weeks of each format — when a mulligan coach matters most).

### 3. Simulator is the iteration bottleneck (and docs claim it's vectorized)
- **Facts:** root CLAUDE.md says "fast vectorized (numpy)"; reality is a
  per-game Python loop (`monte_carlo.py:84` → `simulate_one_game`).
  Overlay latency is fine (200 sims ≈ 250 ms). Materialization is not:
  ~17 h/set makes re-materializing after sim fixes so costly it gets skipped
  → direct cause of issue #1's stale-cache incident.
- **Fix order:** (1) cProfile one `simulate()` call — expected hotspot is the
  castability snapshot (per-turn × per-card × per-land-drop mana CSP);
  (2) memoize castability per (mana-signature, mode-cost) within a game; skip
  re-evaluating cards already marked castable (design doc permits);
  (3) numba on the mana solver if needed (already the sanctioned fallback).
  Avoid a full numpy rewrite — would sacrifice policy auditability.
  Fix the root CLAUDE.md sentence regardless. The equivalence harness
  (`packages/simulation/scripts/equivalence_harness.py`) makes perf work safe.
- **Effort:** medium. **Impact: high** (makes clean-cache discipline livable).

### 4. Doc drift + live/legacy dual path in the production-critical layer
- **Facts:** `overlay/CLAUDE.md`, `recommend/CLAUDE.md`, `recommend/README.md`,
  and `coordinator.py` docstring all present `recommend_asymmetric` as
  production; production is `recommend_choice`. `recommend/CLAUDE.md` claims
  no tests exist (there's `tests/test_recommend_reload.py`). Win-model
  machinery (baseline, +4pp bias, floor, prefetch cache) is legacy but not
  marked as such; occupies ~half of `service.py` and the prime real estate of
  website CLAUDE.md. Model *slot* hardcoded as `choice_v6` in `_frozen.py` +
  `_DEFAULT_CHOICE_MODEL_DIR` — shipping new weights to EXE users means
  publishing under the old name.
- **Why it matters here:** development happens via Claude sessions reading
  CLAUDE.md as ground truth — stale docs become wrong future code.
- **Fix:** one doc-sweep session (mark asymmetric path "legacy — analysis
  scripts only"; rewrite recommend/overlay CLAUDE.mds around
  `recommend_choice`); rename slot to something version-neutral
  (`choice_prod`) at the next forced EXE rebuild. Keep asymmetric code for
  now (win model useful as ensemble/sanity signal).
- **Effort:** hours. **Impact: medium-high** (compounds across sessions).

### 5. Production decision layer is the least-tested layer
- **Facts:** `recommend_choice` has one test file (reload only). Untested:
  `_classify_choice_verdict` boundary semantics (0.75 → marginal_keep),
  deterministic seed derivation, 40–42 deck acceptance,
  `opp_mulligan_count_if_known` NaN convention matching training
  (`service.py:~1242`), reload-lock swap under concurrent recommend.
  Real inconsistency found: `coordinator.py:55` `_REQUIRED_DECK_SIZE = 40`
  rejects legal 41-card decks the service accepts (40–42).
- **Fix:** `packages/recommend/tests/test_service.py` with a stub bundle
  (pattern exists in overlay's `test_coordinator.py` FakeService): threshold
  table-test, seed determinism, NaN convention, deck-size bounds. Align
  coordinator to 40–42.
- **Effort:** half a day. **Impact: medium.**

---

## Other findings

- **Silent-degradation house style:** missing format stats → `{}`
  (`service.py:1216`), missing feature columns → NaN at predict, unknown set
  → all-zero one-hot. Each defensible; together the system can lose most of
  its signal invisibly. Cheap fix: `degradations: list[str]` on
  `ChoiceRecommendation`, rendered small in overlay footer (generalizes the
  website's `set_stats_present`).
- **Security (strong overall):** auto-updater does scheme allowlist,
  traversal-safe names, SHA256-before-atomic-swap, size caps; ships data only,
  no code. Trust anchor = GitHub account (manifest unsigned — fine at
  friends-scale). Minor: enforce https for non-localhost manifest URLs;
  website `ScryfallImages` cache unbounded (irrelevant while 127.0.0.1-bound).
- **Boundary leak:** `recommend` imports `model` privates
  (`_library_from_deck`, `_predict_proba`) + re-exports `_deck_signature` etc.
  for analysis scripts. Documented, but promote to public API (~1h).
- **Duplication:** choice pipeline mirrors win pipeline (~2.4k lines);
  website duplicates `decklist.py`/`hand.py`/`data.py` from
  simulation_viewer. Consolidate the MTGA decklist parser first (violates the
  "fix parsing in one place" rule).
- **Card-encoding QA gap:** LLM_ENCODED cards are authoritative hand-written
  data with no golden tests. Add per-set invariant tests (castable ⇒ has cast
  mode; land ⇒ mana abilities or documented reason; role_features consistent
  with types). Commit a small synthetic-deck equivalence baseline so CI can
  catch sim semantic drift (current harness baselines are gitignored).

## Fundamental-structure recommendation

Make **artifact lineage a first-class concept** instead of a documentation
convention — the version-stamp scheme in #1 is the 20%-effort version of DVC
(don't adopt DVC itself: dependency-heavy, uv+parquet workflow already suits
the owner). Everything else — goldfish sim + XGBoost split, rule-based
policies, files-on-disk instead of services, choice model as production
signal — is right as built.

## Suggested order of attack

1. #1 versioning → 2. #2a set-vocab assert (1h, ideally before v8 ships) →
3. #4 doc sweep → 4. #5 service tests → 5. #3 profile, then decide.
