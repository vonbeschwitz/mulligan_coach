# cards — Claude instructions

## Purpose

Typed card representation for the Mulligan Coach project. Reads
Scryfall's `oracle_cards` JSON (downloaded by the `data-download`
package) and turns each card into a `ParsedCard` that two downstream
packages consume:

* The **simulator** (`packages/simulation`) walks `modes`,
  `mana_abilities`, and `enter_condition` to evaluate castability and
  game state.
* The **XGBoost feature stage** (`packages/model`, downstream) reads
  `role_features` plus 17Lands stats to compute hand- and deck-level
  features.

The two consumers operate on disjoint slices of `ParsedCard`. Don't
mix concerns: don't make the simulator read `role_features`, and don't
make the model walk `effects`.

## Layout

```
src/mulligan_coach_cards/
├── __init__.py                  # Re-exports the public surface
├── mana.py                      # ManaCost + Pip + parse_mana_cost
├── keywords.py                  # EVERGREEN_KEYWORDS, MODE_EMITTING_KEYWORDS, ALT_COST_KEYWORDS,
│                                # IGNORABLE_KEYWORD_LINES, SET_SPECIFIC_KEYWORDS
├── models.py                    # ParsedCard, Mode, Effect (discriminated union), Cost,
│                                # ManaAbility, Predicate, RoleFeatures, CreatureBody
├── parser.py                    # Deterministic Scryfall → ParsedCard
├── store.py                     # Persistent per-set ParsedCard JSON + merge_detector_run
├── loader.py                    # Reads data/raw/scryfall/oracle_cards.<date>.json
└── cli.py                       # `mulligan-coach-cards` typer app: parse-demo, run-detector,
                                 # list-needs-llm, mark
scripts/                         # Dev helpers: summarize_batch, apply_patches,
                                 # mark_layout_blocks
```

## Datatype shape (high level)

`ParsedCard` carries:

* **Identity**: `name`, `set_code`, `collector_number`, `oracle_id`,
  `rarity`, `raw_oracle_text`.
* **Type system**: `type_line`, `types: list[str]`, `subtypes: list[str]`,
  `supertypes: list[str]`. Multi-typed cards (Artifact Creature) get all
  applicable types.
* **Cost / colors**: `mana_cost: ManaCost | None`, `colors: list[Color]`.
* **Creature stats**: `power`, `toughness`, `evergreen_keywords` —
  populated only when `'Creature' in types`.
* **Simulator side**:
  * `modes: list[Mode]` — every legal way to play / activate the card.
    The cast Mode (if any) is first; cycling / land-cycling / channel /
    activated abilities follow.
  * `mana_abilities: list[ManaAbility]` — permanent-resident mana
    abilities, populated for lands, mana dorks, mana rocks, filter
    lands.
  * `enter_condition: Predicate | None` — land-only. `None` = enters
    untapped. `Predicate(kind="always")` = unconditional ETB tapped.
    `Predicate(kind="controls_lands_lt", n=2)` = Deathcap-style
    ("enters tapped unless you control two or more other lands"). Etc.
* **XGBoost side**:
  * `role_features: RoleFeatures` — flat per-card categorization.
* **Outcome**: `status: ParseStatus` (`AUTO` / `NEEDS_LLM`) and
  `reasons: list[str]` (free-text breadcrumbs).

### Mode / Effect / Cost (simulator side)

```python
class Mode:
    kind: Literal["cast", "cycle", "land_cycle", "channel", "activated"]
    cost: Cost
    effects: list[Effect]   # Permanents' cast Mode starts with EntersBattlefieldEffect

class Cost:
    mana: ManaCost          # may be empty
    tap: bool
    untap: bool
    sacrifice: SacrificeSpec | None
    discard_self: bool      # the cycling pattern
    # Pay-N-life is deferred to v2.

# Effect is a discriminated union over `kind`:
# ProduceManaEffect | FetchLandEffect | DrawCardsEffect |
# ScryEffect | EntersBattlefieldEffect | NoopEffect
```

* `FetchLandEffect` has three independent axes: `target_filter`
  (basic / any / specific_subtype), `subtype` (when specific),
  `destination` (battlefield_untapped / battlefield_tapped / hand). The
  TRIGGER axis (ETB / cast / activated / sac) is the enclosing Mode's
  `kind`.
* `NoopEffect` carries a `role_tag` breadcrumb (e.g. `"removal_destroy"`,
  `"life_gain"`). Don't read `role_tag` from downstream code — read
  `role_features` instead.

### ManaAbility

```python
class ManaAbility:
    cost: Cost
    produces: list[list[ManaOption]]  # outer = OR, inner = AND
    condition: Predicate | None
```

Examples:
* Basic Forest: `cost=Cost(tap=True), produces=[["G"]]`.
* Boros Guildgate: `cost=Cost(tap=True), produces=[["R"], ["W"]]`.
* Filter land "{1}, {T}: Add any color": `cost=Cost(tap=True, mana={1})`,
  `produces=[["any"]]`.

### Predicate (closed enum)

Used by `enter_condition`, `ManaAbility.condition`, and
`ProduceManaEffect.condition`. Six kinds:

* `"always"` — unconditional.
* `"controls_lands_ge"` / `"controls_lands_lt"` — count threshold.
* `"controls_basic"` — at least one basic of the named type
  (`Plains`/`Island`/`Swamp`/`Mountain`/`Forest`).
* `"controls_basic_any"` — at least one basic land of any type
  (Agna Qel'a-style "...unless you control a basic land").
* `"controls_color"` — at least one source of the named color.

New predicate kinds are added one at a time as new sets surface them.
Don't reach for a generic expression language — keeping this small
means the simulator's evaluator stays trivial.

### RoleFeatures (XGBoost side)

Flat per-card categorization populated from a single oracle-text pass.
Categories (per design):

1. `is_creature` — already a structural property of the card.
2. `creates_creatures: list[CreatureBody]` — token bodies created by
   the card from any source (ETB, cast, equipment-with-body, cast-trigger,
   activated ability, etc.).
3. `removal_destroy_or_exile`, `removal_burn_damage` (creature-targeted
   only — non-creature destroy/exile sets `is_other` instead).
4. `is_punch_fight`.
5. `combat_trick_power` / `combat_trick_toughness` /
   `combat_trick_granted_keywords` (instants only).
6. `is_equipment` (+ stapled body if any in `creates_creatures`).
7. `is_vehicle` (+ stapled body).
8. `is_removal_aura` / `is_pump_aura` + pump P/T/keywords.
9. `is_planeswalker`.
10. `cards_drawn` / `cards_manipulated` — `cards_manipulated` covers
    scry / loot ("draw N then discard N") / surveil.
11. `is_bounce` — returns target permanent to its owner's hand
    (independent of removal flags; a card can be both).
12. `is_top_library` — puts target permanent on top of its owner's
    library (tuck — softer than bounce because the owner re-draws it).
13. `is_other` — catchall, set when none of the typed categories apply
    OR when the card has a non-creature removal / variable-amount /
    counter-distribution effect.

A card may set multiple flags (Artifact Creature Vehicle = creature +
vehicle, etc.). Categories are not mutually exclusive.

## Parser layering

`parse_card(scryfall_dict) -> ParsedCard` is the single entry point.
It dispatches by primary type (Land > Creature > Instant/Sorcery >
Enchantment > Artifact > Planeswalker), then the per-type branch
emits Modes / ManaAbilities / RoleFeatures.

Four ParseStatus outcomes:

* `AUTO` — the deterministic parser fully understood the card.
  Downstream code can trust the structured fields.
* `NEEDS_LLM` — something blocked deterministic classification.
  `reasons` lists what tripped the parser; awaiting LLM review.
* `LLM_ENCODED` — Claude (the LLM) reviewed the card and hand-encoded
  the structured fields. Treated as authoritative; preserved on
  re-runs of `run-detector`.
* `NEEDS_HUMAN` — Claude reviewed but was uncertain enough that human
  judgement is required. Also preserved on re-runs.

### When the parser bails

* **Layout is non-normal AND not handled.** The parser handles three
  non-normal layouts directly:
    * `transform` — collapses to the front face when the back face has
      no mana cost (or is a land). The recursive parse runs with the
      front-face oracle text + cost + types; back-face content is
      ignored. Front-face name and joint type line are preserved on
      the resulting `ParsedCard` for display. Transform DFCs whose
      back face is castable in its own right still bail.
    * `saga` — encodes only chapter I. `role_features.is_saga = True`
      regardless. Higher chapters happen on subsequent turns and don't
      affect the mulligan-relevant turns 1–4 enough to model in v1.
    * `class` — encodes only the always-on level-1 effect.
      `role_features.is_class = True` regardless. Levels 2 and 3
      require activation and aren't material at mulligan time.
  All other non-normal layouts (split / adventure / modal_dfc / …)
  still bail to NEEDS_LLM.
* **Alt-cost keyword present** that's in `ALT_COST_KEYWORDS` (kicker,
  flashback, escape, mutate, …). Cycling / channel / landcycling are
  NOT in this list — they're in `MODE_EMITTING_KEYWORDS` and emit
  additional Modes.
* **Set-specific keyword present** from `SET_SPECIFIC_KEYWORDS`
  (airbend / waterbend / earthbend / firebending and similar
  one-set-only mechanics). Bails with a clean
  `"set-specific keyword 'X' not modelled"` reason rather than a
  confusing "unrecognised line" trace. Add new entries when a new set
  ships a custom mechanic.
* **Unrecognised oracle line**: anything the per-chunk matchers can't
  classify. The unrecognised text is recorded in `reasons`.

### Per-type sub-branches

Beyond the basic Land / Creature / Instant-or-Sorcery dispatch, the
parser has dedicated sub-branches for:

* **Aura** (`'Aura' in subtypes`) — `_parse_aura` classifies the
  aura's static effect as removal-aura ("can't attack/block",
  "doesn't untap", etc.) or pump-aura ("Enchanted creature gets
  +N/+M and gains <keywords>") and writes the corresponding
  `role_features` fields. Activated abilities on the aura become
  Modes; token creation populates `creates_creatures`.
* **Vehicle / Equipment** (`'Vehicle'` / `'Equipment'` in subtypes)
  — `_parse_artifact_typed` ignores `Crew N` / `Equip {N}` lines and
  triggered abilities (so Vehicles with dies-triggers, evergreen
  keywords, and Crew costs auto-classify), and scans for stapled
  creature tokens.
* **Lands with non-mana activated abilities** — `_parse_land` falls
  through to `_build_activated_mode` for utility lands (Agna Qel'a's
  `{2}{U}, {T}: Draw a card, then discard a card.`, Airship Engine
  Room's `{4}, {T}, Sacrifice this land: Draw a card.`, etc.). The
  activated ability is added to `modes`, not `mana_abilities`.

### Recognised oracle-text patterns (full list)

Each pattern is a chunk-level matcher in `parser.py`. New patterns get
added here.

* **Card draw** — "Draw N cards." → `DrawCardsEffect(n=N)`.
* **Loot** — "Draw N cards, then discard N cards." → `NoopEffect("loot")`,
  `cards_manipulated += N` when called with `rf`.
* **Scry** — "Scry N." → `ScryEffect(n=N)`.
* **Destroy / Exile target X** — generalised over
  creature / nonland permanent / permanent (count as creature removal)
  vs artifact / enchantment / land / artifact-or-enchantment (count
  as `is_other`).
* **Damage** — fixed-amount to creature / any-target sets
  `removal_burn_damage`. Variable-amount damage ("equal to the
  number of <X>") sets `is_other` and a noop effect.
* **Bounce** — "Return target creature / nonland permanent / permanent
  to its owner's hand." → `is_bounce=True`.
* **Tuck** — "Put target permanent on top of its owner's library." →
  `is_top_library=True`.
* **Combat trick** — instants / sorceries with "target creature gets
  +N/+M until end of turn" or "target creature gains <keywords> until
  end of turn" → `combat_trick_*` populated, noop effect emitted.
* **Counter distribution on ETB** — "put a +1/+1 counter on each <X>"
  → `is_other=True`.
* **Token creation** — "create N P/T <color> <subtype> creature
  token(s)" → `creates_creatures` extended with the body.
* **Land fetch** — "Search your library for a basic land card / a
  Forest / a land card, put it onto the battlefield {tapped|untapped}
  / into your hand" → `FetchLandEffect`.
* **Mana production** — "{T}: Add {color}." → `ManaAbility` (lands,
  mana dorks, mana rocks).
* **Conditional ETB-tapped (lands)** — "enters tapped (unless
  N or more lands / a basic land / a basic <type>)".
* **Cycling / land-cycling / channel** — emitted as additional Modes.
* **Static self-modifiers on creatures** — "This creature gets/has/'s
  power..." lines are ignored as noise (variable P/T creatures, etc.
  still auto-classify).
* **"As an additional cost to cast this spell, ..."** — recognised
  and bailed with a clean reason; the rest of the spell still parses
  for `role_features`.

### Extending the parser

When a new pattern is worth handling deterministically:

1. Add the regex / matcher to `parser.py` (search for `_match_*`).
2. Wire it into the appropriate per-type parser branch.
3. Update `role_features` if the new pattern affects categorization.
4. Add a unit test in `tests/test_parser.py`.
5. Re-run the parse-demo (`uv run mulligan-coach-cards --set TLA --n 30`)
   to see the auto-rate move.

If the pattern is too ambiguous or the variation too wide, leave it
to the LLM — the parser's job is high-precision deterministic
classification, not coverage maximisation.

## Tests

```
uv run pytest packages/cards
```

Hand-crafted Scryfall-shaped dicts. No real data download required.

## Current auto-classification rate

After widening to handle Sagas (chapter I only), Classes (level-1 effect
only), transform DFCs with uncastable back faces, and a few additional
static-line tolerances (name-as-self-reference, "creature spells you cast
have …"), across the three current Premier-Draft sets (TMT/ECL/TLA — 738
cards):

| Set | auto | llm_encoded | needs_human | needs_llm |
|---|---|---|---|---|
| TMT | 161 (84.7%) | 29 (15.3%) | 0 | 0 |
| ECL | 218 (81.6%) | 49 (18.4%) | 0 | 0 |
| TLA | 223 (79.4%) | 58 (20.6%) | 0 | 0 |
| **All** | **602 (81.6%)** | **136 (18.4%)** | **0** | **0** |

Reproduce / refresh with:

```
uv run mulligan-coach-cards run-detector --sets TMT,ECL,TLA
# Use --reparse-needs-human to re-parse previously human-flagged cards
# after widening the parser further; --force overrides llm_encoded too.
```

`llm_encoded` cards have their `role_features` hand-set; `modes` /
`mana_abilities` may be partial (the simulator already tolerates that on
non-AUTO cards). `is_saga` and `is_class` flags are set on every Saga /
Class regardless of whether the deterministic parser was able to fully
encode the chapter-I / level-1 effect.

## Known sharp edges

* The `_CREATE_TOKEN_RE` regex captures the FIRST token-creation phrase
  per chunk; multi-token effects ("create a 1/1 white Cat and a 1/1
  black Rat") need extension.
* `_match_etb_tapped_predicate` recognises Deathcap, check-land, and
  any-basic patterns; sea-gate-style "enters tapped unless you control
  X noncreature, nonland permanents" doesn't match yet — add a new
  Predicate kind when needed.
* Filter mana abilities (`{T}: Add one mana of any color.`,
  `{1}, {T}: Add one mana of any color.`) aren't recognised by
  `_extract_taps_for` yet — White Lotus Hideout-style lands bail.
* The Aura branch's destroy/exile matcher doesn't recognise
  "Exile **enchanted** creature" (vs "Exile **target** creature");
  multi-sentence aura activations bail. Workaround in v1: aura still
  classifies its static effect (removal vs pump) for `role_features`.
* Modal cards (Adventure, MDFC, Split, Saga) all bail on the layout
  check today. The Mode list could in principle express them; not
  worth it until the simulator wants to consume them.
* The LLM classifier doesn't exist yet. `NEEDS_LLM` cards have empty
  or partially-populated Modes / `mana_abilities` — downstream code
  must handle that until the classifier lands. `role_features` is
  populated whenever the parser could extract anything, even if
  status is `NEEDS_LLM`.
