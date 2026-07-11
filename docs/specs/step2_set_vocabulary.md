# Spec — Step 2: set-vocabulary fix + cached one-hot patch

Authored by Fable 5 (2026-07-02) per docs_archive/ROADMAP.md Step 2. Implementer:
Opus agent. Reviewer before merge: Fable. Builds on Step 1 (PR #82): this
change is the FIRST real `FEATURES_SEMANTICS_VERSION` bump, so it exercises
the new stamping machinery end to end.

## Problem

`DEFAULT_KNOWN_SETS = ("TMT", "ECL", "TLA")` in
`packages/features/src/mulligan_coach_features/feature_builder.py:83`.
Consequences: SOS trained as the all-zero reference category in
choice_v7/v8; MSH (live on Arena since 2026-06-26) also encodes all-zero at
inference, indistinguishable from SOS; every future unknown set collides
into the same bucket, silently. Nothing errors when a training row's
expansion is outside the vocabulary.

## Design decisions (locked — flag concerns in the PR, but implement as specified)

### A. Vocabulary + version bump

1. `DEFAULT_KNOWN_SETS = ("TMT", "ECL", "TLA", "SOS", "MSH")` — **append**;
   do not reorder existing entries (keeps existing column naming/order
   stable for humans diffing rows).
2. Bump `FEATURES_SEMANTICS_VERSION` 1 → 2 in the same PR (this changes
   `build_feature_row` output: two new columns, and SOS rows' values).
   Update the features CLAUDE.md "known limitations" text that describes
   the old vocabulary.
3. Explicitly NOT in scope: `DEFAULT_KNOWN_EVENT_TYPES` (already complete:
   PremierDraft/Sealed/TradDraft); name-keyed FormatStats (Step 5);
   retraining (a later choice_v9 task — after this PR, training on patched
   caches is what first makes SOS distinguishable).

### B. Train-time vocabulary assert (both trainers)

In `train.train_model` and `choice_train.train_choice_model`, after the
dataframe loads: if any row's `expansion` is not in `DEFAULT_KNOWN_SETS`,
raise `ValueError` naming the offending expansion(s) and their row counts,
with a message directing to (a) add the set to `DEFAULT_KNOWN_SETS` (with
the required `FEATURES_SEMANTICS_VERSION` bump), and (b) re-materialise or
patch the cache. **No bypass flag** — an unrepresentable set silently
training as reference category is exactly the bug class this kills.
(Distinct from Step 1's version check: that catches semantics drift; this
catches coverage gaps. Both must hold.)

### C. Cache patch: library module + thin CLI

New module `packages/model/src/mulligan_coach_model/cache_patch.py`
(importable + tested) plus thin CLI
`packages/model/scripts/patch_set_onehots.py`. Purpose: upgrade existing
v1 caches to v2 semantics WITHOUT re-simulation (~17h/set saved), for both
win caches (`data/processed/model_training/<SET>/<EVENT>/`) and choice
caches (`data/processed/choice_training/<SET>/<EVENT>/`).

Per shard directory:

1. **Eligibility check via `_meta.json`:**
   * meta present, `pipeline_versions == {"simulation": 1, "features": 1}`
     → patch.
   * meta present, features already 2 → skip with "already patched" info
     log (makes re-runs no-ops at the dir level).
   * meta present, any OTHER version combination → refuse the dir with a
     clear error (unknown provenance; don't guess).
   * meta absent (legacy dir) → patch the chunks but leave meta absent
     (stays visibly legacy; training continues to warn on it).
2. **Per chunk (`chunk_*.parquet`), atomically:** read with **pyarrow
   table operations — no pandas round-trip** (avoids dtype/precision drift
   on the ~200 untouched columns). For every set S in the NEW vocabulary,
   set/replace/append column `set_code_{S}` as float64 with
   `1.0 if expansion == S else 0.0` computed from the chunk's stored
   `expansion` column. All other columns must be carried over physically
   untouched. Write tmp sibling + `os.replace` (same pattern as the
   materialiser).
   * Rewriting ALL five columns from `expansion` (not just adding two)
     makes the operation idempotent and self-correcting.
   * Rows whose expansion is outside the new vocabulary: all five columns
     0.0 (matches builder behaviour for unknown sets). Count and report
     them; do not fail.
3. **Post-patch validation, per chunk, before the atomic replace:** row
   count unchanged; for each row `set_code_{expansion} == 1.0` when
   expansion is in vocab; exactly one 1.0 across the five columns (or all
   zero for out-of-vocab); non-`set_code_*` column NAMES unchanged.
4. **Meta bump, per dir, only after every chunk in the dir succeeded:**
   edit the `_meta.json` **as raw JSON** (read dict → update
   `pipeline_versions.features` to 2 → append an entry to a
   `patch_history` list: `{"patch": "set_onehots_v1", "at": <iso>,
   "from_features": 1, "to_features": 2}` → atomic write). Raw-JSON
   editing (not via `ShardMeta`) preserves any unknown keys. Crash between
   chunk patching and meta bump is safe: re-run re-patches idempotently,
   then bumps.
5. **CLI:** `--roots` (default: both cache roots above), `--dry-run`
   (default OFF; prints per-dir eligibility, per-expansion row counts, and
   what would change, writes nothing), and a summary table at the end
   (dirs patched / skipped-already-v2 / legacy / refused, chunks written,
   rows per expansion). Log to stdout; the operator tees.

### D. Docs

* features CLAUDE.md: update vocabulary text; note the v2 bump.
* model CLAUDE.md: short section on the patch script + the train-time
  vocabulary assert.
* docs_archive/ROADMAP.md: mark Step 2 done.

### E. Explicit non-regression note (do NOT "fix" this)

After this PR, inference feature rows contain `set_code_SOS` /
`set_code_MSH`. Production models (choice_v6 slot, v8) were trained
without those feature names; `_predict_proba` builds the XGBoost matrix
from `bundle.feature_names`, so the extra row keys are ignored — SOS decks
remain effectively reference-category for OLD models, exactly matching
their training. No inference behaviour changes for currently-shipped
models. Loading them will emit the Step 1 `version_warning` (features 2 vs
recorded none/1) — expected, log-only, correct.

## Tests (packages/model/tests/test_cache_patch.py + edits)

* feature_builder (packages/features/tests): five one-hot columns emitted;
  SOS and MSH rows one-hot correctly; unknown set → all-zero.
* Trainer assert (both test_train.py + test_choice_train.py): df containing
  an out-of-vocab expansion → ValueError naming it; in-vocab df passes.
* cache_patch, on synthetic chunk parquets (3-column old vocab +
  `expansion` incl. SOS/MSH/out-of-vocab rows, with `_meta.json` at
  features 1):
  * patch produces correct five columns; untouched columns bit-identical
    (compare pyarrow column data before/after); row count/order preserved.
  * meta bumped to 2 with `patch_history`; unknown pre-existing meta keys
    preserved.
  * second run: dir skipped (already v2), chunks byte-identical.
  * legacy dir (no meta): chunks patched, no meta created.
  * meta with unexpected versions (e.g. simulation 2): dir refused.
  * dry-run: nothing on disk changes (compare file mtimes/bytes).
  * crash-recovery simulation: patch chunks but leave meta at 1 (call the
    chunk-level function directly), re-run full patch → succeeds, idempotent.

## Constraints for the implementer

* Feature branch off `main`: `set-vocabulary-v2`. Commit this spec file
  with the implementation. Open a PR; do NOT merge (Fable reviews).
* All four gates green: `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy`, `uv run pytest -q`.
* **Do NOT run the patch script against `data/`** — implementation + tests
  only; the owner runs the real migration after review/merge.
* Do not touch: `models/`, `data/`, `logs/`, untracked scripts.
* Stdlib + pyarrow only (pyarrow is already a workspace dependency).
* Read each package's CLAUDE.md before editing it; match house style
  (typed, docstring-heavy, comments explain why).
* PR description: summary, deviations, test counts, and paste the
  dry-run-style output of the patch tool on the synthetic test fixture so
  the reviewer sees the report format.
