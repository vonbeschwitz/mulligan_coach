# MSH commons + uncommons encoding recheck (pre-release audit)

Working through all 94 MSH commons (done) and 107 uncommons
alphabetically, judging each against
`packages/cards/CARD_ENCODING_GUIDE.md`.
Source: `data/processed/parsed_cards/MSH.json` (334 cards total).

## Uncommons batch 1 (2026-07-07): cards 1–20

**19 clean, 1 fixed.** The parser-hardening rounds clearly paid off —
most would-be findings (recurring-trigger draws on Agent Maria Hill /
Attuma / Beast / Colleen Wing, expensive power-up and land
activations, teamwork encodes) were already correct.

Clean highlights worth a note:
* Arnim Zola (#86) — his token activation is graveyard-gated
  ("two or more creature cards in your graveyard"), so no body per
  §9's assume-condition-not-met; the "tapped" token phrasing also
  doesn't match the token regex, same as HYDRA Troopers.
* Beast Within (#bonus) — the 3/3 Beast goes to the *target's*
  controller (usually the opponent), so correctly no
  creates_creatures.
* Bullseye (#209) — ETB burn-2 credited (discard-a-nonland additional
  cost is reliably payable per §9); the repeatable {3},{T} version
  correctly adds nothing.

Fixed, then **overruled by the owner**: **Black Panther, Vanguard
(#207)** — I had credited the 1/1 Soldier from his "whenever another
nontoken Hero enters, choose one —" trigger per the Sokka precedent.
Owner ruling: a trigger the card can't generate BY ITSELF ("whenever
ANOTHER … enters/leaves") doesn't count. Reverted, and the same
ruling cleared **Simulacrum Synthesizer** (MSH bonus mythic) and
**Suki, Courageous Rescuer** (TLA rare — one more card of train/serve
drift until the next retrain). Self-generated triggers keep their
tokens: Sokka, Madame Masque, Ant-Man Colony Commander, Crescent
Island Temple (own ETB counts itself). Rule codified in guide §4;
applied by `apply_other_trigger_token_ruling_20260707.py` (which
supersedes `apply_msh_unc_batch1_fix_20260707.py`).

**2026-07-07 follow-up — ETB-only trigger ruling.** The owner
generalized the Black Panther/Sokka discussion: triggered abilities
credit role_features only from the permanent's OWN entry (compound
"enters or transforms/leaves/attacks" forms count their guaranteed ETB
half); attack/cast/upkeep/counter/landfall triggers and death triggers
(without a self-sac outlet) credit nothing. Enforced in the parser
(`_is_self_etb_trigger`; `_is_self_etb` now accepts any "this <word>"
subject) and rerun across all five sets: 30 auto cards corrected, 10
llm_encoded/judgment cards patched by
`apply_etb_only_trigger_ruling_20260707.py` (incl. Madame Masque's
token — her connive loot stays — and Bitterblossom, cleared under the
strict reading and flagged for a possible time-based-engine
carve-out). Guide §4 rewritten accordingly.

Next uncommons batch starts at #21 Dark Deed.

Status: **COMPLETE — all 94 commons checked (batches 1–6), all fixes
applied (2026-07-07).** Totals: 76 clean, 18 fixed (plus the parser
hardening the findings drove and one out-of-batch rider, Rancor).
The uncommons/rares have NOT had an equivalent pass; the flash-trick
and dropped-aura-grant scans below already cover the full set.

## Batch 6 (2026-07-07): cards 76–94 (final)

### Clean (15 of 19)

Sundering Growth (#bonus — non-creature removal → is_other; populate
needs an existing token, assume absent per §9), Surveillance Room
(#274 — land ETB surveil credited per the Rumble Arena precedent),
Trickster's Stratagem (#81 — tripwire encode held), Ultron Drone
(#253), Undercover Skrull (#194 — any-color dork; conditional pump
static ignored), Unliving Legionnaire (#119), Vibranium Energy
Daggers (#254), Vision of Love (#158 — Abandon Attachments net-1
precedent; the discard option makes the cost reliably payable, unlike
Deadly Dispute), Visions of Villainy (#120), Volcanic Villain (#159),
Wakandan Drone Flock (#40 — ETB scry 2 wired), Wakandan Royal Guard
(#195 — counters not modelled), We Say Thee Nay! (#82 — soft counter
= is_counterspell, Mana Leak precedent), Web Up (#41 — O-Ring-style
exile = removal, Dimensional Exile precedent; got its flag in the
round-2 ETB wiring), Widow's Bite (#122 — §16 reference card).

### Fixed (4 of 19 + 1 rider) — `apply_msh_batch6_fixes_20260707.py`

1. **Take Up the Shield (#39)** — the exact Saved by the Shell
   precedent (§3): counter + lifelink/indestructible EOT on an
   instant → `combat_trick 1/1 + ['lifelink', 'indestructible']`.
   Was sitting at `is_other`.
2. **Super Suit (#78)** — flash Equipment, ETB auto-attach (+1/+2,
   untap) → `combat_trick 1/2` per the owner-confirmed flash ruling
   (now codified in §3).
3. **Super Speed (#154)** — two fixes: the aura's static grant is
   *haste* (parser had captured the ETB line's temporary first
   strike into the static field), and as a flash pump aura it also
   gets `combat_trick 1/0 + ['first strike']`.
4. **Super Strength (#189)** — dropped "has trample and ward {1}"
   tail restored → `aura_pump_granted_keywords=['trample','ward']`.
5. *(rider)* **Rancor (#bonus-2x2-156, uncommon)** — same
   dropped-grant shape found by the same scan; `['trample']`.

Parser note (not fixed): the aura branch's granted-keywords capture
misses "gets +N/+M and has X and Y" tails and can grab keywords from
the wrong line (Super Speed). Three MSH cards + Rancor were affected;
all hand-fixed. Teach `_parse_aura` the "and has <keywords>" tail if
this recurs in the next set.

## Batches 4–5 (2026-07-07): cards 46–75

### Clean (27 of 30)

Straightforward and correct: Kingpin's Enforcers (#102 — sac-gated
activated draw correctly uncredited per §2, mode kept for the sim),
Knight of Wundagore (#175), Kree Commandos (#19), Kree Sentinel
(#141), Lightning Strike (#142), Machinesmith Automaton (#144),
Murdock's Crusade (#24 — §12 teamwork-modal exile), Ninja of the Hand
(#108), Panther Pounce (#29 — investigate ignored per the Deduce
precedent), Pet Avengers (#178 — 7-mana power-up token uncredited per
the cmc≤3 gate), Powerful Broker (#179), Raft Security Officer (#33),
Rapid Rescue (#181 — §16 mill-as-LookAtTop), Red Room Recruit (#110 —
tripwire loot encode), Repulsor Blast (#150 — tripwire burn-5 encode),
Restorative Technique (#183 — FetchLandEffect), Roxxon Brutes (#113),
S.H.I.E.L.D. Deployment Drone (#73), S.H.I.E.L.D. Spy Kit (#36 —
recurring scry correctly uncredited), Savage Land Dinosaur (#185),
Serpent Specialist (#186), Stark Industries Executive (#153 —
Treasure token not encoded per §4), and the four tapped duals
(Los Diablos Missile Base, Pym Technologies, Stark Industries,
Subterranean Cavern).

Noted, no change: **Project Deathlok Soldier (#109)** — its
"{2}{B}: Return this card from your graveyard to your hand" is a
graveyard-resident activation encoded as a generic activated Mode.
The mode is inert (noop effect, no rf credit — the Stone Docent
conservatism already holds), so it's left as parsed; if a sim
consumer ever starts using activated modes by zone, revisit.

### Fixed (3 of 30) — `apply_msh_batch45_fixes_20260707.py`

1. **I Am Iron Man (#58)** — "becomes a 4/4 with flying until end of
   turn" beside the draw is a combat trick per the Quandrix Charm
   base-P/T differential precedent (§12): `combat_trick 2/2` +
   `granted_keywords=['flying']`; draw unchanged.
2. **K'un-Lun Warrior (#140)** — ETB "sacrifice an artifact or
   discard a card: draw a card" had the loot *credited*
   (`cards_manipulated=1`) but not *wired*; §2 wires
   `DrawCardsEffect(1) + DiscardCardEffect(1)` on the cast mode
   (discard is reliably payable per §9).
3. **Stolen Stark Tech (#114)** — flash Equipment, ETB auto-attach +
   indestructible until end of turn. §3's flash rule extended to a
   flash equipment whose ETB is functionally "flash in to save a
   blocker": `combat_trick 1/0` + `['indestructible']`.
   *Judgment call — §3 literally says "flash creatures"; overrule if
   you'd rather keep the combat-trick field creature-only.*

## Batch 3 (2026-07-07): cards 31–45

### Clean (12 of 15)

| Card | Verdict |
|---|---|
| Giant-Sized Flying Ant (#56) | ETB tap/untap modal — neither mode maps to a role_features field; flash creature without ETB pump stays plain (§3). |
| Go Nuts! (#168) | Teamwork modal: sorcery counter (is_other per §7) / fight → is_punch_fight (§12). |
| Guerrilla Gorilla (#169) | Sac-gated noncreature-artifact/enchantment destroy stays off per §1/§10 (Curious Farm Animals shape). |
| H.E.R.B.I.E. Scout Unit (#247) | ETB draw wired; the "put a land from hand onto the battlefield tapped" rider has no effect kind — unmodelled, and at 4 MV only the T4 land drop could care. Revisit only if a cheap version ships. |
| HULK SMASH! (#135) | Teamwork modal: artifact destroy (no flag) / punch → is_punch_fight. |
| HYDRA Assault Robot (#137) | Recurring face-damage ping — no removal_burn_damage (creature damage only, §1). |
| HYDRA Infiltration (#100) | Opponent discard has no role_features field; is_other. |
| HYDRA Troopers (#101) | Conditional token needs 2+ creature cards in gy — assume not met in the mulligan window (§9 conservatism); no token credited. |
| Hawkeye's Bow (#132) | Equipment statics + recurring face ping — plain is_equipment. |
| Helicarrier Strike (#15) | Teamwork burn encoded at the paid value 4 (§16 reference card). |
| Hell's Kitchen (#268) | Tapped B/R dual. |
| Hero in Training (#bonus-msc-840) | ETB draw wired; conditional lifegain unmodelled. |

### Fixed (3 of 15)

1. **Hire a Crew (#134)** — the "creatures you control get +1/+0
   until end of turn" anthem rider is a combat trick per the Lorehold
   Charm precedent (§12) → `combat_trick_power=1,
   combat_trick_toughness=0` added beside the menace token.
   (`apply_msh_batch3_fixes_20260707.py`)
2. **Hour of Defeat (#99)** — mid-line "… Surveil 1." rider after the
   destroy sentence was dropped by the chunk matcher → 
   `cards_manipulated=1` + `ScryEffect(1)` on the cast mode (§2).
   (same script) Only other mid-line rider in MSH is Colleen Wing's,
   which sits in a recurring trigger and correctly gets no credit.
3. **Hydraulic Helper (#57)** — "{T}: Add {U}. This mana can't be
   spent to cast a nonartifact spell." was encoded as an
   *unrestricted* mana dork; `_RESTRICTED_MANA_RE` only knew the
   "Spend this mana only …" phrasing. Parser fixed (negative phrasing
   now drops the ability, matching the Purple Dragon Punks /
   Hydro-Channeler convention) + detector rerun. Only card in any set
   with this phrasing.

## Batch 2 (2026-07-06): cards 16–30

### Clean (11 of 15)

| Card | Verdict |
|---|---|
| Brave Brawler (#8) | Power-up pump unencoded per §3; activated mode carried for the sim. |
| Call Damage Control (#162) | Graveyard recursion → is_other, documented. |
| Crimson Operative (#bonus-msc-848) | Impulse rider unencoded per the §16 decision. |
| Crowd of True Believers (#14) | Activated pump never combat-trick (§3). |
| Cruel Alliance (#92) | Teamwork-paid outcome = exile either way → removal (§16). |
| Decoy Ploy (#94) | Graveyard recursion → is_other, documented. |
| Depower (#50) | Draw 1 + power-only debuff; cost reducer ignored per §9. |
| Ephemerate (#bonus-mh1-7) | Blink + rebound → is_other; no second mode (rebound is an automatic recast). |
| Fisk Tower (#265) | Tapped W/B dual. |
| Frozen in Ice (#54) | Removal aura (loses abilities + can't untap, §5). |
| Giant Growth (#167) | Combat trick +3/+3. |

### Fixed (4 of 15) — all via the round-2 parser fixes + rerun

1. **Borough Backup (#7)** — "create two 3/2 Heroes" emitted ONE body;
   §4 requires one per token. Parser now multiplies by the count word
   (also fixed Okoye #27, Doctor Doom #95, Robot Domination #111,
   Avengers: Under Siege #205, and multi-token cards in
   TLA/SOS/ECL/TMT).
2. **Futurist Forge (#55)** — ETB "draw a card" was credited in
   role_features but invisible to the simulator; non-creature
   permanents now wire self-ETB effects onto the cast mode
   (also Simulacrum Synthesizer, Everything Pizza, Omni-Cheese Pizza,
   Puca's Eye, The Spirit Oasis).
3. **Dependable Quinjet (#246)** — "{T}: Add one mana of any color."
   was a noop activated mode; now a proper `mana_abilities` entry the
   simulator can use.
4. **Deadly Dispute (#bonus-msc-794)** — fields contradicted the
   documented §16 ruling (reason said "kept is_other", fields had
   `cards_drawn=2` + DrawCardsEffect). Aligned to the ruling via the
   patch script. *Owner may want to revisit the ruling itself — the
   sac cost is trivially payable in practice — but fields and ruling
   now agree.*

### Also enforced while fixing batch 2

§16's recurring-trigger draw policy ("Whenever …" / "At the beginning
of …" draw never credits) moved from per-set audit patches into the
parser — cleared ~50 over-credited cards across all five sets in the
same rerun.

## Batch 1 resolution (2026-07-06)

Owner rulings on the batch-1 findings (now codified in
`CARD_ENCODING_GUIDE.md` §19):

* **Agents of HYDRA (#85)** — finding amended: death triggers are NOT
  encoded at all (too conditional), so the token body was *removed*
  rather than gaining menace. Exception (LLM judgment): a card with
  its own activated sac outlet may encode its death trigger.
* **M.O.D.O.K. (#106)** — "Pay 3 life: connives" assumed to fire once
  on arrival → loot encoding.
* **Villainous Hideout (#276)** — expensive land activations are
  ignored (consistent with existing practice).
* A.I.M. Scientists / Atlantis Attacks / Bold Biochemist fixed as
  proposed.

Parser hardening shipped in the same round (PR branch
`parser-tripwire-msh-audit`): unknown-keyword tripwire
(`KNOWN_KEYWORDS_EXTRA` allowlist; connive/teamwork deliberately
trip), death-trigger skip, activated-ability cmc≤3 crediting gate,
token-keyword capture, "Choose one." period-form modal detection,
"Power-up" label stripping, and the `census-drops` CLI. Detector rerun
on all five sets; 20 flipped/straggler cards hand-encoded by
`apply_tripwire_encodings_20260706.py` (12 MSH, 2 TMT, 1 SOS, 5
pre-existing ECL bonus-sheet stragglers). All five sets are back to
0 needs_llm / 0 needs_human.

## The full alphabetical list (94 commons)

1. A.I.M. Labs (#257) · 2. A.I.M. Scientists (#44) · 3. A.I.M.
Synthoids (#242) · 4. Aerial Doombot (#43) · 5. Agent of Atlas (#3) ·
6. Agents of HYDRA (#85) · 7. Agents of S.H.I.E.L.D. (#5) ·
8. Ant-Man's Army (#161) · 9. Asgardian Citadel (#258) · 10. Atlantean
Cavalry (#45) · 11. Atlantis Attacks (#46) · 12. Avengers Hangar
(#259) · 13. Birnin Zana Plaza (#262) · 14. Blazing Crescendo (#125) ·
15. Bold Biochemist (#48) · 16. Borough Backup (#7) · 17. Brave
Brawler (#8) · 18. Call Damage Control (#162) · 19. Crimson Operative
(#bonus-msc-848) · 20. Crowd of True Believers (#14) · 21. Cruel
Alliance (#92) · 22. Deadly Dispute (#bonus-msc-794) · 23. Decoy Ploy
(#94) · 24. Dependable Quinjet (#246) · 25. Depower (#50) ·
26. Ephemerate (#bonus-mh1-7) · 27. Fisk Tower (#265) · 28. Frozen in
Ice (#54) · 29. Futurist Forge (#55) · 30. Giant Growth (#167) ·
31. Giant-Sized Flying Ant (#56) · 32. Go Nuts! (#168) · 33. Guerrilla
Gorilla (#169) · 34. H.E.R.B.I.E. Scout Unit (#247) · 35. HULK SMASH!
(#135) · 36. HYDRA Assault Robot (#137) · 37. HYDRA Infiltration
(#100) · 38. HYDRA Troopers (#101) · 39. Hawkeye's Bow (#132) ·
40. Helicarrier Strike (#15) · 41. Hell's Kitchen (#268) · 42. Hero in
Training (#bonus-msc-840) · 43. Hire a Crew (#134) · 44. Hour of
Defeat (#99) · 45. Hydraulic Helper (#57) · 46. I Am Iron Man (#58) ·
47. K'un-Lun Warrior (#140) · 48. Kingpin's Enforcers (#102) ·
49. Knight of Wundagore (#175) · 50. Kree Commandos (#19) · 51. Kree
Sentinel (#141) · 52. Lightning Strike (#142) · 53. Los Diablos
Missile Base (#270) · 54. Machinesmith Automaton (#144) · 55. Murdock's
Crusade (#24) · 56. Ninja of the Hand (#108) · 57. Panther Pounce
(#29) · 58. Pet Avengers (#178) · 59. Powerful Broker (#179) ·
60. Project Deathlok Soldier (#109) · 61. Pym Technologies (#271) ·
62. Raft Security Officer (#33) · 63. Rapid Rescue (#181) · 64. Red
Room Recruit (#110) · 65. Repulsor Blast (#150) · 66. Restorative
Technique (#183) · 67. Roxxon Brutes (#113) · 68. S.H.I.E.L.D.
Deployment Drone (#73) · 69. S.H.I.E.L.D. Spy Kit (#36) · 70. Savage
Land Dinosaur (#185) · 71. Serpent Specialist (#186) · 72. Stark
Industries (#272) · 73. Stark Industries Executive (#153) · 74. Stolen
Stark Tech (#114) · 75. Subterranean Cavern (#273) · 76. Sundering
Growth (#bonus-c19-203) · 77. Super Speed (#154) · 78. Super Strength
(#189) · 79. Super Suit (#78) · 80. Surveillance Room (#274) ·
81. Take Up the Shield (#39) · 82. Trickster's Stratagem (#81) ·
83. Ultron Drone (#253) · 84. Undercover Skrull (#194) · 85. Unliving
Legionnaire (#119) · 86. Vibranium Energy Daggers (#254) · 87. Vision
of Love (#158) · 88. Visions of Villainy (#120) · 89. Volcanic Villain
(#159) · 90. Wakandan Drone Flock (#40) · 91. Wakandan Royal Guard
(#195) · 92. We Say Thee Nay! (#82) · 93. Web Up (#41) · 94. Widow's
Bite (#122)

## Batch 1 (2026-07-06): cards 1–15

### Clean (11 of 15)

| Card | Verdict |
|---|---|
| A.I.M. Labs (#257) | Tapped U/B dual, mana ability + enter_condition correct; ETB lifegain unmodeled by design. |
| A.I.M. Synthoids (#242) | Surveil 2 → ScryEffect(2) + cards_manipulated=2, per §2. |
| Aerial Doombot (#43) | Power-up pump not encoded — consistent with §3 (activated pump never combat-trick). |
| Agent of Atlas (#3) | Plain prowess creature. |
| Agents of S.H.I.E.L.D. (#5) | Attack trigger unmodeled per §16. |
| Ant-Man's Army (#161) | Food/Treasure tokens correctly NOT in creates_creatures per §4 (Lita / Deadly Dispute precedent). |
| Asgardian Citadel (#258) | Tapped R/W dual, correct. |
| Atlantean Cavalry (#45) | Recurring draw-second trigger correctly unencoded per §16. |
| Avengers Hangar (#259) | Tapped W/U dual, correct. |
| Birnin Zana Plaza (#262) | Tapped G/W dual, correct. |
| Blazing Crescendo (#125) | Combat trick +3/+1; impulse-draw rider left unencoded per the documented §16 decision. |

### Needs fix (4 of 15)

1. **A.I.M. Scientists (#44)** — ETB connive silently dropped.
   Connive = loot (draw 1, discard 1) per §2: should have
   `cards_manipulated=1`, `cards_drawn=0` (net), and
   `DrawCardsEffect(1) + DiscardCardEffect(1)` on the cast mode after
   `EntersBattlefieldEffect`. Root cause: parser has no connive
   matcher; unparseable ETB triggers are silently dropped (v1 rule),
   so the card auto-classified with the connive invisible.

2. **Agents of HYDRA (#85)** — dies-trigger token body encoded but
   `keywords=[]`; oracle token has **menace**. §4: "Always set token
   keywords from the oracle text." Root cause: `_match_token_creation`
   hardcodes `keywords=[]` (parser.py:1097).

3. **Atlantis Attacks (#46)** — modal 7-drop slipped the MV≥4
   fast-path because `_MODAL_RE` (parser.py:2838) only matches
   "Choose one —" (dash form); MSH templates it as "Choose one."
   (period). Fast-path condition 5 explicitly intends to exclude
   modals. Per §12 aggregation it should get
   `creates_creatures=[6/5 U Leviathan, keywords=['hexproof']]` and
   `is_bounce=True` (no sim effects — §8). Deck-level role counts see
   these flags even on a 7-drop.

4. **Bold Biochemist (#48)** — `cards_drawn=2` credited from the
   "Power-up — {5}{U}: … draw two cards" activation line. The
   activation is ~6 mana (even with the entered-this-turn discount),
   far outside the turn 1–4 window. Per §2 (Sewer-veillance Cam) and
   §18 (Stone Docent: parser credits draw inside activation lines
   without checking relevance), should be cleared to
   `cards_drawn=0`.

## Systemic issues found while checking batch 1 (affect later cards too)

* **Connive is invisible to the parser** — 11 MSH cards connive.
  ETB connives needing the loot fix: A.I.M. Scientists (#44, c),
  Madame Masque (#104, u), Red Room Recruit (#110, c — upcoming in
  batch 5). Attack-trigger connives (Swordsman #116, Kang #217)
  correctly stay unencoded per §16. Judgment calls for the owner:
  M.O.D.O.K. (#106, "Pay 3 life: connives" — cheap repeatable) and
  Villainous Hideout (#276, "{3},{T}: target Villain connives" —
  T4-activatable, cf. Gristle Glutton "mulligan-relevant activation"
  rule in §2).

* **Token keywords dropped on all auto-classified token creators** —
  parser hardcodes `keywords=[]`. 11 MSH cards affected (Borough
  Backup #7 vigilance, Agents of HYDRA #85 menace, Madame Masque #104
  menace, Hire a Crew #134 menace, Pet Avengers #178 vigilance,
  Invisible Woman #17 defender, Super-Skrull #115 defender,
  Construct a Cosmic Cube #90 menace, Alien Invasion #200 haste,
  Avengers: Under Siege #205 menace, Madame Hydra #221 menace).
  SOS (5 cards) and ECL (2) have the same gap outstanding;
  TLA/TMT were patched in their audits.

* **`_MODAL_RE` misses the "Choose one." period form** (no dash) that
  MSH uses. Only Atlantis Attacks slipped to auto this way (the other
  four period-form modals are CMC<4 and were llm_encoded). Parser fix
  recommended so future sets don't repeat this.
