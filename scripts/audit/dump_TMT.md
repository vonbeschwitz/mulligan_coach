# Classification audit dump — TMT (210 cards)

### #1 Action News Crew  [common, status=auto]
- type: Creature — Human Citizen
- cost: {1}{W}
- oracle: Vigilance
Channel — {6}, Discard this card: Put a +1/+1 counter on each creature you control. Draw a card.
- role_features: is_creature

### #2 Agent Bishop, Man in Black  [rare, status=auto]
- type: Legendary Creature — Human Soldier
- cost: {2}{W}
- oracle: At the beginning of combat on your turn, put a +1/+1 counter on each of up to two target creatures.
- role_features: is_creature

### #3 April O'Neil, Kunoichi Trainee  [common, status=llm_encoded]
- type: Legendary Creature — Human Ninja
- cost: {1}{W}
- oracle: When April O'Neil enters, scry 2. (Look at the top two cards of your library, then put any number of them on the bottom and the rest on top in any order.)
April O'Neil can't be blocked by creatures with power 3 or greater.
- role_features: is_creature, cards_manipulated=2

### #4 Dimensional Exile  [uncommon, status=llm_encoded]
- type: Enchantment — Aura
- cost: {1}{W}
- oracle: Enchant basic land you control
When this Aura enters, exile target creature an opponent controls until this Aura leaves the battlefield.
- role_features: removal_destroy_or_exile

### #5 East Wind Avatar  [common, status=auto]
- type: Creature — Bird Spirit Avatar
- cost: {3}{W}
- oracle: Flying, vigilance
Alliance — Whenever another creature you control enters, this creature gets +1/+0 until end of turn.
- role_features: is_creature

### #6 Featherbrained Filcher  [uncommon, status=auto]
- type: Creature — Bird Mutant
- cost: {W}
- oracle: Flying
When this creature leaves the battlefield, create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
- role_features: is_creature

### #7 Grounded for Life  [common, status=auto]
- type: Instant
- cost: {4}{W}
- oracle: This spell costs {3} less to cast if it targets a tapped creature.
Destroy target creature.
- role_features: removal_destroy_or_exile

### #8 Hamato Guardian Stance  [common, status=auto]
- type: Instant
- cost: {W}
- oracle: Target creature gets +1/+3 and gains flying until end of turn. Scry 1. (Look at the top card of your library. You may put that card on the bottom.)
- role_features: combat_trick: +1/+3 grants ['flying']

### #9 High-Flying Ace  [common, status=auto]
- type: Creature — Bird Mutant
- cost: {2}{W}
- oracle: Flying
{3}{W}: Target creature without flying gains flying until end of turn. Activate only as a sorcery.
- role_features: is_creature, combat_trick: grants ['flying']

### #10 Jennika, Bad Apple Big Sister  [common, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {4}{W}
- oracle: When Jennika enters, create a 2/2 red Mutant creature token.
Plainscycling {2} ({2}, Discard this card: Search your library for a Plains card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature, creates_creatures: 2/2 R Mutant

### #11 Koya, Death from Above  [uncommon, status=auto]
- type: Legendary Creature — Mutant Ninja Bird
- cost: {2}{W}
- oracle: Flying
When Koya enters, exile up to one other target creature. At the beginning of the next end step, you may pay {3}{B}. If you don't, return that card to the battlefield under its owner's control.
- role_features: is_creature

### #12 The Last Ronin's Technique  [uncommon, status=auto]
- type: Instant
- cost: {3}{W}
- oracle: Sneak {1}{W} (You may cast this spell for {1}{W} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Create three 1/1 white Ninja Turtle Spirit creature tokens. If this spell's sneak cost was paid, they enter tapped and attacking.
- role_features: creates_creatures: 1/1 W Ninja/Turtle/Spirit

### #13 Leader's Talent  [rare, status=auto]
- type: Enchantment — Class
- cost: {1}{W}
- oracle: (Gain the next level as a sorcery to add its ability.)
Whenever you attack, put a +1/+1 counter on target attacking creature.
{2}{W}: Level 2
Whenever a creature you control leaves the battlefield, if it had a counter on it, you gain 2 life.
{3}{W}: Level 3
Whenever you cast a spell, put a +1/+1 counter on each creature you control.
- role_features: is_class

### #14 Leonardo, Big Brother  [common, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{W}
- oracle: Sneak {W} (You may cast this spell for {W} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Leonardo gets +1/+0 for each other creature you control.
- role_features: is_creature

### #15 Leonardo, Cutting Edge  [rare, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{W}
- oracle: Sneak {W} (You may cast this spell for {W} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Lifelink
Whenever you gain life, put a +1/+1 counter on Leonardo.
- role_features: is_creature

### #16 Leonardo, Leader in Blue  [uncommon, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {W}
- oracle: Sneak {3}{W}{W} (You may cast this spell for {3}{W}{W} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
When Leonardo enters, if his sneak cost was paid, creatures you control get +2/+0 until end of turn.
{1}{W}: Leonardo gains first strike until end of turn.
- role_features: is_creature

### #17 Leonardo, Sewer Samurai  [mythic, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle Samurai
- cost: {3}{W}
- oracle: Sneak {2}{W}{W}
Double strike
During your turn, you may cast creature spells with power or toughness 1 or less from your graveyard. If you cast a spell this way, that creature enters with a finality counter on it. (If a creature with a finality counter on it would die, exile it instead.)
- role_features: is_creature

### #18 Leonardo's Technique  [rare, status=auto]
- type: Sorcery
- cost: {3}{W}
- oracle: Sneak {1}{W} (You may cast this spell for {1}{W} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Return one or two target creature cards each with mana value 3 or less from your graveyard to the battlefield.
- role_features: is_other

### #19 Lita, Little Orphan Amphibian  [uncommon, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{W}
- oracle: Alliance — Whenever another creature you control enters, choose one that hasn't been chosen this turn.
• Put a +1/+1 counter on Lita.
• Create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
• Scry 1.
- role_features: is_creature, cards_manipulated=1, creates_creatures: 1/1  Food

### #20 Make Your Move  [common, status=llm_encoded]
- type: Instant
- cost: {2}{W}
- oracle: Destroy target artifact, enchantment, or creature with power 4 or greater.
- role_features: removal_destroy_or_exile

### #21 Mighty Mutanimals  [uncommon, status=auto]
- type: Creature — Mutant Rebel
- cost: {2}{W}{W}
- oracle: When this creature enters, create a 2/2 red Mutant creature token.
Alliance — Whenever another creature you control enters, put a +1/+1 counter on target creature you control.
- role_features: is_creature, creates_creatures: 2/2 R Mutant

### #22 Prehistoric Pet  [rare, status=auto]
- type: Creature — Dinosaur Ninja
- cost: {W}
- oracle: This creature can't be blocked by creatures with greater power.
{1}{W}, {T}: Return another target creature you control to its owner's hand. Activate only during your turn.
- role_features: is_creature

### #23 Quintessential Katana  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {W}
- oracle: Equipped creature gets +1/+1 and has "Whenever this creature deals combat damage, untap it and you gain 2 life."
Whenever a Ninja you control enters, you may attach this Equipment to it.
Equip {2} ({2}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #24 Sally Pride, Lioness Leader  [rare, status=auto]
- type: Legendary Creature — Cat Mutant Rebel
- cost: {3}{W}{W}
- oracle: When Sally Pride enters, create X 2/2 red Mutant creature tokens, where X is the number of nontoken creatures you control.
Whenever Sally Pride attacks, put a +1/+1 counter on each creature you control.
- role_features: is_creature

### #25 Triceraton Commander  [mythic, status=auto]
- type: Creature — Dinosaur Soldier
- cost: {X}{X}{W}{W}
- oracle: Flying
Whenever this creature attacks, Dinosaurs you control other than this creature get +1/+1 and gain flying until end of turn.
When this creature enters, create X 2/2 white Dinosaur Soldier creature tokens.
- role_features: is_creature

### #26 Turncoat Kunoichi  [rare, status=llm_encoded]
- type: Creature — Mutant Ninja Fox
- cost: {2}{W}
- oracle: Sneak {2}{W}{B} (You may cast this spell for {2}{W}{B} if you also return an unblocked attacker you control to hand during the declare blockers step. It enters tapped and attacking.)
When this creature enters, choose target creature an opponent controls. Exile that creature until this creature leaves the battlefield. If this creature's sneak cost was paid, instead exile the chosen creature.
- role_features: is_creature, removal_destroy_or_exile

### #27 Turtles Forever  [rare, status=auto]
- type: Instant
- cost: {3}{W}
- oracle: Search your library and/or outside the game for exactly four legendary creature cards you own with different names, then reveal those cards. An opponent chooses two of them. Put the chosen cards into your hand and shuffle the rest into your library.
- role_features: is_other

### #28 Uneasy Alliance  [common, status=auto]
- type: Enchantment — Aura
- cost: {1}{W}
- oracle: Enchant creature
Enchanted creature can't attack or block.
{5}, Sacrifice this Aura: Exile enchanted creature. You create a 1/1 black Ninja creature token. Activate only as a sorcery.
- role_features: is_removal_aura, creates_creatures: 1/1 B Ninja | 1/1 B Ninja

### #29 April O'Neil, Hacktivist  [rare, status=auto]
- type: Legendary Creature — Human Scientist
- cost: {3}{U}
- oracle: At the beginning of your end step, draw a card for each card type among spells you've cast this turn.
- role_features: is_creature, cards_drawn=1

### #30 April, Reporter of the Weird  [uncommon, status=auto]
- type: Legendary Creature — Human Detective
- cost: {2}{U}
- oracle: Whenever April deals combat damage to a player, draw that many cards, then discard a card.
- role_features: is_creature

### #31 Bespoke Bō  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {2}{U}
- oracle: When this Equipment enters, return up to one other target nonland permanent to its owner's hand.
Equipped creature gets +2/+1 and has vigilance.
Equip {3} ({3}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #32 Buzz Bots  [common, status=auto]
- type: Artifact Creature — Robot Insect
- cost: {1}{U}
- oracle: Flying, vigilance
When this creature dies, draw a card.
- role_features: is_creature, cards_drawn=1

### #33 Crustacean Commando  [common, status=auto]
- type: Creature — Crab Mutant Soldier
- cost: {1}{U}
- oracle: When this creature enters, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #34 Does Machines  [rare, status=auto]
- type: Enchantment — Class
- cost: {1}{U}
- oracle: (Gain the next level as a sorcery to add its ability.)
When this Class enters, mill two cards, draw two cards, then discard two cards.
{1}{U}: Level 2
When this Class becomes level 2, return up to two target artifact cards from your graveyard to your hand.
{4}{U}: Level 3
At the beginning of combat on your turn, put three +1/+1 counters on target artifact you control. If it isn't a creature, it becomes a 0/0 Robot creature in addition to its other types.
- role_features: is_class, cards_drawn=2

### #35 Donatello, Gadget Master  [rare, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{U}
- oracle: Sneak {1}{U} (You may cast this spell for {1}{U} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Whenever Donatello deals combat damage to a player, create a token that's a copy of target artifact you control.
- role_features: is_creature

### #36 Donatello, Mutant Mechanic  [mythic, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {3}{U}
- oracle: {T}: Put three +1/+1 counters on target artifact you control. If it isn't a creature, it becomes a 0/0 Robot creature in addition to its other types. Activate only as a sorcery.
Whenever an artifact you control is put into a graveyard from the battlefield, if it had counters on it, put those counters on up to one target artifact or creature you control.
- role_features: is_creature

### #37 Donatello, Turtle Techie  [common, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {3}{U}
- oracle: When Donatello enters, if you control an artifact, draw a card.
- role_features: is_creature, cards_drawn=1

### #38 Donatello, Way with Machines  [uncommon, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{U}
- oracle: Flying
Whenever an artifact you control enters, put a +1/+1 counter on Donatello.
- role_features: is_creature

### #39 Donatello's Technique  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {2}{U}
- oracle: Sneak {U} (You may cast this spell for {U} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Draw two cards.
- role_features: cards_drawn=2

### #40 Fugitive Droid  [uncommon, status=auto]
- type: Artifact Creature — Robot Scientist
- cost: {U}
- oracle: This creature can't be blocked if an artifact entered the battlefield under your control this turn.
{U}, Sacrifice this creature: Counter target spell that targets an artifact or creature you control.
- role_features: is_creature, is_counterspell

### #41 Kitsune, Dragon's Daughter  [rare, status=auto]
- type: Legendary Creature — Fox Warlock Avatar
- cost: {4}{U}{U}
- oracle: Vigilance
Whenever Kitsune enters or deals combat damage to a player, you may exchange control of two other target creatures controlled by different players.
- role_features: is_creature

### #42 Kitsune's Technique  [rare, status=auto]
- type: Instant
- cost: {4}{U}{U}
- oracle: Sneak {1}{U} (You may cast this spell for {1}{U} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Target opponent mills half their library, rounded up.
- role_features: is_other

### #43 Krang, Master Mind  [rare, status=llm_encoded]
- type: Legendary Artifact Creature — Utrom Warrior
- cost: {6}{U}{U}
- oracle: Affinity for artifacts (This spell costs {1} less to cast for each artifact you control.)
When Krang enters, if you have fewer than four cards in hand, draw cards equal to the difference.
Krang gets +1/+0 for each other artifact you control.
- role_features: is_creature

### #44 Metalhead  [uncommon, status=auto]
- type: Legendary Artifact Creature — Robot Turtle
- cost: {4}{U}
- oracle: When Metalhead enters, return up to one other target artifact or creature to its owner's hand.
{R}, Sacrifice another artifact: Put a +1/+1 counter on Metalhead. He gains menace and haste until end of turn.
- role_features: is_creature

### #45 Mind Transfer Protocol  [common, status=auto]
- type: Instant
- cost: {2}{U}
- oracle: Until end of turn, target artifact or creature becomes an artifact creature with base power and toughness 4/5.
Draw a card.
- role_features: cards_drawn=1

### #46 Mondo Gecko  [mythic, status=auto]
- type: Legendary Creature — Lizard Mutant
- cost: {1}{U}{U}
- oracle: {1}, Discard a card: Until end of turn, Mondo Gecko becomes the color of your choice and gains hexproof from that color.
Whenever Mondo Gecko deals combat damage to a player, draw a card for each color among permanents you control.
- role_features: is_creature, cards_drawn=1

### #47 Negate  [common, status=auto]
- type: Instant
- cost: {1}{U}
- oracle: Counter target noncreature spell.
- role_features: is_counterspell

### #48 Ooze Spill  [uncommon, status=auto]
- type: Instant
- cost: {1}{U}{U}
- oracle: Counter target spell. Create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_counterspell

### #49 Ray Fillet, Man Ray  [uncommon, status=auto]
- type: Legendary Creature — Fish Mutant
- cost: {3}{U}
- oracle: Flying
When Ray Fillet enters, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
{2}, Remove a +1/+1 counter from a creature you control: Draw a card.
- role_features: is_creature, cards_drawn=1

### #50 Renet, Temporal Apprentice  [rare, status=auto]
- type: Legendary Creature — Human Wizard
- cost: {3}{U}{U}
- oracle: Flash
When Renet enters, return each other nonland permanent that entered this turn to its owner's hand.
- role_features: is_creature

### #51 Retro-Mutation  [common, status=llm_encoded]
- type: Enchantment — Aura
- cost: {2}{U}
- oracle: Flash
Enchant creature
Enchanted creature is a Turtle with base power and toughness 0/1. It can't attack and loses all abilities. (It also loses all other creature types.)
- role_features: is_removal_aura

### #52 Return to the Sewers  [common, status=auto]
- type: Instant
- cost: {3}{U}
- oracle: Target creature's owner puts it on their choice of the top or bottom of their library. You create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_other

### #53 Sewer-veillance Cam  [common, status=auto]
- type: Artifact
- cost: {U}
- oracle: Flash
When this artifact enters or leaves the battlefield, you may tap or untap target creature.
{3}{U}, Sacrifice this artifact: Draw two cards.
- role_features: is_other

### #54 Stockman, Mad Fly-entist  [common, status=auto]
- type: Legendary Creature — Insect Mutant Scientist
- cost: {4}{U}
- oracle: Flying
When Stockman enters, draw a card, then discard a card.
Islandcycling {2} ({2}, Discard this card: Search your library for an Island card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature, cards_manipulated=1

### #55 Turtles in Time  [mythic, status=auto]
- type: Sorcery
- cost: {5}{U}{U}
- oracle: Return all creatures to their owners' hands. Each player may shuffle their hand and graveyard into their library, then each player who does draws seven cards.
Exile Turtles in Time.
- role_features: is_bounce

### #56 Utrom Scientists  [common, status=auto]
- type: Artifact Creature — Utrom Robot Scientist
- cost: {2}{U}
- oracle: When this creature enters, tap up to one target creature and put a stun counter on it. (If a permanent with a stun counter would become untapped, remove one from it instead.)
- role_features: is_creature

### #57 Anchovy & Banana Pizza  [common, status=auto]
- type: Artifact — Food
- cost: {2}{B}{B}
- oracle: When this artifact enters, destroy target creature.
{2}, {T}, Sacrifice this artifact: You gain 3 life.
- role_features: is_other

### #58 Armaggon, Future Shark  [rare, status=auto]
- type: Legendary Creature — Shark Horror Mutant
- cost: {6}{B}{B}
- oracle: Flash
When Armaggon enters, destroy up to three target creatures.
- role_features: is_creature

### #59 Bebop, Warthog Warrior  [common, status=auto]
- type: Legendary Creature — Boar Mutant Warrior
- cost: {4}{B}
- oracle: Menace (This creature can't be blocked except by two or more creatures.)
Rhinos you control have menace.
Swampcycling {2} ({2}, Discard this card: Search your library for a Swamp card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #60 The Cloning of Shredder  [mythic, status=auto]
- type: Enchantment — Saga
- cost: {4}{B}{B}
- oracle: (As this Saga enters and after your draw step, add a lore counter. Sacrifice after III.)
I — Exile target creature card from your graveyard. Create a token that's a copy of it, except it isn't legendary and is a Mutant in addition to its other types.
II, III — Create a token that's a copy of a card exiled with this Saga, except it isn't legendary and is a Mutant in addition to its other types.
- role_features: is_saga

### #61 Death in the Family  [uncommon, status=auto]
- type: Instant
- cost: {1}{B}
- oracle: Exile target creature with mana value 3 or less.
- role_features: removal_destroy_or_exile

### #62 Dream Beavers  [uncommon, status=auto]
- type: Creature — Beaver Nightmare
- cost: {B}
- oracle: Flying
When this creature enters, each opponent loses 1 life and you gain 1 life. Scry 1. (Look at the top card of your library. You may put that card on the bottom.)
- role_features: is_creature

### #63 Foot Mystic  [common, status=auto]
- type: Creature — Human Ninja Warlock
- cost: {3}{B}
- oracle: Lifelink
Disappear — When this creature enters, if a permanent left the battlefield under your control this turn, create a 1/1 black Ninja creature token.
- role_features: is_creature, creates_creatures: 1/1 B Ninja

### #64 Insectoid Exterminator  [common, status=llm_encoded]
- type: Creature — Insect Mutant
- cost: {2}{B}
- oracle: Flying
Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, scry 1. (Look at the top card of your library. You may put that card on the bottom.)
- role_features: is_creature, cards_manipulated=1

### #65 Lord Dregg, Insect Invader  [uncommon, status=auto]
- type: Legendary Creature — Insect Warrior
- cost: {3}{B}
- oracle: Flying
Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, create a 1/1 black Insect Warrior creature token with flying.
{3}{G}, Sacrifice a token: Draw a card.
- role_features: is_creature, cards_drawn=1, creates_creatures: 1/1 B Insect/Warrior

### #66 Madame Null, Power Broker  [rare, status=auto]
- type: Legendary Creature — Demon Advisor
- cost: {2}{B}
- oracle: Deathtouch
Whenever another creature you control enters, you may pay life equal to its power. If you do, put that many +1/+1 counters on it.
- role_features: is_creature

### #67 Ninja Teen  [rare, status=auto]
- type: Enchantment — Class
- cost: {2}{B}
- oracle: (Gain the next level as a sorcery to add its ability.)
Whenever a creature you control leaves the battlefield, each opponent loses 1 life.
{1}{B}: Level 2
Creatures you control get +1/+0 and have menace.
{B}: Level 3
Creature cards in your graveyard have sneak {3}{B}.
You may cast creature spells from your graveyard using their sneak abilities.
- role_features: is_class

### #68 Oroku Saki, Shredder Rising  [common, status=llm_encoded]
- type: Legendary Creature — Human Ninja
- cost: {2}{B}
- oracle: Sneak {1}{B} (You may cast this spell for {1}{B} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Whenever Oroku Saki deals combat damage to a player, you draw a card and lose 1 life.
- role_features: is_creature, cards_drawn=1

### #69 Pain 101  [common, status=auto]
- type: Instant
- cost: {1}{B}
- oracle: Until end of turn, target creature gains deathtouch and "When this creature dies, return it to the battlefield tapped under its owner's control."
- role_features: combat_trick: grants ['deathtouch']

### #70 Paramecia Coloniex  [uncommon, status=auto]
- type: Creature — Zombie Worm
- cost: {1}{B}
- oracle: When this creature enters, mill three cards. (Put the top three cards of your library into your graveyard.)
When this creature dies, you may exile it. When you do, put target creature card from your graveyard on top of your library.
- role_features: is_creature

### #71 Rat King, Verminister  [rare, status=llm_encoded]
- type: Legendary Creature — Rat Avatar
- cost: {1}{B}
- oracle: Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, create a 1/1 black Rat creature token and put a +1/+1 counter on Rat King.
{T}, Sacrifice three Rats: Return target creature card and all other cards with the same name as that card from your graveyard to the battlefield tapped.
- role_features: is_creature, creates_creatures: 1/1 B Rat

### #72 Savanti Romero, Time's Exile  [rare, status=auto]
- type: Legendary Creature — Demon Wizard
- cost: {3}{B}{B}
- oracle: Trample
At the beginning of combat on your turn, put a +1/+1 counter on Savanti Romero. Then you draw X cards and lose X life, where X is the number of counters on Savanti Romero.
- role_features: is_creature

### #73 Shark Shredder, Killer Clone  [rare, status=auto]
- type: Legendary Creature — Shark Octopus Ninja
- cost: {2}{B}{B}
- oracle: Sneak {3}{B}{B}
First strike
Whenever Shark Shredder deals combat damage to a player, put up to one target creature card from that player's graveyard onto the battlefield under your control. It enters tapped and attacking that player.
- role_features: is_creature

### #74 Shredder, Unrelenting  [uncommon, status=auto]
- type: Legendary Creature — Human Ninja
- cost: {4}{B}
- oracle: Sneak {3}{B} (You may cast this spell for {3}{B} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Deathtouch
Whenever Shredder enters or attacks, another target creature you control gains deathtouch until end of turn.
- role_features: is_creature

### #75 Shredder's Armor  [uncommon, status=llm_encoded]
- type: Artifact — Equipment
- cost: {1}{B}
- oracle: Equipped creature gets +2/+1.
When this Equipment enters, attach it to target creature you control.
Equip—Sacrifice another nonland permanent. Activate only once each turn.
- role_features: is_equipment

### #76 Shredder's Revenge  [common, status=llm_encoded]
- type: Sorcery
- cost: {2}{B}
- oracle: Choose one —
• Target player discards two cards.
• Target player draws two cards and loses 2 life.
- role_features: is_other

### #77 Shredder's Technique  [uncommon, status=auto]
- type: Sorcery
- cost: {2}{B}
- oracle: Sneak {B} (You may cast this spell for {B} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Destroy target creature or enchantment. If an enchantment was destroyed this way, you lose 2 life.
- role_features: removal_destroy_or_exile

### #78 South Wind Avatar  [rare, status=auto]
- type: Creature — Snake Spirit Avatar
- cost: {3}{B}
- oracle: Deathtouch
Whenever another creature you control dies, you gain life equal to its toughness.
Whenever you gain life, each opponent loses 1 life.
- role_features: is_creature

### #79 Splinter, Hamato Yoshi  [uncommon, status=auto]
- type: Legendary Creature — Mutant Ninja Rat
- cost: {1}{B}
- oracle: Sneak {B} (You may cast this spell for {B} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Menace (This creature can't be blocked except by two or more creatures.)
Other Ninjas you control get +1/+1.
- role_features: is_creature

### #80 Splinter's Technique  [rare, status=auto]
- type: Sorcery
- cost: {3}{B}
- oracle: Sneak {1}{B} (You may cast this spell for {1}{B} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Search your library for a card, put that card into your hand, then shuffle.
- role_features: is_other

### #81 Squirrelanoids  [common, status=auto]
- type: Creature — Squirrel Mutant
- cost: {B}
- oracle: Deathtouch
- role_features: is_creature

### #82 Stomped by the Foot  [common, status=llm_encoded]
- type: Instant
- cost: {1}{B}
- oracle: Kicker—Sacrifice an artifact or creature. (You may sacrifice an artifact or creature in addition to any other costs as you cast this spell.)
Target creature gets -2/-2 until end of turn. If this spell was kicked, that creature gets -5/-5 until end of turn instead.
- role_features: removal_destroy_or_exile

### #83 Super Shredder  [mythic, status=auto]
- type: Legendary Creature — Mutant Ninja Human
- cost: {1}{B}
- oracle: Menace
Whenever another permanent leaves the battlefield, put a +1/+1 counter on Super Shredder.
- role_features: is_creature

### #84 Tunnel Rats  [common, status=auto]
- type: Creature — Rat
- cost: {1}{B}
- oracle: {4}{B}: Return this card from your graveyard to the battlefield tapped.
- role_features: is_creature

### #85 Bot Bashing Time  [common, status=auto]
- type: Sorcery
- cost: {3}{R}
- oracle: Bot Bashing Time deals 6 damage to target creature. If that creature would die this turn, exile it instead.
- role_features: removal_burn_damage=6

### #86 Broadcast Takeover  [mythic, status=auto]
- type: Sorcery
- cost: {2}{R}{R}{R}
- oracle: Gain control of all artifacts your opponents control until end of turn. Untap them. They gain haste until end of turn.
- role_features: is_other

### #87 Casey Jones, Jury-Rig Justiciar  [uncommon, status=auto]
- type: Legendary Creature — Human Berserker
- cost: {1}{R}
- oracle: Haste
When Casey Jones enters, look at the top four cards of your library. You may reveal an artifact card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.
- role_features: is_creature

### #88 Casey Jones, Vigilante  [rare, status=auto]
- type: Legendary Creature — Human Berserker
- cost: {1}{R}{R}
- oracle: When Casey Jones enters, draw three cards. At the beginning of your next upkeep, discard three cards at random.
- role_features: is_creature, cards_drawn=3

### #89 Cool but Rude  [rare, status=auto]
- type: Enchantment — Class
- cost: {1}{R}
- oracle: (Gain the next level as a sorcery to add its ability.)
Whenever you attack, you may discard a card. If you do, draw a card.
{1}{R}: Level 2
Whenever you discard a card, this Class deals 2 damage to each opponent.
{1}{R}: Level 3
When this Class becomes level 3, search your library for a card, put it into your hand, shuffle, then discard a card at random.
- role_features: is_class, cards_drawn=1

### #90 General Traag, Heart of Stone  [uncommon, status=auto]
- type: Legendary Artifact Creature — Elemental Soldier
- cost: {3}{R}{R}
- oracle: Trample
When General Traag enters, you may sacrifice another artifact. When you do, General Traag deals 4 damage to target creature.
- role_features: is_creature

### #91 Hard-Won Jitte  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {1}{R}
- oracle: Equipped creature has double strike.
Equip {2} ({2}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #92 Improvised Arsenal  [rare, status=auto]
- type: Artifact — Equipment
- cost: {1}{R}
- oracle: Equipped creature gets +1/+0 for each artifact you control.
{4}{R}: Create a token that's a copy of this Equipment.
Equip {R}
- role_features: is_equipment

### #93 Jennika's Technique  [uncommon, status=llm_encoded]
- type: Instant
- cost: {2}{R}
- oracle: Sneak {R} (You may cast this spell for {R} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Jennika's Technique deals 2 damage to each creature.
- role_features: removal_burn_damage=2

### #94 Manhole Missile  [common, status=auto]
- type: Instant
- cost: {1}{R}
- oracle: Manhole Missile deals 3 damage to target creature. You may put a card from your hand on the bottom of your library. If you do, draw a card.
- role_features: removal_burn_damage=3

### #95 Mouser Attack!  [common, status=llm_encoded]
- type: Instant
- cost: {1}{R}
- oracle: Choose one —
• Create a 1/1 colorless Robot artifact creature token.
• Target creature gets +3/+0 and gains first strike until end of turn.
- role_features: combat_trick: +3/+0 grants ['first strike'], creates_creatures: 1/1  Robot

### #96 Mouser Foundry  [common, status=auto]
- type: Artifact
- cost: {1}{R}
- oracle: When this artifact enters or leaves the battlefield, create a 1/1 colorless Robot artifact creature token.
{4}{R}, Sacrifice this artifact: It deals 3 damage to target creature.
- role_features: creates_creatures: 1/1  Robot

### #97 Mutant Town Musicians  [common, status=auto]
- type: Creature — Mutant Bard Performer
- cost: {2}{R}
- oracle: Trample
Alliance — Whenever another creature you control enters, this creature gets +1/+0 until end of turn.
- role_features: is_creature

### #98 Null Group Biological Assets  [common, status=auto]
- type: Creature — Mutant Mercenary
- cost: {2}{R}
- oracle: During your turn, this creature has first strike.
Whenever this creature attacks, you may discard a card. If you do, draw a card.
- role_features: is_creature, cards_drawn=1

### #99 Old Hob, Alleycat Blues  [uncommon, status=auto]
- type: Legendary Creature — Cat Mutant Rebel
- cost: {4}{R}
- oracle: At the beginning of combat on your turn, create a 2/2 red Mutant creature token. It gains haste until end of turn. Destroy it at the beginning of the next end step.
{1}{W}: Target attacking creature token gains indestructible until end of turn.
- role_features: is_creature, creates_creatures: 2/2 R Mutant

### #100 Purple Dragon Punks  [common, status=auto]
- type: Creature — Human Rogue
- cost: {1}{R}
- oracle: {T}: Add {R}. Spend this mana only to cast an artifact spell or to activate an ability.
- role_features: is_creature

### #101 Raphael, Most Attitude  [uncommon, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {3}{R}
- oracle: Menace (This creature can't be blocked except by two or more creatures.)
Alliance — Whenever another creature you control enters, you may exile the top card of your library.
Whenever Raphael attacks, until end of turn, you may play a card exiled with Raphael.
- role_features: is_creature

### #102 Raphael, Ninja Destroyer  [mythic, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{R}{R}
- oracle: Raphael must be blocked if able.
Enrage — Whenever Raphael is dealt damage, add that much {R}. Until end of turn, you don't lose this mana as steps and phases end.
- role_features: is_creature

### #103 Raphael, the Nightwatcher  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{R}{R}
- oracle: Sneak {1}{R}{R} (You may cast this spell for {1}{R}{R} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Attacking creatures you control have double strike.
- role_features: is_creature

### #104 Raphael, Tough Turtle  [common, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{R}
- oracle: Alliance — Whenever another creature you control enters, Raphael deals 1 damage to target opponent.
- role_features: is_creature

### #105 Raphael's Technique  [rare, status=auto]
- type: Instant
- cost: {4}{R}{R}
- oracle: Sneak {2}{R} (You may cast this spell for {2}{R} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Each player may discard their hand and draw seven cards.
- role_features: cards_drawn=7

### #106 Ravenous Robots  [rare, status=auto]
- type: Artifact Creature — Robot
- cost: {1}{R}
- oracle: Whenever you cast an artifact spell, create a 1/1 colorless Robot artifact creature token.
{R}, {T}: Creature tokens you control gain haste until end of turn.
- role_features: is_creature, creates_creatures: 1/1  Robot

### #107 Rock Soldiers  [common, status=auto]
- type: Artifact Creature — Elemental Soldier
- cost: {3}{R}
- oracle: When this creature enters, destroy up to one target noncreature artifact.
- role_features: is_creature

### #108 Slash, Reptile Rampager  [rare, status=auto]
- type: Legendary Creature — Mutant Berserker Turtle
- cost: {3}{R}{R}
- oracle: Alliance — Whenever another creature you control enters, Slash deals 2 damage to each opponent.
Whenever Slash attacks, create a 2/2 red Mutant creature token.
- role_features: is_creature, creates_creatures: 2/2 R Mutant

### #109 Spicy Oatmeal Pizza  [uncommon, status=auto]
- type: Artifact — Food
- cost: {2}{R}
- oracle: When this artifact enters, it deals 4 damage to any target and 3 damage to you.
{2}, {T}, Sacrifice this artifact: You gain 3 life.
- role_features: is_other

### #110 Wingnut, Bat on the Belfry  [uncommon, status=auto]
- type: Legendary Creature — Bat Mutant
- cost: {1}{R}
- oracle: Alliance — Whenever another creature you control enters, Wingnut gains your choice of flying, menace, or haste until end of turn.
Whenever Wingnut attacks, each other attacking creature gets +1/+0 until end of turn.
- role_features: is_creature

### #111 Zog, Triceraton Castaway  [common, status=auto]
- type: Legendary Creature — Dinosaur Soldier
- cost: {4}{R}
- oracle: Reach, trample
When Zog enters, target creature can't block this turn.
Mountaincycling {2} ({2}, Discard this card: Search your library for a Mountain card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #112 Courier of Comestibles  [uncommon, status=auto]
- type: Creature — Human Citizen
- cost: {1}{G}
- oracle: When this creature enters, you may search your library for a Food card, reveal it, put it into your hand, then shuffle. If you don't put a card into your hand this way, create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
- role_features: is_creature

### #113 Cowabunga!  [common, status=llm_encoded]
- type: Sorcery
- cost: {G}
- oracle: Look at the top four cards of your library. You may reveal a Mutant, Ninja, Turtle, or land card from among them and put it into your hand. Put the rest on the bottom of your library in a random order.
- role_features: cards_drawn=1, cards_manipulated=3

### #114 Frog Butler  [common, status=auto]
- type: Creature — Frog Spirit
- cost: {1}{G}
- oracle: Deathtouch
{T}: Add one mana of any color.
{2}: This creature gains reach until end of turn.
- role_features: is_creature

### #115 Groundchuck & Dirtbag  [rare, status=auto]
- type: Legendary Creature — Ox Mole Mutant
- cost: {4}{G}{G}
- oracle: Trample
Whenever you tap a land for mana, add {G}.
- role_features: is_creature

### #116 Guac & Marshmallow Pizza  [common, status=auto]
- type: Artifact — Food
- cost: {G}
- oracle: Flash
When this artifact enters, target creature gets +2/+2 until end of turn. Untap it.
{2}, {T}, Sacrifice this artifact: You gain 3 life.
- role_features: is_other

### #117 Leatherhead, Swamp Stalker  [rare, status=auto]
- type: Legendary Creature — Crocodile Mutant Rogue
- cost: {2}{G}{G}
- oracle: Trample
Leatherhead enters with a hexproof counter on her.
Whenever Leatherhead deals combat damage to a player, you may remove a counter from her. When you do, destroy target artifact or enchantment that player controls.
- role_features: is_creature

### #118 Michelangelo, Game Master  [common, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{G}
- oracle: Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, put a +1/+1 counter on Michelangelo.
- role_features: is_creature

### #119 Michelangelo, Improviser  [mythic, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {3}{G}
- oracle: Sneak {2}{G}{G} (You may cast this spell for {2}{G}{G} if you also return an unblocked attacker you control to hand during the declare blockers step. He enters tapped and attacking.)
Whenever Michelangelo deals combat damage to a player, you may put a creature card and/or a land card from your hand onto the battlefield.
- role_features: is_creature

### #120 Michelangelo, Mutant BFF  [uncommon, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{G}{G}
- oracle: Each creature you control with a counter on it can't be blocked by more than one creature.
Whenever Michelangelo enters or attacks, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #121 Michelangelo, Weirdness to 11  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{G}
- oracle: When Michelangelo enters, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
If one or more +1/+1 counters would be put on a creature you control, that many plus one +1/+1 counters are put on it instead.
- role_features: is_creature

### #122 Michelangelo's Technique  [rare, status=auto]
- type: Sorcery
- cost: {4}{G}
- oracle: Sneak {3}{G} (You may cast this spell for {3}{G} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Look at the top eight cards of your library. Put up to two creature cards with total mana value 6 or less from among them onto the battlefield and the rest on the bottom of your library in a random order.
- role_features: is_other

### #123 Mona Lisa, Science Geek  [uncommon, status=auto]
- type: Legendary Creature — Lizard Mutant
- cost: {2}{G}
- oracle: Reach
{T}: Add X mana of any one color, where X is Mona Lisa's power.
- role_features: is_creature

### #124 Mutagen Man, Living Ooze  [rare, status=llm_encoded]
- type: Legendary Creature — Ooze Mutant
- cost: {X}{G}{G}
- oracle: Trample
Activated abilities of artifact tokens you control cost {1} less to activate.
When Mutagen Man enters, create X Mutagen tokens. (They're artifacts with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #125 Mutant Chain Reaction  [common, status=llm_encoded]
- type: Sorcery
- cost: {2}{G}
- oracle: Destroy up to one target artifact, enchantment, or creature with flying. Create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: removal_destroy_or_exile

### #126 New Generation's Technique  [uncommon, status=auto]
- type: Sorcery
- cost: {3}{G}
- oracle: Sneak {2}{G} (You may cast this spell for {2}{G} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Search your library for up to two basic land cards, put them onto the battlefield tapped, then shuffle.
- role_features: is_other

### #127 Novel Nunchaku  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {2}{G}
- oracle: When this Equipment enters, attach it to target creature you control. When you do, equipped creature fights up to one target creature an opponent controls. (Each deals damage equal to its power to the other.)
Equipped creature gets +1/+1 and has trample.
Equip {3} ({3}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #128 Party Dude  [rare, status=auto]
- type: Enchantment — Class
- cost: {G}
- oracle: (Gain the next level as a sorcery to add its ability.)
When this Class enters, each player creates a Food token.
{1}{G}: Level 2
Whenever an artifact an opponent controls is put into a graveyard from the battlefield, draw a card.
{4}{G}: Level 3
Whenever one or more of your opponents are attacked, up to one target attacking creature gets +X/+X until end of turn, where X is the number of cards in your hand.
- role_features: is_class, cards_drawn=1

### #129 Primordial Pachyderm  [common, status=auto]
- type: Creature — Elephant Avatar
- cost: {3}{G}
- oracle: Reach, trample
When this creature enters, you gain 2 life.
- role_features: is_creature

### #130 Ragamuffin Raptor  [common, status=auto]
- type: Creature — Dinosaur
- cost: {4}{G}
- oracle: When this creature enters, return up to one target creature or Food card from your graveyard to your hand.
- role_features: is_creature

### #131 Rocksteady, Crash Courser  [common, status=auto]
- type: Legendary Creature — Rhino Mutant
- cost: {4}{G}{G}
- oracle: Rocksteady can't be blocked by more than one creature.
Boars you control can't be blocked by more than one creature.
Forestcycling {2} ({2}, Discard this card: Search your library for a Forest card, reveal it, put it into your hand, then shuffle.)
- role_features: is_creature

### #132 Saved by the Shell  [uncommon, status=auto]
- type: Instant
- cost: {1}{G}
- oracle: This spell costs {1} less to cast if you control a Turtle.
Put a +1/+1 counter on target creature you control. It gains trample, hexproof, and indestructible until end of turn.
- role_features: is_other

### #133 Tenderize  [common, status=auto]
- type: Instant
- cost: {1}{G}
- oracle: Target creature you control deals damage equal to its power to target creature an opponent controls.
- role_features: is_other

### #134 Transdimensional Bovine  [rare, status=auto]
- type: Creature — Ox Avatar
- cost: {2}{G}
- oracle: Flying
{T}: Add two mana of any one color.
- role_features: is_creature

### #135 Turtle Power!  [rare, status=llm_encoded]
- type: Enchantment
- cost: {2}{G}
- oracle: Flash
Turtles you control get +2/+2.
- role_features: is_other

### #136 Venus, Torn Between Worlds  [uncommon, status=auto]
- type: Legendary Creature — Mutant Frog Turtle
- cost: {4}{G}
- oracle: Whenever Venus is dealt damage, put that many +1/+1 counters on her. (She must survive the damage to get the counters.)
Whenever a creature you control with a counter on it deals combat damage to a player, you may pay {U}. If you do, draw a card.
- role_features: is_creature, cards_drawn=1

### #137 West Wind Avatar  [uncommon, status=auto]
- type: Creature — Cat Spirit Avatar
- cost: {5}{G}{G}
- oracle: Trample
Whenever this creature enters or attacks, you may sacrifice a token or a land. If you do, you gain 3 life.
Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, draw a card.
- role_features: is_creature, cards_drawn=1

### #138 Zoo Escapees  [common, status=auto]
- type: Creature — Boar Rhino
- cost: {1}{G}
- oracle: When this creature leaves the battlefield, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #139 Baxter Stockman  [uncommon, status=auto]
- type: Legendary Creature — Human Scientist
- cost: {3}{U}{R}
- oracle: When Baxter Stockman enters, create a 1/1 colorless Robot artifact creature token.
At the beginning of combat on your turn, target artifact creature you control gets +3/+0 and gains first strike and vigilance until end of turn.
- role_features: is_creature, creates_creatures: 1/1  Robot

### #140 Bebop & Rocksteady  [rare, status=auto]
- type: Legendary Creature — Boar Rhino Mutant
- cost: {1}{B/G}{B/G}
- oracle: Whenever Bebop & Rocksteady attack or block, sacrifice a permanent unless you discard a card.
- role_features: is_creature

### #141 Brilliance Unleashed  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {4}{U}{R}
- oracle: Choose one or both —
• Brilliance Unleashed deals 5 damage to target creature.
• Choose target artifact card in your graveyard. Return it to the battlefield if it's an artifact creature card. Otherwise, return it to the battlefield and it's a 3/3 Robot artifact creature with flying.
- role_features: removal_burn_damage=5

### #142 Dark Leo & Shredder  [mythic, status=llm_encoded]
- type: Legendary Creature — Mutant Ninja Turtle Human
- cost: {W}{B}
- oracle: Sneak {W}{B}
Attacking Ninjas you control have deathtouch.
Whenever Dark Leo & Shredder deal combat damage to a player, create a 1/1 black Ninja creature token. Then if you control five or more Ninjas, that player loses half their life, rounded up.
- role_features: is_creature, creates_creatures: 1/1 B Ninja

### #143 Don & Leo, Problem Solvers  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {3}{W/U}{W/U}
- oracle: Vigilance
At the beginning of your end step, exile up to one target artifact you control and up to one target creature you control. Then return them to the battlefield under their owners' control.
- role_features: is_creature

### #144 Don & Raph, Hard Science  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{U/R}{U/R}
- oracle: Menace
Whenever Don & Raph attack, the next noncreature spell you cast this turn has affinity for artifacts. (It costs {1} less to cast for each artifact you control.)
- role_features: is_creature

### #145 EPF Point Squad  [common, status=auto]
- type: Creature — Human Soldier
- cost: {1}{R/W}{R/W}
- oracle: Alliance — Whenever another creature you control enters, put a +1/+1 counter on this creature.
- role_features: is_creature

### #146 Foot Elite  [common, status=auto]
- type: Creature — Human Ninja
- cost: {2}{W/B}
- oracle: Whenever this creature attacks, another target creature you control gets +1/+0 and gains indestructible until end of turn. (Damage and effects that say "destroy" don't destroy it.)
- role_features: is_creature

### #147 Foot Ninjas  [common, status=auto]
- type: Creature — Human Ninja
- cost: {4}{W/B}{W/B}
- oracle: Sneak {3}{W/B} (You may cast this spell for {3}{W/B} if you also return an unblocked attacker you control to hand during the declare blockers step. It enters tapped and attacking.)
When this creature enters, you gain 3 life.
- role_features: is_creature

### #148 Genghis Frog  [uncommon, status=auto]
- type: Legendary Creature — Frog Mutant Rogue
- cost: {G}{U}
- oracle: Trample
Whenever Genghis Frog or another Mutant you control enters, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #149 Go Ninja Go  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {R}{W}
- oracle: Choose one or both —
• Exile target creature you control, then return it to the battlefield under its owner's control.
• Go Ninja Go deals damage equal to the greatest power among creatures you control to target creature an opponent controls.
- role_features: is_punch_fight

### #150 Ice Cream Kitty  [common, status=auto]
- type: Artifact Creature — Food Cat Mutant
- cost: {1}{B/G}
- oracle: {2}, Sacrifice another creature or token: Draw a card. Activate only as a sorcery.
{2}, {T}, Sacrifice this creature: You gain 3 life.
- role_features: is_creature, cards_drawn=1

### #151 Karai, Future of the Foot  [uncommon, status=auto]
- type: Legendary Creature — Human Ninja
- cost: {1}{W}{B}
- oracle: Sneak {2}{W}{B} (You may cast this spell for {2}{W}{B} if you also return an unblocked attacker you control to hand during the declare blockers step. She enters tapped and attacking.)
Whenever Karai deals combat damage to a player, return target creature card from your graveyard to your hand. If her sneak cost was paid this turn, instead return that card to the battlefield.
- role_features: is_creature

### #152 Karai's Technique  [uncommon, status=llm_encoded]
- type: Sorcery
- cost: {1}{W}{B}
- oracle: Sneak {W}{B} (You may cast this spell for {W}{B} if you also return an unblocked attacker you control to hand during the declare blockers step.)
Choose one or both —
• Target creature gets +3/+3 until end of turn.
• Target creature gets -3/-3 until end of turn.
- role_features: combat_trick: +3/+3

### #153 Krang & Shredder  [rare, status=auto]
- type: Legendary Creature — Utrom Human Ninja
- cost: {4}{U/B}{U/B}
- oracle: Whenever Krang & Shredder enter or attack, each opponent exiles cards from the top of their library until they exile a nonland card.
Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, you may cast a card exiled with Krang & Shredder without paying its mana cost.
- role_features: is_creature

### #154 The Last Ronin  [mythic, status=auto]
- type: Enchantment — Saga
- cost: {4}{B}{G}
- oracle: (As this Saga enters and after your draw step, add a lore counter. Sacrifice after III.)
I — Destroy all creatures.
II — Mill four cards. When you do, return target creature card from your graveyard to your hand.
III — Whenever a creature you control attacks alone this turn, put three +1/+1 counters on it. It gains trample, lifelink, and indestructible until end of turn.
- role_features: is_saga

### #155 Lessons from Life  [uncommon, status=auto]
- type: Sorcery
- cost: {2}{G}{U}
- oracle: Draw three cards. You may put a land card from your hand onto the battlefield tapped.
- role_features: cards_drawn=3

### #156 Mechanized Ninja Cavalry  [common, status=auto]
- type: Artifact Creature — Robot Ninja
- cost: {1}{R/W}
- oracle: When this creature enters, create a 1/1 colorless Robot artifact creature token.
- role_features: is_creature, creates_creatures: 1/1  Robot

### #157 Mikey & Don, Party Planners  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {2}{G/U}{G/U}
- oracle: Ward {2}
You may look at the top card of your library any time.
You may play lands and cast Mutant, Ninja, or Turtle spells from the top of your library. If you cast a creature spell this way, that creature enters with an additional +1/+1 counter on it.
- role_features: is_creature

### #158 Mikey & Leo, Chaos & Order  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {G/W}{G/W}
- oracle: Whenever you put a counter on a creature you control, draw a card. This ability triggers only once each turn.
- role_features: is_creature, cards_drawn=1

### #159 Mouser Mark III  [common, status=auto]
- type: Artifact Creature — Robot
- cost: {1}{U/R}
- oracle: This creature can't attack unless you control another artifact.
- role_features: is_creature

### #160 The Neutrinos  [uncommon, status=auto]
- type: Legendary Creature — Elf Rebel
- cost: {2}{R}{W}
- oracle: Flying
Alliance — Whenever another creature you control enters, The Neutrinos get +1/+0 until end of turn.
Whenever The Neutrinos attack, exile up to one target creature you own, then return it to the battlefield under your control tapped and attacking.
- role_features: is_creature

### #161 Nobody  [common, status=auto]
- type: Artifact Creature — Human Hero
- cost: {1}{U/R}{U/R}
- oracle: When this creature enters, return up to one other target artifact you control to its owner's hand. Scry 1. (Look at the top card of your library. You may put that card on the bottom.)
- role_features: is_creature

### #162 North Wind Avatar  [mythic, status=auto]
- type: Creature — Dragon Spirit Avatar
- cost: {2}{U}{U}{R}
- oracle: Flying
When this creature enters, if you cast it, you may put a card you own from outside the game into your hand.
- role_features: is_creature

### #163 Pizza Face, Gastromancer  [uncommon, status=auto]
- type: Legendary Artifact Creature — Food Mutant
- cost: {3}{B}{G}
- oracle: When Pizza Face enters, create a Food token.
Disappear — At the beginning of your end step, if a permanent left the battlefield under your control this turn, put three +1/+1 counters on up to one other target artifact or creature. If it isn't a creature, it becomes a 0/0 Mutant creature in addition to its other types.
{10}, {T}, Sacrifice Pizza Face: You gain 15 life.
- role_features: is_creature

### #164 Punk Frogs  [common, status=auto]
- type: Creature — Frog Mutant Rebel
- cost: {3}{G/U}{G/U}
- oracle: Ward {3} (Whenever this creature becomes the target of a spell or ability an opponent controls, counter it unless that player pays {3}.)
- role_features: is_creature

### #165 Putrid Pals  [common, status=auto]
- type: Creature — Human Ooze Mutant
- cost: {2}{B/G}{B/G}
- oracle: Deathtouch
Disappear — This creature enters with two +1/+1 counters on it if a permanent left the battlefield under your control this turn.
- role_features: is_creature

### #166 Raph & Leo, Sibling Rivals  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {1}{R/W}{R/W}
- oracle: Whenever Raph & Leo attack, if it's the first combat phase of the turn, untap one or two target attacking creatures. After this phase, there is an additional combat phase.
- role_features: is_creature

### #167 Raph & Mikey, Troublemakers  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Turtle
- cost: {5}{R/G}{R/G}
- oracle: Trample, haste
Whenever Raph & Mikey attack, reveal cards from the top of your library until you reveal a creature card. Put that card onto the battlefield tapped and attacking and the rest on the bottom of your library in a random order.
- role_features: is_creature

### #168 Slithering Cryptid  [common, status=auto]
- type: Creature — Fish Mutant
- cost: {2}{G/U}
- oracle: When this creature enters, create a Mutagen token. (It's an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
- role_features: is_creature

### #169 Splinter, Radical Rat  [rare, status=auto]
- type: Legendary Creature — Mutant Ninja Rat
- cost: {1}{W/B}{W/B}
- oracle: If a triggered ability of a Ninja creature you control triggers, that ability triggers an additional time.
{1}{U}: Target Ninja can't be blocked this turn.
- role_features: is_creature

### #170 Tainted Treats  [uncommon, status=auto]
- type: Instant
- cost: {1}{B}{G}
- oracle: Destroy target artifact or creature. If its mana value was 4 or less, create a Food token. (It's an artifact with "{2}, {T}, Sacrifice this token: You gain 3 life.")
- role_features: is_other

### #171 Tokka & Rahzar, Terrible Twos  [rare, status=auto]
- type: Legendary Creature — Turtle Wolf Mutant
- cost: {B/R}{B/R}
- oracle: This spell can't be countered.
Menace
Whenever a player casts a spell, if the amount of mana spent to cast it was less than its mana value, Tokka & Rahzar deal 3 damage to that player.
- role_features: is_creature

### #172 Chrome Dome  [rare, status=auto]
- type: Artifact Creature — Robot Ninja
- cost: {2}
- oracle: Other artifact creatures you control get +1/+0.
{5}: Create a token that's a copy of another target artifact you control. That token gains haste. Sacrifice it at the beginning of the next end step.
- role_features: is_creature

### #173 Everything Pizza  [uncommon, status=auto]
- type: Artifact — Food
- cost: {2}
- oracle: When this artifact enters, search your library for a basic land card, reveal it, put it into your hand, then shuffle.
{2}{W}{U}{B}{R}{G}, {T}, Sacrifice this artifact: Target player gains 3 life and draws a card. Each of your opponents discards a card. This artifact deals 3 damage to any target. Put three +1/+1 counters on up to one target creature.
- role_features: is_other

### #174 Henchbots  [uncommon, status=auto]
- type: Artifact Creature — Robot
- cost: {4}
- oracle: When this creature enters, exile target tapped creature an opponent controls until this creature leaves the battlefield.
- role_features: is_creature, removal_destroy_or_exile

### #175 Krang, Utrom Warlord  [mythic, status=auto]
- type: Legendary Artifact Creature — Utrom Robot
- cost: {9}
- oracle: Flying, trample, indestructible, haste
Other artifact creatures you control have flying, trample, indestructible, and haste.
- role_features: is_creature

### #176 Omni-Cheese Pizza  [common, status=auto]
- type: Artifact — Food
- cost: {2}
- oracle: When this artifact enters, draw a card.
{1}, {T}, Sacrifice this artifact: Add one mana of any color.
{2}, {T}, Sacrifice this artifact: You gain 3 life.
- role_features: cards_drawn=1

### #177 The Ooze  [rare, status=auto]
- type: Legendary Artifact
- cost: {2}
- oracle: Whenever a creature you control with a +1/+1 counter on it leaves the battlefield, create a Mutagen token for each +1/+1 counter on it. (A Mutagen token is an artifact with "{1}, {T}, Sacrifice this token: Put a +1/+1 counter on target creature. Activate only as a sorcery.")
{T}: Exile target card from a graveyard. Create a Mutagen token.
- role_features: is_other

### #178 Skateboard  [uncommon, status=auto]
- type: Artifact — Equipment
- cost: {1}
- oracle: When this Equipment enters, tap target permanent.
Equipped creature gets +1/+0 and has haste.
Equip {1} ({1}: Attach to target creature you control. Equip only as a sorcery.)
- role_features: is_equipment

### #179 Technodrome  [mythic, status=auto]
- type: Artifact Creature — Construct
- cost: {2}
- oracle: Reach, trample
This creature can't attack or block unless its power is 6 or greater.
{T}, Sacrifice another artifact: Draw a card. Put a +1/+1 counter on this creature.
- role_features: is_creature

### #180 Turtle Blimp  [uncommon, status=auto]
- type: Artifact — Vehicle
- cost: {5}
- oracle: Flying
When this Vehicle enters, create a 2/2 red Mutant creature token.
Crew 2 (Tap any number of creatures you control with total power 2 or more: This Vehicle becomes an artifact creature until end of turn.)
- role_features: is_vehicle, creates_creatures: 2/2 R Mutant | 2/2 R Mutant

### #181 Turtle Van  [rare, status=auto]
- type: Artifact — Vehicle
- cost: {3}
- oracle: Whenever this Vehicle attacks, put a +1/+1 counter on target creature that crewed it this turn. Then if that creature is a Mutant, Ninja, or Turtle, double the number of +1/+1 counters on it.
Crew 1 (Tap any number of creatures you control with total power 1 or more: This Vehicle becomes an artifact creature until end of turn.)
- role_features: is_vehicle

### #182 Weather Maker  [rare, status=auto]
- type: Artifact
- cost: {3}
- oracle: Landfall — Whenever a land you control enters, put a charge counter on this artifact.
{T}: Add one mana of any color.
{T}, Remove two charge counters from this artifact: Add {C}{C}.
{T}, Remove three charge counters from this artifact: It deals 3 damage to any target.
- role_features: is_mana_rock

### #183 Dimension X  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
When this land enters, you gain 1 life.
{T}: Add {R} or {W}.
- role_features: is_land

### #184 Escape Tunnel  [common, status=llm_encoded]
- type: Land
- cost: —
- oracle: {T}, Sacrifice this land: Search your library for a basic land card, put it onto the battlefield tapped, then shuffle.
{T}, Sacrifice this land: Target creature with power 2 or less can't be blocked this turn.
- role_features: is_land

### #185 Foot Headquarters  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
When this land enters, you gain 1 life.
{T}: Add {W} or {B}.
- role_features: is_land

### #186 Illegitimate Business  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
When this land enters, you gain 1 life.
{T}: Add {B} or {G}.
- role_features: is_land

### #187 Mutant Town  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
When this land enters, you gain 1 life.
{T}: Add {G} or {U}.
- role_features: is_land

### #188 Northampton Farm  [rare, status=auto]
- type: Land
- cost: —
- oracle: {T}: Add {C}.
{1}, {T}: Exile target creature you own.
{2}, {T}, Sacrifice this land: Return a creature card exiled with this land to the battlefield under your control. Return each other card exiled with this land to its owner's hand.
- role_features: is_land, removal_destroy_or_exile

### #189 TCRI Building  [common, status=auto]
- type: Land
- cost: —
- oracle: This land enters tapped.
When this land enters, you gain 1 life.
{T}: Add {U} or {R}.
- role_features: is_land

### #190 Turtle Lair  [uncommon, status=auto]
- type: Land
- cost: —
- oracle: {T}: Add {C}.
{T}: Add one mana of any color. Spend this mana only to cast a Ninja or Turtle spell.
{3}, {T}: Target Ninja or Turtle can't be blocked this turn.
- role_features: is_land

### #bonus-2x2-302 Conqueror's Flail  [rare, status=auto]
- type: Artifact — Equipment
- cost: {2}
- oracle: Equipped creature gets +1/+1 for each color among permanents you control.
As long as this Equipment is attached to a creature, your opponents can't cast spells during your turn.
Equip {2}
- role_features: is_equipment

### #bonus-afr-39 Teleportation Circle  [rare, status=auto]
- type: Enchantment
- cost: {3}{W}
- oracle: At the beginning of your end step, exile up to one target artifact or creature you control, then return that card to the battlefield under its owner's control.
- role_features: is_other

### #bonus-bok-163 Umezawa's Jitte  [rare, status=needs_llm]
- type: Legendary Artifact — Equipment
- cost: {2}
- oracle: Whenever equipped creature deals combat damage, put two charge counters on Umezawa's Jitte.
Remove a charge counter from Umezawa's Jitte: Choose one —
• Equipped creature gets +2/+2 until end of turn.
• Target creature gets -1/-1 until end of turn.
• You gain 2 life.
Equip {2}
- role_features: is_equipment

### #bonus-dis-23 Cytoplast Manipulator  [rare, status=auto]
- type: Creature — Human Wizard Mutant
- cost: {2}{U}{U}
- oracle: Graft 2 (This creature enters with two +1/+1 counters on it. Whenever another creature enters, you may move a +1/+1 counter from this creature onto it.)
{U}, {T}: Gain control of target creature with a +1/+1 counter on it for as long as this creature remains on the battlefield.
- role_features: is_creature

### #bonus-dsc-113 Brainstorm  [common, status=auto]
- type: Instant
- cost: {U}
- oracle: Draw three cards, then put two cards from your hand on top of your library in any order.
- role_features: cards_drawn=3

### #bonus-eve-148 Waves of Aggression  [rare, status=needs_llm]
- type: Sorcery
- cost: {3}{R/W}{R/W}
- oracle: Untap all creatures that attacked this turn. After this main phase, there is an additional combat phase followed by an additional main phase.
Retrace (You may cast this card from your graveyard by discarding a land card in addition to paying its other costs.)
- role_features: is_other

### #bonus-fdn-216 Doubling Season  [mythic, status=auto]
- type: Enchantment
- cost: {4}{G}
- oracle: If an effect would create one or more tokens under your control, it creates twice that many of those tokens instead.
If an effect would put one or more counters on a permanent you control, it puts twice that many of those counters on that permanent instead.
- role_features: is_other

### #bonus-inr-268 Metallic Mimic  [rare, status=needs_llm]
- type: Artifact Creature — Shapeshifter
- cost: {2}
- oracle: As this creature enters, choose a creature type.
This creature is the chosen type in addition to its other types.
Each other creature you control of the chosen type enters with an additional +1/+1 counter on it.
- role_features: is_creature

### #bonus-j22-19 Ashcoat of the Shadow Swarm  [mythic, status=auto]
- type: Legendary Creature — Rat Warlock
- cost: {3}{B}
- oracle: Whenever Ashcoat attacks or blocks, other Rats you control get +X/+X until end of turn, where X is the number of Rats you control.
At the beginning of your end step, you may mill four cards. If you do, return up to two Rat creature cards from your graveyard to your hand. (To mill a card, put the top card of your library into your graveyard.)
- role_features: is_creature

### #bonus-mh1-228 Sword of Sinew and Steel  [mythic, status=auto]
- type: Artifact — Equipment
- cost: {3}
- oracle: Equipped creature gets +2/+2 and has protection from black and from red.
Whenever equipped creature deals combat damage to a player, destroy up to one target planeswalker and up to one target artifact.
Equip {2}
- role_features: is_equipment

### #bonus-mkc-15 Trouble in Pairs  [rare, status=auto]
- type: Enchantment
- cost: {2}{W}{W}
- oracle: If an opponent would begin an extra turn, that player skips that turn instead.
Whenever an opponent attacks you with two or more creatures, draws their second card each turn, or casts their second spell each turn, you draw a card.
- role_features: cards_drawn=1

### #bonus-mkm-270 Undercity Sewers  [rare, status=auto]
- type: Land — Island Swamp
- cost: —
- oracle: ({T}: Add {U} or {B}.)
This land enters tapped.
When this land enters, surveil 1. (Look at the top card of your library. You may put it into your graveyard.)
- role_features: is_land

### #bonus-mma-198 Arcbound Ravager  [rare, status=needs_llm]
- type: Artifact Creature — Beast
- cost: {2}
- oracle: Sacrifice an artifact: Put a +1/+1 counter on this creature.
Modular 1 (This creature enters with a +1/+1 counter on it. When it dies, you may put its +1/+1 counters on target artifact creature.)
- role_features: is_creature

### #bonus-one-118 All Will Be One  [mythic, status=auto]
- type: Enchantment
- cost: {3}{R}{R}
- oracle: Whenever you put one or more counters on a permanent or player, this enchantment deals that much damage to target opponent, creature an opponent controls, or planeswalker an opponent controls.
- role_features: is_other

### #bonus-rix-115 Silverclad Ferocidons  [rare, status=auto]
- type: Creature — Dinosaur
- cost: {5}{R}{R}
- oracle: Enrage — Whenever this creature is dealt damage, each opponent sacrifices a permanent of their choice.
- role_features: is_creature

### #bonus-rvr-217 Rhythm of the Wild  [uncommon, status=needs_llm]
- type: Enchantment
- cost: {1}{R}{G}
- oracle: Creature spells you control can't be countered.
Nontoken creatures you control have riot. (They enter with your choice of a +1/+1 counter or haste.)
- role_features: is_other

### #bonus-shm-73 Plague of Vermin  [rare, status=auto]
- type: Sorcery
- cost: {6}{B}
- oracle: Starting with you, each player may pay any amount of life. Repeat this process until no one pays life. Each player creates a 1/1 black Rat creature token for each 1 life they paid this way.
- role_features: is_other

### #bonus-soc-159 Path to Exile  [uncommon, status=auto]
- type: Instant
- cost: {W}
- oracle: Exile target creature. Its controller may search their library for a basic land card, put that card onto the battlefield tapped, then shuffle.
- role_features: removal_destroy_or_exile

### #bonus-thb-161 Underworld Breach  [rare, status=needs_llm]
- type: Enchantment
- cost: {1}{R}
- oracle: Each nonland card in your graveyard has escape. The escape cost is equal to the card's mana cost plus exile three other cards from your graveyard. (You may cast cards from your graveyard for their escape cost.)
At the beginning of the end step, sacrifice this enchantment.
- role_features: is_other

### #bonus-thb-236 Shadowspear  [rare, status=auto]
- type: Legendary Artifact — Equipment
- cost: {1}
- oracle: Equipped creature gets +1/+1 and has trample and lifelink.
{1}: Permanents your opponents control lose hexproof and indestructible until end of turn.
Equip {2}
- role_features: is_equipment
