# MTG Limited Mana Simulator — Design Document

## Purpose

Monte Carlo simulator for the first 4 turns of a Magic: The Gathering game (goldfish format — no opponent). Given a decklist, simulate many games and report, for each card drawn,  on which turn it first became castable. Primary use: evaluating hands for mulligan decision.

## Output specification

For each simulation run, output per turn (1–4):
- For each card in hand at the start of that turn's main phase: `castable: yes/no`
- "Castable" means: there exists *some* legal land drop this turn (using a land in hand) such that the card could be cast with the resulting mana. This captures option value — a card is castable even if the policy chooses to play a different land for curve reasons.
- For each card across all turns: `first_castable_turn: 1 / 2 / 3 / 4 / 5+`. (5+ = never castable in the 4-turn window.)

Aggregate across N runs to compute, for each card in the decklist:
- P(castable by turn T | in hand)

We will use that in a later step to compute statistics such as P(playing creature on turn 2)

## Game model

### Turn structure (simplified)

For turns 1–4:
1. **Draw step**: draw 1 card (skip on turn 1 if on the play).
2. **Castability snapshot**: for each card in hand, compute `castable_this_turn` — does *any* legal land drop from hand make this card castable this turn? Record to output. **Do this before any actions are taken this turn.**
3. **Land-drop decision**: choose which land in hand to play, per the policy below. Play it. Resolve any ETB effects (including fetches).
4. **Main phase**: cast spells per the policy below (utility spells only — see "Spell-casting policy").
5. **End of turn**: clear mana pool, untap step happens at start of next turn.

Skip combat, skip the opponent, skip end-step triggers.

### On the play vs. on the draw

Parameter, default `on_the_play=True`. Affects whether turn 1 includes a draw.

## Card model

The cards package provides the encoding of cards. Please look at the cards package and its CLAUDE.md file to understand the data structure.

## Land patterns to support

1. **Basic lands** — single mana ability, single color, never ETB tapped.
2. **ETB tapped duals** — like Deathcap Glade: `etb_tapped = Predicate(controls_lands_count < 2)`.
3. **Sac-for-basic lands** (Evolving Wilds): a non-mana activated ability with cost `[Tap, Sacrifice, Pay({1})]` (or whatever) and effect `fetch_land(basic, battlefield_tapped)`.
4. **Filter lands** ({1} → any color): mana ability with `cost=[Tap, Pay({1})]`, `produces=[any_color]`. The mana solver must handle this.
5. **Conditional duals** (1.iv from prior discussion): condition predicate on the mana ability.
6. **IGNORE**: lands that produce mana only for specific spell types or under obscure conditions.

## Land-search effects

A `fetch_land` effect has three axes:
- **Trigger**: ETB, cast trigger, activated ability, sacrifice cost.
- **Target filter**: basic / any land / specific subtype (e.g., Forest).
- **Destination**: `battlefield_untapped` / `battlefield_tapped` / `hand`.

Examples:
- Environmental Scientist: ETB → basic → hand
- Strixhaven Skycoach: ETB → basic → hand
- Cultivate: cast → basic → battlefield_tapped + hand (it does both)
- Three Visits: cast → Forest → battlefield_untapped
- Evolving Wilds: activated (sac) → basic → battlefield_tapped

`LookAtTopEffect` is a sibling for the **top-N filter** case (Midnight Tilling, Cowabunga!, Accumulate Wisdom): pop the top n cards, take the first land if `accepts_land`, fall back to the first nonland if `accepts_nonland`, bottom the rest. Treated as a hand-fetch by the spell-casting policy (S2 / S4) so it gates on whether the hand already has a land.

## Mana sources beyond lands

- **Mana dorks** (creatures with mana abilities): respect summoning sickness. Cannot tap for mana the turn they enter.
- **Mana rocks** (non-creature artifacts): no summoning sickness.
- Both flow through the same `ManaAbility` model.

## Alternative casting costs

Each card has a list of `modes`. The castability check evaluates each mode independently. Modes include:
- **Cycling**: cost = cycling cost, effect = `discard_self + draw(1)`.
- **Land-cycling**: cost = land-cycling cost, effect = `discard_self + fetch_land(specified_subtype, hand)`.
- **Channel / discard-for-effect**: cost = stated cost, effect = stated effect.

Castability for the different modes needs to be tracked separately. So for example spell castable 5+, alternative mode castable 3.

## Castability check (`is_castable`)

Pure function. Inputs: card, game state. Output: `{castable: bool, modes_castable: list[Mode]}`.

Implementation:
1. Enumerate available mana sources: untapped lands, untapped non-summoning-sick mana permanents.
2. For each mode of the card:
   - Build the set of `ManaAbility` instances available to be activated.
   - Solve the mana CSP: can the available abilities, when activated in some sequence (filter lands consume mana to produce mana, so order matters), produce the cost?
3. Return true if any mode succeeds.

The mana CSP for a single cost is small (≤7 sources typically). Brute-force enumeration of mana ability assignments is fine. For filter lands, model the activation graph and check for a valid flow.

### "Castable this turn with any land drop" (the snapshot metric)

For step 2 of the turn structure:
- For each land in hand (plus the option of playing no land): hypothetically play it, then run `is_castable` on each non-land card in hand. A card is `castable_this_turn = True` if at least one land choice (or no-land) makes it castable.
- This is the option-value metric. It's what gets reported in the per-turn output.

Important: a card cast on turn N is "castable" on turn N. Don't mark cards already cast in earlier turns as castable on later turns — their castability is set already and doesn't need to be rechecked in later turns. However, if they haven't been actually played (as discussed in Step 3), keep them as options to play in later turns (e.g. draw spell may have been playable turn 2 but mana was used for a mana dork and now draw spell is castable turn 3)

## Land-drop policy (Step 3 of turn structure)

Apply rules in strict priority order. Each rule is a filter; if it leaves multiple candidates, fall through to the next rule. Final tiebreaker: hand index (lowest first).

**Rule L1 - play what maximizes castability next turn**
For each land that could be played, consider which spells all are castable next turn (without considering the card drawn). From all lands, play the land that makes the higher number of spells castable next turn. If there is a tie, but it is two different spells, prioritize the one that allows to cast a better spell (creature>removal>other spells, within that higher mana cost>lower mana cost)

**Rule L2 — tapped before untapped.**
Give preference to lands that ETB tapped . Sac-for-tapped lands like Evolving Wilds that search up tapped lands count as "tapped" for this rule. The reason is that by playing tapped lands first, you then can play more spells in later turns. (It is counterintuitive to count a one drop as castable because you could have played a plains and then actually play a tapped land, but I think it correctly reflects the option value).

**Rule L3 — Play lands that produce colors that are not yet available.**
Give preference to lands that can produce colors of mana that are not yet producable on the battlefield. E.g. if a plains is already played, give preference to forest over second plains.


**Rule L4 — Prefer lands with duplicates in hand (tiebreaker).**
Among remaining candidates, prefer a land where another copy of the same name is in hand. This is the "Swamp, Swamp, Island → play Swamp first" rule.

**Rule L5 - Prefer lands of main color.**
If there is still a tie, give preference to the land that produces mana of the color of which there are more cards in the deck.

**Final tiebreaker**: hand index, lowest first.

If no land in hand is playable (e.g., empty hand or only non-land cards), skip the land drop.

## Spell-casting policy (Step 4 of turn structure)

Only cast utility spells. Loop until no action improves the state:

**Priority S1 — Mana sources and battlefield-fetch effects.**
Cast spells/abilities that put lands into play or produce mana for future turns. Examples: Cultivate (lands to play), mana dorks, Three Visits, Skyclave Pick-Axe-style mana production.

Within S1, prefer effects that put lands into play *untapped* (Three Visits) over tapped (Cultivate) over hand (Environmental Scientist), because the untapped land helps this turn's later castings, the tapped helps next turn, and the hand land also helps next turn but uses a land drop.

**Priority S2 — Hand-fetch effects (only if no land in hand).**
If hand is empty of lands, cast spells that fetch lands to hand (Environmental Scientist, Strixhaven Skycoach, landcycling). Otherwise these are lower priority — we already have a land drop available.

**Priority S3 — Card draw, cycling, scry.**
Cast/activate effects that find more cards. Useful for digging toward lands or playable spells. Cycling counts as card draw.
For decisions on scry, draw-discard, etc. maximize (1) hitting a land drop every turn and (2) the amount of cards that become playable next turn, i.e. if no land in hand, make sure to try and scry a land on top. If there is a land in hand, but no card is castable next turn, try to scry an additional castable spell on top, etc.

**Priority S4 - Hand-fetch effects (if lands in hand).**
If mana is still available, cast hand-fetch effects to find additional lands.

Make sure to account for how much mana is available. E.g. with 2 mana, only 1 spell for 2-mana can be played even if 2 are castable. 

**Everything else: do not cast.** Mark as castable in the snapshot, but don't actually cast. This includes creatures (other than mana dorks), removal, combat tricks, etc.


## Logging

`verbose=True` flag. When set, log per turn:
- Cards drawn this turn
- Castability snapshot (which cards are castable, with which land choices)
- Land-drop decision (which land, which rule decided it, what alternatives were considered)
- Spell-casting decisions (which spells cast, in what order, why)
- Resulting battlefield state and hand at end of turn

Format: structured (dict or dataclass) so it can be pretty-printed but also programmatically inspected in tests.

## Implementation order

Build incrementally with checkpoints. Each step produces a runnable artifact that can be hand-traced for correctness.

### Step 1: Card and game state model
- Card dataclass with modes, costs, effects.
- Decklist parser (start with a simple format: one card name per line, with a separate JSON/YAML file defining each card's mechanical properties).
- GameState dataclass: library, hand, battlefield (lands, creatures, other), graveyard, mana pool, turn number, land-drop-used.
- Primitives: `draw(n)`, `play_land(card)`, `cast(card, mode, mana_payment)`, `tap(permanent)`, `untap_all()`.
- No policy yet. Manually drive a game to verify state transitions.

### Step 2: `is_castable` and mana solver
- Build the CSP solver for cost satisfaction given available mana abilities.
- Handle basic mana, dual mana, filter lands, summoning sickness.
- Test against hand-traced examples (use the hands from the design conversation).

### Step 3: Castability snapshot
- For each card in hand, evaluate `castable_this_turn` by trying each possible land drop.
- Output structure for per-turn results.

### Step 4: Land-drop policy
- Implement L1 through L4 in order.
- Test on synthetic hands designed to exercise each rule.

### Step 5: Spell-casting policy
- Implement S1 through S3.
- Test that the policy correctly prioritizes mana acceleration over card draw, etc.

### Step 6: Monte Carlo wrapper
- Run N games, aggregate per-card statistics.
- Output: per-card P(in hand by T), P(castable by T), distribution of first-castable-turn.

### Step 7: Logging
- Add verbose logging behind a flag.
- Useful for debugging and for explaining results.

### Step 8 (optional): Mulligans, on-the-draw parameter, etc.

## Testing strategy

Generate random hands from the card pool. Have the deterministic code analyze the hand. Then analyze the hand using LLM (no API call, just load the hand into context) and see if the deterministic code is correct. If not, fix the code. 
Start with simple hands but then go to more complicated ones that include mana dorks, non-basic lands, card draw, etc.

## Decisions locked in (during v1 implementation)

1. **No decklist parser in this package.** The public entrypoint
   `simulate(hand, library, ...)` takes lists of `ParsedCard` directly.
   A text-decklist convenience can land in the `cards` package or the
   website later; mulligan-time consumers (overlay / website) already
   have card names from their own input pipelines.
2. **Hard error on incomplete card data.** `simulate` calls
   `check_deck_encodings` first; any card with `status` of `NEEDS_LLM`
   or `NEEDS_HUMAN`, or with a non-None `mana_cost` and empty `modes`,
   raises `DeckEncodingError` listing every offender. Treating an
   unencoded card as "vanilla" would corrupt downstream training data.
3. **L1 ("castable next turn") considers lands only.** No simulation of
   mana-producing spells we'd cast this turn. Decoupled from S1–S4.
4. **Per-mode + per-card aggregate output.** First-castable-turn is
   tracked separately for each `Mode` of kind `cast` / `cycle` /
   `land_cycle` / `channel`, AND rolled up to a per-card "any mode
   castable" value. Both reach `AggregateStats`.
5. **X costs treated as X = 1.** Most X-spells are useless at X = 0;
   requiring one extra generic mana on top of the printed cost gives
   a more realistic earliest-castable turn.
6. **Logging is JSON-serializable.** All trace types are Pydantic
   models; `pretty_print` renders a transcript on demand.
7. **Random seed control.** `simulate(..., seed=N)` is reproducible
   bit-for-bit.

## Things explicitly out of scope

- Combat
- Opponent / interaction
- Sideboarding
- Lessons / wishboards
- Stack interactions, priority, instants cast on opponent's turn
- Cards with conditional mana production beyond the simple cases listed (e.g. mana that can only be used to play instant/sorceries, etc.)
- Card-specific edge cases that require modeling: Sneak Attack, Cascade, Suspend, etc. (Add as needed.)
- Optimal-play decisions beyond the policy rules. The simulator measures the deck's curve under a reasonable policy, not its skill ceiling.

## Validation

Before trusting Monte Carlo results, validate by:
1. Running  test cases  and confirming expected first-castable-turn.
2. Sampling individual game logs (verbose mode) and hand-checking 5–10 of them.
3. Comparing aggregate statistics on a known deck (e.g., a mono-color deck with all 1-drops should have ~100% turn-1 castability) against intuition.
