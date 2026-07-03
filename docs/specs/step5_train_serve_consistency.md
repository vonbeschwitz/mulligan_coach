# Step 5 spec — train/serve-consistent 17Lands stats join + degradation surfacing

Written 2026-07-02 (Claude Fable 5). Roadmap Step 5, implementing design-review
items #2b (arena_id-keyed `FormatStats`) and the "silent-degradation house
style" finding. Implementation: Opus, against this spec. Final review: Fable.

## Empirical facts this design rests on (verified 2026-07-02)

Do not re-derive these; they were measured directly against the live repo.

1. **The stats join is `card.arena_id -> dict[mtga_id, ...]` everywhere today**
   (`feature_builder.py:_shrunk_for/_zscores_for`), shared by training
   materialisation and inference. `ParsedCard.arena_id` comes from MTGJSON,
   which has NOT ingested any of the five current sets' main cards. Coverage
   in `data/processed/parsed_cards/` today: TMT 20/210, ECL 21/288,
   TLA 61/342, SOS 75/341 (bonus-sheet reprints only — MTGJSON knows old
   printings), MSH 334/334 (fully ingested).
2. **Training caches therefore have partially-live stats features.** First
   chunk per shard, share of rows with nonzero `avg_oh_wr_of_spells`:
   TLA/TMT/ECL ≈ 3–5%, SOS ≈ 80% (SOS bonus-sheet cards are frequent in
   decks). Nonzero values are undiluted WR magnitudes (~0.52–0.57) because
   `_avg_over` averages only over non-None cards. So the trained models saw a
   bimodal distribution: 0.0 for most rows, ~0.55 when a bonus-sheet card was
   present.
3. **`models/choice_prod` genuinely splits on these features** — 96 splits on
   `avg_oh_wr_of_spells`, ~305 splits total across the 41 stats-dependent
   columns (incl. the `*_high_oh_*` castability variants). They are NOT dead
   features; changing their input distribution changes predictions.
4. **The two inference surfaces disagree today.** The overlay's
   `card_index.py` backfills `arena_id` from Arena's own SQLite onto every
   resolved card (`hit.model_copy(update={"arena_id": grpid})`), so overlay
   inference feeds FULLY populated stats features to a model trained on the
   mostly-zero distribution — the overlay has been running with this skew for
   its whole production life. The website loads parsed JSONs directly and
   matches the training distribution. All elite-agreement evals ran through
   the parsed-JSON (website-like) path; the overlay's live regime was never
   evaluated.
5. **Name-based joining is near-perfect.** Across all five sets, 1514/1515
   parsed cards match a ratings row by exact name or front-face
   (`"Front // Back"` → `"Front"`). The single miss is TMT "Bespoke Bō"
   (17Lands spells it without the macron) — fixed by folding diacritics on
   both sides. Ratings parquets contain no basic lands.
6. **TradDraft training shards were built with PremierDraft ratings**
   (`choice_feature_matrix` calls `feature_matrix._build_format_stats`, which
   calls `load_premier_draft_stats`). The service also loads PremierDraft-only
   ratings — that is consistent; keep it.
7. **No cache patch is possible.** The `*_high_oh_*` castability features
   combine the z-score filter with the per-game simulator aggregate, which is
   not stored in the cache. Unlike Step 2's one-hot patch, the v2→v3
   migration below requires full re-materialisation. Do not build a patch
   tool.

## Design

### 1. Name-keyed stats join (shared by training + inference)

Replace the arena_id join with a **card-name join**, making the stats lookup a
pure function of `(card name, ratings parquet)` — independent of MTGJSON lag,
independent of the overlay's Arena-DB backfill, identical across training
materialisation, website, and overlay.

Concretely, in `packages/features`:

* Add a name-folding helper (module-level, exported):
  `fold_card_name(name: str) -> str` = NFKD-normalise, drop combining
  characters (`unicodedata.combining(ch) != 0`). Pure-ASCII names are
  unchanged. This makes "Bespoke Bō" ↔ "Bespoke Bo" match.
* `shrink_stats(...)` returns `dict[str, ShrunkWinRates]` keyed by
  `fold_card_name(stats.name)` (one entry per ratings row — no alias
  entries, because `zscore_stats(shrunk.values())` iterates values and must
  see each card exactly once). Add `name: str` (the unfolded 17Lands display
  name) to `ShrunkWinRates`; keep `mtga_id` as an informational field.
* `zscore_stats(...)` likewise returns `dict[str, CardZScores]` keyed by the
  folded name; add `name: str` to `CardZScores`.
* Add one public lookup helper used by every consumer (feature builder AND
  the recommend service's explanation panel), generic over the value type:

  ```python
  def stats_for_card(card: ParsedCard, table: Mapping[str, T]) -> T | None:
      """Folded-name lookup with DFC front-face fallback."""
      hit = table.get(fold_card_name(card.name))
      if hit is not None:
          return hit
      front = card.name.split(" // ", 1)[0]
      if front != card.name:
          return table.get(fold_card_name(front))
      return None
  ```

* `feature_builder._shrunk_for` / `_zscores_for` delegate to that helper;
  delete `_arena_id`. All `dict[int, ...]` annotations for shrunk/zscores
  become `dict[str, ...]` throughout the workspace (mypy strict will
  enumerate the call sites; expect: `features` itself,
  `model/feature_matrix.py` (`_FormatStats`, `_build_format_stats` — switch
  `by_arena_id.values()` to `by_name.values()`), `model/inference.py`,
  `model/choice_inference.py`, `recommend/service.py` (`FormatStats`,
  `_build_explanation`, `_try_load_format_stats` — use `by_name.values()`),
  plus tests, smoke scripts, and any analysis scripts that build these
  dicts).
* `recommend/service.py::_build_explanation`: the per-card
  `opening_hand_win_rate` lookup switches from `shrunk.get(card.arena_id)`
  to `stats_for_card(card, shrunk)`. (Side effect: the website's per-card
  OH WR column starts populating; the overlay's keeps working without the
  Arena-DB id backfill mattering.)
* `cards.StatsLookup` is unchanged (its `match()` stays for other callers).
  The overlay's arena_id backfill in `card_index.py` is also unchanged — it
  is still needed to resolve grpIds from the log; it simply no longer
  influences feature values.

### 2. Bump `FEATURES_SEMANTICS_VERSION` 2 → 3

Same-PR bump, with a version-history note: "3: 17Lands stats join re-keyed
from arena_id (MTGJSON-dependent) to folded card name. Per-card WR / z-score
features now populate for every card with a ratings row; under v2 they were
zero whenever MTGJSON lacked the printing's arena_id (all main-set cards of
TMT/ECL/TLA/SOS at materialisation time)."

Consequences (all handled by existing Step 1 machinery — no new code):

* Existing v2 caches refuse resume; `--overwrite` re-materialises.
* Training on v2 caches with v3 code refuses without
  `--allow-version-mismatch`.
* `choice_prod` (unstamped, pre-Step-1) already produces a load-time
  `version_warning`; it keeps loading fine.

**Do not build a cache patch tool** (fact 7). Update the features + model
CLAUDE.md "known limitations" notes that describe the MTGJSON-lag zeroing
(they are superseded by this change).

### 3. Transition policy (decided — do not re-litigate in implementation)

Single code path now; heal by retraining. Until the owner re-materialises and
retrains, `choice_prod` receives populated stats features where training was
mostly-zero. Rationale: the overlay — the primary production surface — has
had exactly this skew all along (fact 4), so this unifies the website with
the overlay's long-standing behaviour rather than introducing a new regime;
the degradation surfacing below makes the mismatch visible; and the next
retrain permanently removes it. Gating the join on the model's stamped
features version was considered and rejected: it would preserve TWO behaviour
regimes, and the "consistent" legacy regime is unreproducible anyway (it
depends on MTGJSON's coverage snapshot at each cache's materialisation time).

**Owner action after merge** (record in ROADMAP + PR description): full
re-materialisation of win + choice caches (`--overwrite`) and a retrain, then
promote to `models/choice_prod`. Until promotion, both surfaces show the
version-mismatch degradation.

### 4. Degradation surfacing on `ChoiceRecommendation`

Add to `ChoiceRecommendation` (frozen dataclass — use immutable defaults so
existing constructions in tests keep working):

```python
degradations: tuple[str, ...] = ()
stats_coverage: tuple[int, int] | None = None   # (matched, total) deck spells
```

`degradations` holds short, user-readable strings (≤ ~90 chars each), built in
`recommend_choice` after `_compute_choice_arm`. Four producers, in this order:

1. **No format stats loaded** — `self.stats_by_set.get(set_code) is None`:
   `"No 17Lands ratings loaded for {set_code} — per-card win-rate features are zeroed."`
2. **Partial stats coverage** — stats present but `k > 0` deck spells have no
   ratings row: `"{k} of {n} deck spells have no 17Lands ratings row — their win-rate signal is missing."`
   Coverage counts deck card *instances* whose `categories.is_spell(card)` is
   true (lands excluded — they never feed WR features), matched via
   `stats_for_card(card, shrunk)`. Always populate `stats_coverage=(n-k, n)`
   when stats are present; `(0, n)` when they aren't.
3. **Set unknown to the loaded model** —
   `f"set_code_{set_code}" not in self.choice_bundle.feature_names`:
   `"Model was trained before {set_code} — format-specific adjustments unavailable."`
   (This is deliberately checked against the MODEL's columns, not
   `DEFAULT_KNOWN_SETS`: it catches both an out-of-vocabulary set and an old
   model that predates the vocabulary bump.)
4. **Pipeline version mismatch** —
   `self.choice_bundle.version_warning is not None`:
   `"Model was trained on an older feature pipeline — retrain pending."`
   (Keep the full `version_warning` text in the log line, not the UI string.)

**Per-recommendation logging**: one `log.info` in `recommend_choice`, e.g.
`"recommend_choice: set=%s coverage=%d/%d degradations=%s"` (log `[]` cleanly
when none). This is review item #2c ("log per-recommendation stats coverage").

The legacy `recommend_asymmetric` path gets none of this (out of scope).

### 5. Rendering

* **Website** — `_recommendation.html`: replace the `set_stats_present` warn
  span with a loop over `rec.degradations` rendered as small `warn` lines
  (keep the surrounding "Format: … · on the play …" paragraph). Show
  `stats_coverage` inline when present, e.g. `17Lands data: 21/23 spells`.
  Drop the now-unused `set_stats_present` from the `/recommend` route context
  in `app.py` (both the success path and the model-not-loaded path);
  `_validation.html` and `/validate` are untouched.
* **Overlay** — `gui.py` expanded layout only: a new small word-wrapped label
  under `_context_label` (≈10px, amber `#d9a648`-ish, hidden/empty when no
  degradations), text = the degradation strings joined with `"  ·  "`. In the
  compact pill, when degradations are non-empty append a single `" ⚠"` to the
  pill text — no room for prose. Clear the label in `_render_computing` /
  `_render_missing` / `_render_reset`. No Qt tests exist for gui.py; verify
  by running the overlay pointed at a fixture log if feasible, otherwise note
  manual-verification status in the PR.
* **Headless** — `headless.py`: print degradations one-per-line under the
  verdict (cheap, helps debugging).

## Tests (required)

* `packages/features`: `fold_card_name` (identity on ASCII, strips macron);
  `stats_for_card` (exact hit, DFC front-face fallback, diacritic fold hit,
  miss → None); `shrink_stats`/`zscore_stats` return folded-name-keyed dicts
  with the new `name` field; **regression test for the whole point**:
  `build_feature_row` on a hand/deck whose cards have `arena_id=None`
  produces nonzero `avg_*_wr_*` / z-bucket features when the name-keyed
  table has the rows.
* `packages/recommend` (`test_service.py`, follow the existing stub-predictor
  pattern): degradations parametrised — no stats → producer 1 +
  `stats_coverage=(0, n)`; partial table → producer 2 with exact counts;
  bundle without `set_code_<set>` in `feature_names` → producer 3; bundle
  with `version_warning` → producer 4; fully-healthy call → `()` and full
  coverage. Defaults: constructing `ChoiceRecommendation` without the new
  fields still works.
* `packages/website`: `/recommend` route test asserting a degradation string
  renders (stub service, existing pattern) and that a healthy response shows
  none.
* Full gates: `uv run pytest`, `uv run mypy`-equivalent used by pre-commit,
  `ruff check` AND `ruff format --check` (CI runs both).

## Validation (run, tee to `logs/`, report numbers in the PR)

1. **Join coverage**: script over all five sets — every parsed card vs its
   PremierDraft ratings parquet through `stats_for_card`; expect 1515/1515
   (Bespoke Bō now matching). Tee to `logs/step5_name_join_coverage.log`.
2. **Interim-skew A/B**: for TLA and SOS, build a few plausible 40-card decks
   (17 basics + 23 spells from parsed cards; or adapt
   `scripts/debug_recommend.py`), sample ~20 smoothed hands each, and compute
   `choice_prod` p_keep twice per hand: (a) empty shrunk/zscores dicts
   (≈ training-baseline / old-website behaviour) and (b) the new name-keyed
   tables. Report mean/median/max `|Δp_keep|` per set. Tee to
   `logs/step5_interim_skew_ab.log`. This quantifies what the website's users
   see between merge and retrain — it does NOT gate the merge (the overlay
   already lives in regime (b)), but if median `|Δ|` exceeds ~5 pp, flag it
   prominently in the PR description so the owner prioritises the retrain.

## Docs to update in the same PR

* `packages/features/CLAUDE.md` — known-limitations MTGJSON note (superseded),
  version history 1→2→3, describe the name-keyed join + `fold_card_name`.
* `packages/model/CLAUDE.md` — known-limitations arena_id note (superseded).
* `packages/recommend/CLAUDE.md` — FormatStats keying, degradations +
  coverage fields, logging.
* `packages/overlay/CLAUDE.md` + `packages/website/CLAUDE.md` — degradation
  rendering; correct any "keyed by arena_id" claims.
* `docs/ROADMAP.md` — mark Step 5 DONE with a summary + the owner action
  (re-materialise + retrain + promote).

## Out of scope

* Retraining / re-materialising (owner action; needs ~days of compute).
* Any cache patch tool (impossible — fact 7).
* Event-type-aware ratings (TradDraft stats parquets exist but training used
  PremierDraft ratings; keep parity).
* `/validate`-time degradations, `recommend_asymmetric` degradations.
* Changing `cards.StatsLookup` or the overlay's arena_id backfill.
