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
│                                # IGNORABLE_KEYWORD_LINES, SET_SPECIFIC_KEYWORDS,
│                                # KNOWN_KEYWORDS_EXTRA (tripwire allowlist)
├── models.py                    # ParsedCard, Mode, Effect (discriminated union), Cost,
│                                # ManaAbility, Predicate, RoleFeatures, CreatureBody
├── parser.py                    # Deterministic Scryfall → ParsedCard
├── store.py                     # Persistent per-set ParsedCard JSON + merge_detector_run
├── loader.py                    # Reads data/raw/scryfall/oracle_cards.<date>.json
│                                # + load_arena_id_index() reading MTGJSON AllIdentifiers.json
├── seventeenlands_stats.py      # SeventeenLandsStats + StatsLookup + load_premier_draft_stats:
│                                # parquet → typed per-card 17Lands aggregates with three-tier match
└── cli.py                       # `mulligan-coach-cards` typer app: parse-demo, run-detector,
                                 # list-needs-llm, mark, census-drops
scripts/                         # Dev helpers: summarize_batch, apply_patches,
                                 # mark_layout_blocks
```

## Datatype shape (high level)

`ParsedCard` carries:

* **Identity**: `name`, `set_code`, `collector_number`, `oracle_id`,
  `arena_id` (the MTGA card ID — primary join key for 17Lands stats and
  Arena log events; `None` when MTGJSON hasn't yet ingested the
  printing), `rarity`, `raw_oracle_text`.
* **Type system**: `type_line`, `types: list[str]`, `subtypes: list[str]`,
  `supertypes: list[str]`. Multi-typed cards (Artifact Creature) get all
  applicable types.
* **Cost / colors**: `mana_cost: ManaCost | None`, `colors: list[Color]`.
* **Creature stats**: `power`, `toughness`, `evergreen_keywords` —
  populated only when `'Creature' in types`.
* **Simulator side**:
  * `modes: list[Mode]` — every legal way to play / activate the card.
    The cast Mode (if any) is first; cycling / land-cycling / channel /
    activated abilities follow. **Invariant**: every card whose
    `mana_cost is not None` has at least one cast Mode after passing
    through `save_parsed_cards`. If the LLM-encoding step left modes
    empty (because the card's alt-cost mechanics — kicker, flashback,
    foretell, behold — don't fit the Mode vocabulary), `save_parsed_cards`
    injects a stub `Mode(kind="cast", cost=Cost(mana=printed_cost),
    effects=[NoopEffect(role_tag=DEFAULT_CAST_ROLE_TAG)])`. That gives
    the simulator's `check_deck_encodings` a non-empty modes list while
    keeping it explicit (via the role_tag) that the on-cast effect
    isn't actually modeled.
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
# ProduceManaEffect | FetchLandEffect | LookAtTopEffect |
# DrawCardsEffect | ScryEffect | EntersBattlefieldEffect | NoopEffect
```

* `FetchLandEffect` has three independent axes: `target_filter`
  (basic / any / specific_subtype), `subtype` (when specific),
  `destination` (battlefield_untapped / battlefield_tapped / hand). The
  TRIGGER axis (ETB / cast / activated / sac) is the enclosing Mode's
  `kind`. Use it for **deck-wide** searches.
* `LookAtTopEffect` covers the **top-N** filter case: look at the top
  `n` cards, take a land first if `accepts_land`, fall back to a
  nonland if `accepts_nonland`, bottom the rest. Use this — not
  FetchLandEffect — for "Look at the top 3, put one in your hand"
  (Accumulate Wisdom), "Mill 4 then return a permanent to hand"
  (Midnight Tilling), and "Top 4, may take a creature, ninja or land"
  (Cowabunga!). Type filters more granular than land/nonland (e.g.
  "Mutant or Ninja or Turtle") collapse to `accepts_nonland=True`.
  Variable-N effects (Seismic Sense's "top X where X = lands controlled")
  encode a fixed approximation noted in `reasons`.
* `NoopEffect` carries a `role_tag` breadcrumb (e.g. `"removal_destroy"`,
  `"life_gain"`). Don't read `role_tag` from downstream code — read
  `role_features` instead.

#### Encoding alt-cost casts

Alt-cost mechanics split into two camps depending on **which zone
pays the alt cost**:

* **Hand-resident alt costs** (evoke, kicker, multikicker, madness,
  morph, overload) → encode as a **second `Mode(kind="cast")`** with
  the alt cost in `cost.mana` and effects describing what happens
  when paying the alt cost. The simulator iterates every cast mode
  and picks the cheapest castable one
  (`policy_spells.py:_first_or_none` sorts by mode CMC), so this lets
  a short-mana hand play the alt-cost form. Omit
  `EntersBattlefieldEffect` from evoke modes — the creature is
  sacrificed on entry, never persisting to the battlefield.
* **Graveyard / exile-resident alt costs** (flashback, jump-start,
  aftermath, foretell) → **DO NOT** encode as a second cast mode. The
  simulator treats every cast Mode as castable from hand, so a
  graveyard alt cost would be incorrectly "cast" from hand. Drop the
  alt-cost form; the role_features signal still reaches the model.
  See `CARD_ENCODING_GUIDE.md` §14 for the convention.

The Prepare mechanic (SOS) is the prototype of the right approach for
non-hand-resident casts: a dedicated `Mode.kind` plus
battlefield-resident castability hooks in the simulator. See
`Mode(kind="prepared")` below and the implementation in
`scripts/sos_encoding/SOS_PREPARED_NOTES.md`. New effect kinds get a
resolver in `simulation/effects.py` and (if hand-affecting) a policy
hook in `policy_spells.py`.

#### `Mode(kind="prepared")` — the SOS Prepare mechanic

Prepared spells live on a battlefield permanent (the front-face
creature) and become castable as sorceries while the source is
flagged "prepared" in `GameState.prepared`. The simulator marks any
permanent with at least one `kind="prepared"` mode as prepared on
cast resolution and unmarks it when the prepared mode is cast.

Encoding convention: pre-prepared creatures (those with "This
creature enters prepared" on the front face) get TWO modes — a
normal `kind="cast"` for the creature, and a `kind="prepared"` for
the back-face spell with its own mana cost and effects. Conditional
prepares (creatures that need a separate trigger before becoming
prepared) get only the `kind="cast"` mode. See
`CARD_ENCODING_GUIDE.md` §13.

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
   the card. Sources that count: the permanent's OWN ETB trigger,
   direct cast-resolution effects, stapled equipment/vehicle bodies,
   and cheap (cmc ≤ 3) activated abilities. Attack / cast / upkeep /
   other-permanent triggers and death triggers never count — see
   `CARD_ENCODING_GUIDE.md` §4 (owner ruling 2026-07-07).
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

## 17Lands stats

Per-card 17Lands aggregate statistics live in
`seventeenlands_stats.py`, separate from `ParsedCard`. The split is
deliberate: card structure changes rarely (only when the parser is
extended), but format stats refresh weekly — keeping them in different
files means a stats refresh doesn't churn the persisted ParsedCard
JSON.

Public surface:

* `SeventeenLandsStats` — Pydantic model whose field names mirror the
  17Lands JSON schema one-for-one (no renaming). Win-rate / ALSA / ATA
  / play-rate fields are `float | None` because 17Lands publishes
  `null` for cards with too few games to compute a meaningful rate.
  Game-count fields are always populated.
* `StatsLookup` — frozen dataclass with `by_arena_id: dict[int, ...]`,
  `by_name: dict[str, ...]`, and a `match(card)` helper that runs a
  three-tier fallback: arena_id → exact name → front-face name (split
  on `" // "`). The front-face fallback catches DFCs whose
  `ParsedCard.name` is the joint `Front // Back` form while 17Lands
  uses the front face only.
* `load_premier_draft_stats(set_code, *, data_root=None) -> StatsLookup`
  — reads
  `data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet`
  and returns the lookup with both indices populated.
* `ratings_parquet_path(set_code, event_type='PremierDraft', data_root=None)`
  — canonical path resolver. Set codes are upper-cased.

This module deliberately contains **only raw fields**. Derived
features (earliness score, sample-size shrunk WRs, z-scores within
format) belong in the future `packages/model/` — keeping that boundary
clear means the cards package stays focused on "represent the cards".

Join key sourcing — `arena_id` (a.k.a. MTGA ID) on `ParsedCard` is
populated at parse time from a `(scryfall_oracle_id, set_code) -> mtga_id`
index built by `loader.load_arena_id_index()` from
`data/raw/mtgjson/AllIdentifiers.json`. MTGJSON lags very-new sets by
a few weeks, so newly-released sets may have `arena_id=None` on every
card until MTGJSON catches up — the three-tier fallback in `match()`
keeps things working in the meantime via name canonicalisation.
`merge_detector_run` carries fresh `arena_id` values forward onto
preserved (LLM_ENCODED / NEEDS_HUMAN) entries on every detector run,
so the next run after an MTGJSON refresh picks up the IDs without a
forced re-encode.

Premier Draft only in v1: Sealed / TradDraft / TradSealed parquets
exist on disk but aren't exposed; add a parameter when needed.

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
  All other non-normal layouts (split / adventure / modal_dfc /
  prepare / …) still bail to NEEDS_LLM. The SOS `prepare` layout was
  added to `RELEVANT_LAYOUTS` in `loader.py` so prepare-layout cards
  reach the parser (which then bails to NEEDS_LLM); the LLM encoding
  follows the rules in `CARD_ENCODING_GUIDE.md` §13.
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
* **Unknown Scryfall keyword** (the tripwire, added 2026-07-06): any
  entry in the card's structured `keywords` list that is in none of the
  known keyword sets (including the grandfathered
  `KNOWN_KEYWORDS_EXTRA`) demotes the card to NEEDS_LLM with a clean
  reason, and the MV≥4 fast-path refuses to promote it back. This is
  what catches brand-new mechanics (MSH's Connive slipped through
  silently before it existed). After encoding a new set, run
  `census-drops` (below) and add the settled keyword to
  `KNOWN_KEYWORDS_EXTRA`.
* **Named token** (the tripwire, added 2026-07-07): a "create <Name>,
  a … creature token" phrase (proper-noun-named token) demotes the card
  to NEEDS_LLM and the MV≥4 fast-path refuses to promote it back — the
  count-anchored `_CREATE_TOKEN_RE` can't parse the named form and
  recognising it would need self-ETB / flavor-label fixes broader than
  the token. Reviewer records the body or confirms it's excluded (§4).
* **Unrecognised oracle line**: anything the per-chunk matchers can't
  classify. The unrecognised text is recorded in `reasons`.

### Silent-drop census

The parser's tolerance paths deliberately drop text they don't model
(ignored triggers, death triggers, unparseable activation costs,
static prose). `uv run mulligan-coach-cards census-drops --sets <SET>
[--out <path>]` re-parses a set with a collector active and reports
every dropped chunk by frequency with example cards. Skim it after
each new set's detector run — a high-frequency unfamiliar verb in the
report is a new mechanic worth teaching the parser or the guide.

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
* **Cycling / land-cycling / channel** — emitted as additional Modes,
  via the shared `_build_alt_play_mode` helper called from **every** type
  branch (creature, spell, equipment/vehicle, enchantment/artifact). This
  covers plain `Cycling {N}`, type-cycling (`Mountaincycling {N}`), generic
  `Landcycling {N}`, and the two-word `Basic landcycling {N}` form. These
  are cheap from-hand modes that matter at mulligan time even on a high-MV
  card (pitch it turn 2 to fix mana / dig), so the **MV≥4 fast-path** also
  refuses to auto-promote any NEEDS_LLM card carrying an un-keyworded
  `{cost}, Discard this card:` ability — those route to review instead (e.g.
  SOS Visionary's Dance, whose `{2}, Discard this card: look at top 2`
  filter is hand-encoded as a `channel` mode + `LookAtTopEffect`).
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
have …"), across the current Premier-Draft sets (TMT/ECL/TLA/SOS/MSH/HOB),
including each set's bonus sheet where one exists:

| Set | auto | llm_encoded | needs_human | needs_llm |
|---|---|---|---|---|
| TMT | 144 (68.6%) | 66 (31.4%) | 0 | 0 |
| ECL | 194 (67.4%) | 94 (32.6%) | 0 | 0 |
| TLA | 216 (63.2%) | 126 (36.8%) | 0 | 0 |
| SOS | 186 (54.5%) | 155 (45.5%) | 0 | 0 |
| MSH | 243 (72.8%) | 91 (27.2%) | 0 | 0 |
| HOB | 112 (59.6%) | 76 (40.4%) | 0 | 0 |

(Counts as of the 2026-07-06 detector rerun after the parser-hardening
round — unknown-keyword tripwire, death-trigger skip, activated-ability
cmc≤3 crediting gate, token-keyword capture, period-form modal text;
see `CARD_ENCODING_GUIDE.md` §19. The auto rates are lower than the
previous snapshot mostly because per-set audits keep converting `auto`
cards to hand-checked `llm_encoded`, and totals now include each set's
full bonus sheet. HOB counts are from the 2026-08-11 rerun against the
complete 188-card set — the full main set, blind-pass-verified; its
bonus sheet, if one exists, can only be discovered once 17Lands
publishes HOB ratings.)

SOS has a lower auto rate because the Prepare layout (36 cards) always
bails to NEEDS_LLM, the bonus-sheet reprints add 75 cards from older
sets with mechanics the parser hasn't been taught (Spree, Storm,
Suspend, Morph, etc.), and SOS introduces several new triggered
keywords (Repartee, Opus, Increment, Infusion) on otherwise vanilla
creatures.

MSH's count includes a 60-card bonus sheet (classic reprints — Counterspell,
Path to Exile, Rancor, Dauthi Voidwalker, etc. — plus 7 "Marvel Universe"
character cards) folded in automatically via `_bonus_sheet_scryfall_entries`
in `cli.py`, the same mechanism TLA/TMT/SOS use for their own bonus sheets.
**Important:** that bonus sheet has no set code of its own — it is NOT
Scryfall's "MAR" set (that's an unrelated, pre-existing 2025 "Marvel
Universe" masterpiece set tied to *Marvel's Spider-Man*). An earlier pass
at this (2026-06-21, before 17Lands had published any MSH ratings)
mistakenly treated `--sets MSH,MAR` as the way to pull the bonus sheet,
producing a wrongly-scoped `MAR.json` with only 7 cards; that file has
been deleted. The correct flow needs only `--sets MSH` once a 17Lands
Premier-Draft ratings parquet exists for MSH (`refresh-17lands --sets
MSH` — note this may need to be run directly via the
`seventeenlands.ratings.refresh_ratings` helper rather than the
`refresh-17lands` CLI command if 17Lands game-data CSVs aren't published
yet, since the CLI command 403s on the missing game-data download before
it gets to ratings).

MSH was hand-encoded in two rounds. The first (2026-06-21) covered the
274 primary-set cards, **before the set's full card pool was spoiled**
(Arena release is 2026-06-26) and before 17Lands had any MSH data — see
`scripts/marvel_encoding/build_msh_patches.py` and
`CARD_ENCODING_GUIDE.md` §16 for the conventions it settled (Teamwork N,
dual-castable MDFC creature pairs, -N/-N-until-EOT as removal,
mill-as-look-at-top). All 59 needs_llm cards in that round resolved to
`llm_encoded` (0 `needs_human`) — two were briefly flagged `needs_human`
(#77 Super Intelligence, #224 The Ruinous Wrecking Crew) before the
owner settled the conventions that resolved them on 2026-06-22.

The second round (2026-06-23) covered the 22 needs_llm cards that
appeared once the bonus sheet was correctly folded in (see the
correction note above) — combat-gated sweepers, choose-one-or-more
sweeper aggregation, shuffle-away removal (Chaos Warp), mass-protection
instants kept separate from combat-trick (Heroic Intervention, Teferi's
Protection), and an unrecognized pre-modern keyword (Shadow) appended
directly to `evergreen_keywords`. See `CARD_ENCODING_GUIDE.md` §16's
"Bonus-sheet additions" subsection.

Reproduce / refresh with:

```
uv run mulligan-coach-cards run-detector --sets TMT,ECL,TLA,SOS,MSH
# Use --reparse-needs-human to re-parse previously human-flagged cards
# after widening the parser further; --force overrides llm_encoded too.
```

`llm_encoded` cards have their `role_features` hand-set; `modes` /
`mana_abilities` may be partial, but `save_parsed_cards` enforces the
"every castable card has at least one cast Mode" invariant (see the
`modes` bullet under "Datatype shape" above), so the simulator's
`check_deck_encodings` always passes for these. `is_saga` and `is_class`
flags are set on every Saga / Class regardless of whether the
deterministic parser was able to fully
encode the chapter-I / level-1 effect.

## Known sharp edges

* The `_CREATE_TOKEN_RE` regex captures the FIRST token-creation phrase
  per chunk; multi-token effects ("create a 1/1 white Cat and a 1/1
  black Rat") need extension.
* **Named tokens** ("create Redwing, a legendary 1/1 …") are not parsed:
  the count-anchored `_CREATE_TOKEN_RE` can't consume the proper-noun
  prefix, and recognising them would need self-ETB / flavor-word-label
  fixes beyond the token. Instead the `_flag_named_tokens` tripwire
  (`_NAMED_TOKEN_RE`) routes them to NEEDS_LLM (and the MV≥4 fast-path
  refuses to promote them), the same design as the unknown-keyword
  tripwire. See `CARD_ENCODING_GUIDE.md` §4.
* `_match_etb_tapped_predicate` recognises Deathcap, check-land, and
  any-basic patterns; sea-gate-style "enters tapped unless you control
  X noncreature, nonland permanents" doesn't match yet — add a new
  Predicate kind when needed.
* Conditional mana buffs encode the unconditional baseline only.
  Raucous Audience's `{T}: Add {G}. If you control a creature with
  power 4 or greater, add {G}{G} instead.` lands as `produces=[["G"]]`;
  the conditional doubling is dropped because `Predicate` has no
  "creature with power N+" kind. Adding one is a v2 task — until then,
  the simulator slightly underestimates these cards.
* LLM-encoded cards with **hand-resident** alt-cost keywords (evoke /
  kicker / madness / morph / overload) historically only encoded the
  standard cast Mode and dropped the alt-cost form. The encoding
  guidance now requires a second `Mode(kind="cast")` for the alt cost
  (see "Encoding alt-cost casts" above and `CARD_ENCODING_GUIDE.md`
  §14). **Graveyard/exile-resident** alt costs (flashback / foretell /
  jump-start / aftermath) correctly stay single-mode — do NOT add a
  second cast mode for these. SOS, TLA, and TMT have been reviewed
  against this rule (`CARD_ENCODING_GUIDE.md` §17: TLA kicker spells
  got their kicked mode + role_features MAX; TMT Sneak and sac-kicker
  correctly left single-mode). The ECL Evoke incarnations have not yet
  been re-checked — re-encode when revisited.
* The parser doesn't auto-detect "Look at the top N cards" patterns
  yet; cards using `LookAtTopEffect` are LLM-encoded by hand. A
  deterministic regex would land in `_match_spell_effect` —
  straightforward but not yet written.
* The Aura branch's destroy/exile matcher doesn't recognise
  "Exile **enchanted** creature" (vs "Exile **target** creature");
  multi-sentence aura activations bail. Workaround in v1: aura still
  classifies its static effect (removal vs pump) for `role_features`.
* Modal cards (Adventure, MDFC, Split, Saga) all bail on the layout
  check today. The Mode list could in principle express them; not
  worth it until the simulator wants to consume them.
* LLM classification is a manual per-set encoding pass (Claude
  hand-encodes each NEEDS_LLM card to `LLM_ENCODED`, following
  `CARD_ENCODING_GUIDE.md`), not an automated classifier. Cards still
  sitting at `NEEDS_LLM` (e.g. a set mid-encode) have empty or
  partially-populated Modes / `mana_abilities` — downstream code must
  handle that. `role_features` is populated whenever the parser could
  extract anything, even if status is `NEEDS_LLM`.

## Encoding guide for LLM reviewers

When hand-encoding (LLM_ENCODED) or reviewing existing encodings, read
[`CARD_ENCODING_GUIDE.md`](CARD_ENCODING_GUIDE.md) in this directory.
It captures the project owner's judgment calls on how specific card
patterns should be classified — mass removal, combat tricks, token
counts, loot vs draw, mana rocks with awkward costs, etc. — derived
from the FLAGGED audit at `scripts/audit/FLAGGED_feedback.md`. Follow
its rules so encodings stay consistent.
