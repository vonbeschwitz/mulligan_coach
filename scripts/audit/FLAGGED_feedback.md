# Classification audit — flagged cards

*human comments are in cursive*

Audited every card in `data/processed/parsed_cards/{TMT,ECL,TLA}.json`
(840 cards total) against its oracle text. Cards below are ones where
the stored `role_features` disagrees with what a careful re-read
suggests.

## How to read this file

Per-card entries use this shape:

```
## <set> #<cn> <name>
- oracle: ...
- current: <list of role flags currently set>
- suggested: <list of role flags I think should be set>
- severity: wrong | debatable | minor
- reason: <one-line explanation>
```

Severity legend:
- **wrong**: stored flag clearly wrong (missing or extra) and likely to
  harm modeling.
- **debatable**: borderline call (e.g. partial-removal aura, situational
  bounce, variable-amount draw); worth a look but not a clear bug.
- **minor**: technical mismatch unlikely to materially affect the model
  (e.g. a missing keyword on a token body).

## Themes — recurring patterns to fix in one place

These are not bugs in individual entries; they're systematic issues
across many entries. Fixing the parser / encoder once eliminates many
flags at once.

1. **Earthbend populates creates_creatures incorrectly.** The earthbend
   mechanic transforms a LAND into a creature in place — it does not
   create a token. ~15 TLA cards (Badgermole #166, Earthbender
   Ascension #175, Earthbending Lesson #176, Dai Li Indoctrination
   #93, Fatal Fissure #97, Toph #198/#247, Ba Sing Se #266, etc.) end
   up with a phantom `creates_creatures: [N/N]` body with no colors or
   subtypes. Action: stop the earthbend pattern from writing
   `creates_creatures`.
*I disagree. We want earthbend to be coded as creating creatures. It isn't exact, but similar enough to creating a creature. No change!*


2. **Auras encoded as `removal_destroy_or_exile` instead of
   `is_removal_aura`.** Dimensional Exile (TMT #4) is the clearest
   case: an Aura that exiles a creature on ETB was tagged with the
   spell-side flag instead of the aura-side flag. Per design "exactly
   one of is_removal_aura / is_pump_aura is true on Auras."
*Dimensional Exile is a land aura, not a creature aura, but it removes a creature, so I think the current coding is right. No change!*


3. **`combat_trick_*` set on non-instants.** Schema says combat_trick
   is "instants only," but it's currently set on creatures with
   activated abilities (High-Flying Ace TMT #9, Stratosoarer ECL #72,
   Surly Farrier ECL #196, Soulbright Seeker ECL #157, Bre of Clan
   Stoutarm ECL #207) and on sorceries (Karai's Technique TMT #152,
   Impolite Entrance ECL #146, Goatnap ECL #142, Brigid's Command
   ECL #208, Assert Perfection ECL #164). Either widen the schema or
   stop writing these on non-instants.
*I agree that these should only be combat tricks if the effect can be played from hand at instant speed, so creatures that have ETB pump effects can count as combat trick and Karai's Technique because of the sneak ability. The other creatures with activated ability or ETB and no flash should not count as pump spells*

4. **Loot (draw N then discard N) populates `cards_drawn` instead of
   `cards_manipulated`.** Schema says cards_drawn is net new cards,
   and lists loot under cards_manipulated. Affected: Silvergill
   Peddler ECL #70, Cool but Rude TMT #89, Null Group TMT #98,
   Yuyan Archers TLA #161, Gran-Gran TLA #54, Teo TLA #74, Tweeze
   ECL #162, Gristle Glutton ECL #144, Ashling TMT-bonus ECL #124,
   Flaring Cinder ECL #225, Professor Zei TLA #238, Sokka TLA #240.
*I agree theseshould be marked as cards_manipulated not cards_drawn. We should also make sure they are correctly treated in the simulations (i.e. they actually draw 1 card and discard 1 card in simulation)*


5. **Surveil-on-ETB not captured.** Surveil belongs in
   `cards_manipulated` per schema, but several ETB-surveil creatures
   were left at just `is_creature`: Shore Lurker ECL #34, Twilight
   Diviner ECL #122, Lys Alana Informant ECL #181, Foraging Wickermaw
   ECL #256, Dream Beavers TMT #62, Nobody TMT #161, Rumble Arena TLA
   #277. Similarly TMT bonus Undercity Sewers (surveil 1 on ETB).
*I agree, these need to be added and need to also be reflected in simulation. For simulation they should be treated the same way as scry*

6. **Look-at-top-N-and-take cards stuck at `is_creature`/`is_other`.**
   These manipulate ~3 cards and may draw 1; signal is lost. Casey
   Jones TMT #87, Midnight Tilling ECL #182, the entire Eclipsed cycle
   ECL #217-#221, Master Piandao TLA #28, Water Tribe Rallier TLA #42,
   Aang Crossroads TLA #203, Guru Pathik TLA #223.
*We need to make sure these are properly treated in the simulation, at least for the finding land part. Ideally they would be fully properly reflected"


7. **Duplicate `creates_creatures` entries for the same body.** Per
   design "One entry per distinct token". Found on Uneasy Alliance
   TMT #28, Turtle Blimp TMT #180, Clachan Festival ECL #10, Sapling
   Nursery ECL #192, Catharsis ECL #209, Stalactite Dagger ECL #261,
   Fire Nation Attacks TLA #133, Crescent Island Temple TLA #129,
   Kyoshi Battle Fan TLA #257, Dai Li Agents TLA #214 (with earthbend
   already wrong). Looks like the parser counts per-trigger / per-mention
   instead of deduping.
   *I think here we should change the rule to record all tokens created. So if 3 1/1 cats are created, they should all be recorded*

8. **Token bodies missing keywords from the oracle.** Several token
   creations describe a keyword ("with flying", "with reach", "with
   prowess", "with changeling", "with firebending 1") that isn't
   stored: Lord Dregg TMT #65 (flying), Bitterblossom ECL bonus
   (flying), Sapling Nursery / Tend the Sprigs ECL #192/#197 (reach),
   Crescent Island Temple TLA #129 (prowess), Fire Navy Trebuchet TLA
   #100 (flying), Glen Elendra's Answer ECL #52 (flying), Fire Nation
   Attacks / Firebender Ascension TLA #133/#137 (firebending 1),
   Stalactite Dagger ECL #261 / Personify ECL #28 (changeling).
   *I agree these would be good to add but priority relatively low*

9. **Variable-X token bodies dropped entirely.** When a card creates
   "X tokens" with a known body, the body still belongs in
   `creates_creatures`. Missed on: Sally Pride TMT #24, Triceraton
   Commander TMT #25, Plague of Vermin TMT bonus, Kithkeeper ECL #23,
   United Front TLA #39.
*for simplicity we should assume X=1 everywhere*


10. **Auras missing `aura_pump_granted_keywords`.** Evershrike's Gift
    ECL #15 (flying), Lofty Dreams ECL #58 (flying), Gilt-Leaf's
    Embrace ECL #177 (indestructible).
*I agree these would be good to add but priority relatively low*


11. **Gross vs net for "draw N then discard M" spells.** Brainstorm
    TMT bonus (`cards_drawn=3` should be net 1), Thirst for Identity
    ECL #79 (cards_drawn=3 should be net 1-2), Unexpected Assistance
    ECL #80 (cards_manipulated=3 should be cards_drawn=2),
    Waterbending Lesson TLA #80 (cards_drawn=3 should be net 2-3),
    Abandon Attachments TLA #205 (cards_drawn=2 should be net 1).
*I agree these should be fixed*


12. **Saga chapter-I mass removal not surfaced.** Per design we encode
    chapter I; mass-creature-destroy chapter Is at TMT #154 (Last
    Ronin) and TLA #117 (Rise of Sozin) lose their removal signal
    behind `is_saga`. TLA #27 Yangchen is similar (chapter I exiles
    opp permanents).
*I agree these need to be coded as removal. In fact introducing mass_removal as its own role would be good*


13. **Mana rocks not flagged.** A few non-creature artifacts whose
    primary value is mana don't get `is_mana_rock` because of an
    unusual cost or condition: Springleaf Drum ECL #260 (tap-a-creature
    cost), White Lotus Tile TLA #262 (variable-X), The Great Henge TLA
    bonus, Meteorite TLA bonus (has it, but as a tier-1 mana rock).
*That is OK. Hard to determine in game if requirement is fulfilled. No change!*    

14. **ETB-destroy / ETB-exile not propagating to removal flags.**
    Permanents whose ETB removes a creature should fire
    `removal_destroy_or_exile`: Anchovy & Banana Pizza TMT #57,
    Armaggon TMT #58, Koya TMT #11, Liminal Hold ECL #24, Disruptor of
    Currents ECL #47, Sunderflock ECL #74, Rimekin Recluse ECL #66,
    Glen Elendra's Answer ECL #52 (counterspell missed too), Bespoke
    Bō TMT #31, Metalhead TMT #44, Invasion Submersible TLA #57,
    Wanderwine Farewell ECL #83, Fire Nation Drill TLA #98, Koh
    TLA #107, Noxious Gearhulk TLA bonus, Cityscape Leveler TLA bonus,
    Meteor Sword TLA #258, Spicy Oatmeal Pizza TMT #109.
*I agree these should be changed to reflect this*


## Severity counts (152 flags across 840 cards reviewed)

- **wrong**: 98
- **debatable**: 40
- **minor**: 14

Many of the **wrong** entries cluster into the 14 themes above.
Fixing a theme at the parser/encoder level is much higher-leverage
than per-card patches.

---

# TMT

## TMT #4 Dimensional Exile
- oracle: Enchant basic land you control. When this Aura enters, exile target creature an opponent controls until this Aura leaves the battlefield.
- current: removal_destroy_or_exile
- suggested: is_removal_aura (and possibly removal_destroy_or_exile)
- severity: wrong
- reason: Aura — per design "exactly one of is_removal_aura / is_pump_aura is true on Auras". The encoding skipped the aura branch.

## TMT #8 Hamato Guardian Stance
- oracle: Target creature gets +1/+3 and gains flying until end of turn. Scry 1.
- current: combat_trick: +1/+3 grants ['flying']
- suggested: combat_trick: +1/+3 grants ['flying'], cards_manipulated=1
- severity: wrong
- reason: Scry 1 in the oracle text not captured as cards_manipulated.

## TMT #9 High-Flying Ace
- oracle: Flying. {3}{W}: Target creature without flying gains flying until end of turn. Activate only as a sorcery.
- current: is_creature, combat_trick: grants ['flying']
- suggested: is_creature (no combat_trick fields)
- severity: wrong
- reason: combat_trick_* fields are defined as "instants only" per schema; this is a creature with an activated ability.

## TMT #11 Koya, Death from Above
- oracle: Flying. When Koya enters, exile up to one other target creature. At the beginning of the next end step, you may pay {3}{B}. If you don't, return that card to the battlefield under its owner's control.
- current: is_creature
- suggested: is_creature, removal_destroy_or_exile
- severity: wrong
- reason: ETB exiles a creature (Banishing-Light style — opponent only gets it back if you don't pay). #26 Turncoat Kunoichi has the same effect and IS flagged with removal_destroy_or_exile; this is inconsistent.

## TMT #19 Lita, Little Orphan Amphibian
- oracle: Alliance — Whenever another creature you control enters, choose one … • Create a Food token. (It's an artifact …) • Scry 1.
- current: is_creature, cards_manipulated=1, creates_creatures: 1/1  Food
- suggested: is_creature, cards_manipulated=1 (no creates_creatures)
- severity: wrong
- reason: Food is an artifact token, not a creature token. creates_creatures should be empty.

## TMT #24 Sally Pride, Lioness Leader
- oracle: When Sally Pride enters, create X 2/2 red Mutant creature tokens, where X is the number of nontoken creatures you control. …
- current: is_creature
- suggested: is_creature, creates_creatures: [2/2 R Mutant]
- severity: wrong
- reason: Creates creature tokens with a known body; variable count, but the body should still be listed.

## TMT #25 Triceraton Commander
- oracle: Flying. Whenever this creature attacks, Dinosaurs you control … gain flying until end of turn. When this creature enters, create X 2/2 white Dinosaur Soldier creature tokens.
- current: is_creature
- suggested: is_creature, creates_creatures: [2/2 W Dinosaur/Soldier]
- severity: wrong
- reason: Variable-X token creation; body is known and should be encoded.

## TMT #28 Uneasy Alliance
- oracle: Enchanted creature can't attack or block. {5}, Sacrifice this Aura: Exile enchanted creature. You create a 1/1 black Ninja creature token.
- current: is_removal_aura, creates_creatures: [1/1 B Ninja, 1/1 B Ninja]
- suggested: is_removal_aura, creates_creatures: [1/1 B Ninja]
- severity: wrong
- reason: Duplicated token body — the sac activation creates one 1/1 B Ninja, not two.

## TMT #30 April, Reporter of the Weird
- oracle: Whenever April deals combat damage to a player, draw that many cards, then discard a card.
- current: is_creature
- suggested: is_creature, cards_manipulated=1 (or cards_drawn=N)
- severity: debatable
- reason: Combat-damage looting (variable N draws then discard). Conditional on connect, but the loot signal is missed.

## TMT #31 Bespoke Bō
- oracle: When this Equipment enters, return up to one other target nonland permanent to its owner's hand. Equipped creature gets +2/+1 and has vigilance.
- current: is_equipment
- suggested: is_equipment, is_bounce
- severity: wrong
- reason: ETB bounces a nonland permanent — clearly is_bounce.

## TMT #44 Metalhead
- oracle: When Metalhead enters, return up to one other target artifact or creature to its owner's hand. {R}, Sacrifice another artifact: …
- current: is_creature
- suggested: is_creature, is_bounce
- severity: wrong
- reason: ETB bounces an artifact or creature — is_bounce should fire.

## TMT #50 Renet, Temporal Apprentice
- oracle: Flash. When Renet enters, return each other nonland permanent that entered this turn to its owner's hand.
- current: is_creature
- suggested: is_creature, is_bounce
- severity: debatable
- reason: Mass bounce on ETB. "Returns target … to its owner's hand" — it's not strictly targeted, but it does bounce permanents.

## TMT #52 Return to the Sewers
- oracle: Target creature's owner puts it on their choice of the top or bottom of their library. …
- current: is_other
- suggested: is_top_library (or is_bounce-ish)
- severity: debatable
- reason: Tuck-or-deeper — owner picks top or bottom. Worth deciding whether owner-choice "top or bottom" counts as is_top_library.

## TMT #53 Sewer-veillance Cam
- oracle: Flash. When this artifact enters or leaves, you may tap or untap target creature. {3}{U}, Sacrifice this artifact: Draw two cards.
- current: is_other
- suggested: is_other, cards_drawn=2 (or this stays is_other if activated draws don't count)
- severity: debatable
- reason: Activated ability draws 2 cards. Whether activated card draw should populate cards_drawn is a design question (cf. Buzz Bots dies-trigger draw=1 which IS flagged).

## TMT #57 Anchovy & Banana Pizza
- oracle: When this artifact enters, destroy target creature. {2}, {T}, Sacrifice this artifact: You gain 3 life.
- current: is_other
- suggested: removal_destroy_or_exile
- severity: wrong
- reason: ETB destroys target creature — should be flagged as creature removal.

## TMT #58 Armaggon, Future Shark
- oracle: Flash. When Armaggon enters, destroy up to three target creatures.
- current: is_creature
- suggested: is_creature, removal_destroy_or_exile
- severity: wrong
- reason: ETB destroys up to three creatures — clear creature removal.

## TMT #62 Dream Beavers
- oracle: Flying. When this creature enters, each opponent loses 1 life and you gain 1 life. Scry 1.
- current: is_creature
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: ETB scry 1 not captured.

## TMT #65 Lord Dregg, Insect Invader
- oracle: …Disappear — At the beginning of your end step, …create a 1/1 black Insect Warrior creature token with flying. {3}{G}, Sacrifice a token: Draw a card.
- current: is_creature, cards_drawn=1, creates_creatures: 1/1 B Insect/Warrior
- suggested: is_creature, cards_drawn=1, creates_creatures: 1/1 B Insect/Warrior with keywords=['flying']
- severity: minor
- reason: Token body missing the 'flying' keyword.

## TMT #80 Splinter's Technique
- oracle: Sneak {1}{B}. Search your library for a card, put that card into your hand, then shuffle.
- current: is_other
- suggested: is_other, cards_drawn=1
- severity: debatable
- reason: Unconditional tutor that adds 1 card to hand. Whether tutoring counts toward cards_drawn is a design call.

## TMT #82 Stomped by the Foot
- oracle: Kicker—Sacrifice an artifact or creature. Target creature gets -2/-2 until end of turn. If this spell was kicked, that creature gets -5/-5 until end of turn instead.
- current: removal_destroy_or_exile
- suggested: depends on design — either removal_destroy_or_exile (kills small creatures) or combat_trick (-N/-N debuff)
- severity: debatable
- reason: -2/-2 is creature debuff, not destroy/exile. Whether to call it removal is a judgment call.

## TMT #87 Casey Jones, Jury-Rig Justiciar
- oracle: Haste. When Casey Jones enters, look at the top four cards of your library. You may reveal an artifact card from among them and put it into your hand. Put the rest on the bottom in random order.
- current: is_creature
- suggested: is_creature, cards_manipulated=3 (or similar)
- severity: wrong
- reason: ETB looks at top 4, may take one card. Should at least have cards_manipulated populated (look-at-top pattern).

## TMT #89 Cool but Rude
- oracle: (Class) Whenever you attack, you may discard a card. If you do, draw a card.
- current: is_class, cards_drawn=1
- suggested: is_class, cards_manipulated=1
- severity: debatable
- reason: Conditional loot trigger — net cards = 0 (discard 1, draw 1). Per schema cards_drawn is net; loot belongs in cards_manipulated.

## TMT #90 General Traag, Heart of Stone
- oracle: Trample. When General Traag enters, you may sacrifice another artifact. When you do, General Traag deals 4 damage to target creature.
- current: is_creature
- suggested: is_creature, removal_burn_damage=4 (conditional)
- severity: debatable
- reason: ETB conditional burn (sac an artifact → 4 to creature). The conditional makes this borderline.

## TMT #94 Manhole Missile
- oracle: Manhole Missile deals 3 damage to target creature. You may put a card from your hand on the bottom of your library. If you do, draw a card.
- current: removal_burn_damage=3
- suggested: removal_burn_damage=3, cards_manipulated=1
- severity: minor
- reason: Optional rummage rider — net cards = 0 but you do filter; minor.

## TMT #96 Mouser Foundry
- oracle: When this artifact enters or leaves, create a 1/1 colorless Robot artifact creature token. {4}{R}, Sacrifice this artifact: It deals 3 damage to target creature.
- current: creates_creatures: 1/1 Robot
- suggested: creates_creatures: 1/1 Robot, removal_burn_damage=3
- severity: debatable
- reason: Activated ability deals 3 damage to creature (sac-burn). Whether activated burn populates removal_burn_damage is a design question (cf. #182 Weather Maker similar pattern).

## TMT #98 Null Group Biological Assets
- oracle: During your turn, this creature has first strike. Whenever this creature attacks, you may discard a card. If you do, draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Attack-trigger loot — net 0 cards, belongs in cards_manipulated by the schema's definition.

## TMT #109 Spicy Oatmeal Pizza
- oracle: When this artifact enters, it deals 4 damage to any target and 3 damage to you. {2}, {T}, Sacrifice this artifact: You gain 3 life.
- current: is_other
- suggested: removal_burn_damage=4
- severity: wrong
- reason: ETB deals 4 damage to "any target" — includes creatures, so this is burn-style creature removal.

## TMT #122 Michelangelo's Technique
- oracle: …Look at the top eight cards of your library. Put up to two creature cards with total mana value 6 or less from among them onto the battlefield and the rest on the bottom in random order.
- current: is_other
- suggested: is_other, cards_manipulated=8 (or similar)
- severity: minor
- reason: Sees 8 cards. Not adding to hand (creatures go to battlefield), but cards_manipulated could capture the see-and-bottom signal.

## TMT #132 Saved by the Shell
- oracle: …Put a +1/+1 counter on target creature you control. It gains trample, hexproof, and indestructible until end of turn.
- current: is_other
- suggested: combat_trick_granted_keywords=['trample','hexproof','indestructible'] (and persistent +1/+1 counter)
- severity: debatable
- reason: Instant with keyword grant — fits combat_trick pattern. The +1/+1 counter is permanent, so combat_trick_power/toughness aren't quite right.

## TMT #133 Tenderize
- oracle: Target creature you control deals damage equal to its power to target creature an opponent controls.
- current: is_other
- suggested: is_punch_fight
- severity: wrong
- reason: This is a textbook punch (one-sided fight). Schema explicitly groups punch + fight under is_punch_fight.

## TMT #152 Karai's Technique
- oracle: (Sorcery) Sneak {W}{B}. Choose one or both — Target creature gets +3/+3 until end of turn. Target creature gets -3/-3 until end of turn.
- current: combat_trick: +3/+3
- suggested: is_other (or new category for sorcery debuff)
- severity: wrong
- reason: combat_trick_* is "instants only" per schema; this is a Sorcery. Also the -3/-3 mode is creature debuff that isn't captured.
*this should be marked as combat_trick and removal*


## TMT #154 The Last Ronin (Saga)
- oracle: I — Destroy all creatures. II — … III — …
- current: is_saga
- suggested: is_saga, removal_destroy_or_exile
- severity: debatable
- reason: Chapter I is mass destroy creatures — material at mulligan time. is_saga alone hides the sweeper signal.

## TMT #161 Nobody
- oracle: When this creature enters, return up to one other target artifact you control to its owner's hand. Scry 1.
- current: is_creature
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: ETB scry 1 not captured.

## TMT #170 Tainted Treats
- oracle: Destroy target artifact or creature. If its mana value was 4 or less, create a Food token.
- current: is_other
- suggested: removal_destroy_or_exile
- severity: wrong
- reason: Destroys creature (or artifact) — per design the artifact-OR-creature variant should still count as creature removal since the creature branch fires.

## TMT #180 Turtle Blimp
- oracle: Flying. When this Vehicle enters, create a 2/2 red Mutant creature token. Crew 2.
- current: is_vehicle, creates_creatures: [2/2 R Mutant, 2/2 R Mutant]
- suggested: is_vehicle, creates_creatures: [2/2 R Mutant]
- severity: wrong
- reason: Duplicated token body — ETB creates one 2/2 Mutant.

## TMT #182 Weather Maker
- oracle: Landfall … {T}: Add one mana of any color. {T}, Remove two charge counters: Add {C}{C}. {T}, Remove three charge counters: It deals 3 damage to any target.
- current: is_mana_rock
- suggested: is_mana_rock, removal_burn_damage=3
- severity: debatable
- reason: Activated burn ability ({T} + 3 charge counters: 3 damage to any target). Whether activated burn fires removal_burn_damage is a design question.

## TMT #bonus-dsc-113 Brainstorm
- oracle: Draw three cards, then put two cards from your hand on top of your library in any order.
- current: cards_drawn=3
- suggested: cards_drawn=1, cards_manipulated=2 (or net interpretation)
- severity: wrong
- reason: Brainstorm is famously net +1 card (draw 3, put 2 back). The schema says cards_drawn = net new cards; current value is gross.

## TMT #bonus-mkm-270 Undercity Sewers
- oracle: ({T}: Add {U} or {B}.) This land enters tapped. When this land enters, surveil 1.
- current: is_land
- suggested: is_land, cards_manipulated=1
- severity: wrong
- reason: Surveil 1 on ETB; schema lists surveil under cards_manipulated.

## TMT #bonus-shm-73 Plague of Vermin
- oracle: Starting with you, each player may pay any amount of life. … Each player creates a 1/1 black Rat creature token for each 1 life they paid this way.
- current: is_other
- suggested: creates_creatures: [1/1 B Rat]
- severity: debatable
- reason: Variable-X token creation; body is known. Consistent treatment with #24 / #25 above.

---

# ECL

## ECL #10 Clachan Festival
- oracle: When this enchantment enters, create two 1/1 green and white Kithkin creature tokens. {4}{W}: Create a 1/1 green and white Kithkin creature token.
- current: creates_creatures: [1/1 GW Kithkin, 1/1 GW Kithkin]
- suggested: creates_creatures: [1/1 GW Kithkin]
- severity: wrong
- reason: Duplicated body — per design "one entry per distinct token".

## ECL #15 Evershrike's Gift
- oracle: Enchanted creature gets +1/+0 and has flying.
- current: is_pump_aura, aura_pump: +1/+0
- suggested: is_pump_aura, aura_pump: +1/+0 grants ['flying']
- severity: wrong
- reason: aura_pump_granted_keywords missing 'flying'.

## ECL #23 Kithkeeper
- oracle: Vivid — When this creature enters, create X 1/1 green and white Kithkin creature tokens …
- current: is_creature
- suggested: is_creature, creates_creatures: [1/1 GW Kithkin]
- severity: wrong
- reason: Variable-X token creation; body is known. Consistent with TMT #24 / #25.

## ECL #24 Liminal Hold
- oracle: When this enchantment enters, exile up to one target nonland permanent an opponent controls until this enchantment leaves the battlefield. You gain 2 life.
- current: is_other
- suggested: removal_destroy_or_exile
- severity: wrong
- reason: Banishing-Light-style exile of any nonland permanent (includes creature).

## ECL #27 Morningtide's Light
- oracle: Exile any number of target creatures. At the beginning of the next end step, return those cards to the battlefield tapped under their owners' control.
- current: is_other
- suggested: removal_destroy_or_exile (debatable — temporary)
- severity: debatable
- reason: Temporary exile (returns tapped). Functions as Pollyanna creature removal when you target opponent's creatures.

## ECL #28 Personify
- oracle: Exile target creature you control, then return that card to the battlefield under its owner's control. Create a 1/1 colorless Shapeshifter creature token with changeling.
- current: removal_destroy_or_exile, creates_creatures: [1/1 Shapeshifter]
- suggested: creates_creatures: [1/1 Shapeshifter with changeling]
- severity: wrong
- reason: Only exiles a creature you CONTROL — it's blink, not removal. removal_destroy_or_exile fires wrongly. Token also missing 'changeling' keyword.

## ECL #34 Shore Lurker
- oracle: Flying. When this creature enters, surveil 1.
- current: is_creature
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: ETB surveil 1 not captured.

## ECL #47 Disruptor of Currents
- oracle: Flash. Convoke. When this creature enters, return up to one other target nonland permanent to its owner's hand.
- current: is_creature
- suggested: is_creature, is_bounce
- severity: wrong
- reason: ETB bounces nonland permanent.

## ECL #52 Glen Elendra's Answer
- oracle: This spell can't be countered. Counter all spells your opponents control and all abilities your opponents control. Create a 1/1 blue and black Faerie creature token with flying for each spell and ability countered.
- current: creates_creatures: [1/1 UB Faerie]
- suggested: is_counterspell, creates_creatures: [1/1 UB Faerie with flying]
- severity: wrong
- reason: Counters all opp spells/abilities — is_counterspell clearly applies. Token also missing 'flying' keyword.

## ECL #58 Lofty Dreams
- oracle: …Enchant creature. When this Aura enters, draw a card. Enchanted creature gets +2/+2 and has flying.
- current: is_pump_aura, cards_drawn=1, aura_pump: +2/+2
- suggested: is_pump_aura, cards_drawn=1, aura_pump: +2/+2 grants ['flying']
- severity: wrong
- reason: aura_pump_granted_keywords missing 'flying'.

## ECL #66 Rimekin Recluse
- oracle: When this creature enters, return up to one other target creature to its owner's hand.
- current: is_creature
- suggested: is_creature, is_bounce
- severity: wrong
- reason: ETB bounces a creature (any side).

## ECL #68 Shinestriker
- oracle: Flying. Vivid — When this creature enters, draw cards equal to the number of colors among permanents you control.
- current: is_creature
- suggested: is_creature, cards_drawn=N (variable)
- severity: debatable
- reason: ETB draws cards (variable). Other variable-amount cards are inconsistent — some flagged, some not.

## ECL #70 Silvergill Peddler
- oracle: Whenever this creature becomes tapped, draw a card, then discard a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: Loot trigger (draw then discard) — net 0 cards; belongs in cards_manipulated.

## ECL #72 Stratosoarer
- oracle: Flying. When this creature enters, target creature gains flying until end of turn.
- current: is_creature, combat_trick: grants ['flying']
- suggested: is_creature (no combat_trick fields)
- severity: wrong
- reason: combat_trick is "instants only" per schema; this is a creature with an ETB trigger.

## ECL #74 Sunderflock
- oracle: …When this creature enters, if you cast it, return all non-Elemental creatures to their owners' hands.
- current: is_creature
- suggested: is_creature, is_bounce
- severity: wrong
- reason: ETB mass-bounces creatures (own and opponent's non-Elementals).

## ECL #75 Swat Away
- oracle: The owner of target spell or creature puts it on their choice of the top or bottom of their library.
- current: is_top_library
- suggested: is_top_library, is_counterspell
- severity: debatable
- reason: When targeting a spell, this counters it (and tucks). is_counterspell should also fire.

## ECL #79 Thirst for Identity
- oracle: Draw three cards. Then discard two cards unless you discard a creature card.
- current: cards_drawn=3
- suggested: cards_drawn=1 (or 2 conditional), cards_manipulated=2
- severity: wrong
- reason: Net cards = 1 (draw 3, discard 2). Same gross-vs-net issue as Brainstorm.

## ECL #80 Unexpected Assistance
- oracle: Draw three cards, then discard a card.
- current: cards_manipulated=3
- suggested: cards_drawn=2 (or cards_manipulated=3 + cards_drawn=2)
- severity: wrong
- reason: Net cards = 2 (draw 3, discard 1). cards_drawn should be populated for the net gain.

## ECL #83 Wanderwine Farewell
- oracle: Return one or two target nonland permanents to their owners' hands. Then if you control a Merfolk, create a 1/1 white and blue Merfolk creature token for each permanent returned.
- current: creates_creatures: [1/1 WU Merfolk]
- suggested: creates_creatures: [1/1 WU Merfolk], is_bounce
- severity: wrong
- reason: This card bounces 1-2 nonland permanents — is_bounce is missing.

## ECL #97 Darkness Descends
- oracle: Put two -1/-1 counters on each creature.
- current: is_other
- suggested: removal_destroy_or_exile
- severity: debatable
- reason: Mass -2/-2 sweeper — kills small/medium creatures. Same family as TMT #82 Stomped which is flagged as removal.

## ECL #113 Nameless Inversion
- oracle: Target creature gets +3/-3 and loses all creature types until end of turn.
- current: removal_destroy_or_exile, combat_trick: +3/+-3
- suggested: removal_destroy_or_exile, combat_trick_power=3, combat_trick_toughness=-3
- severity: minor
- reason: Display format "+3/+-3" suggests stored toughness=-3 but rendered awkwardly. Worth verifying the JSON stores -3 correctly.

## ECL #119 Scarblade's Malice
- oracle: Target creature you control gains deathtouch and lifelink until end of turn. When that creature dies this turn, create a 2/2 black and green Elf creature token.
- current: creates_creatures: [2/2 BG Elf]
- suggested: combat_trick_granted_keywords=['deathtouch','lifelink'], creates_creatures: [2/2 BG Elf]
- severity: wrong
- reason: Instant granting deathtouch + lifelink to target — clear combat trick.

## ECL #122 Twilight Diviner
- oracle: When this creature enters, surveil 2. …
- current: is_creature
- suggested: is_creature, cards_manipulated=2
- severity: wrong
- reason: ETB surveil 2 not captured.

## ECL #124 Ashling, Rekindled // Ashling, Rimebound
- oracle: Whenever this creature enters or transforms into Ashling, Rekindled, you may discard a card. If you do, draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Conditional loot — net 0 cards. cards_manipulated=1 is more accurate per schema.

## ECL #142 Goatnap
- oracle: Gain control of target creature until end of turn. Untap that creature. It gains haste until end of turn. If that creature is a Goat, it also gets +3/+0 until end of turn.
- current: combat_trick: +3/+0 grants ['haste']
- suggested: is_other (or threaten-specific flag)
- severity: wrong
- reason: This is a threaten / Act of Treason effect (gain control of opp's creature) — not a combat trick on your own. Also Sorcery type — combat_trick is "instants only".

## ECL #144 Gristle Glutton
- oracle: {T}, Blight 1: Discard a card. If you do, draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Activated loot — net 0 cards. Consistent with the loot pattern flag.

## ECL #146 Impolite Entrance
- oracle: (Sorcery) Target creature gains trample and haste until end of turn. Draw a card.
- current: cards_drawn=1, combat_trick: grants ['trample','haste']
- suggested: cards_drawn=1 (drop combat_trick)
- severity: wrong
- reason: combat_trick is "instants only"; this is a Sorcery.

## ECL #157 Soulbright Seeker
- oracle: {R}: Target creature you control gains trample until end of turn. …
- current: is_creature, combat_trick: grants ['trample']
- suggested: is_creature (no combat_trick fields)
- severity: wrong
- reason: Creature with activated ability — combat_trick is instants only.

## ECL #162 Tweeze
- oracle: Tweeze deals 3 damage to any target. You may discard a card. If you do, draw a card.
- current: removal_burn_damage=3, cards_drawn=1
- suggested: removal_burn_damage=3, cards_manipulated=1
- severity: debatable
- reason: Loot rider — net 0 cards; cards_manipulated more accurate than cards_drawn.

## ECL #164 Assert Perfection
- oracle: (Sorcery) Target creature you control gets +1/+0 until end of turn. It deals damage equal to its power to up to one target creature an opponent controls.
- current: combat_trick: +1/+0
- suggested: is_punch_fight (drop combat_trick — sorcery)
- severity: wrong
- reason: This is a punch (one-sided fight) wrapped with a pump rider. is_punch_fight missing; combat_trick on a sorcery violates schema.

## ECL #177 Gilt-Leaf's Embrace
- oracle: …enchanted creature gains trample and indestructible until end of turn. Enchanted creature gets +2/+0.
- current: is_pump_aura, aura_pump: +2/+0 grants ['trample']
- suggested: is_pump_aura, aura_pump: +2/+0 grants ['trample','indestructible']
- severity: minor
- reason: 'indestructible' is granted on ETB-eot but missing from aura_pump_granted_keywords.

## ECL #181 Lys Alana Informant
- oracle: When this creature enters or dies, surveil 1.
- current: is_creature
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: ETB surveil 1 not captured.

## ECL #182 Midnight Tilling
- oracle: Mill four cards, then you may return a permanent card from among them to your hand.
- current: is_other
- suggested: is_other (or cards_drawn=1, cards_manipulated=4)
- severity: debatable
- reason: Mill-and-take-one is the canonical LookAtTopEffect pattern; CLAUDE.md names this card specifically. role_features stays at is_other while the simulator-side captures it.

## ECL #187 Pitiless Fists
- oracle: …Enchanted creature fights up to one target creature an opponent controls. Enchanted creature gets +2/+2.
- current: is_pump_aura, aura_pump: +2/+2
- suggested: is_pump_aura, aura_pump: +2/+2, is_punch_fight
- severity: debatable
- reason: Aura's ETB fights opp's creature — punch/fight signal lost.

## ECL #192 Sapling Nursery
- oracle: Landfall — Whenever a land you control enters, create a 3/4 green Treefolk creature token with reach.
- current: creates_creatures: [3/4 G Treefolk, 3/4 G Treefolk]
- suggested: creates_creatures: [3/4 G Treefolk with reach]
- severity: wrong
- reason: Duplicate body entry AND missing 'reach' keyword on token.

## ECL #196 Surly Farrier
- oracle: {T}: Target creature you control gets +1/+1 and gains vigilance until end of turn. Activate only as a sorcery.
- current: is_creature, combat_trick: +1/+1 grants ['vigilance']
- suggested: is_creature (no combat_trick fields)
- severity: wrong
- reason: combat_trick on a creature with activated ability — instants only per schema.

## ECL #197 Tend the Sprigs
- oracle: …create a 3/4 green Treefolk creature token with reach.
- current: creates_creatures: [3/4 G Treefolk]
- suggested: creates_creatures: [3/4 G Treefolk with reach]
- severity: minor
- reason: Missing 'reach' keyword on token body.

## ECL #198 Thoughtweft Charge
- oracle: Target creature gets +3/+3 until end of turn. If a creature entered the battlefield under your control this turn, draw a card.
- current: combat_trick: +3/+3
- suggested: combat_trick: +3/+3, cards_drawn=1 (conditional)
- severity: debatable
- reason: Conditional draw rider not captured.

## ECL #207 Bre of Clan Stoutarm
- oracle: {1}{W}, {T}: Another target creature you control gains flying and lifelink until end of turn. …
- current: is_creature, combat_trick: grants ['flying','lifelink']
- suggested: is_creature (no combat_trick fields)
- severity: wrong
- reason: combat_trick on a creature with activated ability — instants only.

## ECL #208 Brigid's Command
- oracle: (Kindred Sorcery) Choose two — • create copy of Kithkin … • opp creates token • target creature gets +3/+3 EOT • target creature fights opp's creature
- current: is_punch_fight, combat_trick: +3/+3, creates_creatures: [1/1 GW Kithkin]
- suggested: is_punch_fight, creates_creatures: [1/1 GW Kithkin] (drop combat_trick — sorcery)
- severity: wrong
- reason: combat_trick on a sorcery violates schema; is_punch_fight stays.

## ECL #209 Catharsis
- oracle: When this creature enters, if {W}{W} was spent to cast it, create two 1/1 green and white Kithkin creature tokens. …
- current: is_creature, creates_creatures: [1/1 GW Kithkin, 1/1 GW Kithkin]
- suggested: is_creature, creates_creatures: [1/1 GW Kithkin]
- severity: wrong
- reason: Duplicate body entry; the ETB makes two of the same token, but per design one entry per distinct body.

## ECL #217 Eclipsed Boggart
- oracle: When this creature enters, look at the top four cards of your library. You may reveal a Goblin, Swamp, or Mountain card from among them and put it into your hand. Put the rest on the bottom in random order.
- current: is_creature
- suggested: is_creature, cards_manipulated=3 (or similar)
- severity: wrong
- reason: ETB look-at-top-4-and-take signal lost. Same pattern as TMT #87 Casey Jones, ECL #218 / #219 / #220 / #221.

## ECL #218 Eclipsed Elf
- oracle: (Same look-at-top-4 pattern as #217)
- current: is_creature
- suggested: is_creature, cards_manipulated=3
- severity: wrong
- reason: Same as #217.

## ECL #219 Eclipsed Flamekin
- oracle: (Same look-at-top-4 pattern as #217)
- current: is_creature
- suggested: is_creature, cards_manipulated=3
- severity: wrong
- reason: Same as #217.

## ECL #220 Eclipsed Kithkin
- oracle: (Same look-at-top-4 pattern as #217)
- current: is_creature
- suggested: is_creature, cards_manipulated=3
- severity: wrong
- reason: Same as #217.

## ECL #221 Eclipsed Merrow
- oracle: (Same look-at-top-4 pattern as #217)
- current: is_creature
- suggested: is_creature, cards_manipulated=3
- severity: wrong
- reason: Same as #217.

## ECL #225 Flaring Cinder
- oracle: When this creature enters and whenever you cast a spell with mana value 4 or greater, you may discard a card. If you do, draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Loot trigger — net 0 cards.

## ECL #256 Foraging Wickermaw
- oracle: When this creature enters, surveil 1. {1}: Add one mana of any color. …
- current: is_creature
- suggested: is_creature, cards_manipulated=1
- severity: wrong
- reason: ETB surveil 1 not captured.

## ECL #260 Springleaf Drum
- oracle: {T}, Tap an untapped creature you control: Add one mana of any color.
- current: is_other
- suggested: is_mana_rock
- severity: wrong
- reason: A non-creature, non-equipment, non-vehicle artifact whose primary purpose is mana production — textbook is_mana_rock. The unusual cost (tap a creature) likely caused the parser to skip the mana ability.

## ECL #261 Stalactite Dagger
- oracle: When this Equipment enters, create a 1/1 colorless Shapeshifter creature token with changeling. …
- current: is_equipment, creates_creatures: [1/1 Shapeshifter, 1/1 Shapeshifter]
- suggested: is_equipment, creates_creatures: [1/1 Shapeshifter with changeling]
- severity: wrong
- reason: Duplicate body entry AND missing 'changeling' keyword on token.

## ECL #bonus-2x2-69 Bitterblossom
- oracle: At the beginning of your upkeep, you lose 1 life and create a 1/1 black Faerie Rogue creature token with flying.
- current: creates_creatures: [1/1 B Faerie/Rogue]
- suggested: creates_creatures: [1/1 B Faerie/Rogue with flying]
- severity: minor
- reason: Missing 'flying' keyword on token body.

---

# TLA

## TLA #3 Zuko's Exile
- oracle: Exile target artifact, creature, or enchantment. …
- current: is_other
- suggested: removal_destroy_or_exile
- severity: wrong
- reason: Targets creature (also artifact/enchantment) — per design when creature is targetable, removal_destroy_or_exile fires.

## TLA #18 Enter the Avatar State
- oracle: …gains flying, first strike, lifelink, and hexproof.
- current: combat_trick: grants ['flying', 'first strike', 'lifelink']
- suggested: combat_trick: grants ['flying','first strike','lifelink','hexproof']
- severity: wrong
- reason: 'hexproof' missing from granted keywords.

## TLA #20 Gather the White Lotus
- oracle: Create a 1/1 white Ally creature token for each Plains you control. Scry 2.
- current: creates_creatures: [1/1 W Ally]
- suggested: creates_creatures: [1/1 W Ally], cards_manipulated=2
- severity: wrong
- reason: Scry 2 not captured as cards_manipulated.

## TLA #27 The Legend of Yangchen (Saga)
- oracle: I — Starting with you, each player chooses up to one permanent with mana value 3 or greater from among permanents your opponents control. Exile those permanents.
- current: is_saga
- suggested: is_saga, removal_destroy_or_exile
- severity: debatable
- reason: Chapter I mass-exiles opponent permanents (creatures); per design we encode chapter I.

## TLA #28 Master Piandao
- oracle: Whenever Master Piandao attacks, look at the top four cards … may reveal an Ally, Equipment, or Lesson card …
- current: is_creature
- suggested: is_creature, cards_manipulated=3 (or similar)
- severity: minor
- reason: Look-at-top-4-and-take signal (on attack) lost.

## TLA #34 Sandbenders' Storm
- oracle: Choose one — Destroy target creature with power 4 or greater. — Earthbend 3.
- current: removal_destroy_or_exile, creates_creatures: [3/3]
- suggested: removal_destroy_or_exile (drop creates_creatures)
- severity: wrong
- reason: Earthbend does NOT create a creature token — it converts a land into a creature in place. The 3/3 body in creates_creatures is semantically wrong.

## TLA #39 United Front
- oracle: Create X 1/1 white Ally creature tokens, then put a +1/+1 counter on each creature you control.
- current: is_other
- suggested: creates_creatures: [1/1 W Ally]
- severity: wrong
- reason: Variable-X token creation with known body — same pattern as Sally Pride / Triceraton Commander.

## TLA #42 Water Tribe Rallier
- oracle: Waterbend {5}: Look at the top four cards … may reveal a creature card with power 3 or less … put it into your hand. …
- current: is_creature
- suggested: is_creature, cards_manipulated=3 (or similar)
- severity: minor
- reason: Activated look-at-top-4-and-take signal lost.

## TLA #54 Gran-Gran
- oracle: Whenever Gran-Gran becomes tapped, draw a card, then discard a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Loot trigger — net 0 cards; belongs in cards_manipulated per schema.

## TLA #57 Invasion Submersible
- oracle: When this Vehicle enters, return up to one other target nonland permanent to its owner's hand.
- current: is_vehicle
- suggested: is_vehicle, is_bounce
- severity: wrong
- reason: ETB bounces nonland permanent.

## TLA #59 Katara, Bending Prodigy
- oracle: Waterbend {6}: Draw a card.
- current: is_creature
- suggested: is_creature, cards_drawn=1
- severity: debatable
- reason: Activated card draw — same pattern as Loch Mare (ECL #57) which IS flagged. Inconsistent.

## TLA #61 The Legend of Kuruk (Saga)
- oracle: I, II — Scry 2, then draw a card.
- current: is_saga
- suggested: is_saga, cards_drawn=1, cards_manipulated=2
- severity: debatable
- reason: Chapter I/II draws + scries. is_saga alone hides the card-draw / manipulation signal.

## TLA #62 Lost Days
- oracle: The owner of target creature or enchantment puts it into their library second from the top or on the bottom.
- current: is_other
- suggested: is_top_library (when targeting creature)
- severity: debatable
- reason: Tuck-or-deeper of creature/enchantment. Same shape as ECL #75 Swat Away / #78 Temporal Cleansing.

## TLA #74 Teo, Spirited Glider
- oracle: Whenever one or more creatures you control with flying attack, draw a card, then discard a card. …
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Loot trigger — net 0 cards.

## TLA #80 Waterbending Lesson
- oracle: Draw three cards. Then discard a card unless you waterbend {2}.
- current: cards_drawn=3
- suggested: cards_drawn=2 (and cards_manipulated=3) — net of discard
- severity: wrong
- reason: Net cards is 2 (or 3 if waterbend). Same gross-vs-net pattern as Brainstorm.

## TLA #93 Dai Li Indoctrination
- oracle: Choose one — Target opponent reveals their hand … you choose … — Earthbend 2.
- current: creates_creatures: [2/2]
- suggested: is_other (drop creates_creatures)
- severity: wrong
- reason: Earthbend doesn't create a token. The 2/2 body in creates_creatures is incorrect.

## TLA #97 Fatal Fissure
- oracle: Choose target creature. When that creature dies this turn, you earthbend 4.
- current: creates_creatures: [4/4]
- suggested: removal_destroy_or_exile (or is_other) — drop creates_creatures
- severity: wrong
- reason: Earthbend doesn't create a token. Also this is conditional creature removal (kills if creature dies this turn — like Surge to Victory's pattern).

## TLA #98 The Fire Nation Drill
- oracle: When The Fire Nation Drill enters, you may tap it. When you do, destroy target creature with power 4 or less.
- current: is_vehicle
- suggested: is_vehicle, removal_destroy_or_exile
- severity: wrong
- reason: Conditional ETB destroy creature.

## TLA #100 Fire Navy Trebuchet
- oracle: …create a 2/1 colorless Construct artifact creature token with flying named Ballistic Boulder that's tapped and attacking. Sacrifice that token at the beginning of the next end step.
- current: is_creature, creates_creatures: [2/1 Construct]
- suggested: is_creature, creates_creatures: [2/1 Construct with flying]
- severity: minor
- reason: Token body missing 'flying' keyword.

## TLA #107 Koh, the Face Stealer
- oracle: When Koh enters, exile up to one other target creature. …
- current: is_creature
- suggested: is_creature, removal_destroy_or_exile
- severity: wrong
- reason: ETB exiles a creature.

## TLA #117 The Rise of Sozin (Saga)
- oracle: I — Destroy all creatures. II — … III — …
- current: is_saga
- suggested: is_saga, removal_destroy_or_exile
- severity: debatable
- reason: Chapter I = mass creature destroy sweeper. Same as TMT #154.

## TLA #128 Combustion Technique
- oracle: Combustion Technique deals damage equal to 2 plus the number of Lesson cards in your graveyard to target creature.
- current: is_other
- suggested: removal_burn_damage=2 (variable approximation)
- severity: debatable
- reason: Variable burn to creature — comparable to Soul Immolation, which is also is_other. Inconsistent if other fixed-burn cards flag.

## TLA #129 Crescent Island Temple
- oracle: …create a 1/1 red Monk creature token with prowess.
- current: creates_creatures: [1/1 R Monk, 1/1 R Monk]
- suggested: creates_creatures: [1/1 R Monk with prowess]
- severity: wrong
- reason: Duplicate body entry AND missing 'prowess' keyword on token.

## TLA #133 Fire Nation Attacks
- oracle: Create two 2/2 red Soldier creature tokens with firebending 1.
- current: creates_creatures: [2/2 R Soldier, 2/2 R Soldier]
- suggested: creates_creatures: [2/2 R Soldier with firebending 1]
- severity: wrong
- reason: Duplicate body entry; token also missing 'firebending 1' keyword (set-specific).

## TLA #137 Firebender Ascension
- oracle: When this enchantment enters, create a 2/2 red Soldier creature token with firebending 1.
- current: creates_creatures: [2/2 R Soldier]
- suggested: creates_creatures: [2/2 R Soldier with firebending 1]
- severity: minor
- reason: Token missing 'firebending 1' keyword.

## TLA #161 Yuyan Archers
- oracle: When this creature enters, you may discard a card. If you do, draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Conditional loot — net 0 cards.

## TLA #166 Badgermole
- oracle: When this creature enters, earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Earthbend doesn't create a creature token.

## TLA #167 Badgermole Cub
- oracle: When this creature enters, earthbend 1.
- current: is_creature, creates_creatures: [1/1]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #173 Earth Kingdom General
- oracle: When this creature enters, earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #174 Earth Rumble
- oracle: Earthbend 2. When you do, up to one target creature you control fights target creature an opponent controls.
- current: creates_creatures: [2/2]
- suggested: is_punch_fight (drop creates_creatures)
- severity: wrong
- reason: Earthbend doesn't create. Also fight effect — is_punch_fight missing.

## TLA #175 Earthbender Ascension
- oracle: When this enchantment enters, earthbend 2. Then search your library for a basic land card …
- current: creates_creatures: [2/2]
- suggested: is_other (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #176 Earthbending Lesson
- oracle: Earthbend 4.
- current: creates_creatures: [4/4]
- suggested: is_other (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #177 Earthen Ally
- oracle: {2}{W}{U}{B}{R}{G}: Earthbend 5.
- current: is_creature, creates_creatures: [5/5]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #182 Haru, Hidden Talent
- oracle: Whenever another Ally you control enters, earthbend 1.
- current: is_creature, creates_creatures: [1/1]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #191 Rebellious Captives
- oracle: Exhaust — {6}: Put two +1/+1 counters on this creature, then earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #193 Rocky Rebuke
- oracle: Target creature you control deals damage equal to its power to target creature an opponent controls.
- current: is_other
- suggested: is_punch_fight
- severity: wrong
- reason: Textbook punch — missing is_punch_fight.

## TLA #198 Toph, the Blind Bandit
- oracle: When Toph enters, earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #203 Aang, at the Crossroads
- oracle: When Aang enters, look at the top five cards … may put a creature card with mana value 4 or less from among them onto the battlefield.
- current: is_creature
- suggested: is_creature, cards_manipulated=4 (or similar)
- severity: minor
- reason: Look-at-top-5 signal lost.

## TLA #205 Abandon Attachments
- oracle: You may discard a card. If you do, draw two cards.
- current: cards_drawn=2
- suggested: cards_drawn=1 (net)
- severity: debatable
- reason: Net cards = 1 (draw 2, discard 1). Gross vs net.

## TLA #210 Bitter Work
- oracle: …Exhaust — {4}: Earthbend 4.
- current: cards_drawn=1, creates_creatures: [4/4]
- suggested: cards_drawn=1 (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #211 Bumi, Unleashed
- oracle: When Bumi enters, earthbend 4.
- current: is_creature, creates_creatures: [4/4]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #214 Dai Li Agents
- oracle: When this creature enters, earthbend 1, then earthbend 1.
- current: is_creature, creates_creatures: [1/1, 1/1]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue (and duplicate entries).

## TLA #219 Earth Village Ruffians
- oracle: When this creature dies, earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #223 Guru Pathik
- oracle: When Guru Pathik enters, look at the top five cards …
- current: is_creature
- suggested: is_creature, cards_manipulated=4 (or similar)
- severity: minor
- reason: Look-at-top-5 signal lost.

## TLA #238 Professor Zei, Anthropologist
- oracle: {T}, Discard a card: Draw a card.
- current: is_creature, cards_drawn=1
- suggested: is_creature, cards_manipulated=1
- severity: debatable
- reason: Activated loot — net 0 cards.

## TLA #240 Sokka, Bold Boomeranger
- oracle: When Sokka enters, discard up to two cards, then draw that many cards.
- current: is_creature
- suggested: is_creature, cards_manipulated=2
- severity: wrong
- reason: ETB loot up to 2 — loot signal lost.

## TLA #246 Toph, Hardheaded Teacher
- oracle: Whenever you cast a spell, earthbend 1. …
- current: is_creature, creates_creatures: [1/1]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #247 Toph, the First Metalbender
- oracle: At the beginning of your end step, earthbend 2.
- current: is_creature, creates_creatures: [2/2]
- suggested: is_creature (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue.

## TLA #257 Kyoshi Battle Fan
- oracle: When this Equipment enters, create a 1/1 white Ally creature token, then attach this Equipment to it.
- current: is_equipment, creates_creatures: [1/1 W Ally, 1/1 W Ally]
- suggested: is_equipment, creates_creatures: [1/1 W Ally]
- severity: wrong
- reason: Duplicate body entry — the ETB creates one Ally.

## TLA #258 Meteor Sword
- oracle: When this Equipment enters, destroy target permanent. …
- current: is_equipment
- suggested: is_equipment, removal_destroy_or_exile
- severity: wrong
- reason: ETB destroys target permanent (includes creature).

## TLA #262 White Lotus Tile
- oracle: This artifact enters tapped. {T}: Add X mana of any one color, where X is the greatest number of creatures you control that have a creature type in common.
- current: is_other
- suggested: is_mana_rock
- severity: debatable
- reason: Non-creature artifact with mana ability — schema-eligible for is_mana_rock. Conditional X may have blocked deterministic detection.

## TLA #266 Ba Sing Se
- oracle: {2}{G}, {T}: Earthbend 2.
- current: is_land, creates_creatures: [2/2]
- suggested: is_land (drop creates_creatures)
- severity: wrong
- reason: Same earthbend issue — activated ability on a land doesn't create a token.

## TLA #277 Rumble Arena
- oracle: When this land enters, scry 1.
- current: is_land
- suggested: is_land, cards_manipulated=1
- severity: wrong
- reason: ETB scry 1 not captured.

## TLA #bonus-bro-233 Cityscape Leveler
- oracle: When you cast this spell and whenever this creature attacks, destroy up to one target nonland permanent. …
- current: is_creature
- suggested: is_creature, removal_destroy_or_exile
- severity: wrong
- reason: Cast-trigger + attack-trigger destroys nonland permanent (includes creature). Status is needs_llm — partial role_features expected — still worth flagging.

## TLA #bonus-cmm-294 The Great Henge
- oracle: …{T}: Add {G}{G}. You gain 2 life. …
- current: cards_drawn=1
- suggested: cards_drawn=1, is_mana_rock
- severity: wrong
- reason: Non-creature, non-equipment, non-vehicle artifact with mana ability — schema-eligible for is_mana_rock.

## TLA #bonus-dmu-235 Meteorite
- oracle: When this artifact enters, it deals 2 damage to any target.
- current: is_mana_rock
- suggested: is_mana_rock, removal_burn_damage=2
- severity: debatable
- reason: ETB deals 2 to any target (includes creature). Activated/static burn signal on mana rocks is inconsistently flagged.

## TLA #bonus-dtk-150 Rending Volley
- oracle: Rending Volley deals 4 damage to target white or blue creature.
- current: is_other
- suggested: removal_burn_damage=4
- severity: wrong
- reason: Color-restricted creature burn. Status is needs_llm — flag for review.

## TLA #bonus-ecc-71 Black Sun's Zenith
- oracle: Put X -1/-1 counters on each creature.
- current: is_other
- suggested: removal_destroy_or_exile
- severity: debatable
- reason: Mass debuff sweeper — same family as ECL #97 Darkness Descends.

## TLA #bonus-otc-170 Humble Defector
- oracle: {T}: Draw two cards. Target opponent gains control of this creature.
- current: is_creature
- suggested: is_creature, cards_drawn=2
- severity: minor
- reason: Activated draw 2 — inconsistent with other activated-draw creatures (Loch Mare).

## TLA #bonus-soc-238 Blasphemous Act
- oracle: Blasphemous Act deals 13 damage to each creature.
- current: is_other
- suggested: removal_burn_damage=13
- severity: debatable
- reason: Mass burn sweeper. Status is needs_llm — flag for review.

## TLA #bonus-tdc-191 Noxious Gearhulk
- oracle: When this creature enters, you may destroy another target creature. …
- current: is_creature
- suggested: is_creature, removal_destroy_or_exile
- severity: wrong
- reason: ETB destroys a creature.
