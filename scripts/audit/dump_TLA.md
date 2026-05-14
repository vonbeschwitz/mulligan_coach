# Classification audit dump — TLA (342 cards)

### #1 Aang's Journey  [common, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {2}
- oracle: Kicker {2} (You may pay an additional {2} as you cast this spell.)
Search your library for a basic land card. If this spell was kicked, instead search your library for a basic land card and a Shrine card. Reveal those cards, put them into your hand, then shuffle.
You gain 2 life.
- role_features: is_other

### #2 Energybending  [uncommon, status=auto]
- type: Instant — Lesson
- cost: {2}
- oracle: Lands you control gain all basic land types until end of turn.
Draw a card.
- role_features: cards_drawn=1

### #3 Zuko's Exile  [common, status=auto]
- type: Instant — Lesson
- cost: {5}
- oracle: Exile target artifact, creature, or enchantment. Its controller creates a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_other

### #4 Aang, the Last Airbender  [uncommon, status=auto]
- type: Legendary Creature — Human Avatar Ally
- cost: {3}{W}
- oracle: Flying
When Aang enters, airbend up to one other target nonland permanent. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
Whenever you cast a Lesson spell, Aang gains lifelink until end of turn.
- role_features: is_creature, is_bounce

### #5 Aang's Iceberg  [rare, status=llm_encoded]
- type: Enchantment
- cost: {2}{W}
- oracle: Flash
When this enchantment enters, exile up to one other target nonland permanent until this enchantment leaves the battlefield.
Waterbend {3}: Sacrifice this enchantment. If you do, scry 2. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: removal_destroy_or_exile, cards_manipulated=2

### #6 Airbender Ascension  [rare, status=auto]
- type: Enchantment
- cost: {1}{W}
- oracle: When this enchantment enters, airbend up to one target creature.
Whenever a creature you control enters, put a quest counter on this enchantment.
At the beginning of your end step, if this enchantment has four or more quest counters on it, exile up to one target creature you control, then return it to the battlefield under its owner's control.
- role_features: is_bounce

### #7 Airbender's Reversal  [uncommon, status=llm_encoded]
- type: Instant — Lesson
- cost: {1}{W}
- oracle: Choose one —
• Destroy target attacking creature.
• Airbend target creature you control. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
- role_features: removal_destroy_or_exile, is_bounce

### #8 Airbending Lesson  [common, status=auto]
- type: Instant — Lesson
- cost: {2}{W}
- oracle: Airbend target nonland permanent. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
Draw a card.
- role_features: is_bounce, cards_drawn=1

### #9 Appa, Loyal Sky Bison  [uncommon, status=auto]
- type: Legendary Creature — Bison Ally
- cost: {4}{W}{W}
- oracle: Flying
Whenever Appa enters or attacks, choose one —
• Target creature you control gains flying until end of turn.
• Airbend another target nonland permanent you control. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
- role_features: is_creature

### #10 Appa, Steadfast Guardian  [mythic, status=auto]
- type: Legendary Creature — Bison Ally
- cost: {2}{W}{W}
- oracle: Flash
Flying
When Appa enters, airbend any number of other target nonland permanents you control. (Exile them. While each one is exiled, its owner may cast it for {2} rather than its mana cost.)
Whenever you cast a spell from exile, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #11 Avatar Enthusiasts  [common, status=auto]
- type: Creature — Human Peasant Ally
- cost: {2}{W}
- oracle: Whenever another Ally you control enters, put a +1/+1 counter on this creature.
- role_features: is_creature

### #12 Avatar's Wrath  [rare, status=llm_encoded]
- type: Sorcery
- cost: {2}{W}{W}
- oracle: Choose up to one target creature, then airbend all other creatures. (Exile them. While each one is exiled, its owner may cast it for {2} rather than its mana cost.)
Until your next turn, your opponents can't cast spells from anywhere other than their hands.
Exile Avatar's Wrath.
- role_features: is_bounce

### #13 Compassionate Healer  [common, status=auto]
- type: Creature — Human Cleric Ally
- cost: {1}{W}
- oracle: Whenever this creature becomes tapped, you gain 1 life and scry 1. (Look at the top card of your library. You may put it on the bottom.)
- role_features: is_creature

### #14 Curious Farm Animals  [common, status=auto]
- type: Creature — Boar Elk Bird Ox
- cost: {W}
- oracle: When this creature dies, you gain 3 life.
{2}, Sacrifice this creature: Destroy up to one target artifact or enchantment.
- role_features: is_creature

### #15 Destined Confrontation  [uncommon, status=auto]
- type: Sorcery
- cost: {2}{W}{W}
- oracle: Each player chooses any number of creatures they control with total power 4 or less, then sacrifices all other creatures they control.
- role_features: is_other

### #16 Earth Kingdom Jailer  [uncommon, status=llm_encoded]
- type: Creature — Human Soldier Ally
- cost: {2}{W}
- oracle: When this creature enters, exile up to one target artifact, creature, or enchantment an opponent controls with mana value 3 or greater until this creature leaves the battlefield.
- role_features: is_creature, removal_destroy_or_exile

### #17 Earth Kingdom Protectors  [uncommon, status=llm_encoded]
- type: Creature — Human Soldier Ally
- cost: {W}
- oracle: Vigilance
Sacrifice this creature: Another target Ally you control gains indestructible until end of turn. (Damage and effects that say "destroy" don't destroy it.)
- role_features: is_creature

### #18 Enter the Avatar State  [uncommon, status=llm_encoded]
- type: Instant — Lesson
- cost: {W}
- oracle: Until end of turn, target creature you control becomes an Avatar in addition to its other types and gains flying, first strike, lifelink, and hexproof. (A creature with hexproof can't be the target of spells or abilities your opponents control.)
- role_features: combat_trick: grants ['flying', 'first strike', 'lifelink']

### #19 Fancy Footwork  [uncommon, status=auto]
- type: Instant — Lesson
- cost: {2}{W}
- oracle: Untap one or two target creatures. They each get +2/+2 until end of turn.
- role_features: combat_trick: +2/+2

### #20 Gather the White Lotus  [uncommon, status=auto]
- type: Sorcery
- cost: {4}{W}
- oracle: Create a 1/1 white Ally creature token for each Plains you control. Scry 2. (Look at the top two cards of your library, then put any number of them on the bottom and the rest on top in any order.)
- role_features: creates_creatures: 1/1 W Ally

### #21 Glider Kids  [common, status=auto]
- type: Creature — Human Pilot Ally
- cost: {2}{W}
- oracle: Flying
When this creature enters, scry 1. (Look at the top card of your library. You may put it on the bottom.)
- role_features: is_creature, cards_manipulated=1

### #22 Glider Staff  [uncommon, status=llm_encoded]
- type: Artifact — Equipment
- cost: {2}{W}
- oracle: When this Equipment enters, airbend up to one target creature. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
Equipped creature gets +1/+1 and has flying.
Equip {2}
- role_features: is_equipment, is_bounce

### #23 Hakoda, Selfless Commander  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {3}{W}
- oracle: Vigilance
You may look at the top card of your library any time.
You may cast Ally spells from the top of your library.
Sacrifice Hakoda: Creatures you control get +0/+5 and gain indestructible until end of turn.
- role_features: is_creature

### #24 Invasion Reinforcements  [uncommon, status=auto]
- type: Creature — Human Warrior Ally
- cost: {1}{W}
- oracle: Flash
When this creature enters, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #25 Jeong Jeong's Deserters  [common, status=llm_encoded]
- type: Creature — Human Rebel Ally
- cost: {1}{W}
- oracle: When this creature enters, put a +1/+1 counter on target creature.
- role_features: is_creature

### #26 Kyoshi Warriors  [common, status=auto]
- type: Creature — Human Warrior Ally
- cost: {3}{W}
- oracle: When this creature enters, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #27 The Legend of Yangchen // Avatar Yangchen  [mythic, status=auto]
- type: Enchantment — Saga // Legendary Creature — Avatar
- cost: {3}{W}{W}
- oracle: (As this Saga enters and after your draw step, add a lore counter.)
I — Starting with you, each player chooses up to one permanent with mana value 3 or greater from among permanents your opponents control. Exile those permanents.
II — You may have target opponent draw three cards. If you do, draw three cards.
III — Exile this Saga, then return it to the battlefield transformed under your control.
- role_features: is_saga

### #28 Master Piandao  [uncommon, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {4}{W}
- oracle: First strike
Whenever Master Piandao attacks, look at the top four cards of your library. You may reveal an Ally, Equipment, or Lesson card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.
- role_features: is_creature

### #29 Momo, Friendly Flier  [rare, status=llm_encoded]
- type: Legendary Creature — Lemur Bat Ally
- cost: {W}
- oracle: Flying
The first non-Lemur creature spell with flying you cast during each of your turns costs {1} less to cast.
Whenever another creature you control with flying enters, Momo gets +1/+1 until end of turn.
- role_features: is_creature

### #30 Momo, Playful Pet  [uncommon, status=llm_encoded]
- type: Legendary Creature — Lemur Bat Ally
- cost: {W}
- oracle: Flying, vigilance
When Momo leaves the battlefield, choose one —
• Create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
• Put a +1/+1 counter on target creature you control.
• Scry 2.
- role_features: is_creature

### #31 Path to Redemption  [common, status=llm_encoded]
- type: Enchantment — Aura
- cost: {1}{W}
- oracle: Enchant creature
Enchanted creature can't attack or block.
{5}, Sacrifice this Aura: Exile enchanted creature. Create a 1/1 white Ally creature token. Activate only during your turn.
- role_features: is_removal_aura, creates_creatures: 1/1 W Ally

### #32 Rabaroo Troop  [common, status=auto]
- type: Creature — Rabbit Kangaroo
- cost: {3}{W}{W}
- oracle: Landfall — Whenever a land you control enters, this creature gains flying until end of turn and you gain 1 life.
Plainscycling {2} ({2}, Discard this card: Search your library for a Plains card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #33 Razor Rings  [common, status=llm_encoded]
- type: Instant
- cost: {1}{W}
- oracle: Razor Rings deals 4 damage to target attacking or blocking creature. You gain life equal to the excess damage dealt this way.
- role_features: removal_burn_damage=4

### #34 Sandbenders' Storm  [common, status=llm_encoded]
- type: Instant
- cost: {3}{W}
- oracle: Choose one —
• Destroy target creature with power 4 or greater.
• Earthbend 3. (Target land you control becomes a 0/0 creature with haste that's still a land. Put three +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: removal_destroy_or_exile, creates_creatures: 3/3

### #35 South Pole Voyager  [rare, status=auto]
- type: Creature — Human Scout Ally
- cost: {1}{W}
- oracle: Whenever this creature or another Ally you control enters, you gain 1 life. If this is the second time this ability has resolved this turn, draw a card.
- role_features: is_creature, cards_drawn=1

### #36 Southern Air Temple  [uncommon, status=auto]
- type: Legendary Enchantment — Shrine
- cost: {3}{W}
- oracle: When Southern Air Temple enters, put X +1/+1 counters on each creature you control, where X is the number of Shrines you control.
Whenever another Shrine you control enters, put a +1/+1 counter on each creature you control.
- role_features: is_other

### #37 Suki, Courageous Rescuer  [rare, status=llm_encoded]
- type: Legendary Creature — Human Warrior Ally
- cost: {1}{W}{W}
- oracle: Other creatures you control get +1/+0.
Whenever another permanent you control leaves the battlefield during your turn, create a 1/1 white Ally creature token. This ability triggers only once each turn.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #38 Team Avatar  [uncommon, status=auto]
- type: Enchantment
- cost: {2}{W}
- oracle: Whenever a creature you control attacks alone, it gets +X/+X until end of turn, where X is the number of creatures you control.
{2}{W}, Discard this card: It deals damage equal to the number of creatures you control to target creature.
- role_features: is_other

### #39 United Front  [mythic, status=auto]
- type: Sorcery
- cost: {X}{W}{W}
- oracle: Create X 1/1 white Ally creature tokens, then put a +1/+1 counter on each creature you control.
- role_features: is_other

### #40 Vengeful Villagers  [uncommon, status=auto]
- type: Creature — Human Citizen
- cost: {3}{W}
- oracle: Whenever this creature attacks, choose target creature an opponent controls. Tap it, then you may sacrifice an artifact or creature. If you do, put a stun counter on the chosen creature. (If a permanent with a stun counter would become untapped, remove one from it instead.)
- role_features: is_creature

### #41 Water Tribe Captain  [common, status=llm_encoded]
- type: Creature — Human Soldier Ally
- cost: {2}{W}
- oracle: {5}: Creatures you control get +1/+1 until end of turn.
- role_features: is_creature

### #42 Water Tribe Rallier  [uncommon, status=llm_encoded]
- type: Creature — Human Soldier Ally
- cost: {1}{W}
- oracle: Waterbend {5}: Look at the top four cards of your library. You may reveal a creature card with power 3 or less from among them and put it into your hand. Put the rest on the bottom of your library in a random order. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #43 Yip Yip!  [common, status=auto]
- type: Instant — Lesson
- cost: {W}
- oracle: Target creature you control gets +2/+2 until end of turn. If that creature is an Ally, it also gains flying until end of turn.
- role_features: combat_trick: +2/+2 grants ['flying']

### #44 Accumulate Wisdom  [uncommon, status=llm_encoded]
- type: Instant — Lesson
- cost: {1}{U}
- oracle: Look at the top three cards of your library. Put one of those cards into your hand and the rest on the bottom of your library in any order. Put each of those cards into your hand instead if there are three or more Lesson cards in your graveyard.
- role_features: cards_drawn=1, cards_manipulated=2

### #45 Benevolent River Spirit  [uncommon, status=llm_encoded]
- type: Creature — Spirit
- cost: {U}{U}
- oracle: As an additional cost to cast this spell, waterbend {5}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
Flying, ward {2} (Whenever this creature becomes the target of a spell or ability an opponent controls, counter it unless that player pays {2}.)
When this creature enters, scry 2.
- role_features: is_creature, cards_manipulated=2

### #46 Boomerang Basics  [uncommon, status=auto]
- type: Sorcery — Lesson
- cost: {U}
- oracle: Return target nonland permanent to its owner's hand. If you controlled that permanent, draw a card.
- role_features: is_bounce

### #47 Crashing Wave  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {U}{U}
- oracle: As an additional cost to cast this spell, waterbend {X}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
Tap up to X target creatures, then distribute three stun counters among any number of tapped creatures your opponents control. (If a permanent with a stun counter would become untapped, remove one from it instead.)
- role_features: is_other

### #48 Ember Island Production  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {3}{U}{U}
- oracle: Choose one —
• Create a token that's a copy of target creature you control, except it's not legendary and it's a 4/4 Hero in addition to its other types.
• Create a token that's a copy of target creature an opponent controls, except it's not legendary and it's a 2/2 Coward in addition to its other types.
- role_features: is_other

### #49 First-Time Flyer  [common, status=auto]
- type: Creature — Human Pilot Ally
- cost: {1}{U}
- oracle: Flying
This creature gets +1/+1 as long as there's a Lesson card in your graveyard.
- role_features: is_creature

### #50 Flexible Waterbender  [common, status=auto]
- type: Creature — Human Warrior Ally
- cost: {3}{U}
- oracle: Vigilance
Waterbend {3}: This creature has base power and toughness 5/2 until end of turn. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #51 Forecasting Fortune Teller  [common, status=auto]
- type: Creature — Human Advisor Ally
- cost: {1}{U}
- oracle: When this creature enters, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #52 Geyser Leaper  [common, status=auto]
- type: Creature — Human Warrior Ally
- cost: {4}{U}
- oracle: Flying
Waterbend {4}: Draw a card, then discard a card. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature, cards_manipulated=1

### #53 Giant Koi  [common, status=auto]
- type: Creature — Fish
- cost: {4}{U}{U}
- oracle: Waterbend {3}: This creature can't be blocked this turn. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
Islandcycling {2} ({2}, Discard this card: Search your library for an Island card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #54 Gran-Gran  [uncommon, status=auto]
- type: Legendary Creature — Human Peasant Ally
- cost: {U}
- oracle: Whenever Gran-Gran becomes tapped, draw a card, then discard a card.
Noncreature spells you cast cost {1} less to cast as long as there are three or more Lesson cards in your graveyard.
- role_features: is_creature, cards_drawn=1

### #55 Honest Work  [uncommon, status=auto]
- type: Enchantment — Aura
- cost: {U}
- oracle: Enchant creature an opponent controls
When this Aura enters, tap enchanted creature and remove all counters from it.
Enchanted creature loses all abilities and is a Citizen with base power and toughness 1/1 and "{T}: Add {C}" named Humble Merchant. (It loses all other creature types and names.)
- role_features: is_removal_aura

### #56 Iguana Parrot  [common, status=auto]
- type: Creature — Lizard Bird Pirate
- cost: {2}{U}
- oracle: Flying, vigilance
Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
- role_features: is_creature

### #57 Invasion Submersible  [uncommon, status=auto]
- type: Artifact — Vehicle
- cost: {2}{U}
- oracle: When this Vehicle enters, return up to one other target nonland permanent to its owner's hand.
Exhaust — Waterbend {3}: This Vehicle becomes an artifact creature. Put three +1/+1 counters on it. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}. Activate each exhaust ability only once.)
- role_features: is_vehicle

### #58 It'll Quench Ya!  [common, status=auto]
- type: Instant — Lesson
- cost: {1}{U}
- oracle: Counter target spell unless its controller pays {2}.
- role_features: is_counterspell

### #59 Katara, Bending Prodigy  [uncommon, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {2}{U}
- oracle: At the beginning of your end step, if Katara is tapped, put a +1/+1 counter on her.
Waterbend {6}: Draw a card. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #60 Knowledge Seeker  [uncommon, status=auto]
- type: Creature — Fox Spirit
- cost: {1}{U}
- oracle: Vigilance
Whenever you draw your second card each turn, put a +1/+1 counter on this creature.
When this creature dies, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #61 The Legend of Kuruk // Avatar Kuruk  [mythic, status=auto]
- type: Enchantment — Saga // Legendary Creature — Avatar
- cost: {2}{U}{U}
- oracle: (As this Saga enters and after your draw step, add a lore counter.)
I, II — Scry 2, then draw a card.
III — Exile this Saga, then return it to the battlefield transformed under your control.
- role_features: is_saga

### #62 Lost Days  [common, status=auto]
- type: Instant — Lesson
- cost: {4}{U}
- oracle: The owner of target creature or enchantment puts it into their library second from the top or on the bottom. You create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_other

### #63 Master Pakku  [uncommon, status=auto]
- type: Legendary Creature — Human Advisor Ally
- cost: {1}{U}
- oracle: Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
Whenever Master Pakku becomes tapped, target player mills X cards, where X is the number of Lesson cards in your graveyard. (They put the top X cards of their library into their graveyard.)
- role_features: is_creature

### #64 The Mechanist, Aerial Artisan  [rare, status=auto]
- type: Legendary Creature — Human Artificer Ally
- cost: {2}{U}
- oracle: Whenever you cast a noncreature spell, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
{T}: Until end of turn, target artifact token you control becomes a 3/1 Construct artifact creature with flying.
- role_features: is_creature

### #65 North Pole Patrol  [uncommon, status=auto]
- type: Creature — Human Soldier Ally
- cost: {2}{U}
- oracle: {T}: Untap another target permanent you control.
Waterbend {3}, {T}: Tap target creature an opponent controls. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #66 Octopus Form  [common, status=auto]
- type: Instant — Lesson
- cost: {U}
- oracle: Target creature you control gets +1/+1 and gains hexproof until end of turn. Untap it. (It can't be the target of spells or abilities your opponents control.)
- role_features: combat_trick: +1/+1 grants ['hexproof']

### #67 Otter-Penguin  [common, status=auto]
- type: Creature — Otter Bird
- cost: {1}{U}
- oracle: Whenever you draw your second card each turn, this creature gets +1/+2 until end of turn and can't be blocked this turn.
- role_features: is_creature

### #68 Rowdy Snowballers  [common, status=auto]
- type: Creature — Human Peasant Ally
- cost: {2}{U}
- oracle: When this creature enters, tap target creature an opponent controls and put a stun counter on it. (If a permanent with a stun counter would become untapped, remove one from it instead.)
- role_features: is_creature

### #69 Secret of Bloodbending  [mythic, status=auto]
- type: Sorcery — Lesson
- cost: {U}{U}{U}{U}
- oracle: As an additional cost to cast this spell, you may waterbend {10}.
You control target opponent during their next combat phase. If this spell's additional cost was paid, you control that player during their next turn instead. (You see all cards that player could see and make all decisions for them.)
Exile Secret of Bloodbending.
- role_features: is_other

### #70 Serpent of the Pass  [uncommon, status=auto]
- type: Creature — Serpent
- cost: {5}{U}{U}
- oracle: If there are three or more Lesson cards in your graveyard, you may cast this spell as though it had flash.
This spell costs {1} less to cast for each noncreature, nonland card in your graveyard.
- role_features: is_creature

### #71 Sokka's Haiku  [uncommon, status=auto]
- type: Instant — Lesson
- cost: {3}{U}{U}
- oracle: Counter target spell.
Draw a card, then mill three cards.
Untap target land.
- role_features: is_counterspell, cards_drawn=1

### #72 The Spirit Oasis  [uncommon, status=auto]
- type: Legendary Enchantment — Shrine
- cost: {2}{U}
- oracle: When The Spirit Oasis enters, draw a card for each Shrine you control.
Whenever another Shrine you control enters, draw a card.
- role_features: cards_drawn=2

### #73 Spirit Water Revival  [rare, status=llm_encoded]
- type: Sorcery
- cost: {1}{U}{U}
- oracle: As an additional cost to cast this spell, you may waterbend {6}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
Draw two cards. If this spell's additional cost was paid, instead shuffle your graveyard into your library, draw seven cards, and you have no maximum hand size for the rest of the game.
Exile Spirit Water Revival.
- role_features: cards_drawn=2

### #74 Teo, Spirited Glider  [uncommon, status=auto]
- type: Legendary Creature — Human Pilot Ally
- cost: {3}{U}
- oracle: Flying
Whenever one or more creatures you control with flying attack, draw a card, then discard a card. When you discard a nonland card this way, put a +1/+1 counter on target creature you control.
- role_features: is_creature, cards_drawn=1

### #75 Tiger-Seal  [rare, status=auto]
- type: Creature — Cat Seal
- cost: {U}
- oracle: Vigilance
At the beginning of your upkeep, tap this creature.
Whenever you draw your second card each turn, untap this creature.
- role_features: is_creature

### #76 Ty Lee, Chi Blocker  [rare, status=auto]
- type: Legendary Creature — Human Performer Ally
- cost: {2}{U}
- oracle: Flash
Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
When Ty Lee enters, tap up to one target creature. It doesn't untap during its controller's untap step for as long as you control Ty Lee.
- role_features: is_creature

### #77 The Unagi of Kyoshi Island  [rare, status=auto]
- type: Legendary Creature — Serpent
- cost: {3}{U}{U}
- oracle: Flash
Ward—Waterbend {4}. (Whenever this creature becomes the target of a spell or ability an opponent controls, counter it unless that player pays {4}. They can tap their artifacts and creatures to help. Each one pays for {1}.)
Whenever an opponent draws their second card each turn, you draw two cards.
- role_features: is_creature, cards_drawn=2

### #78 Wan Shi Tong, Librarian  [mythic, status=auto]
- type: Legendary Creature — Bird Spirit
- cost: {X}{U}{U}
- oracle: Flash
Flying, vigilance
When Wan Shi Tong enters, put X +1/+1 counters on him. Then draw half X cards, rounded down.
Whenever an opponent searches their library, put a +1/+1 counter on Wan Shi Tong and draw a card.
- role_features: is_creature, cards_drawn=1

### #79 Waterbender Ascension  [rare, status=auto]
- type: Enchantment
- cost: {1}{U}
- oracle: Whenever a creature you control deals combat damage to a player, put a quest counter on this enchantment. Then if it has four or more quest counters on it, draw a card.
Waterbend {4}: Target creature can't be blocked this turn. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: cards_drawn=1

### #80 Waterbending Lesson  [common, status=auto]
- type: Sorcery — Lesson
- cost: {3}{U}
- oracle: Draw three cards. Then discard a card unless you waterbend {2}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: cards_drawn=3

### #81 Waterbending Scroll  [uncommon, status=auto]
- type: Artifact
- cost: {1}{U}
- oracle: {6}, {T}: Draw a card. This ability costs {1} less to activate for each Island you control.
- role_features: is_other

### #82 Watery Grasp  [common, status=auto]
- type: Enchantment — Aura
- cost: {U}
- oracle: Enchant creature
Enchanted creature doesn't untap during its controller's untap step.
Waterbend {5}: Enchanted creature's owner shuffles it into their library. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_removal_aura

### #83 Yue, the Moon Spirit  [rare, status=auto]
- type: Legendary Creature — Spirit Ally
- cost: {3}{U}
- oracle: Flying, vigilance
Waterbend {5}, {T}: You may cast a noncreature spell from your hand without paying its mana cost. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #84 Azula Always Lies  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {1}{B}
- oracle: Choose one or both —
• Target creature gets -1/-1 until end of turn.
• Put a +1/+1 counter on target creature.
- role_features: is_other

### #85 Azula, On the Hunt  [uncommon, status=auto]
- type: Legendary Creature — Human Noble
- cost: {3}{B}
- oracle: Firebending 2 (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
Whenever Azula attacks, you lose 1 life and create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #86 Beetle-Headed Merchants  [common, status=auto]
- type: Creature — Human Citizen
- cost: {4}{B}
- oracle: Whenever this creature attacks, you may sacrifice another creature or artifact. If you do, draw a card and put a +1/+1 counter on this creature.
- role_features: is_creature, cards_drawn=1

### #87 Boiling Rock Rioter  [rare, status=auto]
- type: Creature — Human Rogue Ally
- cost: {2}{B}
- oracle: Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
Tap an untapped Ally you control: Exile target card from a graveyard.
Whenever this creature attacks, you may cast an Ally spell from among cards you own exiled with this creature.
- role_features: is_creature

### #88 Buzzard-Wasp Colony  [uncommon, status=auto]
- type: Creature — Bird Insect
- cost: {3}{B}
- oracle: Flying
When this creature enters, you may sacrifice an artifact or creature. If you do, draw a card.
Whenever another creature you control dies, if it had counters on it, put its counters on this creature.
- role_features: is_creature, cards_drawn=1

### #89 Callous Inspector  [common, status=auto]
- type: Creature — Human Soldier
- cost: {B}
- oracle: Menace (This creature can't be blocked except by two or more creatures.)
When this creature dies, it deals 1 damage to you. Create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #90 Canyon Crawler  [common, status=auto]
- type: Creature — Spider Beast
- cost: {4}{B}{B}
- oracle: Deathtouch
When this creature enters, create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
Swampcycling {2} ({2}, Discard this card: Search your library for a Swamp card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #91 Cat-Gator  [uncommon, status=auto]
- type: Creature — Fish Crocodile
- cost: {6}{B}
- oracle: Lifelink
When this creature enters, it deals damage equal to the number of Swamps you control to any target.
- role_features: is_creature

### #92 Corrupt Court Official  [common, status=auto]
- type: Creature — Human Advisor
- cost: {1}{B}
- oracle: When this creature enters, target opponent discards a card.
- role_features: is_creature

### #93 Dai Li Indoctrination  [common, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {1}{B}
- oracle: Choose one —
• Target opponent reveals their hand. You choose a nonland permanent card from it. That player discards that card.
• Earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: creates_creatures: 2/2

### #94 Day of Black Sun  [rare, status=llm_encoded]
- type: Sorcery
- cost: {X}{B}{B}
- oracle: Each creature with mana value X or less loses all abilities until end of turn. Destroy those creatures.
- role_features: removal_destroy_or_exile

### #95 Deadly Precision  [common, status=llm_encoded]
- type: Sorcery
- cost: {B}
- oracle: As an additional cost to cast this spell, pay {4} or sacrifice an artifact or creature.
Destroy target creature.
- role_features: removal_destroy_or_exile

### #96 Epic Downfall  [uncommon, status=auto]
- type: Sorcery
- cost: {1}{B}
- oracle: Exile target creature with mana value 3 or greater.
- role_features: removal_destroy_or_exile

### #97 Fatal Fissure  [uncommon, status=auto]
- type: Instant
- cost: {1}{B}
- oracle: Choose target creature. When that creature dies this turn, you earthbend 4. (Target land you control becomes a 0/0 creature with haste that's still a land. Put four +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: creates_creatures: 4/4

### #98 The Fire Nation Drill  [rare, status=auto]
- type: Legendary Artifact — Vehicle
- cost: {2}{B}{B}
- oracle: Trample
When The Fire Nation Drill enters, you may tap it. When you do, destroy target creature with power 4 or less.
{1}: Permanents your opponents control lose hexproof and indestructible until end of turn.
Crew 2
- role_features: is_vehicle

### #99 Fire Nation Engineer  [uncommon, status=auto]
- type: Creature — Human Artificer
- cost: {2}{B}
- oracle: Raid — At the beginning of your end step, if you attacked this turn, put a +1/+1 counter on another target creature or Vehicle you control.
- role_features: is_creature

### #100 Fire Navy Trebuchet  [uncommon, status=auto]
- type: Artifact Creature — Wall
- cost: {2}{B}
- oracle: Defender, reach
Whenever you attack, create a 2/1 colorless Construct artifact creature token with flying named Ballistic Boulder that's tapped and attacking. Sacrifice that token at the beginning of the next end step.
- role_features: is_creature, creates_creatures: 2/1  Construct

### #101 Foggy Swamp Hunters  [common, status=auto]
- type: Creature — Human Ranger Ally
- cost: {3}{B}
- oracle: As long as you've drawn two or more cards this turn, this creature has lifelink and menace. (It can't be blocked except by two or more creatures.)
- role_features: is_creature

### #102 Foggy Swamp Visions  [rare, status=llm_encoded]
- type: Sorcery
- cost: {1}{B}{B}
- oracle: As an additional cost to cast this spell, waterbend {X}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
Exile X target creature cards from graveyards. For each creature card exiled this way, create a token that's a copy of it. At the beginning of your next end step, sacrifice those tokens.
- role_features: is_other

### #103 Heartless Act  [uncommon, status=llm_encoded]
- type: Instant
- cost: {1}{B}
- oracle: Choose one —
• Destroy target creature with no counters on it.
• Remove up to three counters from target creature.
- role_features: removal_destroy_or_exile

### #104 Hog-Monkey  [common, status=auto]
- type: Creature — Boar Monkey
- cost: {2}{B}
- oracle: At the beginning of combat on your turn, target creature you control with a +1/+1 counter on it gains menace until end of turn. (It can't be blocked except by two or more creatures.)
Exhaust — {5}: Put two +1/+1 counters on this creature. (Activate each exhaust ability only once.)
- role_features: is_creature

### #105 Joo Dee, One of Many  [uncommon, status=auto]
- type: Creature — Human Advisor
- cost: {1}{B}
- oracle: {B}, {T}: Surveil 1. Create a token that's a copy of this creature, then sacrifice an artifact or creature. Activate only as a sorcery. (To surveil 1, look at the top card of your library. You may put it into your graveyard.)
- role_features: is_creature

### #106 June, Bounty Hunter  [uncommon, status=llm_encoded]
- type: Legendary Creature — Human Mercenary
- cost: {1}{B}
- oracle: June can't be blocked as long as you've drawn two or more cards this turn.
{1}, Sacrifice another creature: Create a Clue token. Activate only during your turn. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #107 Koh, the Face Stealer  [mythic, status=auto]
- type: Legendary Creature — Shapeshifter Spirit
- cost: {4}{B}{B}
- oracle: When Koh enters, exile up to one other target creature.
Whenever another nontoken creature dies, you may exile it.
Pay 1 life: Choose a creature card exiled with Koh.
Koh has all activated and triggered abilities of the last chosen card.
- role_features: is_creature

### #108 Lo and Li, Twin Tutors  [uncommon, status=auto]
- type: Legendary Creature — Human Advisor
- cost: {4}{B}
- oracle: When Lo and Li enter, search your library for a Lesson or Noble card, reveal it, put it into your hand, then shuffle.
Noble creatures you control and Lesson spells you control have lifelink.
- role_features: is_creature

### #109 Mai, Scornful Striker  [rare, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {1}{B}
- oracle: First strike
Whenever a player casts a noncreature spell, they lose 2 life.
- role_features: is_creature

### #110 Merchant of Many Hats  [common, status=auto]
- type: Creature — Human Peasant Ally
- cost: {1}{B}
- oracle: {2}{B}: Return this card from your graveyard to your hand.
- role_features: is_creature

### #111 Northern Air Temple  [uncommon, status=auto]
- type: Legendary Enchantment — Shrine
- cost: {B}
- oracle: When Northern Air Temple enters, each opponent loses X life and you gain X life, where X is the number of Shrines you control.
Whenever another Shrine you control enters, each opponent loses 1 life and you gain 1 life.
- role_features: is_other

### #112 Obsessive Pursuit  [rare, status=auto]
- type: Enchantment
- cost: {1}{B}
- oracle: When this enchantment enters and at the beginning of your upkeep, you lose 1 life and create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
Whenever you attack, put X +1/+1 counters on target attacking creature, where X is the number of permanents you've sacrificed this turn. If X is three or more, that creature gains lifelink until end of turn.
- role_features: is_other

### #113 Ozai's Cruelty  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {2}{B}
- oracle: Ozai's Cruelty deals 2 damage to target player. That player discards two cards.
- role_features: is_other

### #114 Phoenix Fleet Airship  [mythic, status=auto]
- type: Artifact — Vehicle
- cost: {2}{B}{B}
- oracle: Flying
At the beginning of your end step, if you sacrificed a permanent this turn, create a token that's a copy of this Vehicle.
As long as you control eight or more permanents named Phoenix Fleet Airship, this Vehicle is an artifact creature.
Crew 1
- role_features: is_vehicle

### #115 Pirate Peddlers  [common, status=auto]
- type: Creature — Human Pirate
- cost: {2}{B}
- oracle: Deathtouch
Whenever you sacrifice another permanent, put a +1/+1 counter on this creature.
- role_features: is_creature

### #116 Raven Eagle  [rare, status=auto]
- type: Creature — Bird Assassin
- cost: {2}{B}
- oracle: Flying
Whenever this creature enters or attacks, exile up to one target card from a graveyard. If a creature card is exiled this way, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
Whenever you draw your second card each turn, each opponent loses 1 life and you gain 1 life.
- role_features: is_creature

### #117 The Rise of Sozin // Fire Lord Sozin  [mythic, status=auto]
- type: Enchantment — Saga // Legendary Creature — Human Noble
- cost: {4}{B}{B}
- oracle: (As this Saga enters and after your draw step, add a lore counter.)
I — Destroy all creatures.
II — Choose a card name. Search target opponent's graveyard, hand, and library for up to four cards with that name and exile them. Then that player shuffles.
III — Exile this Saga, then return it to the battlefield transformed under your control.
- role_features: is_saga

### #118 Ruinous Waterbending  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {1}{B}{B}
- oracle: As an additional cost to cast this spell, you may waterbend {4}. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
All creatures get -2/-2 until end of turn. If this spell's additional cost was paid, whenever a creature dies this turn, you gain 1 life.
- role_features: removal_destroy_or_exile

### #119 Sold Out  [common, status=auto]
- type: Instant
- cost: {3}{B}
- oracle: Exile target creature. If it was dealt damage this turn, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: removal_destroy_or_exile

### #120 Swampsnare Trap  [common, status=llm_encoded]
- type: Enchantment — Aura
- cost: {2}{B}
- oracle: This spell costs {1} less to cast if it targets a creature with flying.
Enchant creature
Enchanted creature gets -5/-3.
- role_features: is_removal_aura

### #121 Tundra Tank  [uncommon, status=auto]
- type: Artifact — Vehicle
- cost: {2}{B}
- oracle: Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
When this Vehicle enters, target creature you control gains indestructible until end of turn.
Crew 1 (Tap any number of creatures you control with total power 1 or more: This Vehicle becomes an artifact creature until end of turn.)
- role_features: is_vehicle

### #122 Wolfbat  [uncommon, status=auto]
- type: Creature — Wolf Bat
- cost: {2}{B}
- oracle: Flying
Whenever you draw your second card each turn, you may pay {B}. If you do, return this card from your graveyard to the battlefield with a finality counter on it. (If a creature with a finality counter on it would die, exile it instead.)
- role_features: is_creature

### #123 Zuko's Conviction  [uncommon, status=llm_encoded]
- type: Instant
- cost: {B}
- oracle: Kicker {4} (You may pay an additional {4} as you cast this spell.)
Return target creature card from your graveyard to your hand. If this spell was kicked, instead put that card onto the battlefield tapped.
- role_features: is_other

### #124 Boar-q-pine  [common, status=auto]
- type: Creature — Boar Porcupine
- cost: {2}{R}
- oracle: Whenever you cast a noncreature spell, put a +1/+1 counter on this creature.
- role_features: is_creature

### #125 Bumi Bash  [common, status=llm_encoded]
- type: Sorcery
- cost: {3}{R}
- oracle: Choose one —
• Bumi Bash deals damage equal to the number of lands you control to target creature.
• Destroy target land creature or nonbasic land.
- role_features: removal_destroy_or_exile

### #126 The Cave of Two Lovers  [uncommon, status=auto]
- type: Enchantment — Saga
- cost: {3}{R}
- oracle: (As this Saga enters and after your draw step, add a lore counter. Sacrifice after III.)
I — Create two 1/1 white Ally creature tokens.
II — Search your library for a Mountain or Cave card, reveal it, put it into your hand, then shuffle.
III — Earthbend 3. (Target land you control becomes a 0/0 creature with haste that's still a land. Put three +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_saga, creates_creatures: 1/1 W Ally

### #127 Combustion Man  [uncommon, status=auto]
- type: Legendary Creature — Human Assassin
- cost: {3}{R}{R}
- oracle: Whenever Combustion Man attacks, destroy target permanent unless its controller has Combustion Man deal damage to them equal to his power.
- role_features: is_creature

### #128 Combustion Technique  [uncommon, status=auto]
- type: Instant — Lesson
- cost: {1}{R}
- oracle: Combustion Technique deals damage equal to 2 plus the number of Lesson cards in your graveyard to target creature. If that creature would die this turn, exile it instead.
- role_features: is_other

### #129 Crescent Island Temple  [uncommon, status=auto]
- type: Legendary Enchantment — Shrine
- cost: {3}{R}
- oracle: When Crescent Island Temple enters, for each Shrine you control, create a 1/1 red Monk creature token with prowess. (Whenever you cast a noncreature spell, it gets +1/+1 until end of turn.)
Whenever another Shrine you control enters, create a 1/1 red Monk creature token with prowess.
- role_features: creates_creatures: 1/1 R Monk | 1/1 R Monk

### #130 Cunning Maneuver  [common, status=llm_encoded]
- type: Instant
- cost: {1}{R}
- oracle: Target creature gets +3/+1 until end of turn.
Create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: combat_trick: +3/+1

### #131 Deserter's Disciple  [common, status=auto]
- type: Creature — Human Rebel Ally
- cost: {1}{R}
- oracle: {T}: Another target creature you control with power 2 or less can't be blocked this turn.
- role_features: is_creature

### #132 Fated Firepower  [mythic, status=auto]
- type: Enchantment
- cost: {X}{R}{R}{R}
- oracle: Flash
This enchantment enters with X fire counters on it.
If a source you control would deal damage to an opponent or a permanent an opponent controls, it deals that much damage plus an amount of damage equal to the number of fire counters on this enchantment instead.
- role_features: is_other

### #133 Fire Nation Attacks  [uncommon, status=llm_encoded]
- type: Instant
- cost: {4}{R}
- oracle: Create two 2/2 red Soldier creature tokens with firebending 1. (Whenever a creature with firebending 1 attacks, add {R}. This mana lasts until end of combat.)
Flashback {8}{R} (You may cast this card from your graveyard for its flashback cost. Then exile it.)
- role_features: creates_creatures: 2/2 R Soldier | 2/2 R Soldier

### #134 Fire Nation Cadets  [common, status=auto]
- type: Creature — Human Soldier
- cost: {R}
- oracle: This creature has firebending 2 as long as there's a Lesson card in your graveyard. (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
{2}: This creature gets +1/+0 until end of turn.
- role_features: is_creature

### #135 Fire Nation Raider  [common, status=auto]
- type: Creature — Human Soldier
- cost: {3}{R}
- oracle: Raid — When this creature enters, if you attacked this turn, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_creature

### #136 Fire Sages  [uncommon, status=auto]
- type: Creature — Human Cleric
- cost: {1}{R}
- oracle: Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
{1}{R}{R}: Put a +1/+1 counter on this creature.
- role_features: is_creature

### #137 Firebender Ascension  [rare, status=auto]
- type: Enchantment
- cost: {1}{R}
- oracle: When this enchantment enters, create a 2/2 red Soldier creature token with firebending 1.
Whenever a creature you control attacking causes a triggered ability of that creature to trigger, put a quest counter on this enchantment. Then if it has four or more quest counters on it, you may copy that ability. You may choose new targets for the copy.
- role_features: creates_creatures: 2/2 R Soldier

### #138 Firebending Lesson  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {R}
- oracle: Kicker {4} (You may pay an additional {4} as you cast this spell.)
Firebending Lesson deals 2 damage to target creature. If this spell was kicked, it deals 5 damage to that creature instead.
- role_features: removal_burn_damage=2

### #139 Firebending Student  [rare, status=auto]
- type: Creature — Human Monk
- cost: {1}{R}
- oracle: Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
Firebending X, where X is this creature's power. (Whenever this creature attacks, add X {R}. This mana lasts until end of combat.)
- role_features: is_creature

### #140 How to Start a Riot  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {2}{R}
- oracle: Target creature gains menace until end of turn. (It can't be blocked except by two or more creatures.)
Creatures target player controls get +2/+0 until end of turn.
- role_features: combat_trick: grants ['menace']

### #141 Iroh's Demonstration  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {1}{R}
- oracle: Choose one —
• Iroh's Demonstration deals 1 damage to each creature your opponents control.
• Iroh's Demonstration deals 4 damage to target creature.
- role_features: removal_burn_damage=4

### #142 Jeong Jeong, the Deserter  [uncommon, status=auto]
- type: Legendary Creature — Human Rebel Ally
- cost: {2}{R}
- oracle: Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
Exhaust — {3}: Put a +1/+1 counter on Jeong Jeong. When you next cast a Lesson spell this turn, copy it and you may choose new targets for the copy. (Activate each exhaust ability only once.)
- role_features: is_creature

### #143 Jet's Brainwashing  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {R}
- oracle: Kicker {3} (You may pay an additional {3} as you cast this spell.)
Target creature can't block this turn. If this spell was kicked, gain control of that creature until end of turn, untap it, and it gains haste until end of turn.
Create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_other

### #144 The Last Agni Kai  [rare, status=llm_encoded]
- type: Instant
- cost: {1}{R}
- oracle: Target creature you control fights target creature an opponent controls. If the creature the opponent controls is dealt excess damage this way, add that much {R}.
Until end of turn, you don't lose unspent red mana as steps and phases end.
- role_features: is_punch_fight

### #145 The Legend of Roku // Avatar Roku  [mythic, status=auto]
- type: Enchantment — Saga // Legendary Creature — Avatar
- cost: {2}{R}{R}
- oracle: (As this Saga enters and after your draw step, add a lore counter.)
I — Exile the top three cards of your library. Until the end of your next turn, you may play those cards.
II — Add one mana of any color.
III — Exile this Saga, then return it to the battlefield transformed under your control.
- role_features: is_saga

### #146 Lightning Strike  [common, status=auto]
- type: Instant
- cost: {1}{R}
- oracle: Lightning Strike deals 3 damage to any target.
- role_features: removal_burn_damage=3

### #147 Mai, Jaded Edge  [uncommon, status=auto]
- type: Legendary Creature — Human Noble
- cost: {1}{R}
- oracle: Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
Exhaust — {3}: Put a double strike counter on Mai. (Activate each exhaust ability only once.)
- role_features: is_creature

### #148 Mongoose Lizard  [common, status=auto]
- type: Creature — Mongoose Lizard
- cost: {4}{R}{R}
- oracle: Menace (This creature can't be blocked except by two or more creatures.)
When this creature enters, it deals 1 damage to any target.
Mountaincycling {2} ({2}, Discard this card: Search your library for a Mountain card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature, removal_burn_damage=1

### #149 Price of Freedom  [uncommon, status=auto]
- type: Sorcery — Lesson
- cost: {1}{R}
- oracle: Destroy target artifact or land an opponent controls. Its controller may search their library for a basic land card, put it onto the battlefield tapped, then shuffle.
Draw a card.
- role_features: cards_drawn=1

### #150 Ran and Shaw  [rare, status=auto]
- type: Legendary Creature — Dragon
- cost: {3}{R}{R}
- oracle: Flying, firebending 2
When Ran and Shaw enter, if you cast them and there are three or more Dragon and/or Lesson cards in your graveyard, create a token that's a copy of Ran and Shaw, except it's not legendary.
{3}{R}: Dragons you control get +2/+0 until end of turn.
- role_features: is_creature

### #151 Redirect Lightning  [rare, status=llm_encoded]
- type: Instant — Lesson
- cost: {R}
- oracle: As an additional cost to cast this spell, pay 5 life or pay {2}.
Change the target of target spell or ability with a single target.
- role_features: is_other

### #152 Rough Rhino Cavalry  [common, status=auto]
- type: Creature — Human Mercenary
- cost: {4}{R}
- oracle: Firebending 2 (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
Exhaust — {8}: Put two +1/+1 counters on this creature. It gains trample until end of turn. (Activate each exhaust ability only once.)
- role_features: is_creature

### #153 Solstice Revelations  [uncommon, status=llm_encoded]
- type: Instant — Lesson
- cost: {2}{R}
- oracle: Exile cards from the top of your library until you exile a nonland card. You may cast that card without paying its mana cost if the spell's mana value is less than the number of Mountains you control. If you don't cast that card this way, put it into your hand.
Flashback {6}{R} (You may cast this card from your graveyard for its flashback cost. Then exile it.)
- role_features: cards_drawn=1

### #154 Sozin's Comet  [mythic, status=llm_encoded]
- type: Sorcery
- cost: {3}{R}{R}
- oracle: Each creature you control gains firebending 5 until end of turn. (Whenever it attacks, add {R}{R}{R}{R}{R}. This mana lasts until end of combat.)
Foretell {2}{R} (During your turn, you may pay {2} and exile this card from your hand face down. Cast it on a later turn for its foretell cost.)
- role_features: is_other

### #155 Tiger-Dillo  [uncommon, status=auto]
- type: Creature — Cat Armadillo
- cost: {1}{R}
- oracle: This creature can't attack or block unless you control another creature with power 4 or greater.
- role_features: is_creature

### #156 Treetop Freedom Fighters  [common, status=auto]
- type: Creature — Human Rebel Ally
- cost: {2}{R}
- oracle: Haste
When this creature enters, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #157 Twin Blades  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {2}{R}
- oracle: Flash
When this Equipment enters, attach it to target creature you control. That creature gains double strike until end of turn.
Equipped creature gets +1/+1.
Equip {2} ({2}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #158 Ty Lee, Artful Acrobat  [uncommon, status=auto]
- type: Legendary Creature — Human Performer
- cost: {2}{R}
- oracle: Prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
Whenever Ty Lee attacks, you may pay {1}. When you do, target creature can't block this turn.
- role_features: is_creature

### #159 War Balloon  [uncommon, status=auto]
- type: Artifact — Vehicle
- cost: {2}{R}
- oracle: Flying
{1}: Put a fire counter on this Vehicle.
As long as this Vehicle has three or more fire counters on it, it's an artifact creature.
Crew 3 (Tap any number of creatures you control with total power 3 or more: This Vehicle becomes an artifact creature until end of turn.)
- role_features: is_vehicle

### #160 Wartime Protestors  [rare, status=auto]
- type: Creature — Human Rebel Ally
- cost: {3}{R}
- oracle: Haste
Whenever another Ally you control enters, put a +1/+1 counter on that creature and it gains haste until end of turn.
- role_features: is_creature

### #161 Yuyan Archers  [common, status=auto]
- type: Creature — Human Archer
- cost: {1}{R}
- oracle: Reach
When this creature enters, you may discard a card. If you do, draw a card.
- role_features: is_creature, cards_drawn=1

### #162 Zhao, the Moon Slayer  [rare, status=llm_encoded]
- type: Legendary Creature — Human Soldier
- cost: {1}{R}
- oracle: Menace
Nonbasic lands enter tapped.
{7}: Put a conqueror counter on Zhao.
As long as Zhao has a conqueror counter on him, nonbasic lands are Mountains. (They lose all other land types and abilities and have "{T}: Add {R}.")
- role_features: is_creature

### #163 Zuko, Exiled Prince  [uncommon, status=auto]
- type: Legendary Creature — Human Noble
- cost: {3}{R}
- oracle: Firebending 3 (Whenever this creature attacks, add {R}{R}{R}. This mana lasts until end of combat.)
{3}: Exile the top card of your library. You may play that card this turn.
- role_features: is_creature

### #164 Allies at Last  [uncommon, status=llm_encoded]
- type: Instant
- cost: {2}{G}
- oracle: Affinity for Allies (This spell costs {1} less to cast for each Ally you control.)
Up to two target creatures you control each deal damage equal to their power to target creature an opponent controls.
- role_features: is_punch_fight

### #165 Avatar Destiny  [rare, status=auto]
- type: Enchantment — Aura
- cost: {2}{G}{G}
- oracle: Enchant creature you control
Enchanted creature gets +1/+1 for each creature card in your graveyard and is an Avatar in addition to its other types.
When enchanted creature dies, mill cards equal to its power. Return this card to its owner's hand and up to one creature card milled this way to the battlefield under your control.
- role_features: is_pump_aura, aura_pump: +1/+1

### #166 Badgermole  [common, status=auto]
- type: Creature — Badger Mole
- cost: {4}{G}
- oracle: When this creature enters, earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
Creatures you control with +1/+1 counters on them have trample.
- role_features: is_creature, creates_creatures: 2/2

### #167 Badgermole Cub  [mythic, status=auto]
- type: Creature — Badger Mole
- cost: {1}{G}
- oracle: When this creature enters, earthbend 1. (Target land you control becomes a 0/0 creature with haste that's still a land. Put a +1/+1 counter on it. When it dies or is exiled, return it to the battlefield tapped.)
Whenever you tap a creature for mana, add an additional {G}.
- role_features: is_creature, creates_creatures: 1/1

### #168 The Boulder, Ready to Rumble  [uncommon, status=auto]
- type: Legendary Creature — Human Warrior Performer
- cost: {3}{G}
- oracle: Whenever The Boulder attacks, earthbend X, where X is the number of creatures you control with power 4 or greater. (Target land you control becomes a 0/0 creature with haste that's still a land. Put X +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature

### #169 Bumi, King of Three Trials  [uncommon, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {5}{G}
- oracle: When Bumi enters, choose up to X, where X is the number of Lesson cards in your graveyard —
• Put three +1/+1 counters on Bumi.
• Target player scries 3.
• Earthbend 3. (Target land you control becomes a 0/0 creature with haste that's still a land. Put three +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature

### #170 Cycle of Renewal  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {2}{G}
- oracle: Sacrifice a land. Search your library for up to two basic land cards, put them onto the battlefield tapped, then shuffle.
- role_features: is_other

### #171 Diligent Zookeeper  [rare, status=auto]
- type: Creature — Human Citizen Ally
- cost: {3}{G}
- oracle: Each non-Human creature you control gets +1/+1 for each of its creature types, to a maximum of 10.
- role_features: is_creature

### #172 The Earth King  [rare, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {3}{G}
- oracle: When The Earth King enters, create a 4/4 green Bear creature token.
Whenever one or more creatures you control with power 4 or greater attack, search your library for up to that many basic land cards, put them onto the battlefield tapped, then shuffle.
- role_features: is_creature, creates_creatures: 4/4 G Bear

### #173 Earth Kingdom General  [uncommon, status=auto]
- type: Creature — Human Soldier Ally
- cost: {3}{G}
- oracle: When this creature enters, earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
Whenever you put one or more +1/+1 counters on a creature, you may gain that much life. Do this only once each turn.
- role_features: is_creature, creates_creatures: 2/2

### #174 Earth Rumble  [uncommon, status=auto]
- type: Sorcery
- cost: {3}{G}
- oracle: Earthbend 2. When you do, up to one target creature you control fights target creature an opponent controls. (To earthbend 2, target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped. Creatures that fight each deal damage equal to their power to the other.)
- role_features: creates_creatures: 2/2

### #175 Earthbender Ascension  [rare, status=auto]
- type: Enchantment
- cost: {2}{G}
- oracle: When this enchantment enters, earthbend 2. Then search your library for a basic land card, put it onto the battlefield tapped, then shuffle.
Landfall — Whenever a land you control enters, put a quest counter on this enchantment. When you do, if it has four or more quest counters on it, put a +1/+1 counter on target creature you control. It gains trample until end of turn.
- role_features: creates_creatures: 2/2

### #176 Earthbending Lesson  [common, status=auto]
- type: Sorcery — Lesson
- cost: {3}{G}
- oracle: Earthbend 4. (Target land you control becomes a 0/0 creature with haste that's still a land. Put four +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: creates_creatures: 4/4

### #177 Earthen Ally  [rare, status=auto]
- type: Creature — Human Soldier Ally
- cost: {G}
- oracle: This creature gets +1/+0 for each color among Allies you control.
{2}{W}{U}{B}{R}{G}: Earthbend 5. (Target land you control becomes a 0/0 creature with haste that's still a land. Put five +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature, creates_creatures: 5/5

### #178 Elemental Teachings  [rare, status=auto]
- type: Instant — Lesson
- cost: {4}{G}
- oracle: Search your library for up to four land cards with different names and reveal them. An opponent chooses two of those cards. Put the chosen cards into your graveyard and the rest onto the battlefield tapped, then shuffle.
- role_features: is_other

### #179 Flopsie, Bumi's Buddy  [uncommon, status=auto]
- type: Legendary Creature — Ape Goat
- cost: {4}{G}{G}
- oracle: When Flopsie enters, put a +1/+1 counter on each creature you control.
Each creature you control with power 4 or greater can't be blocked by more than one creature.
- role_features: is_creature

### #180 Foggy Swamp Vinebender  [common, status=auto]
- type: Creature — Human Plant Ally
- cost: {3}{G}
- oracle: This creature can't be blocked by creatures with power 2 or less.
Waterbend {5}: Put a +1/+1 counter on this creature. Activate only during your turn. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #181 Great Divide Guide  [rare, status=auto]
- type: Creature — Human Scout Ally
- cost: {1}{G}
- oracle: Each land and Ally you control has "{T}: Add one mana of any color."
- role_features: is_creature

### #182 Haru, Hidden Talent  [uncommon, status=auto]
- type: Legendary Creature — Human Peasant Ally
- cost: {1}{G}
- oracle: Whenever another Ally you control enters, earthbend 1. (Target land you control becomes a 0/0 creature with haste that's still a land. Put a +1/+1 counter on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature, creates_creatures: 1/1

### #183 Invasion Tactics  [uncommon, status=auto]
- type: Enchantment
- cost: {4}{G}
- oracle: When this enchantment enters, creatures you control get +2/+2 until end of turn.
Whenever one or more Allies you control deal combat damage to a player, draw a card.
- role_features: cards_drawn=1

### #184 Kyoshi Island Plaza  [uncommon, status=auto]
- type: Legendary Enchantment — Shrine
- cost: {3}{G}
- oracle: When Kyoshi Island Plaza enters, search your library for up to X basic land cards, where X is the number of Shrines you control. Put those cards onto the battlefield tapped, then shuffle.
Whenever another Shrine you control enters, search your library for a basic land card, put it onto the battlefield tapped, then shuffle.
- role_features: is_other

### #185 Leaves from the Vine  [uncommon, status=llm_encoded]
- type: Enchantment — Saga
- cost: {1}{G}
- oracle: (As this Saga enters and after your draw step, add a lore counter. Sacrifice after III.)
I — Mill three cards, then create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
II — Put a +1/+1 counter on each of up to two target creatures you control.
III — Draw a card if there's a creature or Lesson card in your graveyard.
- role_features: is_saga

### #186 The Legend of Kyoshi // Avatar Kyoshi  [mythic, status=auto]
- type: Enchantment — Saga // Legendary Creature — Avatar
- cost: {4}{G}{G}
- oracle: (As this Saga enters and after your draw step, add a lore counter.)
I — Draw cards equal to the greatest power among creatures you control.
II — Earthbend X, where X is the number of cards in your hand. That land becomes an Island in addition to its other types.
III — Exile this Saga, then return it to the battlefield transformed under your control.
- role_features: is_saga

### #187 Origin of Metalbending  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {1}{G}
- oracle: Choose one —
• Destroy target artifact or enchantment.
• Put a +1/+1 counter on target creature you control. It gains indestructible until end of turn. (Damage and effects that say "destroy" don't destroy it.)
- role_features: combat_trick: +1/+1 grants ['indestructible']

### #188 Ostrich-Horse  [common, status=auto]
- type: Creature — Bird Horse
- cost: {2}{G}
- oracle: When this creature enters, mill three cards. You may put a land card from among them into your hand. If you don't, put a +1/+1 counter on this creature. (To mill three cards, put the top three cards of your library into your graveyard.)
- role_features: is_creature

### #189 Pillar Launch  [common, status=auto]
- type: Instant
- cost: {G}
- oracle: Target creature gets +2/+2 and gains reach until end of turn. Untap it.
- role_features: combat_trick: +2/+2 grants ['reach']

### #190 Raucous Audience  [common, status=auto]
- type: Creature — Human Citizen
- cost: {1}{G}
- oracle: {T}: Add {G}. If you control a creature with power 4 or greater, add {G}{G} instead.
- role_features: is_creature

### #191 Rebellious Captives  [common, status=auto]
- type: Creature — Human Peasant Ally
- cost: {1}{G}
- oracle: Exhaust — {6}: Put two +1/+1 counters on this creature, then earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped. Activate each exhaust ability only once.)
- role_features: is_creature, creates_creatures: 2/2

### #192 Rockalanche  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {2}{G}
- oracle: Earthbend X, where X is the number of Forests you control. (Target land you control becomes a 0/0 creature with haste that's still a land. Put X +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
Flashback {5}{G} (You may cast this card from your graveyard for its flashback cost. Then exile it.)
- role_features: is_other

### #193 Rocky Rebuke  [common, status=auto]
- type: Instant
- cost: {1}{G}
- oracle: Target creature you control deals damage equal to its power to target creature an opponent controls.
- role_features: is_other

### #194 Saber-Tooth Moose-Lion  [common, status=auto]
- type: Creature — Elk Cat
- cost: {4}{G}{G}
- oracle: Reach
Forestcycling {2} ({2}, Discard this card: Search your library for a Forest card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #195 Seismic Sense  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {G}
- oracle: Look at the top X cards of your library, where X is the number of lands you control. You may reveal a creature or land card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.
- role_features: cards_drawn=1, cards_manipulated=3

### #196 Shared Roots  [uncommon, status=auto]
- type: Sorcery — Lesson
- cost: {1}{G}
- oracle: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.
- role_features: is_other

### #197 Sparring Dummy  [uncommon, status=auto]
- type: Artifact Creature — Scarecrow
- cost: {1}{G}
- oracle: Defender
{T}: Mill a card. You may put a land card milled this way into your hand. You gain 2 life if a Lesson card is milled this way. (To mill a card, put the top card of your library into your graveyard.)
- role_features: is_creature

### #198 Toph, the Blind Bandit  [uncommon, status=llm_encoded]
- type: Legendary Creature — Human Warrior Ally
- cost: {2}{G}
- oracle: When Toph enters, earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
Toph's power is equal to the number of +1/+1 counters on lands you control.
- role_features: is_creature, creates_creatures: 2/2

### #199 True Ancestry  [uncommon, status=llm_encoded]
- type: Sorcery — Lesson
- cost: {1}{G}
- oracle: Return up to one target permanent card from your graveyard to your hand.
Create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
- role_features: is_other

### #200 Turtle-Duck  [common, status=auto]
- type: Creature — Turtle Bird
- cost: {G}
- oracle: {3}: Until end of turn, this creature has base power 4 and gains trample.
- role_features: is_creature

### #201 Unlucky Cabbage Merchant  [uncommon, status=auto]
- type: Creature — Human Citizen
- cost: {1}{G}
- oracle: When this creature enters, create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
Whenever you sacrifice a Food, you may search your library for a basic land card and put it onto the battlefield tapped. If you search your library this way, put this creature on the bottom of its owner's library, then shuffle.
- role_features: is_creature

### #202 Walltop Sentries  [common, status=auto]
- type: Creature — Human Soldier Ally
- cost: {2}{G}
- oracle: Reach, deathtouch
When this creature dies, if there's a Lesson card in your graveyard, you gain 2 life.
- role_features: is_creature

### #203 Aang, at the Crossroads // Aang, Destined Savior  [rare, status=auto]
- type: Legendary Creature — Human Avatar Ally // Legendary Creature — Avatar Ally
- cost: {2}{G}{W}{U}
- oracle: Flying
When Aang enters, look at the top five cards of your library. You may put a creature card with mana value 4 or less from among them onto the battlefield. Put the rest on the bottom of your library in a random order.
When another creature you control leaves the battlefield, transform Aang at the beginning of the next upkeep.
- role_features: is_creature

### #204 Aang, Swift Savior // Aang and La, Ocean's Fury  [rare, status=auto]
- type: Legendary Creature — Human Avatar Ally // Legendary Creature — Avatar Spirit Ally
- cost: {1}{W}{U}
- oracle: Flash
Flying
When Aang enters, airbend up to one other target creature or spell. (Exile it. While it's exiled, its owner may cast it for {2} rather than its mana cost.)
Waterbend {8}: Transform Aang.
- role_features: is_creature, is_bounce

### #205 Abandon Attachments  [common, status=llm_encoded]
- type: Instant — Lesson
- cost: {1}{U/R}
- oracle: You may discard a card. If you do, draw two cards.
- role_features: cards_drawn=2

### #206 Air Nomad Legacy  [uncommon, status=auto]
- type: Enchantment
- cost: {W}{U}
- oracle: When this enchantment enters, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
Creatures you control with flying get +1/+1.
- role_features: is_other

### #207 Avatar Aang // Aang, Master of Elements  [mythic, status=auto]
- type: Legendary Creature — Human Avatar Ally // Legendary Creature — Avatar Ally
- cost: {R}{G}{W}{U}
- oracle: Flying, firebending 2
Whenever you waterbend, earthbend, firebend, or airbend, draw a card. Then if you've done all four this turn, transform Avatar Aang.
- role_features: is_creature, cards_drawn=1

### #208 Azula, Cunning Usurper  [rare, status=auto]
- type: Legendary Creature — Human Noble Rogue
- cost: {2}{U}{B}{B}
- oracle: Firebending 2 (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
When Azula enters, target opponent exiles a nontoken creature they control, then they exile a nonland card from their graveyard.
During your turn, you may cast cards exiled with Azula and you may cast them as though they had flash. Mana of any type can be spent to cast those spells.
- role_features: is_creature

### #209 Beifong's Bounty Hunters  [rare, status=auto]
- type: Creature — Human Mercenary
- cost: {2}{B}{G}
- oracle: Whenever a nonland creature you control dies, earthbend X, where X is that creature's power. (Target land you control becomes a 0/0 creature with haste that's still a land. Put X +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature

### #210 Bitter Work  [uncommon, status=auto]
- type: Enchantment
- cost: {1}{R}{G}
- oracle: Whenever you attack a player with one or more creatures with power 4 or greater, draw a card.
Exhaust — {4}: Earthbend 4. Activate only during your turn. (Target land you control becomes a 0/0 creature with haste that's still a land. Put four +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped. Activate each exhaust ability only once.)
- role_features: cards_drawn=1, creates_creatures: 4/4

### #211 Bumi, Unleashed  [mythic, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {3}{R}{G}
- oracle: Trample
When Bumi enters, earthbend 4.
Whenever Bumi deals combat damage to a player, untap all lands you control. After this phase, there is an additional combat phase. Only land creatures can attack during that combat phase.
- role_features: is_creature, creates_creatures: 4/4

### #212 Cat-Owl  [common, status=auto]
- type: Creature — Cat Bird
- cost: {3}{W/U}
- oracle: Flying
Whenever this creature attacks, untap target artifact or creature.
- role_features: is_creature

### #213 Cruel Administrator  [uncommon, status=auto]
- type: Creature — Human Soldier
- cost: {3}{B}{R}
- oracle: Raid — This creature enters with a +1/+1 counter on it if you attacked this turn.
Whenever this creature attacks, create a 2/2 red Soldier creature token with firebending 1. (Whenever it attacks, add {R}. This mana lasts until end of combat.)
- role_features: is_creature, creates_creatures: 2/2 R Soldier

### #214 Dai Li Agents  [uncommon, status=auto]
- type: Creature — Human Soldier
- cost: {3}{B}{G}
- oracle: When this creature enters, earthbend 1, then earthbend 1. (To earthbend 1, target land you control becomes a 0/0 creature with haste that's still a land. Put a +1/+1 counter on it. When it dies or is exiled, return it to the battlefield tapped.)
Whenever this creature attacks, each opponent loses X life and you gain X life, where X is the number of creatures you control with +1/+1 counters on them.
- role_features: is_creature, creates_creatures: 1/1 | 1/1

### #215 Dragonfly Swarm  [uncommon, status=auto]
- type: Creature — Dragon Insect
- cost: {1}{U}{R}
- oracle: Flying, ward {1} (Whenever this creature becomes the target of a spell or ability an opponent controls, counter it unless that player pays {1}.)
This creature's power is equal to the number of noncreature, nonland cards in your graveyard.
When this creature dies, if there's a Lesson card in your graveyard, draw a card.
- role_features: is_creature, cards_drawn=1

### #216 Earth Kingdom Soldier  [common, status=auto]
- type: Creature — Human Soldier
- cost: {4}{G/W}
- oracle: Vigilance
When this creature enters, put a +1/+1 counter on each of up to two target creatures you control.
- role_features: is_creature

### #217 Earth King's Lieutenant  [rare, status=auto]
- type: Creature — Human Soldier Ally
- cost: {G}{W}
- oracle: Trample
When this creature enters, put a +1/+1 counter on each other Ally creature you control.
Whenever another Ally you control enters, put a +1/+1 counter on this creature.
- role_features: is_creature

### #218 Earth Rumble Wrestlers  [common, status=auto]
- type: Creature — Human Warrior Performer
- cost: {3}{R/G}
- oracle: Reach
This creature gets +1/+0 and has trample as long as you control a land creature or a land entered the battlefield under your control this turn.
- role_features: is_creature

### #219 Earth Village Ruffians  [common, status=auto]
- type: Creature — Human Soldier Rogue
- cost: {2}{B/G}
- oracle: When this creature dies, earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature, creates_creatures: 2/2

### #220 Fire Lord Azula  [rare, status=auto]
- type: Legendary Creature — Human Noble
- cost: {1}{U}{B}{R}
- oracle: Firebending 2 (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
Whenever you cast a spell while Fire Lord Azula is attacking, copy that spell. You may choose new targets for the copy. (A copy of a permanent spell becomes a token.)
- role_features: is_creature

### #221 Fire Lord Zuko  [rare, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {R}{W}{B}
- oracle: Firebending X, where X is Fire Lord Zuko's power. (Whenever this creature attacks, add X {R}. This mana lasts until end of combat.)
Whenever you cast a spell from exile and whenever a permanent you control enters from exile, put a +1/+1 counter on each creature you control.
- role_features: is_creature

### #222 Foggy Swamp Spirit Keeper  [uncommon, status=auto]
- type: Creature — Human Druid Ally
- cost: {1}{U}{B}
- oracle: Lifelink
Whenever you draw your second card each turn, create a 1/1 colorless Spirit creature token with "This token can't block or be blocked by non-Spirit creatures."
- role_features: is_creature, creates_creatures: 1/1  Spirit

### #223 Guru Pathik  [uncommon, status=auto]
- type: Legendary Creature — Human Monk Ally
- cost: {2}{G/U}{G/U}
- oracle: When Guru Pathik enters, look at the top five cards of your library. You may reveal a Lesson, Saga, or Shrine card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.
Whenever you cast a Lesson, Saga, or Shrine spell, put a +1/+1 counter on another target creature you control.
- role_features: is_creature

### #224 Hama, the Bloodbender  [uncommon, status=auto]
- type: Legendary Creature — Human Warlock
- cost: {2}{U/B}{U/B}{U/B}
- oracle: When Hama enters, target opponent mills three cards. Exile up to one noncreature, nonland card from that player's graveyard. For as long as you control Hama, you may cast the exiled card during your turn by waterbending {X} rather than paying its mana cost, where X is its mana value. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature

### #225 Hei Bai, Spirit of Balance  [uncommon, status=auto]
- type: Legendary Creature — Bear Spirit
- cost: {2}{W/B}{W/B}
- oracle: Whenever Hei Bai enters or attacks, you may sacrifice another creature or artifact. If you do, put two +1/+1 counters on Hei Bai.
When Hei Bai leaves the battlefield, put its counters on target creature you control.
- role_features: is_creature

### #226 Hermitic Herbalist  [uncommon, status=auto]
- type: Creature — Human Druid Ally
- cost: {G}{U}
- oracle: {T}: Add one mana of any color.
{T}: Add two mana in any combination of colors. Spend this mana only to cast Lesson spells.
- role_features: is_creature

### #227 Iroh, Grand Lotus  [rare, status=auto]
- type: Legendary Creature — Human Noble Ally
- cost: {3}{G}{U}{R}
- oracle: Firebending 2
During your turn, each non-Lesson instant and sorcery card in your graveyard has flashback. The flashback cost is equal to that card's mana cost. (You may cast a card from your graveyard for its flashback cost. Then exile it.)
During your turn, each Lesson card in your graveyard has flashback {1}.
- role_features: is_creature

### #228 Iroh, Tea Master  [rare, status=auto]
- type: Legendary Creature — Human Citizen Ally
- cost: {1}{R}{W}
- oracle: When Iroh enters, create a Food token.
At the beginning of combat on your turn, you may have target opponent gain control of target permanent you control. When you do, create a 1/1 white Ally creature token. Put a +1/+1 counter on that token for each permanent you own that your opponents control.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #229 Jet, Freedom Fighter  [uncommon, status=auto]
- type: Legendary Creature — Human Rebel Ally
- cost: {2}{R/W}{R/W}{R/W}
- oracle: When Jet enters, he deals damage equal to the number of creatures you control to target creature an opponent controls.
When Jet dies, put a +1/+1 counter on each of up to two target creatures.
- role_features: is_creature

### #230 Katara, the Fearless  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {G}{W}{U}
- oracle: If a triggered ability of an Ally you control triggers, that ability triggers an additional time.
- role_features: is_creature

### #231 Katara, Water Tribe's Hope  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {2}{W}{U}{U}
- oracle: Vigilance
When Katara enters, create a 1/1 white Ally creature token.
Waterbend {X}: Creatures you control have base power and toughness X/X until end of turn. X can't be 0. Activate only during your turn. (While paying a waterbend cost, you can tap your artifacts and creatures to help. Each one pays for {1}.)
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #232 The Lion-Turtle  [rare, status=llm_encoded]
- type: Legendary Creature — Elder Cat Turtle
- cost: {1}{G}{U}
- oracle: Vigilance, reach
When The Lion-Turtle enters, you gain 3 life.
The Lion-Turtle can't attack or block unless there are three or more Lesson cards in your graveyard.
{T}: Add one mana of any color.
- role_features: is_creature

### #233 Long Feng, Grand Secretariat  [uncommon, status=auto]
- type: Legendary Creature — Human Advisor
- cost: {1}{B/G}{B/G}
- oracle: Whenever another creature you control or a land you control is put into a graveyard from the battlefield, put a +1/+1 counter on target creature you control.
- role_features: is_creature

### #234 Messenger Hawk  [common, status=auto]
- type: Creature — Bird Scout
- cost: {2}{U/B}
- oracle: Flying
When this creature enters, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
This creature gets +2/+0 as long as you've drawn two or more cards this turn.
- role_features: is_creature

### #235 Ozai, the Phoenix King  [mythic, status=auto]
- type: Legendary Creature — Human Noble
- cost: {2}{B}{B}{R}{R}
- oracle: Trample, firebending 4, haste
If you would lose unspent mana, that mana becomes red instead.
Ozai has flying and indestructible as long as you have six or more unspent mana.
- role_features: is_creature

### #236 Platypus-Bear  [common, status=auto]
- type: Creature — Platypus Bear
- cost: {1}{G/U}
- oracle: Defender
When this creature enters, mill two cards. (Put the top two cards of your library into your graveyard.)
As long as there is a Lesson card in your graveyard, this creature can attack as though it didn't have defender.
- role_features: is_creature

### #237 Pretending Poxbearers  [common, status=auto]
- type: Creature — Human Citizen Ally
- cost: {1}{W/B}
- oracle: When this creature dies, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #238 Professor Zei, Anthropologist  [uncommon, status=auto]
- type: Legendary Creature — Human Advisor Ally
- cost: {U/R}{U/R}
- oracle: {T}, Discard a card: Draw a card.
{1}, {T}, Sacrifice Professor Zei: Return target instant or sorcery card from your graveyard to your hand. Activate only during your turn.
- role_features: is_creature, cards_drawn=1

### #239 Sandbender Scavengers  [rare, status=auto]
- type: Creature — Human Rogue
- cost: {W}{B}
- oracle: Whenever you sacrifice another permanent, put a +1/+1 counter on this creature.
When this creature dies, you may exile it. When you do, return target creature card with mana value less than or equal to this creature's power from your graveyard to the battlefield.
- role_features: is_creature

### #240 Sokka, Bold Boomeranger  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {U}{R}
- oracle: When Sokka enters, discard up to two cards, then draw that many cards.
Whenever you cast an artifact or Lesson spell, put a +1/+1 counter on Sokka.
- role_features: is_creature

### #241 Sokka, Lateral Strategist  [uncommon, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {1}{W/U}{W/U}
- oracle: Vigilance
Whenever Sokka and at least one other creature attack, draw a card.
- role_features: is_creature, cards_drawn=1

### #242 Sokka, Tenacious Tactician  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {1}{U}{R}{W}
- oracle: Menace, prowess (Whenever you cast a noncreature spell, this creature gets +1/+1 until end of turn.)
Other Allies you control have menace and prowess.
Whenever you cast a noncreature spell, create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #243 Suki, Kyoshi Warrior  [uncommon, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {2}{G/W}{G/W}
- oracle: Suki's power is equal to the number of creatures you control.
Whenever Suki attacks, create a 1/1 white Ally creature token that's tapped and attacking.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #244 Sun Warriors  [uncommon, status=auto]
- type: Creature — Human Warrior Ally
- cost: {2}{R}{W}
- oracle: Firebending X, where X is the number of creatures you control. (Whenever this creature attacks, add X {R}. This mana lasts until end of combat.)
{5}: Create a 1/1 white Ally creature token.
- role_features: is_creature, creates_creatures: 1/1 W Ally

### #245 Tolls of War  [uncommon, status=auto]
- type: Enchantment
- cost: {W}{B}
- oracle: When this enchantment enters, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
Whenever you sacrifice a permanent during your turn, create a 1/1 white Ally creature token. This ability triggers only once each turn.
- role_features: creates_creatures: 1/1 W Ally

### #246 Toph, Hardheaded Teacher  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {2}{R}{G}
- oracle: When Toph enters, you may discard a card. If you do, return target instant or sorcery card from your graveyard to your hand.
Whenever you cast a spell, earthbend 1. If that spell is a Lesson, put an additional +1/+1 counter on that land. (Target land you control becomes a 0/0 creature with haste that's still a land. Put a +1/+1 counter on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature, creates_creatures: 1/1

### #247 Toph, the First Metalbender  [rare, status=auto]
- type: Legendary Creature — Human Warrior Ally
- cost: {1}{R}{G}{W}
- oracle: Nontoken artifacts you control are lands in addition to their other types. (They don't gain the ability to {T} for mana.)
At the beginning of your end step, earthbend 2. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_creature, creates_creatures: 2/2

### #248 Uncle Iroh  [uncommon, status=llm_encoded]
- type: Legendary Creature — Human Noble Ally
- cost: {1}{R/G}{R/G}
- oracle: Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
Lesson spells you cast cost {1} less to cast.
- role_features: is_creature

### #249 Vindictive Warden  [common, status=auto]
- type: Creature — Human Soldier
- cost: {2}{B/R}
- oracle: Menace (This creature can't be blocked except by two or more creatures.)
Firebending 1 (Whenever this creature attacks, add {R}. This mana lasts until end of combat.)
{3}: This creature deals 1 damage to each opponent.
- role_features: is_creature

### #250 Wandering Musicians  [common, status=auto]
- type: Creature — Human Bard Ally
- cost: {3}{R/W}
- oracle: Whenever this creature attacks, creatures you control get +1/+0 until end of turn.
- role_features: is_creature

### #251 White Lotus Reinforcements  [uncommon, status=auto]
- type: Creature — Human Soldier Ally
- cost: {1}{G}{W}
- oracle: Vigilance
Other Allies you control get +1/+1.
- role_features: is_creature

### #252 Zhao, Ruthless Admiral  [uncommon, status=auto]
- type: Legendary Creature — Human Soldier
- cost: {2}{B/R}{B/R}
- oracle: Firebending 2 (Whenever this creature attacks, add {R}{R}. This mana lasts until end of combat.)
Whenever you sacrifice another permanent, creatures you control get +1/+0 until end of turn.
- role_features: is_creature

### #253 Zuko, Conflicted  [rare, status=llm_encoded]
- type: Legendary Creature — Human Rogue
- cost: {B}{R}
- oracle: At the beginning of your first main phase, choose one that hasn't been chosen and you lose 2 life —
• Draw a card.
• Put a +1/+1 counter on Zuko.
• Add {R}.
• Exile Zuko, then return him to the battlefield under an opponent's control.
- role_features: is_creature, cards_drawn=1

### #254 Barrels of Blasting Jelly  [common, status=auto]
- type: Artifact
- cost: {1}
- oracle: {1}: Add one mana of any color. Activate only once each turn.
{5}, {T}, Sacrifice this artifact: It deals 5 damage to target creature.
- role_features: is_mana_rock

### #255 Bender's Waterskin  [common, status=auto]
- type: Artifact
- cost: {3}
- oracle: Untap this artifact during each other player's untap step.
{T}: Add one mana of any color.
- role_features: is_mana_rock

### #256 Fire Nation Warship  [uncommon, status=auto]
- type: Artifact — Vehicle
- cost: {3}
- oracle: Reach
When this Vehicle dies, create a Clue token. (It's an artifact with "{2}, Sacrifice this token: Draw a card.")
Crew 2 (Tap any number of creatures you control with total power 2 or more: This Vehicle becomes an artifact creature until end of turn.)
- role_features: is_vehicle

### #257 Kyoshi Battle Fan  [common, status=auto]
- type: Artifact — Equipment
- cost: {2}
- oracle: When this Equipment enters, create a 1/1 white Ally creature token, then attach this Equipment to it.
Equipped creature gets +1/+0.
Equip {2} ({2}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment, creates_creatures: 1/1 W Ally | 1/1 W Ally

### #258 Meteor Sword  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {7}
- oracle: When this Equipment enters, destroy target permanent.
Equipped creature gets +3/+3.
Equip {3} ({3}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #259 Planetarium of Wan Shi Tong  [mythic, status=auto]
- type: Legendary Artifact
- cost: {6}
- oracle: {1}, {T}: Scry 2.
Whenever you scry or surveil, look at the top card of your library. You may cast that card without paying its mana cost. Do this only once each turn. (Look at the card after you scry or surveil.)
- role_features: is_other

### #260 Trusty Boomerang  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {1}
- oracle: Equipped creature has "{1}, {T}: Tap target creature. Return Trusty Boomerang to its owner's hand."
Equip {1} ({1}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #261 The Walls of Ba Sing Se  [mythic, status=auto]
- type: Legendary Artifact Creature — Wall
- cost: {8}
- oracle: Defender
Other permanents you control have indestructible.
- role_features: is_creature

### #262 White Lotus Tile  [mythic, status=auto]
- type: Artifact
- cost: {4}
- oracle: This artifact enters tapped.
{T}: Add X mana of any one color, where X is the greatest number of creatures you control that have a creature type in common.
- role_features: is_other

### #263 Abandoned Air Temple  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped unless you control a basic land.
{T}: Add {W}.
{3}{W}, {T}: Put a +1/+1 counter on each creature you control.
- role_features: is_land

### #264 Agna Qel'a  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped unless you control a basic land.
{T}: Add {U}.
{2}{U}, {T}: Draw a card, then discard a card.
- role_features: is_land, cards_manipulated=1

### #265 Airship Engine Room  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {U} or {R}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #266 Ba Sing Se  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped unless you control a basic land.
{T}: Add {G}.
{2}{G}, {T}: Earthbend 2. Activate only as a sorcery. (Target land you control becomes a 0/0 creature with haste that's still a land. Put two +1/+1 counters on it. When it dies or is exiled, return it to the battlefield tapped.)
- role_features: is_land, creates_creatures: 2/2

### #267 Boiling Rock Prison  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {B} or {R}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #268 Fire Nation Palace  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped unless you control a basic land.
{T}: Add {R}.
{1}{R}, {T}: Target creature you control gains firebending 4 until end of turn. (Whenever it attacks, add {R}{R}{R}{R}. This mana lasts until end of combat.)
- role_features: is_land

### #269 Foggy Bottom Swamp  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {B} or {G}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #270 Jasmine Dragon Tea Shop  [rare, status=auto]
- type: Land
- cost: —
- oracle: {T}: Add {C}.
{T}: Add one mana of any color. Spend this mana only to cast an Ally spell or activate an ability of an Ally source.
{5}, {T}: Create a 1/1 white Ally creature token.
- role_features: is_land, creates_creatures: 1/1 W Ally

### #271 Kyoshi Village  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {G} or {W}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #272 Meditation Pools  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {G} or {U}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #273 Misty Palms Oasis  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {W} or {B}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #274 North Pole Gates  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {W} or {U}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #275 Omashu City  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {R} or {G}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #276 Realm of Koh  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped unless you control a basic land.
{T}: Add {B}.
{3}{B}, {T}: Create a 1/1 colorless Spirit creature token with "This token can't block or be blocked by non-Spirit creatures."
- role_features: is_land, creates_creatures: 1/1  Spirit

### #277 Rumble Arena  [common, status=auto]
- type: Land
- cost: —
- oracle: Vigilance
When this land enters, scry 1. (Look at the top card of your library. You may put it on the bottom.)
{T}: Add {C}.
{1}, {T}: Add one mana of any color.
- role_features: is_land

### #278 Secret Tunnel  [rare, status=auto]
- type: Land — Cave
- cost: —
- oracle: This land can't be blocked.
{T}: Add {C}.
{4}, {T}: Two target creatures you control that share a creature type can't be blocked this turn.
- role_features: is_land

### #279 Serpent's Pass  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {U} or {B}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #280 Sun-Blessed Peak  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {R} or {W}.
{4}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #281 White Lotus Hideout  [uncommon, status=auto]
- type: Land
- cost: —
- oracle: {T}: Add {C}.
{T}: Add one mana of any color. Spend this mana only to cast a Lesson or Shrine spell.
{1}, {T}: Add one mana of any color.
- role_features: is_land

### #bonus-2x2-107 Dockside Extortionist  [mythic, status=auto]
- type: Creature — Goblin Pirate
- cost: {1}{R}
- oracle: When this creature enters, create X Treasure tokens, where X is the number of artifacts and enchantments your opponents control. (Treasure tokens are artifacts with "{T}, Sacrifice this token: Add one mana of any color.")
- role_features: is_creature

### #bonus-2x2-32 Teferi's Protection  [rare, status=needs_llm]
- type: Instant
- cost: {2}{W}
- oracle: Until your next turn, your life total can't change and you gain protection from everything. All permanents you control phase out. (While they're phased out, they're treated as though they don't exist. They phase in before you untap during your untap step.)
Exile Teferi's Protection.
- role_features: is_other

### #bonus-2x2-50 Force of Negation  [rare, status=auto]
- type: Instant
- cost: {1}{U}{U}
- oracle: If it's not your turn, you may exile a blue card from your hand rather than pay this spell's mana cost.
Counter target noncreature spell. If that spell is countered this way, exile it instead of putting it into its owner's graveyard.
- role_features: is_counterspell

### #bonus-2xm-171 Heartbeat of Spring  [rare, status=auto]
- type: Enchantment
- cost: {2}{G}
- oracle: Whenever a player taps a land for mana, that player adds one mana of any type that land produced.
- role_features: is_other

### #bonus-2xm-213 Rhys the Redeemed  [rare, status=auto]
- type: Legendary Creature — Elf Warrior
- cost: {G/W}
- oracle: {2}{G/W}, {T}: Create a 1/1 green and white Elf Warrior creature token.
{4}{G/W}{G/W}, {T}: For each creature token you control, create a token that's a copy of that creature.
- role_features: is_creature, creates_creatures: 1/1 GW Elf/Warrior

### #bonus-8ed-86 Intruder Alarm  [rare, status=needs_llm]
- type: Enchantment
- cost: {2}{U}
- oracle: Creatures don't untap during their controllers' untap steps.
Whenever a creature enters, untap all creatures.
- role_features: is_other

### #bonus-bng-111 Searing Blood  [uncommon, status=auto]
- type: Instant
- cost: {R}{R}
- oracle: Searing Blood deals 2 damage to target creature. When that creature dies this turn, Searing Blood deals 3 damage to the creature's controller.
- role_features: removal_burn_damage=2

### #bonus-bro-233 Cityscape Leveler  [mythic, status=needs_llm]
- type: Artifact Creature — Construct
- cost: {8}
- oracle: Trample
When you cast this spell and whenever this creature attacks, destroy up to one target nonland permanent. Its controller creates a tapped Powerstone token.
Unearth {8}
- role_features: is_creature

### #bonus-c13-54 Prosperity  [uncommon, status=needs_llm]
- type: Sorcery
- cost: {X}{U}
- oracle: Each player draws X cards.
- role_features: is_other

### #bonus-clb-815 Warstorm Surge  [rare, status=auto]
- type: Enchantment
- cost: {5}{R}
- oracle: Whenever a creature you control enters, it deals damage equal to its power to any target.
- role_features: is_other

### #bonus-clu-141 Lightning Bolt  [uncommon, status=auto]
- type: Instant
- cost: {R}
- oracle: Lightning Bolt deals 3 damage to any target.
- role_features: removal_burn_damage=3

### #bonus-cmm-139 Bloodchief Ascension  [rare, status=auto]
- type: Enchantment
- cost: {B}
- oracle: At the beginning of each end step, if an opponent lost 2 or more life this turn, you may put a quest counter on this enchantment. (Damage causes loss of life.)
Whenever a card is put into an opponent's graveyard from anywhere, if this enchantment has three or more quest counters on it, you may have that player lose 2 life. If you do, you gain 2 life.
- role_features: is_other

### #bonus-cmm-236 Insurrection  [mythic, status=auto]
- type: Sorcery
- cost: {5}{R}{R}{R}
- oracle: Untap all creatures and gain control of them until end of turn. They gain haste until end of turn.
- role_features: is_other

### #bonus-cmm-294 The Great Henge  [mythic, status=auto]
- type: Legendary Artifact
- cost: {7}{G}{G}
- oracle: This spell costs {X} less to cast, where X is the greatest power among creatures you control.
{T}: Add {G}{G}. You gain 2 life.
Whenever a nontoken creature you control enters, put a +1/+1 counter on it and draw a card.
- role_features: cards_drawn=1

### #bonus-cmm-295 Heroic Intervention  [rare, status=needs_llm]
- type: Instant
- cost: {1}{G}
- oracle: Permanents you control gain hexproof and indestructible until end of turn.
- role_features: is_other

### #bonus-cmm-77 Bribery  [mythic, status=auto]
- type: Sorcery
- cost: {3}{U}{U}
- oracle: Search target opponent's library for a creature card and put that card onto the battlefield under your control. Then that player shuffles.
- role_features: is_other

### #bonus-cmr-89 Sakashima of a Thousand Faces  [mythic, status=auto]
- type: Legendary Creature — Human Rogue
- cost: {3}{U}
- oracle: You may have Sakashima enter as a copy of another creature you control, except it has Sakashima's other abilities.
The "legend rule" doesn't apply to permanents you control.
Partner (You can have two commanders if both have partner.)
- role_features: is_creature

### #bonus-ddu-30 Treetop Village  [uncommon, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
{T}: Add {G}.
{1}{G}: This land becomes a 3/3 green Ape creature with trample until end of turn. It's still a land. (It can deal excess combat damage to the player or planeswalker it's attacking.)
- role_features: is_land

### #bonus-dmr-244 Dark Depths  [mythic, status=needs_llm]
- type: Legendary Snow Land
- cost: —
- oracle: Dark Depths enters with ten ice counters on it.
{3}: Remove an ice counter from Dark Depths.
When Dark Depths has no ice counters on it, sacrifice it. If you do, create Marit Lage, a legendary 20/20 black Avatar creature token with flying and indestructible.
- role_features: is_land

### #bonus-dmr-59 Mystic Remora  [rare, status=needs_llm]
- type: Enchantment
- cost: {U}
- oracle: Cumulative upkeep {1} (At the beginning of your upkeep, put an age counter on this permanent, then sacrifice it unless you pay its upkeep cost for each age counter on it.)
Whenever an opponent casts a noncreature spell, you may draw a card unless that player pays {4}.
- role_features: cards_drawn=1

### #bonus-dmu-235 Meteorite  [common, status=auto]
- type: Artifact
- cost: {5}
- oracle: When this artifact enters, it deals 2 damage to any target.
{T}: Add one mana of any color.
- role_features: is_mana_rock

### #bonus-dtk-150 Rending Volley  [uncommon, status=needs_llm]
- type: Instant
- cost: {R}
- oracle: This spell can't be countered.
Rending Volley deals 4 damage to target white or blue creature.
- role_features: is_other

### #bonus-ecc-115 Return of the Wildspeaker  [rare, status=needs_llm]
- type: Instant
- cost: {4}{G}
- oracle: Choose one —
• Draw cards equal to the greatest power among non-Human creatures you control.
• Non-Human creatures you control get +3/+3 until end of turn.
- role_features: is_other

### #bonus-ecc-71 Black Sun's Zenith  [rare, status=needs_llm]
- type: Sorcery
- cost: {X}{B}{B}
- oracle: Put X -1/-1 counters on each creature. Shuffle Black Sun's Zenith into its owner's library.
- role_features: is_other

### #bonus-fut-16 Scout's Warning  [rare, status=auto]
- type: Instant
- cost: {W}
- oracle: The next creature card you play this turn can be played as though it had flash.
Draw a card.
- role_features: cards_drawn=1

### #bonus-gpt-75 Shattering Spree  [uncommon, status=needs_llm]
- type: Sorcery
- cost: {R}
- oracle: Replicate {R} (When you cast this spell, copy it for each time you paid its replicate cost. You may choose new targets for the copies.)
Destroy target artifact.
- role_features: is_other

### #bonus-iko-11 Drannith Magistrate  [rare, status=auto]
- type: Creature — Human Wizard
- cost: {1}{W}
- oracle: Your opponents can't cast spells from anywhere other than their hands.
- role_features: is_creature

### #bonus-inr-242 Join the Dance  [uncommon, status=needs_llm]
- type: Sorcery
- cost: {G}{W}
- oracle: Create two 1/1 white Human creature tokens.
Flashback {3}{G}{W} (You may cast this card from your graveyard for its flashback cost. Then exile it.)
- role_features: is_other

### #bonus-inr-69 Imprisoned in the Moon  [common, status=needs_llm]
- type: Enchantment — Aura
- cost: {2}{U}
- oracle: Enchant creature, land, or planeswalker
Enchanted permanent is a colorless land with "{T}: Add {C}" and loses all other card types and abilities.
- role_features: is_other

### #bonus-inv-237 Captain Sisay  [rare, status=auto]
- type: Legendary Creature — Human Soldier
- cost: {2}{G}{W}
- oracle: {T}: Search your library for a legendary card, reveal that card, put it into your hand, then shuffle.
- role_features: is_creature

### #bonus-j22-6 Lita, Mechanical Engineer  [mythic, status=auto]
- type: Legendary Artifact Creature — Artificer
- cost: {2}{W}
- oracle: Vigilance
At the beginning of your end step, untap each other artifact creature you control.
{3}{W}, {T}: Create a 5/5 colorless Vehicle artifact token named Zeppelin with flying and crew 3. (It has "Tap any number of creatures you control with total power 3 or more: This token becomes an artifact creature until end of turn.")
- role_features: is_creature

### #bonus-m12-218 Sundial of the Infinite  [rare, status=auto]
- type: Artifact
- cost: {2}
- oracle: {1}, {T}: End the turn. Activate only during your turn. (Exile all spells and abilities from the stack. Discard down to your maximum hand size. Damage wears off, and "this turn" and "until end of turn" effects end.)
- role_features: is_other

### #bonus-m13-129 Fervor  [rare, status=auto]
- type: Enchantment
- cost: {2}{R}
- oracle: Creatures you control have haste. (They can attack and {T} as soon as they come under your control.)
- role_features: is_other

### #bonus-m14-47 Clone  [rare, status=auto]
- type: Creature — Shapeshifter
- cost: {3}{U}
- oracle: You may have this creature enter as a copy of any creature on the battlefield.
- role_features: is_creature

### #bonus-m20-43 Agent of Treachery  [rare, status=auto]
- type: Creature — Human Rogue
- cost: {5}{U}{U}
- oracle: When this creature enters, gain control of target permanent.
At the beginning of your end step, if you control three or more permanents you don't own, draw three cards.
- role_features: is_creature, cards_drawn=3

### #bonus-m20-9 Brought Back  [rare, status=needs_llm]
- type: Instant
- cost: {W}{W}
- oracle: Choose up to two target permanent cards in your graveyard that were put there from the battlefield this turn. Return them to the battlefield tapped.
- role_features: is_other

### #bonus-mat-9 Training Grounds  [rare, status=needs_llm]
- type: Enchantment
- cost: {U}
- oracle: Activated abilities of creatures you control cost {2} less to activate. This effect can't reduce the mana in that cost to less than one mana.
- role_features: is_other

### #bonus-mh1-197 Eladamri's Call  [rare, status=needs_llm]
- type: Instant
- cost: {G}{W}
- oracle: Search your library for a creature card, reveal that card, put it into your hand, then shuffle.
- role_features: is_other

### #bonus-mh1-247 Sunbaked Canyon  [rare, status=auto]
- type: Land
- cost: —
- oracle: {T}, Pay 1 life: Add {R} or {W}.
{1}, {T}, Sacrifice this land: Draw a card.
- role_features: is_land

### #bonus-mkc-213 Koma, Cosmos Serpent  [mythic, status=auto]
- type: Legendary Creature — Serpent
- cost: {3}{G}{G}{U}{U}
- oracle: This spell can't be countered.
At the beginning of each upkeep, create a 3/3 blue Serpent creature token named Koma's Coil.
Sacrifice another Serpent: Choose one —
• Tap target permanent. Its activated abilities can't be activated this turn.
• Koma gains indestructible until end of turn.
- role_features: is_creature, creates_creatures: 3/3 U Serpent

### #bonus-ncc-215 Clone Legion  [mythic, status=auto]
- type: Sorcery
- cost: {7}{U}{U}
- oracle: For each creature target player controls, create a token that's a copy of that creature.
- role_features: is_other

### #bonus-ncc-283 Beastmaster Ascension  [rare, status=auto]
- type: Enchantment
- cost: {2}{G}
- oracle: Whenever a creature you control attacks, you may put a quest counter on this enchantment.
As long as this enchantment has seven or more quest counters on it, creatures you control get +5/+5.
- role_features: is_other

### #bonus-nec-9 Release to Memory  [rare, status=auto]
- type: Instant
- cost: {3}{W}
- oracle: Exile target opponent's graveyard. For each creature card exiled this way, create a 1/1 colorless Spirit creature token.
- role_features: creates_creatures: 1/1  Spirit

### #bonus-ody-102 Standstill  [uncommon, status=auto]
- type: Enchantment
- cost: {1}{U}
- oracle: When a player casts a spell, sacrifice this enchantment. If you do, each of that player's opponents draws three cards.
- role_features: is_other

### #bonus-ody-329 Tarnished Citadel  [rare, status=auto]
- type: Land
- cost: —
- oracle: {T}: Add {C}.
{T}: Add one mana of any color. This land deals 3 damage to you.
- role_features: is_land

### #bonus-otc-170 Humble Defector  [uncommon, status=auto]
- type: Creature — Human Rogue
- cost: {1}{R}
- oracle: {T}: Draw two cards. Target opponent gains control of this creature. Activate only during your turn.
- role_features: is_creature

### #bonus-pca-13 Three Dreams  [rare, status=auto]
- type: Sorcery
- cost: {4}{W}
- oracle: Search your library for up to three Aura cards with different names, reveal them, put them into your hand, then shuffle.
- role_features: is_other

### #bonus-por-87 Cruel Tutor  [rare, status=needs_llm]
- type: Sorcery
- cost: {2}{B}
- oracle: Search your library for a card, then shuffle and put that card on top. You lose 2 life.
- role_features: is_other

### #bonus-ptk-108 Diaochan, Artful Beauty  [rare, status=auto]
- type: Legendary Creature — Human Advisor
- cost: {3}{R}
- oracle: {T}: Destroy target creature of your choice, then destroy target creature of an opponent's choice. Activate only during your turn, before attackers are declared.
- role_features: is_creature, removal_destroy_or_exile

### #bonus-ptk-152 Taunting Challenge  [rare, status=needs_llm]
- type: Sorcery
- cost: {1}{G}{G}
- oracle: All creatures able to block target creature this turn do so.
- role_features: is_other

### #bonus-ptk-3 Empty City Ruse  [uncommon, status=needs_llm]
- type: Sorcery
- cost: {W}
- oracle: Target opponent skips all combat phases of their next turn.
- role_features: is_other

### #bonus-soc-130 Fabled Passage  [rare, status=auto]
- type: Land
- cost: —
- oracle: {T}, Sacrifice this land: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle. Then if you control four or more lands, untap that land.
- role_features: is_land

### #bonus-soc-238 Blasphemous Act  [rare, status=needs_llm]
- type: Sorcery
- cost: {8}{R}
- oracle: This spell costs {1} less to cast for each creature on the battlefield.
Blasphemous Act deals 13 damage to each creature.
- role_features: is_other

### #bonus-soc-249 Mirrorwing Dragon  [mythic, status=auto]
- type: Creature — Dragon
- cost: {3}{R}{R}
- oracle: Flying
Whenever a player casts an instant or sorcery spell that targets only this creature, that player copies that spell for each other creature they control that the spell could target. Each copy targets a different one of those creatures.
- role_features: is_creature

### #bonus-soc-260 Volcanic Torrent  [uncommon, status=auto]
- type: Sorcery
- cost: {4}{R}
- oracle: Cascade (When you cast this spell, exile cards from the top of your library until you exile a nonland card that costs less. You may cast it without paying its mana cost. Put the exiled cards on the bottom in a random order.)
Volcanic Torrent deals X damage to each creature and planeswalker your opponents control, where X is the number of spells you've cast this turn.
- role_features: is_other

### #bonus-soi-244 Fevered Visions  [rare, status=auto]
- type: Enchantment
- cost: {1}{U}{R}
- oracle: At the beginning of each player's end step, that player draws a card. If the player is your opponent and has four or more cards in hand, this enchantment deals 2 damage to that player.
- role_features: is_other

### #bonus-tdc-191 Noxious Gearhulk  [mythic, status=auto]
- type: Artifact Creature — Construct
- cost: {4}{B}{B}
- oracle: Menace
When this creature enters, you may destroy another target creature. If a creature is destroyed this way, you gain life equal to its toughness.
- role_features: is_creature

### #bonus-tdc-254 Elemental Bond  [uncommon, status=auto]
- type: Enchantment
- cost: {2}{G}
- oracle: Whenever a creature you control with power 3 or greater enters, draw a card.
- role_features: cards_drawn=1

### #bonus-uma-81 Visions of Beyond  [rare, status=auto]
- type: Instant
- cost: {U}
- oracle: Draw a card. If a graveyard has twenty or more cards in it, draw three cards instead.
- role_features: cards_drawn=1

### #bonus-zen-228 Valakut, the Molten Pinnacle  [rare, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
Whenever a Mountain you control enters, if you control at least five other Mountains, you may have this land deal 3 damage to any target.
{T}: Add {R}.
- role_features: is_land

### #bonus-znc-80 Rites of Flourishing  [rare, status=auto]
- type: Enchantment
- cost: {2}{G}
- oracle: At the beginning of each player's draw step, that player draws an additional card.
Each player may play an additional land on each of their turns.
- role_features: is_other
