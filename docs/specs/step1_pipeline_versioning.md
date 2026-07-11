# Spec — Step 1: pipeline version stamping + hash-based split

Authored by Fable 5 (2026-07-02) per docs_archive/ROADMAP.md Step 1. Implementer:
Opus agent. Reviewer before merge: Fable.

## Problem

Training features are outputs of code (simulator + feature builder) cached
in parquet for ~17h/set. Nothing records which code version built a cache,
which caches trained a model, or whether the live code matches the model.
Two real incidents: choice_v7 trained on mixed simulator versions; the
deck-wide castability change shifted feature distributions under unchanged
column names. Additionally, `_grouped_split` is permutation-index based, so
a draft's train/val/test assignment depends on the count and first-appearance
order of unique draft_ids — re-materialising a cache reshuffles the split
and makes cross-run model comparisons silently leak (see
`models/choice_v8/LINEAGE.md`).

## Design decisions (locked — do not re-litigate; flag concerns in the PR)

### A. Version constants

1. `packages/simulation/src/mulligan_coach_simulation/__init__.py`:
   `SIMULATION_SEMANTICS_VERSION: int = 1`
   Docstring rule: bump in the same PR as ANY change that alters
   `simulate()` output for a fixed `(hand, library, on_the_play, seed)` —
   policy changes, effect resolution, new mechanics, RNG-consumption
   changes. Formatting/perf changes that keep bit-identical output
   (verified via the equivalence harness) do NOT bump.
2. `packages/features/src/mulligan_coach_features/__init__.py`:
   `FEATURES_SEMANTICS_VERSION: int = 1`
   Rule: bump on any change to `build_feature_row` output values or column
   set for fixed inputs — including `DEFAULT_KNOWN_SETS` changes.
3. New module `packages/model/src/mulligan_coach_model/versioning.py`:
   * `pipeline_versions() -> dict[str, int]` →
     `{"simulation": ..., "features": ...}` (imported from the two packages).
   * `SHARD_META_FILENAME = "_meta.json"` (underscore prefix keeps it out
     of the `chunk_*.parquet` globs).
   * `ShardMeta` dataclass: `pipeline_versions: dict[str, int]`,
     `set_code: str`, `event_type: str`, `n_sims_per_row: int`,
     `created_at: str` (ISO), `unverified_legacy: bool = False`.
   * `write_shard_meta(dir, meta)` — atomic (tmp sibling + `os.replace`),
     `read_shard_meta(dir) -> ShardMeta | None`, and a
     `ShardVersionError(ValueError)` raised by the checks below.
   * JSON on disk is a flat dict; tolerate unknown keys on read
     (forward compat).

### B. Materialiser integration

Apply identically to `feature_matrix.materialize_feature_matrix` and the
choice materialiser in `choice_feature_matrix.py`:

* **Fresh dir (no chunks):** write `_meta.json` before streaming rows.
* **`overwrite=True`:** delete chunks AND `_meta.json`, then write fresh meta.
* **Resume, meta present:** compare `pipeline_versions`, `n_sims_per_row`,
  `set_code`, `event_type` against the current run. Any mismatch →
  raise `ShardVersionError` naming exactly what differs and advising
  `--overwrite`. (n_sims mismatch matters: mixing per-row sim budgets
  mixes feature noise levels.)
* **Resume, chunks exist but NO meta (legacy shard):** log a prominent
  WARNING, then write meta with current versions and
  `unverified_legacy=True` so future runs are checked. Do not block —
  this is the grace path for all pre-existing shards.

### C. Training integration

In `train.train_model` and the choice-model training entry point:

* Derive shard dirs as `{p.parent for p in parquet_paths}`; read each meta.
* Checks (new kwarg `allow_version_mismatch: bool = False`, exposed as
  `--allow-version-mismatch` on `packages/model/scripts/train_choice_model.py`
  and `retrain_all.py`):
  * Dir without meta → WARNING (legacy), recorded in lineage as `null`.
  * Metas disagree with each other, or with live `pipeline_versions()` →
    raise `ShardVersionError` unless `allow_version_mismatch=True`
    (the v7 incident is exactly "cache older than live code" — block it
    by default).
* `metadata.json` additions (both models):
  * `"pipeline_versions"`: live versions at train time.
  * `"shard_lineage"`: list of `{dir, pipeline_versions|null,
    unverified_legacy, n_sims_per_row}` per shard dir.
  * `"version_mismatch_allowed"`: bool.
  * `"split_method"`: `"draftid_hash_v1"` (part D).
* Loaders must tolerate all of these keys being absent (old models).

### D. Materialisation-invariant split

Replace the body of `_grouped_split` in BOTH `train.py` (4-way:
train/val/calib/test) and `choice_train.py` (3-way: train/val/test).
Keep names and signatures; rewrite docstrings.

* Per unique draft_id:
  `u = int.from_bytes(sha256(f"{seed}:{draft_id}".encode()).digest()[:8], "big") / 2**64`
* Assign by cumulative thresholds, ordered: val, then calib (win model
  only), then test, else train. E.g. 3-way: `u < val_frac` → val;
  `u < val_frac + test_frac` → test; else train.
* Properties to preserve/document: a draft's assignment depends only on
  `(seed, draft_id)` — invariant to dataset composition, row order, and
  re-materialisation. Split sizes are now binomial around the target
  fractions rather than exact — fine at our draft counts; existing tests
  that assert exact sizes must be updated to assert approximate fractions
  (± a few %) plus disjointness and determinism.
* Add a module-docstring note: models trained with the old permutation
  split are NOT split-comparable with hash-split models; each model's own
  held-out metrics remain the only honest cross-model comparison.

### E. Load-time check (warn, never fail)

`ModelBundle.load` and `ChoiceModelBundle.load`: if metadata has
`pipeline_versions`, compare to live `pipeline_versions()`; on mismatch or
absence, log one WARNING and set `bundle.version_warning: str | None`.
Never raise — frozen-EXE users legitimately run skewed versions; surfacing
in the UI is ROADMAP Step 5, not this PR.

### F. Docs

* simulation + features CLAUDE.md: short "when to bump the semantics
  version" section (same-PR rule; equivalence harness exemption for
  bit-identical perf work).
* model CLAUDE.md: `_meta.json` shape, check behaviour,
  `--allow-version-mismatch`, hash split + comparability note.
* ROADMAP.md: mark Step 1 done (in the same PR).

### G. Tests (follow existing conventions in each package's tests/)

* versioning: ShardMeta round-trip; read of unknown-keys JSON; missing file
  → None.
* materialiser (both win + choice, using the existing in-memory-DuckDB test
  harness): fresh dir writes meta; resume-on-match proceeds; version
  mismatch raises; n_sims mismatch raises; legacy dir warns + stamps
  `unverified_legacy`; overwrite resets meta.
* training: mixed-meta dirs raise; `allow_version_mismatch=True` proceeds
  and records it; metadata.json contains the new keys.
* split (both variants): same draft_id → same bucket across two datasets
  with different id sets and orders; disjointness; determinism per seed;
  different seeds differ; approximate fractions on ~10k synthetic ids;
  4-way variant covers calib band.
* bundle load: metadata without new keys → `version_warning` set, no raise.

## Constraints for the implementer

* Feature branch off `main` (suggest `pipeline-version-stamping`); when
  green, push and open a PR (do NOT merge — Fable reviews first).
* All four gates green locally before the PR: `uv run ruff check`,
  `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q`.
* Stdlib only (hashlib/json/dataclasses) — no new dependencies.
* Do not touch: `models/`, `data/`, anything under `logs/`, untracked
  `scripts/` debris, or `packages/model/scripts/tune_choice_v*.py`.
* Do not bump the two new constants past 1 — they define "current
  semantics" as version 1.
* Read the relevant CLAUDE.md before editing each package (root rule).
* Match surrounding code style: typed, docstring-heavy, comments explain
  constraints/why (this repo's owner reads the code to learn).
