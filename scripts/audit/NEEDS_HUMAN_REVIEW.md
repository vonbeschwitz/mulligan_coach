# Cards still needing a judgment call from the project owner

These cards were flagged in `FLAGGED_feedback.md` as `debatable` *and* the systematic fixes in `apply_flagged_fixes.py` didn't take a position. For each entry below: the current encoding is shown, plus what the audit thought might be worth changing. Decide whether to:

1. Accept current — no action.
2. Add a fix — extend `apply_flagged_fixes.py` with a new PER_CARD_FIXES entry.
3. Defer — re-flag in `FLAGGED_feedback.md` with the rationale.

Most of these are activated-burn / activated-draw, conditional triggers, or shape mismatches where the schema doesn't have a clean home for what the card does.

---

## TMT #4 Dimensional Exile
- type: Enchantment — Aura
- cost: {1}{W}
- oracle: Enchant basic land you control
When this Aura enters, exile target creature an opponent controls until this Aura leaves the battlefield.
- current role_features: removal_destroy_or_exile=True
- status: llm_encoded
- audit note: Dimensional Exile — owner: keep current encoding (land aura that removes a creature; existing flag is right).

## ECL #260 Springleaf Drum
- type: Artifact
- cost: {1}
- oracle: {T}, Tap an untapped creature you control: Add one mana of any color.
- current role_features: is_other=True
- status: auto
- audit note: Springleaf Drum — owner: mana rock with conditional cost (tap a creature) — too situational to encode.

## TLA #262 White Lotus Tile
- type: Artifact
- cost: {4}
- oracle: This artifact enters tapped.
{T}: Add X mana of any one color, where X is the greatest number of creatures you control that have a creature type in common.
- current role_features: is_other=True
- status: auto
- audit note: White Lotus Tile — owner: mana rock with conditional cost (creature-type X) — too situational to encode.

## TMT #30 April, Reporter of the Weird
- type: Legendary Creature — Human Detective
- cost: {2}{U}
- oracle: Whenever April deals combat damage to a player, draw that many cards, then discard a card.
- current role_features: is_creature=True
- status: auto
- audit note: April, Reporter of the Weird — owner: loot only on combat damage; too conditional to encode as cards_manipulated.

## TMT #53 Sewer-veillance Cam
- type: Artifact
- cost: {U}
- oracle: Flash
When this artifact enters or leaves the battlefield, you may tap or untap target creature.
{3}{U}, Sacrifice this artifact: Draw two cards.
- current role_features: is_other=True
- status: auto
- audit note: Sewer-veillance Cam — owner: activated draw on sac, kept as-is for simplicity.

## TMT #80 Splinter's Technique
- type: Sorcery
- cost: {3}{B}
- oracle: Sneak {1}{B} (You may cast this spell for {1}{B} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Search your library for a card, put that card into your hand, then shuffle.
- current role_features: is_other=True
- status: auto
- audit note: Splinter's Technique — owner: tutor adds 1 card, kept as-is for simplicity.

## TMT #82 Stomped by the Foot
- type: Instant
- cost: {1}{B}
- oracle: Kicker—Sacrifice an artifact or creature. (You may sacrifice an artifact or creature in addition to any other costs as you cast this spell.)
Target creature gets -2/-2 until end of turn. If this spell was kicked, that creature gets -5/-5 until end of turn instead.
- current role_features: removal_destroy_or_exile=True
- status: llm_encoded
- audit note: Stomped by the Foot — owner: kept as-is, the existing removal flag is right.

## TMT #90 General Traag, Heart of Stone
- type: Legendary Artifact Creature — Elemental Soldier
- cost: {3}{R}{R}
- oracle: Trample
When General Traag enters, you may sacrifice another artifact. When you do, General Traag deals 4 damage to target creature.
- current role_features: is_creature=True
- status: auto
- audit note: General Traag, Heart of Stone — owner: conditional removal (sac an artifact to deal 4) too situational to encode.

## TMT #96 Mouser Foundry
- type: Artifact
- cost: {1}{R}
- oracle: When this artifact enters or leaves the battlefield, create a 1/1 colorless Robot artifact creature token.
{4}{R}, Sacrifice this artifact: It deals 3 damage to target creature.
- current role_features: creates_creatures=[{'power': '1', 'toughness': '1', 'colors': [], 'subtypes': ['Robot'], 'keywords': []}]
- status: auto
- audit note: Mouser Foundry — owner: don't encode sac-effects that cost mana.

## TMT #182 Weather Maker
- type: Artifact
- cost: {3}
- oracle: Landfall — Whenever a land you control enters, put a charge counter on this artifact.
{T}: Add one mana of any color.
{T}, Remove two charge counters from this artifact: Add {C}{C}.
{T}, Remove three charge counters from this artifact: It deals 3 damage to any target.
- current role_features: is_mana_rock=True
- status: auto
- audit note: Weather Maker — owner: keep only as mana rock; activated burn is too situational.

## TMT #bonus-shm-73 Plague of Vermin
- type: Sorcery
- cost: {6}{B}
- oracle: Starting with you, each player may pay any amount of life. Repeat this process until no one pays life. Each player creates a 1/1 black Rat creature token for each 1 life they paid this way.
- current role_features: is_other=True
- status: auto
- audit note: Plague of Vermin — owner: variable-X token w/ life-pay; kept as-is.

## ECL #27 Morningtide's Light
- type: Sorcery
- cost: {3}{W}
- oracle: Exile any number of target creatures. At the beginning of the next end step, return those cards to the battlefield tapped under their owners' control.
Until your next turn, prevent all damage that would be dealt to you.
Exile Morningtide's Light.
- current role_features: is_other=True
- status: auto
- audit note: Morningtide's Light — owner: temporary exile isn't removal (creatures return at end step).

## ECL #113 Nameless Inversion
- type: Kindred Instant — Shapeshifter
- cost: {1}{B}
- oracle: Changeling (This card is every creature type.)
Target creature gets +3/-3 and loses all creature types until end of turn.
- current role_features: removal_destroy_or_exile=True, combat_trick_power=3, combat_trick_toughness=-3
- status: llm_encoded
- audit note: Nameless Inversion — owner: confirmed both combat_trick + removal flags are correct.

## ECL #198 Thoughtweft Charge
- type: Instant
- cost: {1}{G}
- oracle: Target creature gets +3/+3 until end of turn. If a creature entered the battlefield under your control this turn, draw a card.
- current role_features: combat_trick_power=3, combat_trick_toughness=3
- status: auto
- audit note: Thoughtweft Charge — owner: don't encode the conditional card-draw rider.

