# How Mulligan Coach Works

Mulligan Coach looks at your opening hand and your decklist and tells you how
likely a good player would be to keep the hand. This document explains where
that number comes from, what the algorithm does and does not take into
account, and the known limitations you should keep in mind when reading its
advice.

## The big picture

When you see a recommendation, two things have just happened behind the
scenes:

1. **Your hand was "goldfished" a couple hundred times.** A Monte Carlo
   simulator plays out the first four turns of the game over and over: it
   shuffles the rest of your deck, draws cards, makes land drops, casts mana
   creatures and ramp spells, cycles and draws where sensible — and records
   how the hand develops. The app runs 200 of these simulated playouts per
   hand — the same number used when training the model, and enough that
   re-running the same hand barely moves the result. From those playouts it
   computes things like "89% chance you make your third land drop on turn 3"
   or "62% chance you can cast a creature on turn 2."

2. **A trained model turned that into a verdict.** A machine-learning model
   (XGBoost) takes the simulation results, plus statistics about your hand
   and deck — including each card's win-rate data from
   [17Lands](https://www.17lands.com) — and predicts: *what is the
   probability that a good player would keep this hand?* The model learned
   that from about 1.4 million real keep/mulligan decisions in 17Lands
   replay data, spanning several recent draft formats. That probability is
   what drives the verdict you see.

The number the app displays is the *mulligan percentage* — 100 minus the
keep-probability. The verdict comes in five bands of that number: below 15%
the hand is a **clear keep**, 15–35% is a **marginal keep**, 35–55% is a grey
**borderline** band where the app deliberately withholds judgement, 55–75% is
a **marginal mulligan**, and above 75% is a **clear mulligan**. The band
boundaries were calibrated against the decisions of elite players
(Diamond/Mythic rank with high win rates).

## What the simulation takes into account

The simulator plays a simplified but fairly faithful version of Magic's early
turns. Specifically, it models:

- **Drawing cards each turn**, including skipping the draw on turn 1 when you
  are on the play. Whether you are on the play or on the draw is part of
  every calculation.
- **Land drops and land types.** Basic lands, duals, lands that enter the
  battlefield tapped (including conditionally, like "tapped unless you
  control two or more other lands"), filter lands that need mana to make
  mana, and fetch-style lands like Evolving Wilds that sacrifice to find a
  basic.
- **Color requirements.** The simulator solves the actual mana puzzle each
  turn: not just "do I have three lands," but "can these specific lands
  produce {1}{W}{W}?"
- **Mana acceleration.** Mana dorks (with summoning sickness respected), 
  mana rocks, and ramp spells like Cultivate
  that put lands onto the battlefield or into your hand.
- **Card draw, cantrips, cycling, and landcycling.** The simulated player
  cycles a card or casts a draw spell when it helps — in particular, if the
  hand has no land, land-fetching effects and landcycling are prioritized to
  find one. Scry and look-at-top effects are played with a simple sensible
  policy (dig for a land when you need one, otherwise dig for castable
  spells).
- **Alternative costs and modes.** A card that can be cast normally *or*
  cycled is tracked both ways — an expensive bomb with landcycling is
  correctly treated as much better in a mana-hungry hand than the same bomb
  without it.
- **Reasonable sequencing.** The land-drop policy plays tapped lands early,
  prioritizes adding missing colors, and generally plays the land that
  maximizes what you can cast next turn. The spell policy casts mana sources
  first, then land-finders, then card draw.

The simulation deliberately does **not** play out combat or model an
opponent. Its job is narrower: measure how smoothly the hand develops —
lands, mana, colors, castable spells turn by turn. Judging whether that
development wins games is the model's job.

## What the model layers on top

The model's input is roughly 200 numbers describing the hand, and about half
of them come straight out of the simulation: the land-drop and expected-mana
numbers above, plus a grid of castability probabilities — P(any creature
castable on turn 2), P(any removal castable by turn 3), P(any 4-drop castable
on turn 4), how much of your hand's colored mana is producible by turn 4, and
so on, turn by turn. The simulation is a large part of what the model judges the hand on.

The rest of the features describe the hand and deck directly:

- **Card quality from 17Lands data.** Each card's opening-hand and
  games-in-hand win rates, statistically "shrunk" so that a card with few
  recorded games isn't over- or under-rated on noise. The model knows whether
  your hand's spells are format all-stars or replacement-level.
- **Hand shape.** Land count, curve, how many cheap creatures, how many
  removal spells, double-pip cards ({W}{W}-style costs), how many colors the
  hand demands.
- **Deck shape.** Your deck's land percentage, curve, removal density, and
  color profile — a 4-land hand reads differently in an 18-land deck than a
  15-land one.
- **Roles.** Counts of removal, combat tricks, equipment, card draw,
  bombs-with-escape-hatches (expensive cards that carry a cycling mode), etc.
- **Context.** On the play or draw, how many mulligans you've already taken,
  and which set and event type you're playing.

The model was trained on real keep/mulligan decisions from 17Lands replay
data — every candidate hand a player saw, whether they kept it or shipped
it — across multiple recent Premier and Traditional Draft formats, about
1.4 million decisions in total. The training set was limited to players with positive win records, so
the number you see means roughly: *given hands that looked like this, how
often did good players keep?* The verdict thresholds on top of
that probability are then calibrated against the choices of elite players
specifically.

## Known shortcomings

These are the gaps not simulated to reduce complexity :

**Mana sources the simulator ignores.**

- **Treasure tokens are not modeled.** A card that makes a Treasure on entry
  is valued through its win-rate statistics, but the simulator does not count
  the Treasure as future mana or color fixing. Hands with early treasure makers
  will be underrated.
- **Restricted mana is ignored.** Lands or creatures that make mana "only to
  cast creature spells" (or similar conditions) are treated as if they made
  no mana at all, rather than modeling the restriction. 

**Card mechanics that are simplified or invisible.**

- **Only the first four turns are simulated** (plus a turn-5 land-drop
  check). Late-game considerations enter only indirectly, through the card
  win-rate statistics.
- **No combat, no opponent, no interaction.** The simulator never blocks,
  never gets attacked, never has a spell countered.
- **Alternative costs paid from other zones** — flashback, foretell, and
  similar — are not played out by the simulator (the card's overall
  statistics still carry that value).
- **Modal cards** (Adventures, modal double-faced cards, "choose one"
  spells) are encoded with their combined abilities, so are likely somewhat overvalued.
- **X spells are treated as X = 1**, a floor for "when is this first worth
  casting."
- **Death triggers and most activated abilities on expensive cards** are not
  part of the mechanical encoding — again, the 17Lands win rates carry most
  of that value implicitly.

**Model-level caveats.**

- **It predicts what players do, not what is provably optimal.** The
  training data is real human decisions, so if players systematically
  misjudge a certain hand type, the model inherits that bias. Two things
  soften this: the weakest players are filtered out of the training data,
  and the verdict bands are calibrated so that "clear" verdicts agree with
  what elite players actually do.
- **New sets start data-poor.** Early in a set 17Lands data will be less reliable due to low sample size. 
- **The simulated player is competent, not perfect.** Land-drop and
  scry/draw decisions follow good general-purpose rules, not exhaustive
  optimal play, so hands that depend on very precise sequencing may be
  slightly undervalued.

**What this means in practice:** trust the clear keeps and clear mulligans —
those bands are right the overwhelming majority of the time (elite players
disagree with a "clear keep" only about 1% of the time). Treat the marginal
bands as a nudge, and treat the borderline band as exactly what it says: a
hand where your own read of the hand should decide.
