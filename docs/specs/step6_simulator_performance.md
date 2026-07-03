# Step 6 — simulator performance (design review #3)

Written 2026-07-03 (Claude Fable 5). Design + correctness reasoning for
the roadmap's Step 6: make the goldfish simulator fast enough that
re-materialising feature caches after a sim fix stops being a
multi-hour deterrent, WITHOUT changing a single bit of output.

**Hard constraint:** `simulate()` output must stay bit-identical for a
fixed `(hand, library, on_the_play, seed)`. Verified with
`packages/simulation/scripts/equivalence_harness.py` (three pre-change
baselines: TLA 50×20, TLA 100×40, SOS 50×20 — the SOS corpus exercises
the Prepare mechanic). Because output is unchanged,
`SIMULATION_SEMANTICS_VERSION` does NOT bump and existing feature
caches stay valid.

## Profile findings (pre-change)

Corpus: 50 real TLA rows × 20 sims (the harness corpus), warm run.
Un-profiled wall clock 2.25 ms/game; cProfile totals below (5.64 s,
overhead ~2.5×, relative shares are what matter):

| Where | Share | Detail |
|---|---|---|
| `can_pay_cost` → `_search` (mana CSP) | ~56% | 144.7k calls, only 62% hit rate on the per-game cache; 54.3k DFS runs |
| `sorted(...)` | ~27% cum | mostly `_try_satisfy` re-sorting the same requirement list on **every DFS node** (1.14M priority-lambda calls) |
| `available_mana_abilities` | ~13% | 135.7k calls — recomputed per (card × mode × candidate-land) in the snapshot and per hand card in the L1 lookahead, though it only depends on the battlefield |
| `ManaPool.copy` | ~10% | 768k copies, one per DFS goal-check |

The castability snapshot and L1 land-lookahead both funnel into the
CSP, so the CSP cache's hit rate is the single biggest lever.

## Why the old cache key wastes hits

The per-game key was `(id(cost), sorted (id(ability), source.instance_id)
pairs)`. Including `instance_id` means two states whose mana is
*identical in kind* — e.g. "2 Forests + 1 Plains" reached via Forest #3
vs Forest #8 as the hypothetical land drop — get different keys. Worse,
`instance_id`s reset every game, so 20 sims of the same deck can share
nothing and the cache must be cleared per game (a cached `ManaPayment`
holds `AbilityRef`s to the previous game's `Card` objects — consuming
one across games would tap dead objects).

## Change 1 — identity-sequence cache key + position-encoded payments

New key: `(id(cost), tuple(id(ref.ability) for ref in sorted_abilities))`
where `sorted_abilities` is the cmc-sorted list the DFS actually walks.
New cached value: `(cost, tuple(ability objects), payment-as-positions | None)`
where payment-as-positions is `((position_in_sorted_abilities, chosen_option), …)`.
On a hit, positions are rebound to the *current* call's `AbilityRef`s:
`[(sorted_abilities[pos], option) for pos, option in cached]`.

### Correctness argument (this is the part that needs scrutiny)

1. **The DFS is a pure function of (cost identity, post-sort ability
   identity sequence).** `_search` reads abilities only through
   `ability.cost.mana` and `ability.produces` — both attributes of the
   `ManaAbility` object, never of the source `Card`. The requirement
   list comes from `_expand_cost(cost)`, a pure function of the cost
   object. Branching order is deterministic. Therefore two calls whose
   sorted ability lists have the same *identity sequence* produce the
   same winning position-pattern and the same option choices — and
   rebinding those positions into the current sorted list selects
   exactly the sources a fresh DFS would have tapped. Traces
   (`payment_sources` in `SpellCastEvent`) stay bit-identical.
2. **The key must be the exact post-sort sequence, not a canonicalised
   multiset.** Sorting the ids (or otherwise canonicalising) would let
   two states with different DFS walk orders share an entry; the
   position-pattern from one order can select different abilities in
   the other. Order-sensitive keys forgo a few hits to stay exact.
3. **`id()` reuse is impossible while an entry lives**, because the
   value stores strong references to the cost and every ability in the
   key. A matching id therefore proves object identity — same guard
   pattern as the existing `_EXPAND_COST_CACHE`, extended to the
   ability tuple. This makes the cache correct with **no reset at
   all**; clearing is purely memory hygiene.
4. **Cache lifetime moves from per-game to per-`simulate()` call.**
   `engine.simulate_one_game` no longer clears; `monte_carlo.simulate`,
   `monte_carlo.iter_traces`, and `mulligan.simulate_mulligan_from_deck`
   clear at entry (bounds memory to one deck's shape diversity, ~10³
   entries). All n_runs games of one deck now share the cache — the
   materialiser's inner loop is exactly this shape. Population order of
   a pure-function cache cannot affect any individual result, so
   cross-game reuse is equivalence-safe.

## Change 2 — pre-sort requirement lists once per cost

`_try_satisfy` / `_try_satisfy_inplace` ran
`sorted(reqs, key=_REQ_PRIORITY[...])` on every DFS node. The
requirement list for a given cost never changes, so `_expand_cost` now
stores it already priority-sorted and the per-node sorts are gone.
`sorted` is stable, so pre-sorting once yields byte-for-byte the same
sequence the per-call sort produced — greedy allocation order, and
therefore results, are unchanged.

## Change 3 — hoist `available_mana_abilities` out of inner loops

The ability list depends only on battlefield + tapped + summoning-sick
state, yet was recomputed:

* per (card × mode × candidate-land) in `castability_snapshot` — now
  computed **once per candidate land** (play → enumerate → undo, per
  candidate instead of per card×mode×candidate);
* per hand card in `policy_land._castable_spells_next_turn` — now once
  per L1 lookahead (threaded into `is_castable` via a new optional
  `abilities` parameter);
* per S-tier picker in `policy_spells.pick_next_action` — now once per
  invocation, threaded through the three options-yielding helpers
  (each keeps a `None` default that recomputes, so direct/test callers
  are unaffected).

Safety: nothing in the loops between hoist point and use mutates the
battlefield/tapped/sick state (`can_pay_cost` ignores its `state`
parameter entirely), and `play_land`/undo for a hypothetical candidate
is exactly the code the old inner loop ran — evaluated once instead of
N times with, by purity, the same result. Call *order* into the CSP
cache changes; cached values are pure-function results, so order
cannot change any individual result.

## Change 4 — mechanical micro-optimisations

* `AbilityRef` gains a precomputed `cmc` slot; the two hot sorts
  (`available_mana_abilities`, `can_pay_cost`) use it instead of
  chasing `ability.cost.mana.cmc` through pydantic attribute access
  (same key values ⇒ same stable sort ⇒ same order).
* `_search` / `_try_satisfy` / `_pay_requirement` / `_take_*` operate
  on raw 7-int lists instead of allocating `ManaPool` wrappers per
  node. `ManaPool` remains as the public/test-facing type; identical
  arithmetic, same drain order.

## Deliberately NOT done, and why

* **Skip re-evaluating already-castable cards in later snapshots.**
  The design doc permits it *in spirit*, but the per-turn
  `CastabilityRecord`s (including `witness_land_choice`, which can
  legitimately differ turn-to-turn) are part of the trace, and the
  monotonic carry-forward is applied downstream by the aggregator.
  Skipping would change traces → bump `SIMULATION_SEMANTICS_VERSION` →
  invalidate every existing cache right when the Step-5 retrain is
  pending. With the identity-sequence cache, a re-evaluation of an
  already-castable card is one dict hit per (mode, candidate) anyway —
  the win wasn't worth a semantics bump.
* **Numba on the mana solver.** Sanctioned fallback, but the measured
  win above came in without adding a compilation dependency; revisit
  only if materialisation is still the bottleneck after this lands.
* **Numpy vectorisation across games.** Rejected in the design review
  (sacrifices policy auditability).

## Verification

1. Three `--save` baselines taken on unmodified `main` (TLA 50×20,
   TLA 100×40, SOS 50×20).
2. `--check` all three after the change: every `AggregateStats` field
   and every per-game trace SHA-256 must match bit-for-bit. The SOS
   corpus covers prepared modes; the harness replays real materialiser
   (deck, hand, seed) triples.
3. Remember the harness only covers replayed cases — hence the
   correctness arguments above rather than "the harness passed".
   The cache-key design (§Change 1) is the load-bearing piece.
4. Full `uv run pytest packages/simulation` (and the whole suite),
   `ruff check` + `ruff format --check`, `mypy`.

## Measured results

* **Equivalence:** all three baselines `--check` OK — every
  `AggregateStats` field and every per-game trace SHA-256 bit-identical.
  `SIMULATION_SEMANTICS_VERSION` stays 1; existing caches remain valid.
* **Harness wall clock** (pre → post; note the harness runs each row
  twice — once for aggregates, once verbose for trace hashes):
  * TLA 50×20: 4.37 → 2.26 ms/game (1.9×)
  * TLA 100×40: 5.22 → 2.46 ms/game (2.1×)
  * SOS 50×20: 5.24 → 2.59 ms/game (2.0×)
* **Pure `simulate()` workload** (`profile_hotspots.py` warm pass,
  TLA 50×20): 2.25 → 1.15 ms/game — **~2.0×**, on top of PR #27's
  earlier 1.85×. Expected materialisation ~17 h/set → ~8.5 h/set.
* **cProfile on the same corpus:** top-level CSP DFS runs 54.3k → 9.9k
  (cache hit rate 62% → 93%); `available_mana_abilities` calls
  135.7k → 24.3k; per-node `sorted` in `_try_satisfy` eliminated
  (was 1.14M priority-lambda calls).
* **Remaining profile shape:** `can_pay_cost` bookkeeping (~9% self),
  pydantic trace-object construction (~8%), `stats.aggregate` (~4%).
  Next levers if ever needed: `model_construct` for the bulk trace
  models, then numba on the solver. Neither is worth the review
  surface today.

Also added `packages/simulation/scripts/profile_hotspots.py` — a
cProfile companion that loads the exact harness corpus and profiles
only the `simulate()` calls, so future perf passes start from the
same measurement this one did.
