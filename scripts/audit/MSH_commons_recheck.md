# MSH commons encoding recheck (pre-release audit)

Working through all 94 MSH commons alphabetically, 15 per batch,
judging each against `packages/cards/CARD_ENCODING_GUIDE.md`.
Source: `data/processed/parsed_cards/MSH.json` (334 cards total).

Status: **batches 1–2 done (cards 1–30), all fixes applied
(2026-07-06)**. Next batch starts at #31 Giant-Sized Flying Ant.

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
