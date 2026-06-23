"""Build patches.json for all MSH + MAR needs_llm cards.

Run this script to (re)generate the Marvel Super Heroes encoding patches,
then apply with packages/cards/scripts/apply_patches.py.

The encoders here follow the rules in
``packages/cards/CARD_ENCODING_GUIDE.md``. New rules introduced by MSH
(see the guide's new section 16 for the formal writeup):

* **Teamwork N** (additional, optional cost: tap creatures you control
  with total power >= N for a bonus effect) — treated like kicker: the
  TEAMWORK-enhanced outcome is encoded into role_features (max value,
  same convention as modal/kicker scalars), but NO second cast Mode is
  added because the cost (tapping OTHER permanents) isn't representable
  in the ``Cost`` model (no "tap creatures with total power >= N" cost
  component exists, by design — see CLAUDE.md's Cost model).
* **MDFC pairs where BOTH faces are independently-castable creatures**
  (Tony Stark // The Invincible Iron Man and friends) — encoded as TWO
  ``Mode(kind="cast")`` entries, one per face, each with
  ``EntersBattlefieldEffect``. The front face's own "pay mana, transform
  as a sorcery" activated ability is a permanent-resident upgrade, not a
  hand-resident alt cost — it's left unencoded (too far outside the
  turn 1-4 mulligan window, and the simulator has no "transformed"
  state to track it against).
* **-N/-N until end of turn on an instant** is treated as an extension
  of the existing "-N/-N counters" removal threshold (CLAUDE §7) to the
  temporary-debuff shape: N >= 2 counts as removal (kills the common
  2-toughness floor), same cutoff as permanent counters.
* **Mill 2, take a permanent card** (Rapid Rescue) is modeled with
  ``LookAtTopEffect`` even though the unchosen cards go to the
  graveyard rather than the bottom of the library — immaterial for the
  simulator, which never inspects graveyard/library order, only
  hand contents.
* **Triggered / recurring abilities are not modeled for card draw**,
  including on permanents and Auras (Super Intelligence's "draw a card
  each upkeep") — too far outside the turn 1-4 mulligan window to size
  reliably. An Aura that fits neither is_removal_aura nor is_pump_aura
  under this policy simply sets neither; it falls through to is_other.
* **Edict effects and type-unrestricted "destroy target token"** both
  count toward ``removal_destroy_or_exile`` (The Ruinous Wrecking
  Crew) — "each player sacrifices a creature of their choice" is
  removal of the opponent's choice, and a token-destroy mode counts
  even though the token type isn't restricted to creatures.
* **Impulse-draw effects** ("exile, may play until end of next turn")
  are deliberately left unencoded — Blazing Crescendo's single exiled
  card is too conditional on having mana up later, and Hex Magic's
  exile-then-draw count is hand-size-dependent so there's no fixed N
  to assign.

All 59 needs_llm cards in this batch resolved to either ``llm_encoded``
(57) or were corrected from an initial ``needs_human`` flag back to
``llm_encoded`` (2: #77, #224) once the owner settled the conventions
above on 2026-06-22.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_CARDS_SRC = _REPO / "packages" / "cards" / "src"
if str(_CARDS_SRC) not in sys.path:
    sys.path.insert(0, str(_CARDS_SRC))

from mulligan_coach_cards.mana import parse_mana_cost  # noqa: E402

PATCHES_PATH = _HERE / "patches.json"


def cost(
    mana_raw: str = "",
    *,
    tap: bool = False,
    sacrifice: str | None = None,
    discard_self: bool = False,
) -> dict[str, Any]:
    mc = parse_mana_cost(mana_raw)
    return {
        "mana": mc.model_dump(mode="json"),
        "tap": tap,
        "untap": False,
        "sacrifice": {"target": sacrifice} if sacrifice else None,
        "discard_self": discard_self,
    }


def cast_mode(mana_raw: str, effects: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"kind": "cast", "cost": cost(mana_raw), "effects": list(effects or [])}


def activated_mode(
    mana_raw: str, effects: list[dict[str, Any]], *, tap: bool = False
) -> dict[str, Any]:
    return {"kind": "activated", "cost": cost(mana_raw, tap=tap), "effects": effects}


def etb() -> dict[str, Any]:
    return {"kind": "enters_battlefield"}


def noop(role_tag: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": "noop"}
    if role_tag is not None:
        out["role_tag"] = role_tag
    return out


def draw(n: int) -> dict[str, Any]:
    return {"kind": "draw_cards", "n": n}


def discard(n: int = 1) -> dict[str, Any]:
    return {"kind": "discard_card", "n": n}


def scry(n: int) -> dict[str, Any]:
    return {"kind": "scry", "n": n}


def fetch_land(*, target: str, dest: str, subtype: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "fetch_land",
        "target_filter": target,
        "destination": dest,
        "count": 1,
    }
    if subtype is not None:
        out["subtype"] = subtype
    return out


def look_top(n: int, *, accepts_land: bool = True, accepts_nonland: bool = True) -> dict[str, Any]:
    return {
        "kind": "look_at_top",
        "n": n,
        "accepts_land": accepts_land,
        "accepts_nonland": accepts_nonland,
    }


def mana_ability(mana_raw: str, *options: list[str], tap: bool = True) -> dict[str, Any]:
    return {
        "cost": cost(mana_raw, tap=tap),
        "produces": [list(opt) for opt in options],
        "condition": None,
    }


PATCHES: list[dict[str, Any]] = []
P = PATCHES.append


def patch(set_code: str, collector: str, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "set_code": set_code,
        "collector_number": str(collector),
        "status": status,
        "patch": fields,
    }


def llm(
    set_code: str,
    collector: str,
    *,
    role_features: dict[str, Any] | None = None,
    modes: list[dict[str, Any]] | None = None,
    mana_abilities: list[dict[str, Any]] | None = None,
    reasons: list[str] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if role_features is not None:
        fields["role_features"] = role_features
    if modes is not None:
        fields["modes"] = modes
    if mana_abilities is not None:
        fields["mana_abilities"] = mana_abilities
    if reasons is not None:
        fields["reasons"] = reasons
    return patch(set_code, collector, "llm_encoded", **fields)


# ===========================================================================
# MSH
# ===========================================================================

P(
    llm(
        "MSH",
        "9",
        reasons=[
            "llm: shield-counter damage prevention + hexproof grant to other Heroes "
            "— no role_features field models damage prevention or keyword-grant "
            "statics; left at is_creature only.",
        ],
    )
)

P(
    llm(
        "MSH",
        "15",
        role_features={"removal_burn_damage": 4},
        reasons=[
            "llm: Teamwork 2 — base 2 dmg, 4 dmg if teamwork paid (tap creatures "
            "total power 2+). Encoded the teamwork-enhanced max value per the "
            "kicker/modal max-value convention (CLAUDE §12). No second Mode: "
            "Teamwork's cost (tapping OTHER creatures) isn't representable in "
            "the Cost model.",
        ],
    )
)

# #18 Jennifer Walters {1}{W} 2/3 // The Sensational She-Hulk {3}{G}{W}{W} 6/6
P(
    llm(
        "MSH",
        "18",
        modes=[cast_mode("{1}{W}", [etb()]), cast_mode("{3}{G}{W}{W}", [etb()])],
        reasons=[
            "llm: modal_dfc, both faces independently castable from hand — two "
            "cast Modes per CLAUDE §16. Both faces' 'opponents can't cast spells "
            "during your turn' stax text and She-Hulk's damage-redirect trigger "
            "have no role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "21",
        role_features={"is_mana_rock": True},
        reasons=[
            "llm: clean {T}: Add {W} mana ability (already parsed); the harness "
            "+ infinity-ability flicker engine is gated by a 6-mana activation "
            "and is a late-game value engine, not mulligan-relevant — left "
            "unencoded. Set is_mana_rock for the reliable mana ability.",
        ],
    )
)

P(
    llm(
        "MSH",
        "23",
        modes=[cast_mode("{2}{W}", [etb()]), cast_mode("{2}{R}{W}{W}", [etb()])],
        reasons=[
            "llm: modal_dfc, both faces independently castable — two cast Modes "
            "per CLAUDE §16. Prowess + counter-distribution triggers have no "
            "role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "24",
        role_features={"removal_destroy_or_exile": True},
        reasons=[
            "llm: Teamwork 4, modal 'Street Justice' (exile creature toughness "
            "4+) / 'Legal Justice' (exile enchantment MV 4+). Aggregated per "
            "CLAUDE §12: removal_destroy_or_exile from the creature-exile mode; "
            "the enchantment-exile mode is non-creature (is_other).",
        ],
    )
)

P(
    llm(
        "MSH",
        "28",
        role_features={"cards_manipulated": 2},
        modes=[cast_mode("{1}{W}", [etb(), scry(2)])],
        reasons=[
            "llm: Saga, chapter I = Scry 2 (encoded per CLAUDE §6/§2). Chapters "
            "II (cheat a Hero MV<=3 onto the battlefield or draw) and III "
            "(board-wide +1/+1 counter) are not encoded per the chapter-I-only "
            "convention.",
        ],
    )
)

P(
    llm(
        "MSH",
        "32",
        reasons=[
            "llm: 'Seismic Takedown' (tap a creature/land whenever you cast a "
            "noncreature spell) is a repeated, conditional value engine — no "
            "role_features field fits; left at is_creature only.",
        ],
    )
)

# #49 Bruce Banner {U} 1/1 // The Incredible Hulk {2}{R}{R}{G}{G} 8/8
P(
    llm(
        "MSH",
        "49",
        modes=[cast_mode("{U}", [etb()]), cast_mode("{2}{R}{R}{G}{G}", [etb()])],
        reasons=[
            "llm: modal_dfc, both faces independently castable — two cast Modes "
            "per CLAUDE §16. Bruce Banner's own '{X}{X},{T}: Draw X cards, "
            "sorcery-speed' activated ability is left unencoded — repeatable, "
            "mana-gated activated draw is excluded by the same conservatism as "
            "the Sewer-veillance Cam precedent (CLAUDE §2). Hulk's Enrage "
            "(extra-combat-on-damage) is a combat mechanic, not modeled.",
        ],
    )
)

P(
    llm(
        "MSH",
        "50",
        reasons=[
            "llm: already-correct auto encoding (draw 1, -4/-0 debuff). The "
            "conditional cost reduction (targets an attacking creature) is the "
            "unconditional-baseline convention (CLAUDE §9) — ignored.",
        ],
    )
)

P(
    llm(
        "MSH",
        "53",
        reasons=[
            "llm: ETB auto-attach + static +1/+1/flying/ward{1} grant is standard "
            "Equipment territory (is_equipment auto-set by the store); no "
            "role_features field models Equipment static pump bonuses. The bail "
            "reason ('Equip {2}{U}' unrecognised) is a parser gap — the equip-cost "
            "ignorable-line regex doesn't yet handle a colored mana symbol in the "
            "equip cost; candidate for a future parser widening, not blocking here.",
        ],
    )
)

P(
    llm(
        "MSH",
        "60",
        reasons=[
            "llm: Improvise (cost reduction, not modeled) + 'noncreature spells you "
            "cast have improvise' (same). No role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "63",
        reasons=[
            "llm: counter-granted hexproof + draw-triggered counter growth on "
            "itself — Kid Loki doesn't draw cards, it reacts to the player's draws. "
            "No role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "67",
        reasons=[
            "llm: 'no maximum hand size' (no field) + Embiggen Fist's draw is "
            "gated on casting a targeted spell at your own creature — conditional "
            "triggered draw, left OFF per the Thoughtweft Charge precedent "
            "(CLAUDE §2).",
        ],
    )
)

P(
    llm(
        "MSH",
        "69",
        role_features={
            "creates_creatures": [
                {
                    "power": "1",
                    "toughness": "1",
                    "colors": ["U"],
                    "subtypes": ["Merfolk"],
                    "keywords": [],
                }
            ]
        },
        reasons=[
            "llm: 'whenever you cast a noncreature spell with blue pips, create "
            "that many 1/1 Merfolk' — variable-X token creation, encoded as a "
            "single 1/1 Merfolk body per the X=1-minimum convention (CLAUDE §4). "
            "Namor's own variable power (= Merfolk you control) is not a "
            "modeled stat.",
        ],
    )
)

P(
    llm(
        "MSH",
        "72",
        role_features={"is_pump_aura": True, "removal_destroy_or_exile": True},
        reasons=[
            "llm: judgment call — Aura ETB permanently exiles a target creature "
            "('until this Aura leaves the battlefield', same permanence shape "
            "as the Dimensional Exile precedent in CLAUDE §5, hence "
            "removal_destroy_or_exile=True) while transforming YOUR enchanted "
            "creature into a copy of the exiled one. The enchanted-creature "
            "effect is a beneficial transformation, not a numeric pump, but "
            "CLAUDE §5 requires exactly one of is_removal_aura/is_pump_aura — "
            "chose is_pump_aura as the better fit since the enchanted creature "
            "benefits. Flagging for owner review if this read is wrong.",
        ],
    )
)

# #80 Tony Stark {1}{U} 1/3 // The Invincible Iron Man {4}{U}{R} 5/5
P(
    llm(
        "MSH",
        "80",
        modes=[cast_mode("{1}{U}", [etb()]), cast_mode("{4}{U}{R}", [etb()])],
        reasons=[
            "llm: modal_dfc, both faces independently castable — two cast Modes "
            "per CLAUDE §16. Tony Stark's own '{1},{T}: look at top 4, take an "
            "artifact' ability is a narrow tutor-like effect, left unencoded "
            "per the tutor precedent (CLAUDE §10). Iron Man's free-artifact-"
            "drop combat trigger and the front face's pay-mana transform "
            "ability are permanent-resident value engines, not modeled.",
        ],
    )
)

P(
    llm(
        "MSH",
        "82",
        role_features={"is_counterspell": True},
        reasons=[
            "llm: 'Counter target spell unless controller pays {2}' (or {4} "
            "with Teamwork) — is_counterspell regardless of the pay-cost "
            "escape clause.",
        ],
    )
)

P(
    llm(
        "MSH",
        "84",
        reasons=[
            "llm: ETB tap + strip-abilities lockdown is neither destroy/exile nor "
            "bounce/tuck — no role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "87",
        reasons=[
            "llm: connive-on-cast-black-spell is a repeated, conditional loot "
            "engine (left OFF per CLAUDE §2 conservatism); Boast is an attack-"
            "gated graveyard-recursion ability on a permanent already in play, "
            "correctly left as an unencoded alt-cost-keyword bail.",
        ],
    )
)

P(
    llm(
        "MSH",
        "88",
        reasons=[
            "llm: 'Villain spells cost {1} less' (cost reduction, not modeled) + "
            "conditional connive-on-Villain-ETB (repeated, conditional, left OFF "
            "per CLAUDE §2).",
        ],
    )
)

P(
    llm(
        "MSH",
        "92",
        reasons=[
            "llm: already-correct auto encoding (removal_destroy_or_exile=True "
            "from the base exile mode). Teamwork's lifegain bonus has no "
            "role_features field.",
        ],
    )
)

P(
    llm(
        "MSH",
        "93",
        role_features={"removal_destroy_or_exile": True},
        reasons=[
            "llm: judgment call extending CLAUDE §7's '-N/-N counters, N>=2 "
            "counts as removal' threshold from permanent counters to a "
            "temporary until-end-of-turn debuff on an instant — -4/-4 EOT "
            "kills the overwhelming majority of Limited creatures, same "
            "functional role as a kill spell. Flagging the extension for "
            "owner review.",
        ],
    )
)

P(
    llm(
        "MSH",
        "94",
        reasons=[
            "llm: graveyard-to-hand recursion (Villain or Hero card) — not a "
            "library draw, not a bounce/tuck of a battlefield permanent. No "
            "role_features field fits.",
        ],
    )
)

P(
    llm(
        "MSH",
        "103",
        reasons=[
            "llm: ETB targeted discard (opponent reveals N, you pick one, they "
            "discard) — no role_features field models hand disruption.",
        ],
    )
)

P(
    llm(
        "MSH",
        "118",
        reasons=[
            "llm: Teamwork 4, reanimate a creature card from graveyard straight to "
            "the battlefield — not a token (the real card returns), not a land "
            "fetch. No role_features field fits; same gap as #69's reanimation-"
            "adjacent shape. Left at is_other.",
        ],
    )
)

P(
    llm(
        "MSH",
        "120",
        role_features={"cards_drawn": 2},
        modes=[cast_mode("{2}{B}", [draw(2)])],
        reasons=[
            "llm: clean 'draw two cards' (lose 2 life, no field). Conditional "
            "cost reduction (control a Villain) ignored per the unconditional-"
            "baseline convention (CLAUDE §9).",
        ],
    )
)

P(
    llm(
        "MSH",
        "122",
        role_features={
            "combat_trick_granted_keywords": ["deathtouch"],
            "removal_destroy_or_exile": True,
        },
        reasons=[
            "llm: Teamwork 3, modal 'gains deathtouch EOT' (combat trick, "
            "instant) / '-2/-2 EOT' (removal per the N=2 threshold, CLAUDE §7, "
            "extended to temporary debuffs same as #93). Aggregated per "
            "CLAUDE §12.",
        ],
    )
)

P(
    llm(
        "MSH",
        "124",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
            "removal_burn_damage": 3,
        },
        reasons=[
            "llm: modal 'Choose one or both' — 3 dmg to each creature (mass "
            "removal, Blasphemous-Act-style per CLAUDE §1) / destroy target "
            "land + ramp (non-creature, is_other). Aggregated per CLAUDE §12.",
        ],
    )
)

P(
    llm(
        "MSH",
        "125",
        role_features={"combat_trick_power": 3, "combat_trick_toughness": 1},
        reasons=[
            "llm: instant +3/+1 EOT — clean combat trick. 'Exile top card, may "
            "play it until end of next turn' is an impulse-draw effect, "
            "decided 2026-06-22 to leave unencoded: the card never enters "
            "hand (distinct from the to-HAND look-at-top-N rules in §15), "
            "and the single exiled card is too conditional on having mana "
            "available later to count as a reliable draw. See CLAUDE §16.",
        ],
    )
)

P(
    llm(
        "MSH",
        "130",
        reasons=[
            "llm: 'Trick Arrows' is a repeated, mana-gated, attack-triggered modal "
            "engine (Net / Explosive-to-player / Boomerang-loot) — each leaf is "
            "either non-creature-targeted (Explosive hits a player, not "
            "removal_burn_damage) or too conditional/repeated. Left unencoded "
            "per the established conservatism for repeated activated/triggered "
            "engines.",
        ],
    )
)

P(
    llm(
        "MSH",
        "133",
        reasons=[
            "llm: 'exile your hand, draw that many cards, may play the exiled "
            "cards until end of next turn' — net cards-in-hand is unchanged (X "
            "exiled, X drawn back), so cards_drawn is not bumped. Decided "
            "2026-06-22 to leave the impulse-play bonus unencoded too: X is "
            "hand-size-dependent, so there's no fixed N to assign even if we "
            "wanted to model it. See CLAUDE §16.",
        ],
    )
)

P(
    llm(
        "MSH",
        "135",
        role_features={"is_punch_fight": True},
        reasons=[
            "llm: Teamwork 4, modal 'destroy target noncreature artifact' "
            "(non-creature, is_other) / punch (creature deals dmg = power to "
            "opponent's creature, CLAUDE §1 Tenderize-style). The detector's "
            "stale 'removal_damage_variable' noop tag was a partial guess, not "
            "an authoritative role_features set — corrected here to "
            "is_punch_fight.",
        ],
    )
)

P(
    llm(
        "MSH",
        "151",
        reasons=[
            "llm: cost-reduction static (instant/sorcery MV>=4 cost less, scaled "
            "by her power) — not modeled, no role_features mapping.",
        ],
    )
)

P(
    llm(
        "MSH",
        "155",
        role_features={"combat_trick_granted_keywords": ["double strike", "trample"]},
        reasons=[
            "llm: Teamwork 1, base grants double strike EOT, teamwork adds "
            "trample EOT — union of both keyword grants per the modal "
            "aggregation convention (CLAUDE §12).",
        ],
    )
)

P(
    llm(
        "MSH",
        "158",
        modes=[cast_mode("{1}{R}", [draw(2), discard(1)])],
        reasons=[
            "llm: already-correct auto encoding (cards_drawn=1 net, "
            "cards_manipulated=2 gross) matching the Abandon Attachments "
            "'discard 1, draw 2, net 1' precedent (CLAUDE §2) — the optional "
            "sac-artifact-or-discard cost is treated as taken (the common, "
            "rational line). Added the matching DrawCardsEffect+DiscardCardEffect "
            "Mode the auto pass left empty.",
        ],
    )
)

P(
    llm(
        "MSH",
        "162",
        reasons=[
            "llm: graveyard-to-hand recursion across 4 card types — same gap as "
            "#94/#118; no role_features field fits.",
        ],
    )
)

P(
    llm(
        "MSH",
        "166",
        role_features={"is_punch_fight": True},
        reasons=[
            "llm: modal 'double power/toughness EOT' (sorcery — combat-trick "
            "fields suppressed per CLAUDE §3) / fight. is_punch_fight from the "
            "fight mode only.",
        ],
    )
)

P(
    llm(
        "MSH",
        "168",
        role_features={"is_punch_fight": True},
        reasons=[
            "llm: Teamwork 3, modal '+1/+1 counter on target creature' (sorcery, "
            "permanent counter, is_other per CLAUDE §7) / fight. is_punch_fight "
            "from the fight mode only.",
        ],
    )
)

P(
    llm(
        "MSH",
        "176",
        reasons=[
            "llm: repeated upkeep modal (own +1/+1 counter / remove-a-counter-then-"
            "draw) — the draw leg requires a creature with a counter to remove, "
            "too conditional/repeated. Left unencoded per CLAUDE §2.",
        ],
    )
)

P(
    llm(
        "MSH",
        "181",
        role_features={"cards_drawn": 1, "cards_manipulated": 1},
        modes=[cast_mode("{G}", [look_top(2, accepts_land=True, accepts_nonland=True)])],
        reasons=[
            "llm: 'Mill two cards, may put a permanent card from among them "
            "into your hand' modeled as LookAtTopEffect(n=2) per CLAUDE §15 "
            "(cards_drawn+=1, cards_manipulated+=N-1=1) even though the "
            "unchosen card goes to the graveyard rather than the bottom of the "
            "library — immaterial for the simulator, which doesn't track "
            "graveyard/library order.",
        ],
    )
)

P(
    llm(
        "MSH",
        "183",
        modes=[cast_mode("{2}{G}", [fetch_land(target="basic", dest="battlefield_tapped")])],
        reasons=[
            "llm: 'search library for a basic land, put onto the battlefield "
            "tapped' — clean FetchLandEffect. The +1/+1 counter rider and "
            "'target player' phrasing (assumed self-target, the rational case) "
            "have no role_features field.",
        ],
    )
)

P(
    llm(
        "MSH",
        "214",
        reasons=[
            "llm: 'can't be blocked' evasion static — no role_features field for unblockable.",
        ],
    )
)

# #219 King T'Challa {1}{W}{U} 3/2 // Black Panther, Hope Enduring {4}{W}{U} 3/3
P(
    llm(
        "MSH",
        "219",
        modes=[cast_mode("{1}{W}{U}", [etb()]), cast_mode("{4}{W}{U}", [etb()])],
        reasons=[
            "llm: modal_dfc, both faces independently castable — two cast Modes "
            "per CLAUDE §16. King T'Challa's 'draw on a player's 2nd card each "
            "turn' is conditional on extra-draw synergy elsewhere (left OFF); "
            "Black Panther's 'draw on combat damage to a player' matches the "
            "combat-damage-triggered-draw exclusion (CLAUDE §2, April the "
            "Reporter precedent) exactly.",
        ],
    )
)

P(
    llm(
        "MSH",
        "220",
        reasons=[
            "llm: Extort (repeated, optional, mana-gated 1-life drain per spell "
            "cast) — RoleFeatures has no life-gain/drain field at all, and the "
            "effect is repeated/conditional regardless. The attack-trigger "
            "power/toughness-swap rider is a combat mechanic, not modeled.",
        ],
    )
)

P(
    llm(
        "MSH",
        "226",
        reasons=[
            "llm: custom Ward variant (poison counters, not modeled) + a death-"
            "triggered edict ('each opponent sacrifices a nontoken creature') — "
            "RoleFeatures has no edict/sacrifice-effect field, and the trigger is "
            "conditional (requires another deathtouch creature you control to "
            "die). Left unencoded.",
        ],
    )
)

P(
    llm(
        "MSH",
        "228",
        role_features={"combat_trick_granted_keywords": ["indestructible"]},
        reasons=[
            "llm: Flash creature, ETB may tap itself to grant indestructible "
            "to another creature EOT — matches the 'flash creature with ETB "
            "pump/keyword-grant effect' combat-trick exception (CLAUDE §3) "
            "exactly.",
        ],
    )
)

P(
    llm(
        "MSH",
        "235",
        reasons=[
            "llm: 'discard a card or pay {2}' additional cost + matching Ward "
            "variant — non-mana additional costs aren't modeled beyond the "
            "printed mana cost (CLAUDE's general Cost-model scope); no draw/"
            "removal/token effect to flag.",
        ],
    )
)

P(
    llm(
        "MSH",
        "237",
        reasons=[
            "llm: tutor-to-battlefield (search library and/or graveyard for an "
            "artifact creature card) — tutor effects stay is_other per CLAUDE "
            "§10. Not a land fetch.",
        ],
    )
)

P(
    llm(
        "MSH",
        "239",
        reasons=[
            "llm: already-correct auto encoding (cast + late-game graveyard-"
            "reanimation activated mode). '+2/+0 per attached Equipment' is a "
            "variable stat with no role_features field.",
        ],
    )
)

P(
    llm(
        "MSH",
        "243",
        role_features={"is_mana_rock": True},
        modes=[cast_mode("{5}", [etb()])],
        mana_abilities=[mana_ability("", ["C", "C", "C"])],
        reasons=[
            "llm: '{T}: Add {C}{C}{C}' is a clean mana ability the auto pass "
            "mis-modeled as a generic 'activated_unknown' noop Mode instead of "
            "a ManaAbility — corrected here (mana_abilities populated, the "
            "redundant noop Mode dropped, is_mana_rock set). Improvise (cost "
            "reduction) and 'enters tapped' (no penalty field for non-land "
            "ETB-tapped artifacts) are not modeled.",
        ],
    )
)

P(
    llm(
        "MSH",
        "250",
        reasons=[
            "llm: variable power (= legendary creatures controlled, not a modeled "
            "stat) + an ability-copying engine (ETB/attack-triggered keyword "
            "theft) — no role_features field fits.",
        ],
    )
)

P(
    llm(
        "MSH",
        "256",
        reasons=[
            "llm: 'draw a card if attacking with power>=4' matches the combat-"
            "damage/combat-triggered-draw exclusion (CLAUDE §2) exactly. The "
            "Power-up ability (+1/+1 x2 counters, once) is a pure stat boost — "
            "sim doesn't track stats (CLAUDE §8) — left unencoded.",
        ],
    )
)

# ---------------------------------------------------------------------------
# NEEDS_HUMAN — genuinely ambiguous; owner should settle the convention.
# ---------------------------------------------------------------------------

P(
    llm(
        "MSH",
        "75",
        reasons=[
            "llm: 'Artifact spells you cast cost {1} less' (cost reduction, not "
            "modeled) + a sorcery-speed activated copy-effect engine ('{1},{T}: "
            "target artifact becomes a copy of a second target artifact') — niche "
            "value engine, no role_features field fits.",
        ],
    )
)

P(
    llm(
        "MSH",
        "77",
        reasons=[
            "llm: 'At the beginning of the upkeep of enchanted creature's "
            "controller, that player draws a card' — decided 2026-06-22: "
            "left unencoded under the general policy of not modeling "
            "triggered abilities for card-draw purposes (recurring/upkeep "
            "triggers are too far outside the turn 1-4 mulligan window to "
            "size reliably). Neither is_removal_aura nor is_pump_aura is "
            "set — this Aura doesn't fit either bucket, which CLAUDE §5's "
            "binary doesn't account for; the card falls through to "
            "is_other via the store invariant instead. See CLAUDE §16.",
        ],
    )
)

P(
    llm(
        "MSH",
        "224",
        role_features={"removal_destroy_or_exile": True},
        reasons=[
            "llm: '{X}{B}{R}, enters with X +1/+1 counters; ETB choose up to "
            "X — discard+draw / opponent loses 2 life / destroy target "
            "token / each player sacrifices a creature of their choice.' "
            "Decided 2026-06-22: removal_destroy_or_exile=True, justified "
            "independently by both the 'destroy target token' mode (type-"
            "unrestricted, but token removal counts) and the 'each player "
            "sacrifices a creature' edict mode (removal of the opponent's "
            "choice) — aggregated per the Charm/modal convention (CLAUDE "
            "§12). The discard+draw mode (net 0, a loot) and opponent-"
            "loses-2-life mode aren't separately encoded. See CLAUDE §16.",
        ],
    )
)


# ===========================================================================
# MAR
# ===========================================================================

P(
    llm(
        "MAR",
        "87",
        role_features={"cards_drawn": 0},
        reasons=[
            "llm: correcting a stale auto-guess. 'Mine Vibranium — {3}: Move "
            "all +1/+1 counters from target land to target creature; if one "
            "or more moved, gain that much life and draw a card' is gated on "
            "counters already being on a land (placed by the separate 'Survey "
            "the Realm' trigger) — board-state-dependent, mana-gated activated "
            "draw, same conservatism as the Sewer-veillance Cam precedent "
            "(CLAUDE §2). The detector had auto-set cards_drawn=1; corrected "
            "to 0. 'Survey the Realm' (counter on a land on creature ETB) has "
            "no role_features field (not a creature buff, not a token).",
        ],
    )
)

P(
    llm(
        "MAR",
        "88",
        reasons=[
            "llm: both abilities ('Throw' damage-divide, ''Catch' auto-equip) "
            "require an Equipment already attached/in hand — synergy-dependent, "
            "no Equipment guaranteed. Left unencoded.",
        ],
    )
)

P(
    llm(
        "MAR",
        "97",
        reasons=[
            "llm: 'double all damage dealt' + damage-triggered counter growth + "
            "regenerate — all combat mechanics; sim doesn't model combat (CLAUDE "
            "§8). No role_features field fits.",
        ],
    )
)


def main() -> None:
    import json

    PATCHES_PATH.write_text(json.dumps(PATCHES, indent=2), encoding="utf-8")
    print(f"Wrote {len(PATCHES)} patches to {PATCHES_PATH}")


if __name__ == "__main__":
    main()
