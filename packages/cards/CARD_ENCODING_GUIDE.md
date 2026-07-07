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
SOS additions (Prepare mechanic, modal-card aggregation, flashback
non-encoding) were settled during the SOS encoding round on 2026-05-14;
see `scripts/sos_encoding/build_sos_patches.py` for examples and
`scripts/sos_encoding/SOS_PREPARED_NOTES.md` for the simulator change
that landed alongside.

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
- **Flash Equipment / Auras that pump on arrival** → set combat-trick
  fields too (owner-confirmed 2026-07-07 on MSH Stolen Stark Tech).
  The play pattern is exactly "flash in to save/win a combat":
  Stolen Stark Tech (#114, ETB auto-attach, +1/+0 + indestructible
  EOT → trick 1/0 ['indestructible']), Super Suit (#78, ETB
  auto-attach, +1/+2 + untap → trick 1/2), Super Speed (#154, flash
  pump aura, +1/+0 haste + first-strike EOT → trick 1/0
  ['first strike'], while the §5 aura fields keep the STATIC grants).
  A flash Equipment WITHOUT an ETB auto-attach doesn't qualify — the
  equip cost gates it past the combat.
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

### Triggered abilities credit ONLY the permanent's own ETB

(Owner ruling 2026-07-07, refined the same day from the Black
Panther / Sokka discussion into a general rule.) A triggered ability
contributes tokens / draws / any role_features signal **only when the
trigger is the permanent's own entry**:

- **Count — self-ETB**: "When this creature enters, …" and compound
  forms that include the own entry: ECL Brigid ("Whenever this
  creature enters or transforms into …"), TMT Mouser Foundry ("When
  this artifact enters or leaves the battlefield"), SOS Stadium
  Tidalmage ("Whenever this creature enters or attacks" — the ETB
  half fires guaranteed; nested conditions are still judged per §2,
  so Tidalmage's "may draw, if you do discard" lands as a net-0
  loot). Crescent Island Temple keeps its body (own ETB counts itself
  for the "for each Shrine" scaling — minimum one).
- **Don't count — everything else**: attack triggers (Suki Kyoshi
  Warrior, Slash, Fire Navy Trebuchet, Cruel Administrator's
  earthbend), cast triggers (Sokka Tenacious Tactician, Madame Hydra,
  Ravenous Robots, Namor, Murmuring Mystic), upkeep / combat-start
  engines (Bitterblossom, Bitterbloom Bearer, Koma, Old Hob,
  Kinbinding, Alien Invasion — cleared under the strict reading;
  revisit if the owner wants a carve-out for unconditional time-based
  engines), counter-placement / draw-second / landfall / leaves-
  the-battlefield triggers (Ant-Man Colony Commander, Madame Masque's
  token — her ETB connive stays, Construct a Cosmic Cube, Mole Man,
  Invisible Woman, Garrison Excavator, Tolls of War), and
  "whenever ANOTHER permanent …" triggers (Black Panther Vanguard,
  Simulacrum Synthesizer, Suki Courageous Rescuer).
- **Death triggers never count** (§19) unless the permanent has its
  own sacrifice outlet (LLM judgment).

Enforced deterministically in the parser (`_is_self_etb_trigger`);
llm_encoded stragglers cleared by
`scripts/audit/apply_etb_only_trigger_ruling_20260707.py` and
`scripts/audit/apply_other_trigger_token_ruling_20260707.py`.

### Named tokens route to review

Named tokens ("create Redwing, a legendary 1/1 blue Bird Scout creature
token …") carry a proper-noun prefix the count-anchored
`_CREATE_TOKEN_RE` can't consume, so the deterministic parser can't size
the body. Fully parsing them would need self-ETB and flavor-word-label
fixes broader than the token itself (MSH Falcon's "Avian Telepathy —"
label; Ka-Zar's no-comma short name "Ka-Zar of the Savage Land"), so
instead a tripwire (`parser._flag_named_tokens` / `_NAMED_TOKEN_RE`,
2026-07-07) demotes any such card to NEEDS_LLM and the MV≥4 fast-path
refuses to promote it. When reviewing one, record the body per the rules
above **or** confirm it's correctly excluded: a named token in an
expensive activated ability (White Tiger's `{5}{G}` power-up, cmc>3 →
no body per §19) or a later Saga chapter (The Coming of Galactus,
ch IV → no body per §6) contributes nothing. The self-ETB named tokens
that DO count: Falcon's Redwing (1/1 U Bird Scout, flying), Ka-Zar's
Zabu (2/2 G Cat).

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

### Additional costs → assume the resource ISN'T available

When a card carries a mandatory *additional* cost to cast (beyond its
mana cost), decide whether that cost is reliably payable in the
turn 1–4 window. **Bump the cast Mode's `cost.mana` (not the printed
`mana_cost`, which stays Scryfall-true — same split as Spree, §12) to
the cheapest cost we can guarantee.** The rule of thumb:

- **Reliably payable → ignore it, keep the printed cost.** Pay-life and
  discard-a-card are (almost) always available, so a spell with "pay N
  life" or "discard a card" as an additional cost keeps its printed mana
  cost. Example: **Redirect Lightning** ("pay 5 life or pay {2}") stays
  `{R}` — you can always pay the life.
- **Not reliably payable → assume it isn't, and pay the mana
  equivalent.** Sacrificing an artifact/creature, or tapping other
  creatures, needs board state we can't count on early. If the cost
  offers a mana alternative, use it; if it's a fixed mana add-on, add
  it. Examples (TLA): **Deadly Precision** ("pay {4} or sacrifice an
  artifact or creature") → cast Mode `{4}{B}`; **Benevolent River
  Spirit** (mandatory "waterbend {5}", where tapping your creatures is
  only optional *help*) → cast Mode `{5}{U}{U}`.
- **Optional additional cost ("you may …") → keep the printed cost.**
  The card does something at its base cost; the extra is a kicker-style
  upgrade (aggregate role_features to the paid outcome per §12, but the
  base Mode stays castable). Examples: the "you may waterbend {N}"
  spells (Secret of Bloodbending, Spirit Water Revival, Ruinous
  Waterbending) and sac-kicker (TMT Stomped by the Foot).
- **Caller-chosen X that can be 0 → keep the printed cost.** A
  *mandatory* "waterbend {X}" is payable at X=0 (no resource needed), so
  the base mana cost is reliably available. Examples: Crashing Wave,
  Foggy Swamp Visions.
- **Conditional cost *reducers* ("costs {N} less if …") → keep the full
  printed cost.** Assuming the discount isn't active is already the
  conservative call, so no change (The Great Henge, Blasphemous Act,
  Gran-Gran).

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

## 12. Modal cards (Choose one / Charm / Spree)

For modal cards — "Choose one — A / B / C", multi-color Charms, and
Spree-style "additional cost" choices — aggregate `role_features` flags
across **every** legal mode, not just the "primary" one a player will
most often pick. The role_features feature space has no "modal"
concept, so summing flags reflects the option value the card gives.

Sim side: a modal spell with an unconditionally-available draw / filter
mode also wires that mode's effects onto the cast Mode — see §18.

### Booleans — set if any mode triggers

If any mode of a modal spell would set a flag in isolation, set it on
the aggregate:

- Lorehold Charm `{R}{W}` — sac artifact / reanimate ≤2 / +1/+1
  anthem EOT → `combat_trick_power=1, combat_trick_toughness=1`
  (the anthem mode is the only one that maps to a flag; sac-artifact
  and gy-reanimate are no-ops in role_features).
- Prismari Charm `{U}{R}` — surveil 2 + draw / 1 dmg / bounce nonland
  → `cards_drawn=1, cards_manipulated=2, removal_burn_damage=1,
  is_bounce=True` (every mode contributes a flag).
- Silverquill Charm `{W}{B}` — +1/+1 counters / exile pow≤2 / drain 3
  → `combat_trick_power=2, combat_trick_toughness=2,
  removal_destroy_or_exile=True`.
- Witherbloom Charm `{B}{G}` — sac → draw 2 / gain 5 / destroy nonland
  ≤2 → `cards_drawn=2, removal_destroy_or_exile=True`.
- Quandrix Charm `{G}{U}` — counter unless 2 / destroy ench / 5/5 base
  EOT → `is_counterspell=True, combat_trick_power=3,
  combat_trick_toughness=3` (5/5 base ≈ +3/+3 over a typical 2/2;
  use the differential as the combat-trick scalar).

### Scalar damage — take the MAX across modes

For `removal_burn_damage: int`, take the LARGER value across modes
(the player picks the better option for the situation):

- Artistic Process `{3}{R}{R}` — modal "6 to creature OR 2 to each
  non-yours OR 3/3 flying token" → `removal_burn_damage=6` (the
  targeted-burn option), plus `is_mass_removal=True` and
  `removal_destroy_or_exile=True` (6 dmg kills).
- Splatter Technique `{1}{U}{U}{R}{R}` — "draw 4 OR 4 dmg sweeper"
  → `cards_drawn=4, removal_burn_damage=4, is_mass_removal=True,
  removal_destroy_or_exile=True`.
- Burst Lightning kicker — `{R}` burn 2 / kicker `{4}{R}` burn 4 →
  `removal_burn_damage=4` (max). Kicker is a from-hand alt cost so
  the kicked Mode IS encoded (see §15).

### Combat-tricks — pick the largest pump

When multiple modes pump (e.g. counters mode + base-P/T mode), pick
the largest scalar across modes for `combat_trick_power` /
`combat_trick_toughness`. See Quandrix Charm above.

### `creates_creatures` aggregation

Include every body any mode creates, one entry per token.

### Spree — encode base + cheapest +mode

Spree spells require at least one paid `+{cost}` choice to do
anything; encoding the base printed cost alone makes the card look
free, which the simulator would treat as castable on T1. Encode the
**printed cost + 1** (the cheapest mode):

- Requisition Raid `{W}` Spree → encode cost `{1}{W}`.
- Return the Favor `{R}{R}` Spree → encode cost `{1}{R}{R}`.

The +modes themselves are mostly out-of-scope for the simulator (none
of them are mana / draw / fetch); leave `role_features` as `is_other`
until a Spree card with a sim-relevant +mode appears.

---

## 13. SOS Prepare mechanic

The Prepare mechanic prints a creature on the front face and a sorcery
or instant on the back face. While the creature is *prepared*, the
controller may cast a copy of the back-face spell from exile, paying
its mana cost separately, and then unprepare the source.

The simulator tracks prepared status via `GameState.prepared` and
treats `Mode(kind="prepared")` as a battlefield-resident sorcery-speed
cast. See `scripts/sos_encoding/SOS_PREPARED_NOTES.md` for the
implementation.

### Pre-prepared (front face says "This creature enters prepared")

Encode TWO modes:

- The creature's normal `Mode(kind="cast")` with the printed cost and
  `EntersBattlefieldEffect`. The engine's `_place_after_cast` flags
  the resulting permanent as prepared automatically because it has
  at least one `kind="prepared"` mode.
- A `Mode(kind="prepared")` with the prepare spell's mana cost and
  effects. Use the same effect vocabulary as a regular cast mode
  (`FetchLandEffect`, `DrawCardsEffect`, `LookAtTopEffect`, etc.).

`role_features` are merged across the creature + prepare spell — from
the player's perspective the card is one slot that gives both. So
Studious First-Year `{G}` // Rampant Growth `{1}{G}` keeps
`is_creature=True` (the body) but the simulator's S1c picker will
auto-cast the prepared FetchLandEffect on a later turn.

Reference the helper `prepared_mode(...)` in
`scripts/sos_encoding/build_sos_patches.py` for the canonical encoding.

### Conditionally prepared (no "enters prepared")

If becoming prepared requires a separate trigger (attack, gain life,
cast your third spell, control 8+ lands, …) we do **not** encode the
prepare spell. Encode the creature's `Mode(kind="cast")` only, and
omit the `Mode(kind="prepared")` entirely.

Without a `kind="prepared"` mode, the engine's `_place_after_cast`
won't flag the permanent as prepared and the prepare spell is
invisible to the simulator — which matches the gameplay reality that
the spell only fires after a separate event we don't model.

Examples currently encoded this way: #13 Emeritus of Truce, #23 Joined
Researchers, #33 Spiritcall Enthusiast, #46 Encouraging Aviator,
#52 Harmonized Trio, #85 Grave Researcher, #88 Leech Collector,
#98 Scathing Shadelock, #99 Scheming Silvertongue, #113 Emeritus of
Conflict, #170 Abigale Poet Laureate, #198 Kirol History Buff,
#237 Tam Observant Sequencer.

### S5 — the policy enabler

The simulator's S5 picker (`_pick_s5_cast_prepared_enabler`) casts a
hand creature whose prepared mode is mulligan-relevant (fetch / draw /
land-find) when no other tier matches. Without S5, Studious
First-Year wouldn't be cast on T1 (the policy doesn't normally cast
plain creatures) and its prepared Rampant Growth would never become
available on T2.

S5 fires LAST in the priority chain, so any genuinely-better action
(real ramp, real draw, mana dork) takes precedence.

---

## 14. Alt-cost mechanics — when to encode as a second cast mode

The `Mode(kind="cast")` discriminator covers spells played from hand.
For mechanics that pay the alt cost from a different zone, the
simulator's policy would mis-treat a second cast mode as "cast from
hand at the alt cost," which is wrong. Decide based on **where the
alt cost is paid from**:

### Alt cost paid from hand → second `Mode(kind="cast")` (or other appropriate kind)

- **Evoke** (ECL Catharsis) — second cast Mode at the evoke cost.
  Effects describe what happens when the creature is sacrificed on
  entry; omit `EntersBattlefieldEffect` because the creature dies
  immediately.
- **Kicker / Multikicker** — second cast Mode at `printed + kicker`
  with the kicked effects (Burst Lightning kicker `{4}` → second
  Mode at `{4}{R}` with `removal_burn_damage=4`).
- **Madness** — alt cast from exile after discarding; treat as
  hand-resident for the simulator.
- **Morph** — second cast Mode at `{3}` (face-down 2/2). Effects:
  `EntersBattlefieldEffect` only — the face-down body is vanilla.
- **Overload** — second cast Mode at the overload cost; effects mirror
  the targeted version but mass.

### Alt cost paid from graveyard (or another non-hand zone) → DO NOT encode

The simulator iterates cast modes for cards **in hand**; encoding a
graveyard-resident alt cost as `kind="cast"` lets the policy "cast"
it from hand at the alt cost (wrong both because the card isn't in
the gy and because the behavior duplicates the original cast mode at
a higher cost). Drop these alt-cost modes from the encoding entirely;
the role_features signal carries the value to the model:

- **Flashback** — drop the flashback Mode. SOS examples: #7 Antiquities
  on the Loose, #9 Daydream, #10 Dig Site Inventory, #25 Practiced
  Offense, #112 Duel Tactics, #135 Tome Blast, #204 Molten Note,
  #216 Pursue the Past, #bonus-fdn-80 Bulk Up.
- **Group Project** (#17) — flashback cost is "tap three untapped
  creatures" (non-mana); drop both because we can't model the cost
  AND because flashback shouldn't be a second cast mode anyway.
- **Jump-start** — same shape as flashback; drop the jump-start mode.
- **Aftermath** — drop the aftermath mode.
- **Suspend** — currently encoded as a single cast mode at the suspend
  cost when the card has no normal mana cost (Living End). For cards
  with both a normal cost AND suspend, drop the suspend mode and
  keep the normal cast.
- **Foretell** — drop the foretell mode. (No SOS examples; same
  principle as flashback.)

If a graveyard-resident alt cost ever needs proper sim support, the
right approach is to mirror the SOS Prepare implementation — a new
`Mode.kind` plus battlefield/exile-resident castability, not a second
cast Mode.

### Cycling, channel, land-cycling — keep as-is

These have their own mode kinds (`cycle`, `channel`, `land_cycle`) and
the simulator already knows how to treat them as alternatives to
casting from hand.

---

## 15. Look-at-top-N — `cards_drawn += 1`, `cards_manipulated += N - 1`

The look-at-top-N pattern (Stock Up, Sleight of Hand, Flow State,
Expressive Iteration, Follow the Lumarets) consistently encodes:

- `cards_drawn += 1` — the card put in hand counts as a draw.
- `cards_manipulated += N - 1` — the cards looked at but not taken.

Set both, plus a `LookAtTopEffect(n=N, accepts_land=True,
accepts_nonland=True)` on the cast Mode so the simulator's S2/S4
policy can use the card as a probabilistic hand-fetch.

For multi-take patterns (Stock Up: top 5, take 2) bump `cards_drawn`
by the number taken: `cards_drawn=2, cards_manipulated=3` (5 - 2).

Variable-N (Stargaze: top 2X, take X) uses X=1 minimum per the
encoding guide §9 convention: `cards_drawn=1, cards_manipulated=2`.

---

## 16. MSH (Marvel Super Heroes) additions

Settled during the MSH encoding round on 2026-06-21; see
`scripts/marvel_encoding/build_msh_patches.py` for the worked examples.

### Teamwork N — encode the teamwork-enhanced outcome, no second Mode

Teamwork N is an optional additional cost ("you may tap any number of
creatures you control with total power N or more") that upgrades the
spell's effect if paid. Treat it like kicker for role_features purposes
— encode the BETTER (teamwork-paid) outcome using the existing
max-value modal convention (§12) — but do **not** add a second cast
Mode. Unlike kicker, the alt cost here is "tap OTHER permanents," which
has no representation in the `Cost` model (no "tap creatures with
total power >= N" cost component exists, by design). Examples:
Helicarrier Strike (#15, removal_burn_damage=4, the teamwork value),
Team Tactics (#155, combat_trick_granted_keywords includes the
teamwork-only trample grant).

### MDFC pairs where both faces are independently-castable creatures

Cards like Tony Stark // The Invincible Iron Man (#80) are `modal_dfc`
layout where BOTH faces are creatures with their own printed mana cost
— not a `transform` DFC. Encode TWO `Mode(kind="cast")` entries, one
per face, each with `EntersBattlefieldEffect`. Aggregate role_features
across both faces per the modal-card principle in §12.

Several of these cards' front faces also carry a "{cost}: Transform
[name]. Activate only as a sorcery." activated ability — a
permanent-resident upgrade path that doesn't require holding the back
face in hand. Leave this unencoded: it's gated by a high mana cost
(typically 4-6), is rarely relevant inside the turn 1-4 mulligan
window, and the simulator has no "transformed" state to evaluate it
against. Examples: #18 Jennifer Walters // The Sensational She-Hulk,
#23 Monica Rambeau // Photon, #49 Bruce Banner // The Incredible Hulk,
#80 Tony Stark // The Invincible Iron Man, #219 King T'Challa // Black
Panther.

### -N/-N until end of turn (instant) — extends the counter threshold

§7's "-N/-N counters, N>=2 counts as removal" rule was written for
permanent counters. Extend the same N>=2 threshold to temporary
until-end-of-turn debuffs on instants — a -2/-2 or -4/-4 EOT instant
kills the same creatures a permanent counter would, just for one
combat. Examples: Dark Deed (#93, -4/-4 EOT → removal_destroy_or_exile),
Widow's Bite (#122, the -2/-2 EOT mode → removal_destroy_or_exile,
aggregated alongside the deathtouch-grant mode's combat-trick flag).

### Mill N, take a permanent card — model as LookAtTopEffect

"Mill two cards, you may put a permanent card from among them into
your hand" (Rapid Rescue, #181) is modeled with `LookAtTopEffect(n=2)`
per the existing §15 convention (cards_drawn+=1,
cards_manipulated+=N-1) even though the unchosen cards go to the
graveyard rather than the bottom of the library. The simulator never
inspects graveyard/library order — only hand contents — so the
distinction is immaterial.

### Impulse draw ("exile, may play until end of next turn") — leave unencoded

Decided 2026-06-22. Blazing Crescendo (#125) and Hex Magic (#133) both
exile cards and let the player play them later, rather than drawing
them to hand — this doesn't fit the existing `cards_drawn` convention
(the card never enters hand). Leave both unencoded:

- Blazing Crescendo's single exiled card is too conditional on having
  mana available on a later turn to count as a reliable draw.
- Hex Magic's exile-then-draw count equals your hand size at cast
  time, which the encoder has no way to fix to a concrete N (unlike
  the X=1-minimum convention for token bodies, there's no sensible
  floor here — a card drawn this way is genuinely unbounded).

### Triggered / recurring abilities are not modeled for card draw

Decided 2026-06-22. This extends the existing conservatism around
conditional/repeated draw (§2) into a general policy: card-draw
triggers gated on a recurring event (upkeep, attack, "whenever you
cast...", etc.) are left unencoded regardless of what permanent type
they're printed on, because they're too far outside the turn 1-4
mulligan window to size reliably.

Super Intelligence (#77) — an Aura granting "at the beginning of the
upkeep of enchanted creature's controller, that player draws a
card" — is the canonical example. It sets neither `is_removal_aura`
nor `is_pump_aura`; §5's binary doesn't have a bucket for a recurring
value-engine Aura, and this policy means there's nothing else to flag,
so the card falls through to `is_other` via the store invariant. Don't
force auras of this shape into the pump/removal binary.

### Edict effects and type-unrestricted "destroy target token" → removal_destroy_or_exile

Decided 2026-06-22, on The Ruinous Wrecking Crew (#224, an X-cost ETB
modal with a "destroy target token" mode and an "each player
sacrifices a creature of their choice" mode among its options). Both
modes independently justify `removal_destroy_or_exile=True`,
aggregated per the Charm/modal convention (§12):

- **Edicts** ("each player sacrifices a creature of their choice") are
  removal of the opponent's choice — treat the same as targeted
  destroy/exile.
- **"Destroy target token"** counts even though the token type isn't
  restricted to creatures — most tokens encountered in Limited are
  creature tokens, and a false positive here is cheap.

The card's other two modes (discard-then-draw, a net-0 loot; opponent
loses 2 life) aren't separately encoded.

### Bonus-sheet additions (decided 2026-06-23)

MSH's 60-card bonus sheet (classic reprints, discovered via
`_bonus_sheet_scryfall_entries` once 17Lands published MSH ratings —
see `packages/cards/CLAUDE.md`'s "Current auto-classification rate"
section for the correction story behind this) introduced needs_llm
cards with a few new shapes:

**Combat-gated sweepers count as removal.** Fight to the Death
("destroy all blocking creatures and all blocked creatures") requires
an active combat with declared blockers, but it's a dedicated removal
spell, not a rarely-fired downside ability — treat it like
`is_punch_fight` cards, which are flagged despite the sim not modeling
combat (§8). This is distinct from the General Traag / Mouser Foundry
"too situational" precedent (§10), which is about resource-gated
activated abilities on creatures that usually never fire, not about
ordinary combat-conditional removal spells.

**"Choose one or more" sweepers aggregate like a Charm.** Final Act
("choose one or more — destroy all creatures / all planeswalkers / all
battles / exile all graveyards / opponents lose all counters") sets
`removal_destroy_or_exile` + `is_mass_removal` from the
destroy-all-creatures mode only; the other modes have no role_features
field and aren't separately encoded. Same aggregation principle as
Spree and Charms (§12), just without Spree's additional-cost structure.

**Shuffle-into-library removal → `removal_destroy_or_exile`, not
`is_top_library`.** Chaos Warp (shuffle target permanent into its
owner's library, they reveal-and-maybe-replace from the top) is closer
to full removal than to `is_top_library`: against a creature target,
the creature is gone with certainty, and what comes back is a random
gamble usually lower-impact in Limited. `is_top_library` is reserved
for controlled, *recurring* placement on top of the library (Swat
Away, Return to the Sewers) — a softer, more findable effect than a
shuffle.

**Mass-protection instants are not single-target combat tricks.**
Heroic Intervention ("permanents you control gain hexproof and
indestructible until end of turn") and Teferi's Protection (life total
locked, protection from everything, all your permanents phase out)
both grant protective effects board-wide, not to "target creature."
`combat_trick_granted_keywords` (§3) is scoped to saving/winning ONE
combat for a targeted creature; stretching it to cover symmetric,
board-wide protection would misrepresent the field. Both cards are left
at `is_other`.

**Unrecognized old keywords get appended directly to
`evergreen_keywords`.** Dauthi Voidwalker's Shadow is a pre-modern
evergreen keyword the parser's vocabulary doesn't recognize. Since
combat/evasion isn't simulated anyway, just append the lowercase
keyword string to `evergreen_keywords` in the patch rather than leaving
it as an unrecognised-line parser note.

**Sac-an-artifact-or-creature draw stays unencoded, even when the cost
is easy to pay.** Deadly Dispute (sacrifice an artifact or creature as
an additional cost; draw 2 + make a Treasure) is structurally the same
shape as the "sac-or-activated draw with mana cost" precedent
(Sewer-veillance Cam, §2) even though its cost is far easier to satisfy
in practice (any spare token/dork qualifies, unlike sacrificing the
card's own permanent). Kept `is_other` for consistency with the
existing precedent rather than carving out an exception — flagged in
the card's `reasons` in case the owner wants to revisit.

## 17. TLA / TMT alt-cost review (2026-06-30)

A focused pass over every alternative-casting-cost card in TLA and TMT,
mirroring the SOS alt-cost cleanup. Findings and conventions applied:

### Mana kicker → second `Mode(kind="cast")` + role_features MAX

The four TLA mana-kicker spells had only the base cast mode, and one had
role_features pinned to the *unkicked* outcome. Fixed to the §12/§14
convention (Burst Lightning is the reference: two cast modes, scalar
role_features = the kicked value):

- **#138 Firebending Lesson** ({R}, Kicker {4}: 2 dmg → 5 dmg) —
  `removal_burn_damage` 2 → **5** (the kicked value, per §12), plus the
  {4}{R} kicked cast mode. This was the one genuine functional bug: the
  model was seeing a 2-damage spell, not the 5-damage removal it usually
  is.
- **#1 Aang's Journey** ({2}, Kicker {2}) — added the {4} kicked cast
  mode (still a basic-land-to-hand fetch; the extra Shrine tutor is a
  nonland card that stays `is_other`). role_features unchanged.
- **#123 Zuko's Conviction** ({B}, Kicker {4}) — added the {4}{B} kicked
  cast mode (reanimate; unmodeled, `is_other`). role_features unchanged.
- **#143 Jet's Brainwashing** ({R}, Kicker {3}) — added the {3}{R} kicked
  cast mode (threaten; unmodeled, `is_other`). role_features unchanged.

The second (kicked) cast mode is always the more expensive one, so the
simulator — which casts the cheapest playable mode — never picks it;
adding it is for consistency with the convention and any future
mode-counting feature. The part that *matters* is the role_features MAX
(Firebending Lesson).

### Non-mana kicker → NO second cast mode

**TMT #82 Stomped by the Foot** kicks by *sacrificing an artifact or
creature* (not mana): base -2/-2, kicked -5/-5. Per the §16 Teamwork
precedent for non-mana alt costs, we do **not** add a second cast mode
(the base is already castable; a sac-cost mode would be strictly more
restrictive). role_features already sits at `removal_destroy_or_exile`
(correct for both -N/-N modes per §16), so no change.

### Sneak (TMT) → drop the alt cost, keep the normal-cost cast mode

Sneak is a from-hand alternative cost ("You may cast this spell for {N}
if you also return an unblocked attacker you control to hand during the
declare blockers step"). Although hand-resident, the discount is gated on
a mid-combat board state (an unblocked attacker) the simulator can't
represent, and the sneak cost is usually *cheaper* than the printed cost
— so encoding it as a second cast mode would make these creatures look
castable a turn or two early (the trap §14 warns about for graveyard
costs; same reasoning as Force of Negation's free pitch). Convention:
**encode only the normal `Mode(kind="cast")` at the printed cost; drop
the sneak cost.** `sneak` is already in
`keywords.py:IGNORABLE_KEYWORD_LINES` so these auto-classify. Verified
all 26 TMT Sneak cards use the printed cost, not the sneak cost.

### Graveyard / exile-resident costs — verified correct (no change)

All TLA flashback/foretell spells (#133 Fire Nation Attacks, #153
Solstice Revelations, #154 Sozin's Comet, #192 Rockalanche) already carry
only their normal-cost cast mode with the alt cost dropped, per §14. Iroh,
Grand Lotus (#227) is a creature that *grants* flashback — correct as a
plain creature. Force of Negation (free pitch) and Hama (grants a
waterbend cast to an *exiled* card, not to itself) are also correct.

### Additional (non-mana) costs → sim-cost bump

A follow-up pass (same day) checked every "additional cost to cast"
card. Two understated their castability because the cast Mode used only
the printed mana cost even though a resource we can't guarantee is
required. Bumped the **cast Mode** cost (printed `mana_cost` unchanged),
per the new rule in §9:

- **Deadly Precision** ({B}, "pay {4} or sacrifice an artifact or
  creature") → cast Mode `{4}{B}` (assume no sac fodder).
- **Benevolent River Spirit** ({U}{U}, mandatory "waterbend {5}") → cast
  Mode `{5}{U}{U}` (assume no creatures to tap).

Everything else was correctly left alone — pay-life additional costs
(Redirect Lightning), optional "you may waterbend {N}" upgrades,
X=0-payable "waterbend {X}" (Crashing Wave, Foggy Swamp Visions), and
conditional cost reducers. See §9 for the full decision table.

### Bonus-sheet "special guest" backlog — all encoded (2026-06-30)

The alt-cost review surfaced a backlog of un-encoded (`needs_llm`)
"special guest" bonus-sheet reprints — 17 in TLA, 6 in TMT — that
`check_deck_encodings` rejected. At the owner's request they were all
hand-encoded in the same pass; both sets are now 0 `needs_llm` / 0
`needs_human`. Decisions worth remembering (all consistent with the
rules above):

- **Alt-cost cards** confirmed the §14 split: Cityscape Leveler
  (unearth), Join the Dance (flashback, → two 1/1 Human tokens), Waves
  of Aggression (retrace), Underworld Breach (escape, → `is_other`) all
  drop their graveyard-resident alt cost; Shattering Spree (replicate,
  destroy artifact → `is_other`) drops the variable-copy cost.
- **Mass-protection instants** Teferi's Protection and Heroic
  Intervention → `is_other` (§16), not combat tricks.
- **Recurring triggered draw** Mystic Remora → `is_other`; its
  parser-set `cards_drawn=1` was cleared per §16 (triggered/upkeep draw
  isn't modeled).
- **Return of the Wildspeaker** (modal instant) aggregates per §12:
  `cards_drawn=1` (variable draw, min 1) + `combat_trick_power/toughness=3`
  (the mass +3/+3 mode).
- **Prosperity** ("each player draws X") → `cards_drawn=1` + a
  `DrawCardsEffect(n=1)` on the cast mode (X=1 minimum, §9).
- **Imprisoned in the Moon** (turns a creature/pw into a do-nothing
  land) → `is_removal_aura` (§5 "loses all abilities" bucket).
- **Tutors** Eladamri's Call / Cruel Tutor → `is_other` (§10).
- **Dark Depths** → `is_land` only; the {30}-of-activations Marit Lage
  is far too gated to model as token creation.
- **Umezawa's Jitte** → `is_equipment` only (counter-gated modal
  abilities aren't mulligan-relevant); Metallic Mimic / Arcbound
  Ravager → `is_creature` only; Intruder Alarm / Training Grounds /
  Rhythm of the Wild → `is_other`.

## 18. Random-commons spot check (2026-07-03)

A review of 20 random TLA/TMT/SOS commons (seed 20260703) surfaced a
few fixes and one new convention, applied by
`scripts/audit/apply_review_20260703_fixes.py`.

### Modal draw modes get sim effects

The owner's call: a modal instant/sorcery whose draw (or filter) mode
is **unconditionally available at the spell's cost** wires that mode's
effects onto the cast Mode, so the simulator sees the card as playable
card draw — mirroring the §12 "player picks the best option" logic that
already governs `role_features`. (Previously modal cards carried the
draw only in `role_features`; there was no reason for the asymmetry.)

- Splatter Technique → `DrawCardsEffect(4)`; Shredder's Revenge →
  `DrawCardsEffect(2)`; Ashling's Command → `DrawCardsEffect(2)`;
  Sygg's Command → `DrawCardsEffect(1)`; Return of the Wildspeaker →
  `DrawCardsEffect(1)` (variable draw, min 1 per §9); Prismari Charm →
  `ScryEffect(2) + DrawCardsEffect(1)` (both from its
  "surveil 2, then draw" mode — the chosen mode does both).
- **Gated modes stay unwired**: Witherbloom Charm (draw needs a
  sacrifice — assume the fodder isn't there, §9), Glorious Decay (draw
  mode needs a graveyard target, often absent turns 1–4), Zuko,
  Conflicted / Professor Dellian Fel (the choice isn't made at cast
  time — it's a recurring trigger / planeswalker activation).

### "Target player draws" is self-targetable

Shredder's Revenge's "Target player draws two cards and loses 2 life"
mode reads as opponent disruption but is Sign in Blood templating — the
caster can (and in Limited often does) target themselves. Count it as a
draw mode: `cards_drawn=2` per §12.

### Triggered-draw clearances (§16 back-applied)

Two TMT cards predating the §16 policy still carried parser-set
`cards_drawn=1` from recurring triggers; both cleared, matching the
April, Reporter of the Weird precedent (§2) and the Mystic Remora
cleanup (§17): Oroku Saki, Shredder Rising (combat-damage trigger) and
April O'Neil, Hacktivist (end-step trigger).

### Stats on graveyard-resident activations don't count

Stone Docent's "{W}, Exile this card from your graveyard: … Surveil 1"
had parser-set `cards_manipulated=1` — but the creature has to already
be dead for the ability to exist, so it can't contribute in the
mulligan window the way cast/ETB surveil does. Cleared (same
conservatism as Sewer-veillance Cam, §2). Watch for the deterministic
parser crediting scry/surveil/draw chunks inside activation lines; it
doesn't check the activation zone.

### Net-0 discard-self filters are `cards_drawn=0`

Visionary's Dance's "{2}, Discard this card: look at top 2, take 1"
channel mode replaces itself — net hand change is 0, like cycling
(Locust Spray) and Gristle Glutton's net-0 loot. `cards_drawn` corrected
1 → 0; `cards_manipulated=1` and the `LookAtTopEffect` stay. §15's
"look-at-top sets cards_drawn += 1" applies to *cast-mode*
look-at-top (Sleight of Hand shapes), where the net really is +1.

## 19. Parser hardening + owner rulings (2026-07-06)

Settled during the pre-release MSH commons recheck (batch 1 findings in
`scripts/audit/MSH_commons_recheck.md`; encodings applied by
`scripts/audit/apply_tripwire_encodings_20260706.py`).

### Death triggers are not encoded

"When/Whenever … dies" triggers are too conditional on board state to
credit `role_features` — no `creates_creatures`, no `cards_drawn`, no
`cards_manipulated` from a death trigger. Canonical example: Agents of
HYDRA (MSH #85, dies → 2/1 menace token) records **no** token body.
Same for death-trigger draw (TMT Buzz Bots' "When this creature dies,
draw a card" no longer credits `cards_drawn`).

**Exception:** if the card itself carries an activated sacrifice outlet
for the permanent (so the death trigger is effectively an activated
ability), the LLM reviewer may encode the trigger's effect — judge
case-by-case; the deterministic parser always skips.

### Activated abilities credit role_features only when cmc ≤ 3

The mulligan-relevance rule of §2's Gristle Glutton note is now a hard
deterministic gate in `_build_activated_mode`:

- Cost parses and `cmc <= 3` → role_features credited as before
  (Agna Qel'a's `{2}{U}, {T}` loot keeps `cards_manipulated=1`).
- Cost parses and `cmc > 3` → the Mode is still built (the simulator
  gates by cost itself) but **no** role_features are credited: Bold
  Biochemist's `{5}{U}` power-up draw, Everything Pizza's 7-mana burn,
  Pet Avengers / Ultron Drone / Sun Warriors / Jasmine Dragon Tea
  Shop's expensive token activations all cleared (Mouser Foundry /
  Weather Maker conservatism, §1).
- Cost doesn't parse (Blight N, remove-a-counter, pay-life,
  discard-a-card, loyalty `+N:`) → draw/scry/loot signals are NOT
  credited (token scan still runs). This also cleared Ice Cream Kitty
  and Charging Strifeknight (sac-/discard-gated draw — consistent with
  Sewer-veillance Cam, §2) and Professor Dellian Fel (loyalty draw).

### Connive = loot

- **ETB connive** ("When this creature enters, it connives.") → loot
  per §2: `cards_manipulated=1`, `cards_drawn=0`, and
  `DrawCardsEffect(1) + DiscardCardEffect(1)` on the cast mode.
  A.I.M. Scientists (#44), Red Room Recruit (#110), Madame Masque
  (#104).
- **Cheap on-arrival activated connive**: M.O.D.O.K.'s "Pay 3 life:
  connives" is assumed to fire **once when it arrives** (owner ruling —
  pay-life is reliably payable, §9) → same loot encoding.
- **Attack-trigger / recurring connives** stay unencoded per §16
  (Swordsman #116, Kang #217, Leader Super-Genius #64).
- **Expensive land activations are ignored** (owner ruling):
  Villainous Hideout's `{3}, {T}: target Villain connives` gets no
  credit — matches how the simulator never uses expensive utility-land
  activations in the mulligan window.

### Unknown-keyword tripwire + drop census

The parser now bails to NEEDS_LLM when a card's structured Scryfall
`keywords` list contains anything outside the known vocabulary
(`keywords.py:KNOWN_KEYWORDS_EXTRA` holds the grandfathered
residuals; `connive` and `teamwork` are deliberately excluded so their
cards route to review). The MV≥4 fast-path also refuses such cards.
**When a new set ships:** run
`uv run mulligan-coach-cards census-drops --sets <SET>` after the
detector and skim the report — it lists every silently-dropped oracle
chunk by frequency (this is the report that would have caught connive
on day one). Encode the flipped cards, then add the new keyword to
`KNOWN_KEYWORDS_EXTRA` once its convention is settled here.

### Smaller fixes in the same round

- **Token keywords** are now parser-captured from "…creature token
  with <keywords>" (evergreen keywords only, stops at the first
  non-keyword word) — §4's rule no longer needs hand-patching.
  Backfilled menace/vigilance/defender/haste/flying/deathtouch tokens
  across MSH/SOS/ECL.
- **"Choose one." (period form)** now counts as modal text for the
  fast-path exclusion (MSH templating; caught Atlantis Attacks #46 and
  SOS bonus-sheet Akroma's Will — the latter encoded `is_other` per
  §16 mass-protection).
- **Teamwork encodings** (§16 applied): Atlantis Attacks aggregates
  per §12 (self-targetable "target player creates" → 6/5 Leviathan
  body, plus `is_bounce`); Repulsor Blast keeps
  `removal_burn_damage=5` (the teamwork rider is face damage);
  Earth's Mightiest Heroes is a 6-mana battlefield tutor → `is_other`
  (reveal-to-battlefield is not a §15 hand-fetch).
- **ECL bonus-sheet stragglers** (5 cards that had sat `needs_llm`
  since the set shipped) encoded: Heat Shimmer (copy token →
  `is_other`), Manamorphose (`cards_drawn=1` + DrawCardsEffect; mana
  production left unencoded), Dolmen Gate (`is_other`), Painter's
  Servant (plain creature), Idyllic Tutor (`is_other`, §10).
- **TMT Bebop / Rocksteady**: their typecycling reminder text trips
  the discard-self fast-path guard, so they surfaced for review; the
  parsed encoding (cast + land_cycle) was already correct and the
  tribal-lord statics stay unmodelled.

### Round 2 (same day, from the batch-2 commons review)

Four more parser-vs-guide gaps found while reviewing MSH commons
16–30, all now enforced deterministically:

- **Multi-token counts** — `_match_token_creation` now emits one
  CreatureBody **per token** ("create two 3/2 Heroes" → two bodies),
  finally matching §4's revised rule (Borough Backup, Okoye, Robot
  Domination, Empty the Warrens, Boggart Mischief, …). The aura and
  vehicle/equipment branches' top-level token scans were removed at
  the same time — they double-counted bodies the per-chunk scans
  already record.
- **Recurring-trigger draw is now skipped by the parser** — §16's
  policy ("Whenever …" / "At the beginning of …" draw is never
  credited) had only ever been enforced by one-off audit patches;
  `_extract_triggered_signal` now skips draw/loot/scry for recurring
  triggers directly. This cleared ~50 cards across the five sets
  (Political Triumph, Reconnaissance Mission, Sword of Fire and Ice,
  Agent of Treachery's end-step draw, …). One-shot "When … enters"
  triggers still credit; attack-trigger earthbend token bodies are
  unaffected (Sokka precedent, §4).
- **Non-creature permanents wire self-ETB effects onto the cast
  mode** — mirrors the creature branch, so cheap artifact/enchantment
  cantrips are visible to the simulator (Futurist Forge's ETB draw,
  Simulacrum Synthesizer's scry 2, Everything Pizza's basic-to-hand
  fetch). Bonus: ETB-removal enchantments now set
  `removal_destroy_or_exile` per §1 (Web Up, Super Villain Lockup).
- **Vehicles/equipment can carry mana abilities** — Dependable
  Quinjet's "{T}: Add one mana of any color." lands in
  `mana_abilities` (a Manalith with wheels). §1's `is_mana_rock`
  still deliberately excludes vehicles/equipment.
- **Deadly Dispute field/ruling mismatch fixed** — the 2026-06-23
  encode recorded the §16 "kept is_other, no draw credit" ruling in
  its reasons but left the parser-populated `cards_drawn=2` +
  DrawCardsEffect in place. Fields now match the ruling.

## 20. When to update this guide

Update when:

- The owner makes a new judgment call on a card class (mass removal,
  threaten, sac-effects, etc.).
- A new mechanic is added (firebending, waterbending, earthbending,
  prepared → documented above).
- A pattern shows up repeatedly in `FLAGGED_feedback.md` and is
  resolved one way for the canonical case.

Cite the affected card(s) when you add a rule so future readers can
trace the decision back to a real example.
