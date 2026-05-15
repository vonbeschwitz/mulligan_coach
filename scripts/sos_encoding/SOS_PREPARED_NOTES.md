# SOS — `Prepared` mechanic and encoding notes

## What "Prepared" does

From SOS rules: an associated designation any permanent can have. While
a permanent is *prepared*, you may cast a copy of its associated
*prepare spell* from exile and remove the prepared designation. The
prepare spell pays its own mana cost, at sorcery speed.

In SOS Limited, only creatures use Prepared. The card is printed as a
split layout (Scryfall `layout: "prepare"`):

* Front face — a creature that is sometimes "enters prepared", and
  sometimes only becomes prepared via a separate triggered/activated
  ability.
* Back face — the *prepare spell* (sorcery or instant).

There are 36 Prepared cards in SOS:

* **23 enter prepared** ("This creature enters prepared.") — the
  prepare spell becomes castable starting the turn after the creature
  resolves.
* **13 conditionally prepared** — the creature is not prepared on ETB;
  some other event has to fire first (attack, gain life, three spells
  cast, eight lands controlled, etc.).

Project owner rules (2026-05-14):

* **Conditionally prepared** — encode only the creature; ignore the
  prepare spell entirely.
* **Pre-prepared** — encode the creature *and* the prepare spell. The
  simulator should reflect the prepare spell's effect (especially for
  cheap creatures whose prepare spell ramps or draws cards).

## Implementation (Option C)

A new `Mode.kind = "prepared"` was added to the cards-side schema. The
simulator was extended to handle it:

1. **Schema** — `packages/cards/src/mulligan_coach_cards/models.py`:
   `Mode.kind` now accepts `"prepared"`.
2. **State** — `packages/simulation/src/mulligan_coach_simulation/runtime.py`:
   `GameState.prepared: set[int]` tracks which battlefield instance_ids
   are flagged prepared. Added to `GameStateSnapshot` for
   snapshot/restore. `_remove_from_battlefield` clears the flag.
3. **Cast handler** — when a card with at least one `prepared` mode is
   cast (its `kind="cast"` mode resolves), `_place_after_cast` adds its
   instance_id to `state.prepared`. When a `kind="prepared"` mode is
   cast, `state.cast` removes the flag and resolves the spell's effects;
   the source stays on the battlefield (the prepare spell is a
   *copy*, the source isn't consumed).
4. **Policy** —
   `packages/simulation/src/mulligan_coach_simulation/policy_spells.py`:
   * `_battlefield_prepared_options` yields castable prepared modes
     from prepared permanents.
   * Tiers S1a / S1c / S2 / S3 / S4 chain prepared options after their
     hand and battlefield-activated counterparts.
   * **New S5** — `_pick_s5_cast_prepared_enabler` casts a hand
     creature whose prepared mode is mulligan-relevant (fetch / draw /
     land-find), so the creature ETBs prepared and the prepare spell
     becomes available next turn. Last-resort tier: only fires when no
     other tier matches.
5. **Castability snapshot** —
   `packages/simulation/src/mulligan_coach_simulation/castability.py`:
   the snapshot loop now also walks prepared modes of prepared
   permanents on the battlefield, so first-castable-turn tracking
   reports them alongside hand modes.
6. **First-castable tracking** —
   `packages/simulation/src/mulligan_coach_simulation/engine.py`:
   `_HAND_MODE_KINDS` includes `"prepared"` so `ModeFirstTurn` entries
   are pre-populated.
7. **Tests** — `packages/simulation/tests/test_prepared.py` covers:
   the prepared flag on cast, that conditional-prepared cards aren't
   flagged, S5 casting on T1, prepared ramp firing on T2 via S1c, and
   prepared draw firing on T2 via S3.

### Side fix: plain `DrawCardsEffect` now fires

While building this, I confirmed `apply_mode_effects` deliberately
defers `DrawCardsEffect` to "the policy" — but the policy didn't
actually fire it for non-loot cases. So Preordain (and any prepared
draw spell, like Elite Interceptor's Rejoinder) was scrying but never
drawing the kept-on-top card. Fixed in `policy_spells.cast_main_phase`:
after `_resolve_scry`, plain `DrawCardsEffect`s on the just-cast mode
fire via `state.draw`. Loot effects still fire eagerly in
`apply_mode_effects` (their existing path). One existing test
(`test_scry_bottoms_uncastable_keeps_castable`) was updated — it had
been asserting the kept-on-top card stays in the library, which only
held because the draw was silently dropped.

## Files touched

* `packages/data-download/src/mulligan_coach_data_download/config.py`
  — added SOS to `DEFAULT_SETS`.
* `packages/cards/src/mulligan_coach_cards/loader.py` — added
  `"prepare"` to `RELEVANT_LAYOUTS`.
* `packages/cards/src/mulligan_coach_cards/models.py` — added
  `"prepared"` to `Mode.kind`.
* `packages/simulation/src/mulligan_coach_simulation/runtime.py` —
  added `prepared` set, snapshot/restore handling, mark-on-cast,
  unmark-on-prepared-cast, clear on remove-from-battlefield.
* `packages/simulation/src/mulligan_coach_simulation/policy_spells.py`
  — added `_battlefield_prepared_options`, `_chain` helper, S5 picker;
  threaded prepared options into S1a/S1c/S2/S3/S4. Also wired plain
  `DrawCardsEffect` resolution into `cast_main_phase`.
* `packages/simulation/src/mulligan_coach_simulation/castability.py`
  — snapshot walks prepared permanents alongside hand cards.
* `packages/simulation/src/mulligan_coach_simulation/engine.py` —
  `_HAND_MODE_KINDS` includes `"prepared"`.
* `packages/simulation/tests/test_prepared.py` — 6 new tests.
* `packages/simulation/tests/test_policy_spells.py` — updated the two
  scry tests to reflect the now-correct draw-after-scry behavior.
* `data/processed/parsed_cards/SOS.json` — 341 SOS cards encoded
  (189 auto, 152 LLM-encoded). 23 pre-prepared cards have a second
  Mode of kind="prepared" with the prepare spell's effects.
* `scripts/sos_encoding/build_sos_patches.py` — the script that
  built the 152 LLM patches.
* `scripts/sos_encoding/patches.json` — the patches themselves
  (for traceability).

## Other encoding fixes applied during the same review

* **Flashback cards (10)** — second `kind="cast"` mode dropped. The
  simulator iterates cast modes from hand and would treat flashback as
  an alternative cast, which is wrong (flashback is graveyard-only).
  This brings SOS in line with the older TLA/ECL/TMT convention; older
  entries with stale flashback modes can be cleaned up incrementally.
* **#17 Group Project** — flashback cost is "tap three untapped
  creatures" (non-mana), so the flashback mode is doubly wrong. Stripped.
* **Spree cards** (#bonus-otj-26 Requisition Raid, #bonus-otj-142
  Return the Favor) — base cost bumped by `{1}` to reflect the
  cheapest legal mode, since Spree requires at least one paid +mode
  to do anything.
* **Charm aggregation** — Quandrix Charm and Silverquill Charm
  expanded to flag *every* mode's role (combat trick + counterspell or
  removal). Lorehold and Witherbloom and Prismari were already
  consistent.
* **Modal sweepers** — Artistic Process bumped to `removal_burn_damage=6`
  (max across modes for option value) plus `removal_destroy_or_exile`.
  Splatter Technique added `removal_burn_damage=4`. Burst Lightning's
  kicker mode counted as `removal_burn_damage=4` (max across cast
  modes; kicker is paid from hand so the alt-cast Mode is legitimate).
* **#49 Flow State** — added `cards_drawn=1` (consistency with the
  look-at-top-N convention used by Expressive Iteration / Stock Up).
* **#227 Snooping Page** — cleared `cards_drawn` (parser had picked it
  up from a combat-damage trigger; per guide §2 those are too
  conditional to count).
* **#bonus-cn2-58 Subterranean Tremors** — cleared `creates_creatures`
  (the 8/8 Lizard token only fires at X≥8; the X=1 conservative-min
  convention says no token).
