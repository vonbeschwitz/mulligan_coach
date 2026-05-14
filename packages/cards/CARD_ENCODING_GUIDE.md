# Card encoding guide

This guide captures the judgment calls and edge cases the project owner
has settled when classifying Magic cards into `ParsedCard.role_features`
(the XGBoost feature side) and the simulator-side fields (`modes`,
`mana_abilities`, etc.).

It complements `packages/cards/CLAUDE.md` (which describes the data
shape) by recording **how** the owner wants individual card patterns
to be encoded. New LLM-encoded cards should follow these rules.

The rules below were derived from auditing 840 cards in TMT / ECL /
TLA and resolving every disagreement (see
`scripts/audit/FLAGGED_feedback.md` and `apply_flagged_fixes.py`).
Update this guide when a new convention is settled; cite the affected
cards so future readers can verify.

---

## 1. Top-level role flags

A card may set multiple flags — these categories are NOT mutually
exclusive. Set every flag whose semantics apply. The catchall
`is_other` is set automatically by `_ensure_role_invariants` iff no
specific flag fires.

### Type-derived flags (always set by the store)

`is_creature` / `is_planeswalker` / `is_equipment` / `is_vehicle` /
`is_land` are derived from `types` / `subtypes` and forced on by
`store._ensure_role_invariants`. You don't need to set them by hand —
just make sure `types` / `subtypes` is correct.

### `is_mana_rock`

> A non-creature, non-equipment, non-vehicle artifact whose primary
> value is mana production.

- Set when the artifact has a usable mana ability that the player can
  reliably activate.
- **Do** set on cards like The Great Henge (`{T}: Add {G}{G}`)
  even when they have other effects.
- **Don't** set when the mana ability has an awkward condition that
  the player can't reliably meet:
  - Springleaf Drum (`{T}, Tap an untapped creature: Add any color`)
    — requires a tapped creature; the owner judged this too situational
    to count.
  - White Lotus Tile (`{T}: Add X mana of any one color, where X is the
    number of creatures sharing a type`) — variable-X, kept as-is.

### `removal_destroy_or_exile`

> Creature-targeted destroy or exile, grouped together per design.

- Set for any card whose primary effect destroys or exiles a creature
  (or creature + artifact / nonland permanent / permanent).
- Set on permanents (creatures, vehicles, equipment, enchantments,
  artifacts) whose ETB / cast trigger destroys / exiles a creature.
  - Koh, Koya, Armaggon, Spicy Oatmeal Pizza, Cityscape Leveler, etc.
- **Don't** set for temporary exile (returns the creature at end step
  or end of next turn) — Morningtide's Light is not removal.
- **Don't** set for conditional removal that's too situational to be
  the card's identity:
  - General Traag (sac an artifact to deal 4) — kept off.
  - Mouser Foundry ({4}{R}, sacrifice this: 3 damage) — kept off
    because the activation costs mana AND a sacrifice; usually never
    happens in normal play.

### `is_mass_removal`

> Card destroys / exiles / -N/-Ns ALL creatures or all opp permanents.

- Set in addition to `removal_destroy_or_exile` (a sweeper is still
  creature removal).
- Set on Sagas whose chapter I is a board sweeper (The Last Ronin,
  The Rise of Sozin, Legend of Yangchen).
- Set on `Put X -A/-B counters on each creature` shapes (Black Sun's
  Zenith) — assume X = 2.
- Set on `Each creature gets -N/-N until end of turn` if the total
  toughness reduction is >= 2 (Languish-style).
- Set on mass burn (`X deals N to each creature`) — Blasphemous Act.

### `removal_burn_damage: int`

> Set iff the card deals direct damage to creatures.

- Records the **damage amount** (an int), not just a boolean.
- Set for fixed-amount damage to creature / any-target burn (Lightning
  Bolt, Anchovy & Banana Pizza, Spicy Oatmeal Pizza).
- Set for color-restricted creature burn (Rending Volley — "4 to
  target white or blue creature").
- For **variable damage**, encode a conservative-minimum approximation:
  - Combustion Technique (`2 plus Lessons in graveyard`) → encode as 2
    (assume the minimum case).
- **Don't** set for activated burn on a permanent with mana cost:
  - Weather Maker — kept as `is_mana_rock` only.

### `is_punch_fight`

> Punch (one-sided fight) and fight spells, grouped per design.

- Set on `target creature X fights target creature Y` (Tenderize,
  Rocky Rebuke, Earth Rumble).
- Set on `target creature you control deals damage equal to its power
  to target creature an opponent controls` (Tenderize-style punch).
- Set on aura with ETB-fight trigger (Pitiless Fists).
- Set on sorcery with combined punch + pump (Assert Perfection,
  Brigid's Command) — the punch flag stays; combat-trick is suppressed
  per §3.

### `is_counterspell`

> Counters target spell or activated ability.

- Set on cards like Cancel, Negate, Essence Scatter, Mana Leak.
- Set on cards that may target a spell as part of a modal effect,
  even if the primary mode is something else:
  - Swat Away (`target spell or creature → top or bottom of library`)
    — both `is_counterspell` AND `is_top_library` are set.
- Set on cards that counter all opp spells / abilities — Glen Elendra's
  Answer.

### `is_bounce` / `is_top_library`

- `is_bounce`: returns target permanent to its owner's hand. Set on
  ETB-bounce permanents (Bespoke Bō, Metalhead, Rimekin Recluse, etc.).
- `is_top_library`: tucks target permanent on top of its owner's
  library — softer than bounce because the owner re-draws it. Owner's
  choice variants ("top or bottom") count as is_top_library (Return
  to the Sewers, Swat Away, Lost Days).

---

## 2. Card draw, manipulation, and loot

### `cards_drawn: int` — **NET** new cards, not gross

- Brainstorm draws 3 then puts 2 back — net = 1. Set `cards_drawn=1`.
- Thirst for Identity draws 3 then discards 2 — net = 1.
- Unexpected Assistance draws 3 then discards 1 — net = 2.
- Waterbending Lesson draws 3, conditional discard — net = 2.
- Abandon Attachments — discard 1, draw 2 — net = 1.

### `cards_manipulated: int`

> Cards seen but not necessarily drawn. Scry / loot / surveil count.
> Look-at-top-N effects count the cards bottomed (N - 1 typically).

- **Scry N** → `cards_manipulated += N` and a `ScryEffect(n=N)` on the
  cast mode. Examples: Preordain (scry 2), Hamato Guardian Stance
  (scry 1 rider), Gather the White Lotus (scry 2), Rumble Arena
  (ETB scry 1 on a land).
- **Surveil N** → same as scry. Examples: Shore Lurker, Twilight
  Diviner, Lys Alana Informant, Undercity Sewers.
- **Loot** (draw N, then discard M):
  - `cards_manipulated += N` (gross), `cards_drawn += max(0, N - M)` (net).
  - Cast mode emits `DrawCardsEffect(n=N) + DiscardCardEffect(n=M)`.
  - Examples: Silvergill Peddler, Cool but Rude, Yuyan Archers,
    Sokka, Tweeze, Gristle Glutton (activated loot still counts
    when the activation is mulligan-relevant).

### Look-at-top-N

Cards like "Look at the top N, may take a creature / land / ninja, bottom
the rest" emit a `LookAtTopEffect(n=N, accepts_land=…, accepts_nonland=…)`
on the cast mode AND set `cards_manipulated += (N - 1)` (the player
sees N, bottoms ~N-1). The simulator's S2 hand-fetch policy treats
`LookAtTopEffect(accepts_land=True)` as a probabilistic hand-fetch.

Examples: Casey Jones, Midnight Tilling, Eclipsed cycle, Master
Piandao, Aang at the Crossroads, Guru Pathik.

### Conditional / variable draw

- **Vivid / colors-based**: encode the MINIMUM realistic value. Shinestriker
  (`draw cards equal to the number of colors among permanents you
  control`) → encode `cards_drawn=1` (at minimum the card itself ETBs
  as a colored permanent).
- **Combat-damage-triggered loot / draw**: leave OFF. Too conditional
  on game state — April, Reporter of the Weird stays at just
  `is_creature`.
- **Conditional pump-rider draw**: leave OFF. Thoughtweft Charge
  (`If a creature entered ... draw a card`) doesn't get cards_drawn.

### Sac-or-activated draw with mana cost

For `{N}, Sacrifice this: Draw 2`, the activation costs both mana AND
the permanent. We don't encode it as card-draw — Sewer-veillance Cam
stays at `is_other`. Same principle: too situational and gated.

---

## 3. Combat tricks — instants only (with two exceptions)

The schema says `combat_trick_power` / `combat_trick_toughness` /
`combat_trick_granted_keywords` are "instants only". The intent is to
capture cards the player can flash in to save / win a combat. Apply
the rule **literally** with these clarifications:

- **Instants** with `target creature gets +N/+M until end of turn` or
  `target creature gains <keywords> until end of turn` → set the
  combat-trick fields.
- **Flash creatures** with ETB pump effects → set combat-trick fields.
  No flash → no combat-trick (Stratosoarer, Glen-Elendra Liege, etc.
  do not qualify).
- **Sorceries** → suppress combat-trick fields, even if they pump.
  Special case: sorceries with an instant-speed alternative (e.g.
  Sneak ability) keep combat-trick. Karai's Technique (Sorcery,
  Sneak {1}{B}, +3/+3) → keep combat-trick AND add `removal_destroy_or_exile`
  for the -3/-3 mode.
- **Activated abilities on a creature** → never combat-trick (the
  activation cost gates it). High-Flying Ace, Surly Farrier,
  Bre of Clan Stoutarm, Stratosoarer, Soulbright Seeker — strip
  combat-trick.
- **Threaten / Act-of-Treason** (gain control of opp's creature):
  not a combat trick. Goatnap stays at `is_other`.
- **Instant with +1/+1 counter + keyword grant**: treat as combat
  trick (the counter is functionally +1/+1 plus the keywords).
  Saved by the Shell → `combat_trick_power=1, combat_trick_toughness=1,
  granted_keywords=['trample','hexproof','indestructible']`.

---

## 4. Token creation (`creates_creatures`)

### One entry per token created

Per the owner's revised rule: record **every** token created, not
deduped. If a card creates "two 1/1 Kithkins", emit two CreatureBody
entries. If a card creates two distinct bodies ("a 2/2 Mutant and a
1/1 Servo"), emit two CreatureBody entries. This matches the data
the model actually wants to consume.

Examples:
- Catharsis ("Create two 1/1 Kithkins on ETB") → 2 entries.
- Crescent Island Temple ("Create a 1/1 Monk with prowess") → 1 entry.
- Stalactite Dagger / Kyoshi Battle Fan (ETB creates a single token) →
  1 entry each (the parser previously double-emitted; fixed).

### Variable-X token bodies

Assume X = 1. Sally Pride / Triceraton Commander / Kithkeeper / United
Front all get a SINGLE entry with the named body. Don't try to encode
"X tokens" — the model treats this as the minimum case.

### Token keywords

Always set token keywords from the oracle text. Examples:
- Lord Dregg's Insect Warrior — `keywords=['flying']`.
- Sapling Nursery's Treefolk — `keywords=['reach']`.
- Crescent Island Temple's Monk — `keywords=['prowess']`.
- Fire Nation Attacks' Soldier — `keywords=['firebending 1']`.
- Personify / Stalactite Dagger's Shapeshifter — `keywords=['changeling']`.

### Earthbend (TLA mechanic)

Earthbend N transforms a land into an N/N creature in place. We
**encode this as token creation** (`creates_creatures: [{power: N,
toughness: N}]`) even though no token is technically created — the
owner's call is that the effect is "close enough" to a token for
modeling purposes.

### Non-creature tokens

Food / Clue / Treasure / Map tokens are NOT creature tokens — don't
populate `creates_creatures` for them. Lita, Little Orphan Amphibian
creates a Food token → no entry.

---

## 5. Aura specifics

> Exactly one of `is_removal_aura` / `is_pump_aura` is true on Auras.

- **Removal auras**: enchanted creature can't attack/block, doesn't
  untap, has defender, loses abilities, is tapped indefinitely, or is
  exiled by the aura's ETB.
- **Pump auras**: enchanted creature gets +N/+M and/or gains
  keywords. Populate `aura_pump_power`, `aura_pump_toughness`,
  `aura_pump_granted_keywords`.
- An aura whose ETB exiles a creature (Dimensional Exile) is a
  removal aura with `is_removal_aura=True` AND
  `removal_destroy_or_exile=True`. Both flags fire.

### Aura ETB triggers

Capture them in addition to the pump/removal classification:
- Pitiless Fists (ETB fight + +2/+2 pump) → `is_pump_aura=True` AND
  `is_punch_fight=True`.

---

## 6. Sagas and Classes (chapter-I / level-1 only)

The parser encodes only chapter I (Sagas) and the always-on level-1
effect (Classes). `is_saga` / `is_class` is always set.

- **Mass-removal chapter I** → set `is_mass_removal=True` AND
  `removal_destroy_or_exile=True` (The Last Ronin, Rise of Sozin,
  Legend of Yangchen).
- **Card-draw / scry chapter I** → set `cards_drawn` and
  `cards_manipulated` per the standard rules (Legend of Kuruk: chapter
  I/II is scry 2 + draw 1).

---

## 7. Counters (positive and negative)

### +1/+1 counters on target

- On an **instant**: treat as combat-trick pump +1/+1
  (Saved by the Shell).
- On a **sorcery**: leave as `is_other`. The counter is permanent but
  the player can't flash it in.

### -N/-N counters

- On a **single target**:
  - N >= 3: treat as `removal_destroy_or_exile` (kills most creatures).
  - N == 2: also removal (kills 2-toughness, the most common floor).
  - N == 1: `is_other` (debuff, rarely lethal alone).
- **On each creature** (mass): set `is_mass_removal`. Also set
  `removal_destroy_or_exile` if total magnitude × count >= 2
  (Darkness Descends, Black Sun's Zenith).

### Counter distribution (`put a +1/+1 counter on each Ally`)

`is_other`. Not removal, not a combat trick — board-wide buff.

---

## 8. Simulation-side encoding (`modes`, effects)

### When to attach a sim effect

Mulligan-relevant means turns 1–4. If an effect plausibly affects
land drops / castability in that window, encode it as a sim effect.

- Land fetches → `FetchLandEffect`.
- Card draw / loot → `DrawCardsEffect(+ DiscardCardEffect)`.
- Scry / surveil → `ScryEffect`.
- Look-at-top-N → `LookAtTopEffect`.

### When to NOT attach a sim effect

- Removal (destroy / exile / damage) — sim doesn't model combat.
- Combat tricks — sim doesn't model combat.
- Token creation — sim doesn't track non-mana permanents in detail.
- Counters (+1/+1, -N/-N) — sim doesn't track stats.
- Mana ramp via spells goes on `modes`, not effects (use
  `FetchLandEffect` for fetches; mana-producing spells need their
  own treatment).

### Cycling / channel / waterbend → separate Mode

- Cycling: `Mode(kind="cycle", cost=Cost(mana=..., discard_self=True),
  effects=[DrawCardsEffect(n=1)])`.
- Land-cycling: `kind="land_cycle"` + `FetchLandEffect(...,
  destination="hand")`.
- Channel: `kind="channel"` with the stated cost+effect.
- Waterbend: `kind="activated"` with the colored mana pips DEMOTED to
  generic (per `_demote_cost_to_generic`).

### Permanent cast Modes

Always start the cast Mode's effects with `EntersBattlefieldEffect()`
(the engine relies on this ordering).

---

## 9. Cost / mana

- Use `mana_cost=parse_mana_cost(raw)` from the parser.
- `Cost.mana` is parsed mana; non-mana cost components (`tap`,
  `untap`, `sacrifice`, `discard_self`) are separate flags.
- X-cost cards: `mana_cost.has_x=True` but `cmc=0`. The MV≥4 fast-path
  excludes X-cost cards from auto-promotion. For X-spells, the
  simulator's design call is X = 1 (treats every X as if 1 was paid).
  For mass-removal-with-X, parser uses X = 2 (see §1 / §7).
- Conditional mana buffs encode the unconditional baseline only.
  Raucous Audience (`{T}: Add {G}. If you control a creature with
  power 4 or greater, add {G}{G} instead.`) → `produces=[["G"]]`. The
  conditional doubling is dropped.

---

## 10. Examples of "keep current encoding"

These are cases where it's tempting to bump but the owner has decided
to leave alone. If you encounter a similar pattern, prefer the
current encoding.

- **Mana rocks with awkward costs** — Springleaf Drum, White Lotus
  Tile. Don't bump to `is_mana_rock`.
- **Conditional / connect removal** — General Traag, April,
  Mouser Foundry. Don't bump to removal_*.
- **Temporary exile** — Morningtide's Light. Not removal.
- **Activated draw on sac** — Sewer-veillance Cam. Stays `is_other`.
- **Tutor effects** — Splinter's Technique. Stays `is_other`.
- **Conditional draw riders** — Thoughtweft Charge. Don't add
  cards_drawn.

---

## 11. Process notes (for the LLM reviewer)

1. **Read the oracle text** carefully — Magic templating has many
   close-but-different shapes (target vs each, until-EOT vs permanent,
   conditional vs unconditional).
2. **Type-line first** — the type flags (`is_creature` etc.) flow from
   `types` / `subtypes`. Get those right.
3. **One pass per flag family** — don't try to set everything in one
   sweep; walk through removal, manipulation, tokens, combat-trick
   separately.
4. **When in doubt, prefer `is_other`** — under-classifying a card
   loses signal; mis-classifying a card poisons the model. Leave a
   note in `reasons` so a future human can find it.
5. **Always emit a cast Mode for castable cards** — even if the
   effects are unmodelled, the simulator needs SOME cast mode for
   castability checks. Use a `NoopEffect(role_tag="…")` if no other
   effect applies. The store enforces this via
   `_ensure_default_cast_mode_for_castable`.

---

## 12. When to update this guide

Update when:

- The owner makes a new judgment call on a card class (mass removal,
  threaten, sac-effects, etc.).
- A new mechanic is added (firebending, waterbending, earthbending →
  documented above).
- A pattern shows up repeatedly in `FLAGGED_feedback.md` and is
  resolved one way for the canonical case.

Cite the affected card(s) when you add a rule so future readers can
trace the decision back to a real example.
