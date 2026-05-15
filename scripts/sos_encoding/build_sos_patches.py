"""Build patches.json for all SOS needs_llm cards.

Run this script to (re)generate the SOS encoding patches, then apply
with packages/cards/scripts/apply_patches.py.

The encoders here follow the rules in
``packages/cards/CARD_ENCODING_GUIDE.md``. New rules introduced by SOS:

* **Prepare layout** — split-card-style face. Front is a creature with
  the keyword "Prepared"; back is a sorcery/instant the player may cast
  a copy of from exile while the creature is prepared.

  Per project owner instructions (2026-05-14):
  * If the front face says "This creature enters prepared", the prepared
    spell is *immediately available* after the creature is cast. We
    encode the prepared spell as a second cast Mode (mirroring the
    flashback / evoke convention) so the simulator sees the future
    cast option. The mode is keyed kind="prepared" pending simulator
    support — for now the simulator will silently ignore unknown mode
    kinds. The prepared spell's role_features semantics (token
    creation, ramp signal, etc.) are merged into the card's role_features.
  * If becoming prepared requires a separate trigger / activated ability
    (attacking, gaining life, casting your third spell, etc.), we
    encode ONLY the creature side and omit the prepared spell.
* **Repartee / Opus / Increment / Infusion** — all triggered abilities
  on creatures or spells that don't materially affect the simulator's
  mulligan-relevance window (no immediate ETB ramp/draw/token in those
  triggers). Encoded as plain creatures + role_features for the body.
"""

from __future__ import annotations

import json
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
NEEDS_LLM_PATH = _REPO / "data" / "processed" / "parsed_cards" / "SOS_needs_llm.json"


def cost(
    mana_raw: str = "",
    *,
    tap: bool = False,
    sacrifice: str | None = None,
    discard_self: bool = False,
) -> dict[str, Any]:
    """Build a Cost dict from a raw mana string + optional non-mana parts."""
    mc = parse_mana_cost(mana_raw)
    out: dict[str, Any] = {
        "mana": mc.model_dump(mode="json"),
        "tap": tap,
        "untap": False,
        "sacrifice": {"target": sacrifice} if sacrifice else None,
        "discard_self": discard_self,
    }
    return out


def cast_mode(
    mana_raw: str,
    effects: list[dict[str, Any]] | None = None,
    *,
    tap: bool = False,
    sacrifice: str | None = None,
    discard_self: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "cast",
        "cost": cost(mana_raw, tap=tap, sacrifice=sacrifice, discard_self=discard_self),
        "effects": list(effects or []),
    }


def activated_mode(
    mana_raw: str, effects: list[dict[str, Any]], *, tap: bool = False, sacrifice: str | None = None
) -> dict[str, Any]:
    return {
        "kind": "activated",
        "cost": cost(mana_raw, tap=tap, sacrifice=sacrifice),
        "effects": effects,
    }


def cycle_mode(mana_raw: str, draws: int = 1) -> dict[str, Any]:
    return {
        "kind": "cycle",
        "cost": cost(mana_raw, discard_self=True),
        "effects": [{"kind": "draw_cards", "n": draws}],
    }


def prepared_mode(mana_raw: str, effects: list[dict[str, Any]]) -> dict[str, Any]:
    """The SOS Prepare mechanic's sorcery-speed follow-up cast.

    Mode kind ``prepared`` is recognized by the simulator: when a card with
    a prepared mode is cast (via its kind="cast" mode), the resulting
    permanent is flagged in ``GameState.prepared``. The S-tier policy then
    treats prepared modes on the battlefield as castable spells, similar
    to activated abilities.
    """
    return {
        "kind": "prepared",
        "cost": cost(mana_raw),
        "effects": effects,
    }


# ---------------------------------------------------------------------------
# Effect builders.
# ---------------------------------------------------------------------------


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


def fetch_land(
    *, target: str, dest: str, subtype: str | None = None, count: int = 1
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "kind": "fetch_land",
        "target_filter": target,
        "destination": dest,
        "count": count,
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


def produce_mana(*options: list[str]) -> dict[str, Any]:
    return {"kind": "produce_mana", "options": [list(opt) for opt in options]}


# ---------------------------------------------------------------------------
# Role feature helpers.
# ---------------------------------------------------------------------------


def body(
    power: str,
    toughness: str,
    *,
    colors: list[str] | None = None,
    subtypes: list[str] | None = None,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "power": str(power),
        "toughness": str(toughness),
        "colors": list(colors or []),
        "subtypes": list(subtypes or []),
        "keywords": list(keywords or []),
    }


# ===========================================================================
# Patch entries.
# ===========================================================================


def patch(set_code: str, collector: str, status: str, **fields: Any) -> dict[str, Any]:
    return {
        "set_code": set_code,
        "collector_number": str(collector),
        "status": status,
        "patch": fields,
    }


def llm(
    collector: str,
    *,
    role_features: dict[str, Any] | None = None,
    modes: list[dict[str, Any]] | None = None,
    reasons: list[str] | None = None,
    mana_abilities: list[dict[str, Any]] | None = None,
    enter_condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a SOS llm_encoded patch."""
    fields: dict[str, Any] = {}
    if role_features is not None:
        fields["role_features"] = role_features
    if modes is not None:
        fields["modes"] = modes
    if reasons is not None:
        fields["reasons"] = reasons
    if mana_abilities is not None:
        fields["mana_abilities"] = mana_abilities
    if enter_condition is not None:
        fields["enter_condition"] = enter_condition
    return patch("SOS", collector, "llm_encoded", **fields)


def needs_human(collector: str, why: str) -> dict[str, Any]:
    return patch("SOS", collector, "needs_human", reasons=[why])


PATCHES: list[dict[str, Any]] = []
P = PATCHES.append


# ---------------------------------------------------------------------------
# SECTION 1: PREPARE-LAYOUT CARDS (36)
# Per CLAUDE rules: pre-prepared encode the spell, conditionally-prepared do not.
# ---------------------------------------------------------------------------

# --- 1a. Pre-prepared (23 cards) — encode the prepared spell ---

# #12 Elite Interceptor {W} (1/2) // Rejoinder {1}{W} (Sorcery: tap or untap target creature, draw a card)
P(
    llm(
        "12",
        role_features={
            "is_creature": True,
            "cards_drawn": 1,
        },
        modes=[
            cast_mode("{W}", [etb()]),
            prepared_mode("{1}{W}", [noop("prepared:rejoinder_tap_draw"), draw(1)]),
        ],
        reasons=[
            "llm: prepare layout. 1W 1/2 creature; enters prepared.",
            "llm: prepared spell Rejoinder = 1W tap-or-untap target + draw 1 (sorcery speed, after creature ETB)",
            "llm: prepared spell encoded as 2nd cast mode pending simulator support — see SOS_PREPARED_NOTES.md",
        ],
    )
)

# #19 Honorbound Page {3}{W} (3/3 first strike) // Forum's Favor {W} (Sorcery: target creature +1/+0, flying EOT)
P(
    llm(
        "19",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{3}{W}", [etb()]),
            prepared_mode("{W}", [noop("prepared:pump_flying_eot")]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 3/3 first strike; enters prepared.",
            "llm: prepared spell Forum's Favor = W combat trick (+1/+0 + flying EOT) — sorcery speed in this case",
        ],
    )
)

# #27 Quill-Blade Laureate {1}{W} (1/1 double strike) // Twofold Intent {1}{W} (Sorcery: target creature +1/+0 double strike EOT)
P(
    llm(
        "27",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{W}", [etb()]),
            prepared_mode("{1}{W}", [noop("prepared:pump_double_strike_eot")]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 1/1 double strike; enters prepared.",
            "llm: prepared spell Twofold Intent = 1W combat-trick-like sorcery pump",
        ],
    )
)

# #40 Campus Composer {3}{U} (3/4 Ward {2}) // Aqueous Aria {4}{U} (Sorcery: create a 3/3 blue/red Elemental token with flying)
P(
    llm(
        "40",
        role_features={
            "is_creature": True,
            "creates_creatures": [
                body("3", "3", colors=["U", "R"], subtypes=["Elemental"], keywords=["flying"])
            ],
        },
        modes=[
            cast_mode("{3}{U}", [etb()]),
            prepared_mode("{4}{U}", [noop("prepared:create_3_3_flying_elemental")]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 3/4 ward 2; enters prepared.",
            "llm: prepared spell Aqueous Aria = 5-mana token creation",
        ],
    )
)

# #45 Emeritus of Ideation {3}{U}{U} (5/5 Flying Ward 2) // Ancestral Recall {U} (Instant: target player draws three cards)
# Front face: enters prepared + attack-trigger to re-prepare. We treat as pre-prepared per ETB.
P(
    llm(
        "45",
        role_features={
            "is_creature": True,
            "cards_drawn": 3,
        },
        modes=[
            cast_mode("{3}{U}{U}", [etb()]),
            prepared_mode("{U}", [draw(3)]),
        ],
        reasons=[
            "llm: prepare layout. 5-mana 5/5 flying ward 2; enters prepared.",
            "llm: prepared spell Ancestral Recall = U draw 3",
        ],
    )
)

# #55 Jadzi, Steward of Fate {2}{U} (2/4) // Oracle's Gift {X}{X}{U} (Sorcery: create X 0/0 Fractals with X +1/+1 counters each)
# Has ETB: When Jadzi enters, draw two then discard two. Enters prepared.
P(
    llm(
        "55",
        role_features={
            "is_creature": True,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{2}{U}", [etb(), draw(2), discard(2)]),
            prepared_mode("{X}{X}{U}", [noop("prepared:create_x_fractal_tokens")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 2/4 with ETB loot (draw 2 discard 2); enters prepared.",
            "llm: prepared spell Oracle's Gift = X-cost token creation; encoded as is_other (X-token)",
        ],
    )
)

# #56 Landscape Painter {1}{U} (2/1) // Vibrant Idea {4}{U} (Sorcery: draw two cards)
P(
    llm(
        "56",
        role_features={
            "is_creature": True,
            "cards_drawn": 2,
        },
        modes=[
            cast_mode("{1}{U}", [etb()]),
            prepared_mode("{4}{U}", [draw(2)]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/1; enters prepared.",
            "llm: prepared spell Vibrant Idea = 5-mana draw 2",
        ],
    )
)

# #67 Skycoach Conductor {2}{U} (2/3 Flash Flying Vigilance) // All Aboard {U} (Instant: exile target non-Pilot creature you control then return to battlefield)
P(
    llm(
        "67",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{U}", [etb()]),
            prepared_mode("{U}", [noop("prepared:flicker_own_creature")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 2/3 flash flying vigilance; enters prepared.",
            "llm: prepared spell All Aboard = U flicker own non-Pilot creature",
        ],
    )
)

# #68 Spellbook Seeker {3}{U} (3/3 Flying) // Careful Study {U} (Sorcery: draw 2 then discard 2 — net 0)
P(
    llm(
        "68",
        role_features={
            "is_creature": True,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{3}{U}", [etb()]),
            prepared_mode("{U}", [draw(2), discard(2)]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 3/3 flying; enters prepared.",
            "llm: prepared spell Careful Study = U loot 2 (net 0, but cards_manipulated += 2)",
        ],
    )
)

# #72 Adventurous Eater {2}{B} (3/2) // Have a Bite {B} (Sorcery: +1/+1 counter target creature, gain 1 life)
P(
    llm(
        "72",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
            prepared_mode("{B}", [noop("prepared:counter_lifegain")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/2; enters prepared.",
            "llm: prepared spell Have a Bite = B +1/+1 counter + gain 1 life (sorcery)",
        ],
    )
)

# #76 Cheerful Osteomancer {3}{B} (4/2) // Raise Dead {B} (Sorcery: return target creature card from graveyard to hand)
P(
    llm(
        "76",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{3}{B}", [etb()]),
            prepared_mode("{B}", [noop("prepared:return_creature_from_graveyard")]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 4/2; enters prepared.",
            "llm: prepared spell Raise Dead = B return target creature card from gy to hand",
        ],
    )
)

# #80 Emeritus of Woe {3}{B} (5/4) // Demonic Tutor {1}{B} (Sorcery: search library for a card to hand)
# Front face: enters prepared + EOS-trigger to re-prepare if 2+ creatures died.
P(
    llm(
        "80",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{3}{B}", [etb()]),
            prepared_mode("{1}{B}", [noop("prepared:tutor_any_card")]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 5/4; enters prepared (and re-preparable).",
            "llm: prepared spell Demonic Tutor = 1B tutor; we don't model tutors in role_features (per encoding guide §10)",
        ],
    )
)

# #109 Blazing Firesinger {2}{R} (2/3) // Seething Song {2}{R} (Instant: Add {R}{R}{R}{R}{R})
# A ramp/burst-mana spell. Important for simulator!
P(
    llm(
        "109",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{R}", [etb()]),
            prepared_mode("{2}{R}", [produce_mana(["R", "R", "R", "R", "R"])]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 2/3; enters prepared.",
            "llm: prepared spell Seething Song = 2R produce 5R; mana burst — needs sim awareness",
        ],
    )
)

# #117 Goblin Glasswright {1}{R} (2/2) // Craft with Pride {R} (Sorcery: create a Treasure token)
P(
    llm(
        "117",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}", [etb()]),
            prepared_mode("{R}", [noop("prepared:create_treasure")]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/2; enters prepared.",
            "llm: prepared spell Craft with Pride = R create a Treasure (mana ramp signal)",
        ],
    )
)

# #122 Maelstrom Artisan {1}{R}{R} (3/2 Haste) // Rocket Volley {1}{R} (Sorcery: destroy target nonbasic land)
P(
    llm(
        "122",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}{R}", [etb()]),
            prepared_mode("{1}{R}", [noop("prepared:destroy_nonbasic_land")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/2 haste; enters prepared.",
            "llm: prepared spell Rocket Volley = 1R land destruction",
        ],
    )
)

# #126 Pigment Wrangler {4}{R} (4/4 Flying) // Striking Palette {R} (Sorcery: copy next instant/sorcery this turn)
P(
    llm(
        "126",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{4}{R}", [etb()]),
            prepared_mode("{R}", [noop("prepared:copy_next_instant_or_sorcery")]),
        ],
        reasons=[
            "llm: prepare layout. 5-mana 4/4 flying; enters prepared.",
            "llm: prepared spell Striking Palette = R copy next instant/sorcery this turn",
        ],
    )
)

# #131 Strife Scholar {2}{R} (3/2 Ward Pay 2 life) // Awaken the Ages {5}{R} (Sorcery: create two 2/2 red/white Spirit tokens)
P(
    llm(
        "131",
        role_features={
            "is_creature": True,
            "creates_creatures": [
                body("2", "2", colors=["R", "W"], subtypes=["Spirit"]),
                body("2", "2", colors=["R", "W"], subtypes=["Spirit"]),
            ],
        },
        modes=[
            cast_mode("{2}{R}", [etb()]),
            prepared_mode("{5}{R}", [noop("prepared:create_two_2_2_spirits")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/2 ward(life); enters prepared.",
            "llm: prepared spell Awaken the Ages = 6-mana create 2 spirits",
        ],
    )
)

# #145 Emeritus of Abundance {2}{G} (3/4 Vigilance) // Regrowth {1}{G} (Sorcery: return any card from gy to hand)
# Front face: enters prepared + landfall-style attack trigger. Pre-prepared.
P(
    llm(
        "145",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{G}", [etb()]),
            prepared_mode("{1}{G}", [noop("prepared:return_any_card_from_graveyard")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/4 vigilance; enters prepared (re-preparable when 8+ lands).",
            "llm: prepared spell Regrowth = 1G return any card from gy to hand",
        ],
    )
)

# #152 Infirmary Healer {1}{G} (2/3) // Stream of Life {X}{G} (Sorcery: target player gains X life)
P(
    llm(
        "152",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{G}", [etb()]),
            prepared_mode("{X}{G}", [noop("prepared:gain_x_life")]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/3; enters prepared.",
            "llm: prepared spell Stream of Life = X+G lifegain (no sim impact)",
        ],
    )
)

# #162 Studious First-Year {G} (1/1) // Rampant Growth {1}{G} (Sorcery: search basic land tapped to battlefield)
# Critical mana ramp creature! User explicitly called this out.
P(
    llm(
        "162",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{G}", [etb()]),
            prepared_mode(
                "{1}{G}",
                [
                    fetch_land(target="basic", dest="battlefield_tapped"),
                ],
            ),
        ],
        reasons=[
            "llm: prepare layout. 1-mana 1/1 bear; enters prepared.",
            "llm: prepared spell Rampant Growth = 1G basic-land ramp; encoded as fetch_land effect",
            "llm: USER CALLOUT — this card needs sim support for prepared ramp on T2+ to model correctly",
        ],
    )
)

# #166 Vastlands Scavenger {1}{G}{G} (4/4 Deathtouch) // Bind to Life {4}{G} (Instant: Mill 7 then put a creature card from among them onto the battlefield)
P(
    llm(
        "166",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{G}{G}", [etb()]),
            prepared_mode("{4}{G}", [noop("prepared:reanimate_via_mill")]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 4/4 deathtouch; enters prepared.",
            "llm: prepared spell Bind to Life = 5-mana mill 7 + reanimate",
        ],
    )
)

# #199 Lluwen, Exchange Student {2}{B}{G} (3/4) // Pest Friend {B/G} (Sorcery: create a 1/1 black/green Pest token with attack-lifegain trigger)
# Front face: enters prepared + activated to re-prepare (exile gy creature card)
P(
    llm(
        "199",
        role_features={
            "is_creature": True,
            "creates_creatures": [body("1", "1", colors=["B", "G"], subtypes=["Pest"])],
        },
        modes=[
            cast_mode("{2}{B}{G}", [etb()]),
            prepared_mode("{B/G}", [noop("prepared:create_pest_token")]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 3/4 multicolor; enters prepared (re-preparable).",
            "llm: prepared spell Pest Friend = BG/hybrid token creation",
        ],
    )
)

# #223 Sanar, Unfinished Genius {U}{R} (0/4) // Wild Idea {3}{U}{R} (Sorcery: tutor for an instant or sorcery)
# Front face: enters prepared + activated treasure on cast-instant/sorcery condition
P(
    llm(
        "223",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{U}{R}", [etb()]),
            prepared_mode("{3}{U}{R}", [noop("prepared:tutor_instant_sorcery")]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 0/4 multicolor; enters prepared.",
            "llm: prepared spell Wild Idea = 5-mana instant/sorcery tutor",
        ],
    )
)


# --- 1b. Conditionally prepared (13 cards) — encode ONLY the creature ---

# #13 Emeritus of Truce {1}{W}{W} (3/3) // Swords to Plowshares
# Front: ETB creates 1/1 Inkling for target player; conditional re-prepare
P(
    llm(
        "13",
        role_features={
            "is_creature": True,
            "creates_creatures": [
                body("1", "1", colors=["W", "B"], subtypes=["Inkling"], keywords=["flying"])
            ],
        },
        modes=[
            cast_mode("{1}{W}{W}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/3 with conditional prepared (only if opp has more creatures).",
            "llm: per project owner rule — conditional prepared spells are NOT encoded.",
            "llm: ETB creates a 1/1 white/black Inkling token with flying for any target player",
        ],
    )
)

# #23 Joined Researchers {1}{W} (2/2 First strike) // Secret Rendezvous {1}{W}{W}
# Front: EOS trigger that re-prepares when opp has more cards in hand. Not pre-prepared.
P(
    llm(
        "23",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{W}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/2 first strike with conditional prepare (opp has more cards in hand).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #33 Spiritcall Enthusiast {2}{W} (3/3) // Scrollboost {1}{W}
# Front: triggers on tokens entering. Not pre-prepared.
P(
    llm(
        "33",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{W}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/3 with conditional prepare (when tokens you control enter).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #46 Encouraging Aviator {2}{U} (2/3 Flying) // Jump {U}
# Front: prepares on attack. Not pre-prepared.
P(
    llm(
        "46",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{U}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 2/3 flying with conditional prepare (on attack).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #52 Harmonized Trio {U} (1/1) // Brainstorm {U}
# Front: T,Tap two creatures to prepare. Not pre-prepared.
P(
    llm(
        "52",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{U}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 1-mana 1/1 with activated prepare (T + tap 2).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #85 Grave Researcher {2}{B} (3/3) // Reanimate {B}
# Front: upkeep surveil 1 + conditional prepare on 3+ creatures in gy. Not pre-prepared.
P(
    llm(
        "85",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 3/3 with upkeep surveil + conditional prepare (3+ creatures in gy).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
            "llm: upkeep surveil 1 doesn't count for cards_manipulated (turn-based; not at-cast-time)",
        ],
    )
)

# #88 Leech Collector {1}{B} (2/2) // Bloodletting {B}
# Front: prepares when you gain life. Not pre-prepared.
P(
    llm(
        "88",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{B}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/2 with conditional prepare on lifegain.",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #98 Scathing Shadelock {4}{B} (4/6) // Venomous Words {B}
# Front: first main phase trigger to prepare. Not pre-prepared (but reliable later).
P(
    llm(
        "98",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{4}{B}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 5-mana 4/6 with first-main-phase prepare trigger.",
            "llm: not pre-prepared (delayed until next first main phase) — prepared spell ignored per owner rule.",
        ],
    )
)

# #99 Scheming Silvertongue {1}{B} (1/3 Flying Lifelink) // Sign in Blood {B}{B}
# Front: prepares on second main phase if gained 2+ life. Not pre-prepared.
P(
    llm(
        "99",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{B}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 1/3 flying lifelink with conditional prepare.",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #113 Emeritus of Conflict {1}{R} (2/2 First strike) // Lightning Bolt {R}
# Front: prepares on third spell each turn. Not pre-prepared.
P(
    llm(
        "113",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/2 first strike with conditional prepare (third spell/turn).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #170 Abigale, Poet Laureate {1}{W}{B} (2/3 Flying) // Heroic Stanza {1}{W/B}
# Front: prepares whenever you cast a creature spell. Not pre-prepared.
P(
    llm(
        "170",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{W}{B}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 3-mana 2/3 flying with creature-cast prepare trigger.",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #198 Kirol, History Buff {R}{W} (2/3) // Pack a Punch {1}{R}{W}
# Front: prepares when cards leave your gy. Not pre-prepared.
P(
    llm(
        "198",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{R}{W}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 2-mana 2/3 multicolor with conditional prepare (gy leaves).",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)

# #237 Tam, Observant Sequencer {2}{G}{U} (4/3) // Deep Sight {G}{U}
# Front: landfall-style prepare. Not pre-prepared.
P(
    llm(
        "237",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{G}{U}", [etb()]),
        ],
        reasons=[
            "llm: prepare layout. 4-mana 4/3 multicolor with landfall prepare trigger.",
            "llm: not pre-prepared — prepared spell ignored per owner rule.",
        ],
    )
)


# ---------------------------------------------------------------------------
# SECTION 2: SOS-original primary cards (non-prepare layout)
# ---------------------------------------------------------------------------

# --- White ---

# #7 Antiquities on the Loose {1}{W}{W} Sorcery: create two 2/2 Spirits, +1 counter rider if cast non-hand. Flashback {4}{W}{W}.
# Flashback mode dropped: sim treats cast modes as alt-from-hand which is wrong for flashback (gy-only).
P(
    llm(
        "7",
        role_features={
            "creates_creatures": [
                body("2", "2", colors=["R", "W"], subtypes=["Spirit"]),
                body("2", "2", colors=["R", "W"], subtypes=["Spirit"]),
            ],
        },
        modes=[
            cast_mode("{1}{W}{W}", [noop("token_creation")]),
        ],
        reasons=[
            "llm: 3-mana 2x 2/2 Spirit tokens; flashback {4}{W}{W} dropped (gy-only cast not modeled)"
        ],
    )
)

# #9 Daydream {W} Sorcery: blink (exile own creature, return with +1/+1 counter). Flashback {2}{W}.
P(
    llm(
        "9",
        role_features={},
        modes=[
            cast_mode("{W}", [noop("blink_own_creature_with_counter")]),
        ],
        reasons=["llm: 1-mana blink-own-creature with +1/+1 counter; flashback {2}{W} dropped"],
    )
)

# #10 Dig Site Inventory {W} Sorcery: +1/+1 counter on creature you control + vigilance EOT. Flashback {W}.
P(
    llm(
        "10",
        role_features={},
        modes=[
            cast_mode("{W}", [noop("counter_pump_with_vigilance")]),
        ],
        reasons=["llm: 1-mana +1/+1 counter on own creature, vigilance EOT; flashback {W} dropped"],
    )
)

# #16 Graduation Day {W} Enchantment with Repartee trigger (+1/+1 counter on creature on instant/sorcery target-creature cast).
P(
    llm(
        "16",
        role_features={
            # Just an enchantment that gives buffs based on triggered ability — sim irrelevant.
        },
        modes=[
            cast_mode("{W}", [etb()]),
        ],
        reasons=["llm: 1-mana enchantment with Repartee trigger; no sim-affecting ETB"],
    )
)

# #17 Group Project {1}{W} Sorcery: create a 2/2 Spirit. Flashback—Tap three untapped creatures.
P(
    llm(
        "17",
        role_features={
            "creates_creatures": [body("2", "2", colors=["R", "W"], subtypes=["Spirit"])],
        },
        modes=[
            cast_mode("{1}{W}", [noop("create_spirit")]),
        ],
        reasons=[
            "llm: 2-mana create a 2/2 Spirit; flashback cost = tap 3 untapped creatures (non-mana, dropped)",
        ],
    )
)

# #20 Informed Inkwright {1}{W} (2/2 Vigilance) — Repartee triggers create Inkling tokens
# Repartee creates conditional tokens — leave OFF per encoding-guide rule on conditional pump-rider draw / tokens.
P(
    llm(
        "20",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{W}", [etb()]),
        ],
        reasons=[
            "llm: 2-mana 2/2 vigilance with Repartee trigger creating 1/1 Inkling tokens conditionally",
            "llm: conditional token creation NOT counted in creates_creatures (per encoding guide §2 'conditional pump-rider' rule extended)",
        ],
    )
)

# #25 Practiced Offense {2}{W} Sorcery: distribute +1/+1 counters + double strike or lifelink target creature. Flashback {1}{W}.
P(
    llm(
        "25",
        role_features={
            # Counter distribution + combat trick rider on a sorcery: per guide §7 "+1/+1 counter on sorcery → is_other"
            "is_other": True,
        },
        modes=[
            cast_mode("{2}{W}", [noop("counter_distribution_plus_pump")]),
        ],
        reasons=["llm: 3-mana counter-distribution + sorcery pump; flashback {1}{W} dropped"],
    )
)

# #28 Rapier Wit {1}{W} Instant: tap target creature, stun if your turn, draw a card.
# Tap+stun is creature-soft-removal-ish. Draw is a real card draw.
P(
    llm(
        "28",
        role_features={
            "cards_drawn": 1,
        },
        modes=[
            cast_mode("{1}{W}", [draw(1)]),
        ],
        reasons=["llm: 2-mana tap target + stun-on-your-turn + draw 1 (instant)"],
    )
)

# #29 Rehearsed Debater {2}{W} (3/3 Vigilance) — Repartee gives +1/+1 EOT
P(
    llm(
        "29",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{W}", [etb()]),
        ],
        reasons=["llm: 3-mana 3/3 vigilance with Repartee self-pump trigger"],
    )
)

# #32 Soaring Stoneglider {2}{W} (4/3 Flying Vigilance) — additional cost: exile 2 from gy or pay {1}{W}
# Treat additional cost as part of casting cost — but the player can choose to pay {1}{W} so effective worst-case cost is {3}{W}{W}.
# Per encoding guide we encode the printed cost. The "additional cost" doesn't affect mana CSP (it's modal) — let's use {3}{W}{W} as primary printed.
# Actually, looking again: the printed cost IS {2}{W} but you must EITHER exile 2 cards OR pay {1}{W}. Minimum payable cost = {2}{W} + {1}{W} = {3}{W}{W} when no gy.
# In Limited there often won't be 2 cards in gy turn 4. For mulligan-relevance the safer interpretation: minimum cost = {3}{W}{W}.
# But we encode the printed cost since the player can sometimes pay alt. Conservative call: encode printed cost (sim assumption is player can sometimes meet the alt).
# I'll go with printed {2}{W} + reasons noting the additional cost.
P(
    llm(
        "32",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{W}", [etb()]),
        ],
        reasons=[
            "llm: 3-mana 4/3 flying vigilance with additional cost: exile 2 from gy OR pay {1}{W}",
            "llm: encoded printed cost only — additional cost is satisfiable by either branch",
        ],
    )
)

# #35 Stirring Hopesinger {2}{W} (1/3 Flying Lifelink) — Repartee +1/+1 each creature you control
P(
    llm(
        "35",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{W}", [etb()]),
        ],
        reasons=["llm: 3-mana 1/3 flying lifelink with Repartee anthem-trigger"],
    )
)


# --- Blue ---

# #42 Deluge Virtuoso {2}{U} (2/2) — ETB tap creature + stun counter; Opus trigger
# ETB is a soft-removal/tap effect. Doesn't fit role_features categories well — leave as is_creature only.
P(
    llm(
        "42",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{U}", [etb()]),
        ],
        reasons=["llm: 3-mana 2/2 with ETB tap+stun (creature only); Opus trigger ignored"],
    )
)

# #43 Divergent Equation {X}{X}{U} Instant: return up to X instant/sorcery cards from gy. Then exile.
P(
    llm(
        "43",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{X}{X}{U}", [noop("return_x_instants_or_sorceries_from_gy")]),
        ],
        reasons=["llm: X-cost gy-return for instants/sorceries; exiles itself after"],
    )
)

# #48 Exhibition Tidecaller {U} (0/2) — Opus mill trigger
P(
    llm(
        "48",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{U}", [etb()]),
        ],
        reasons=["llm: 1-mana 0/2 with Opus mill trigger; sim-irrelevant"],
    )
)

# #49 Flow State {1}{U} Sorcery: look at top 3 take 1, conditional rebuy
# This is a hand-fetch / dig effect — encode as look_at_top.
# Per the encoding guide on look-at-top-N: cards_drawn=1 (the card put in
# hand) + cards_manipulated=N-1.
P(
    llm(
        "49",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{1}{U}", [look_top(3, accepts_land=True, accepts_nonland=True)]),
        ],
        reasons=["llm: 2-mana look-at-top-3 take 1 to hand; +1 card, manipulated=2"],
    )
)

# #51 Fractalize {X}{U} Instant: target creature becomes Fractal X+1/X+1
# Combat-trick-ish but variable. Variable-power on a single creature → no combat-trick, is_other.
P(
    llm(
        "51",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{X}{U}", [noop("fractal_transform_target")]),
        ],
        reasons=["llm: X-cost transform target creature into X+1/X+1 Fractal; not modeled"],
    )
)

# #58 Mathemagics {X}{X}{U}{U} Sorcery: target player draws 2^X cards
# X cost; minimum X=1 → draw 2 cards. Encode as draw 2.
P(
    llm(
        "58",
        role_features={
            "cards_drawn": 2,
        },
        modes=[
            cast_mode("{X}{X}{U}{U}", [draw(2)]),
        ],
        reasons=[
            "llm: X^2-cost target player draws 2^X; min X=1 → draw 2 (matches X=1 sim convention)"
        ],
    )
)

# #60 Muse Seeker {1}{U} (1/2) — Opus draw + conditional discard
P(
    llm(
        "60",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{U}", [etb()]),
        ],
        reasons=[
            "llm: 2-mana 1/2 with Opus draw-then-conditional-discard; conditional → not counted"
        ],
    )
)

# #63 Pensive Professor {1}{U}{U} (0/2) — Increment + counter triggers
P(
    llm(
        "63",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{U}{U}", [etb()]),
        ],
        reasons=["llm: 3-mana 0/2 with Increment + +1/+1-counter triggered abilities"],
    )
)

# #64 Procrastinate {X}{U} Sorcery: tap target creature, put 2X stun counters
P(
    llm(
        "64",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{X}{U}", [noop("tap_plus_2x_stun")]),
        ],
        reasons=["llm: X-cost tap+stun; soft removal-ish but no modeled flag"],
    )
)

# #66 Run Behind {3}{U} Instant: tucks target creature top-or-bottom of library (cost reduction if target is attacking)
# This is is_top_library (per encoding guide).
P(
    llm(
        "66",
        role_features={
            "is_top_library": True,
        },
        modes=[
            cast_mode("{3}{U}", [noop("top_or_bottom_target")]),
        ],
        reasons=["llm: 4-mana tuck target creature (cost reduction if attacking); is_top_library"],
    )
)

# #69 Tester of the Tangential {1}{U} (1/1) — Increment + combat trigger
P(
    llm(
        "69",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{U}", [etb()]),
        ],
        reasons=["llm: 2-mana 1/1 with Increment + combat trigger"],
    )
)

# #70 Textbook Tabulator {2}{U} (0/3) — Increment + ETB surveil 2
# ETB surveil 2 → cards_manipulated=2
P(
    llm(
        "70",
        role_features={
            "is_creature": True,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{2}{U}", [etb(), scry(2)]),  # surveil ~ scry for sim purposes
        ],
        reasons=["llm: 3-mana 0/3 with Increment + ETB surveil 2 (encoded as scry 2 for sim)"],
    )
)


# --- Black ---

# #77 Cost of Brilliance {2}{B} Sorcery: target draws 2 + loses 2 + +1/+1 counter on creature
# Net: cards_drawn=2 (you target yourself), is_other for the rest
P(
    llm(
        "77",
        role_features={
            "cards_drawn": 2,
        },
        modes=[
            cast_mode("{2}{B}", [draw(2)]),
        ],
        reasons=[
            "llm: 3-mana target player draws 2 + loses 2 + +1/+1 counter; you target yourself"
        ],
    )
)

# #79 Dissection Practice {B} Instant: opp loses 1 / +1/+1 / -1/-1 modal.
# Modal — not removal (only -1/-1, kills 1-toughness only). Combat trick only on +1/+1 mode.
P(
    llm(
        "79",
        role_features={
            # +1/+1 instant mode → combat trick.
            "combat_trick_power": 1,
            "combat_trick_toughness": 1,
        },
        modes=[
            cast_mode("{B}", [noop("modal_lifedrain_or_pump_or_debuff")]),
        ],
        reasons=["llm: 1-mana modal: opp -1 / +1+1 EOT / -1-1 EOT; combat trick on the pump mode"],
    )
)

# #81 End of the Hunt {1}{B} Sorcery: opp exiles their highest-MV creature/PW
# Edict-style exile. Not direct creature removal in the targeted sense, but functionally removal.
# Per encoding guide §1, exile is removal_destroy_or_exile when it's "a creature or planeswalker they control".
P(
    llm(
        "81",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{1}{B}", [noop("opp_exiles_largest_creature_or_pw")]),
        ],
        reasons=["llm: 2-mana opp exiles their largest creature/PW; counts as removal"],
    )
)

# #83 Foolish Fate {2}{B} Instant: destroy target creature + Infusion lifeloss
P(
    llm(
        "83",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{2}{B}", [noop("destroy_creature_plus_infusion_lifeloss")]),
        ],
        reasons=["llm: 3-mana destroy target creature + Infusion 3 life loss"],
    )
)

# #86 Last Gasp {1}{B} Instant: target creature -3/-3 EOT
# Per encoding guide §7: "-N/-N N>=3 → removal"
P(
    llm(
        "86",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{1}{B}", [noop("removal_minus_3_minus_3")]),
        ],
        reasons=[
            "llm: 2-mana -3/-3 target creature EOT; -3/-3 → removal_destroy_or_exile per guide §7"
        ],
    )
)

# #87 Lecturing Scornmage {B} (1/1) — Repartee +1/+1 counter trigger
P(
    llm(
        "87",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{B}", [etb()]),
        ],
        reasons=["llm: 1-mana 1/1 with Repartee self-pump trigger"],
    )
)

# #90 Melancholic Poet {1}{B} (2/2) — Repartee opp loses 1 / you gain 1 trigger
P(
    llm(
        "90",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{B}", [etb()]),
        ],
        reasons=["llm: 2-mana 2/2 with Repartee drain trigger"],
    )
)

# #91 Moseo, Vein's New Dean {2}{B} (2/1 Flying) — ETB creates a 1/1 Pest with attack-lifegain
P(
    llm(
        "91",
        role_features={
            "is_creature": True,
            "creates_creatures": [body("1", "1", colors=["B", "G"], subtypes=["Pest"])],
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
        ],
        reasons=["llm: 3-mana 2/1 flying with ETB Pest token + Infusion EOS draw trigger"],
    )
)

# #92 Poisoner's Apprentice {2}{B} (2/2) — Infusion ETB -4/-4 if you gained life this turn
# Conditional removal — sometimes is, sometimes isn't. Per encoding guide §7, leave OFF if conditional.
P(
    llm(
        "92",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
        ],
        reasons=[
            "llm: 3-mana 2/2 with conditional ETB -4/-4 (Infusion); too conditional for removal flag"
        ],
    )
)

# #95 Pull from the Grave {2}{B} Sorcery: return up to 2 creature cards from gy + 2 life
P(
    llm(
        "95",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{2}{B}", [noop("return_two_creatures_from_gy")]),
        ],
        reasons=["llm: 3-mana return up to 2 creature cards from gy + gain 2 life"],
    )
)

# #96 Rabid Attack {1}{B} Instant: any number of creatures get +1/+0 + dies-trigger draw card
P(
    llm(
        "96",
        role_features={
            "combat_trick_power": 1,
            "combat_trick_toughness": 0,
        },
        modes=[
            cast_mode("{1}{B}", [noop("attack_pump_with_dies_draw")]),
        ],
        reasons=["llm: 2-mana any-target +1/+0 + dies-draw rider; combat trick"],
    )
)

# #103 Ulna Alley Shopkeep {2}{B} (2/3 Menace) — Infusion +2/+0 conditional
P(
    llm(
        "103",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
        ],
        reasons=["llm: 3-mana 2/3 menace with Infusion conditional self-pump"],
    )
)

# #105 Withering Curse {1}{B}{B} Sorcery: -2/-2 all creatures EOT + Infusion destroy all instead
# Per encoding guide §1: "Each creature gets -N/-N until end of turn" — total reduction >=2 → mass removal
P(
    llm(
        "105",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{1}{B}{B}", [noop("mass_minus_2_minus_2_or_destroy_all")]),
        ],
        reasons=["llm: 3-mana mass -2/-2 all (Infusion: destroy all instead) — mass removal"],
    )
)


# --- Red ---

# #108 Artistic Process {3}{R}{R} Sorcery: modal — 6 to creature / 2 to each non-yours / create 3/3 flying Elemental
# Aggregated: burn=6 (max across modes for option value) + mass_removal + removal_destroy_or_exile (6 kills) + token.
P(
    llm(
        "108",
        role_features={
            "removal_burn_damage": 6,
            "removal_destroy_or_exile": True,
            "is_mass_removal": True,
            "creates_creatures": [
                body("3", "3", colors=["U", "R"], subtypes=["Elemental"], keywords=["flying"])
            ],
        },
        modes=[
            cast_mode("{3}{R}{R}", [noop("modal_burn_or_sweeper_or_token")]),
        ],
        reasons=[
            "llm: 5-mana modal — 6 to creature OR 2 to each non-yours OR 3/3 flying token (aggregated, burn=max)"
        ],
    )
)

# #111 Choreographed Sparks {R}{R} Instant: copy target instant/sorcery OR copy target creature spell
P(
    llm(
        "111",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{R}{R}", [noop("copy_spell")]),
        ],
        reasons=["llm: 2-mana copy your instant/sorcery and/or creature spell; modal"],
    )
)

# #112 Duel Tactics {R} Sorcery: 1 to creature + can't block. Flashback {1}{R}.
P(
    llm(
        "112",
        role_features={
            "removal_burn_damage": 1,
        },
        modes=[
            cast_mode("{R}", [noop("burn_one_plus_no_block")]),
        ],
        reasons=["llm: 1-mana burn 1 + can't-block rider; flashback {1}{R} dropped"],
    )
)

# #114 Expressive Firedancer {1}{R} (2/2) — Opus pump + double strike rider trigger
P(
    llm(
        "114",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}", [etb()]),
        ],
        reasons=["llm: 2-mana 2/2 with Opus combat-pump triggers"],
    )
)

# #115 Flashback {R} Instant: target instant/sorcery in gy gains flashback EOT (cost = mv)
# A form of card advantage but the "draw" comes via casting from gy. Not a draw effect per se.
P(
    llm(
        "115",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{R}", [noop("grant_flashback_to_target_in_gy")]),
        ],
        reasons=["llm: 1-mana grant flashback to target instant/sorcery in gy; gy-recursion"],
    )
)

# #119 Impractical Joke {R} Sorcery: 3 to up to 1 creature/PW, can't be prevented
P(
    llm(
        "119",
        role_features={
            "removal_burn_damage": 3,
        },
        modes=[
            cast_mode("{R}", [noop("burn_3")]),
        ],
        reasons=["llm: 1-mana burn 3 to creature/PW (sorcery)"],
    )
)

# #125 Molten-Core Maestro {1}{R} (2/2 Menace) — Opus +1/+1 + R-mana rider
# The R-mana rider is conditional (5+ mana spell), so we don't encode it as ramp.
P(
    llm(
        "125",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}", [etb()]),
        ],
        reasons=["llm: 2-mana 2/2 menace with Opus self-pump + conditional R-rider"],
    )
)

# #129 Seize the Spoils {2}{R} Sorcery: additional cost discard a card. Draw 2 + create Treasure.
# Net: cards_drawn=1 (draw 2 - discard 1).
P(
    llm(
        "129",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{2}{R}", [draw(2), discard(1)]),
        ],
        reasons=[
            "llm: 3-mana additional discard 1, draw 2 + create Treasure; net +1 card + treasure ramp"
        ],
    )
)

# #130 Steal the Show {2}{R} Sorcery: discard hand & redraw OR damage = instant/sorcery in gy to target
# Modal — neither is direct removal/draw in any reliable way. Mark is_other.
P(
    llm(
        "130",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{2}{R}", [noop("modal_discard_redraw_or_burn_x")]),
        ],
        reasons=["llm: 3-mana modal — wheel-target-opp / damage = instants in gy"],
    )
)

# #134 Thunderdrum Soloist {1}{R} (1/3 Reach) — Opus 1-damage trigger
P(
    llm(
        "134",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{R}", [etb()]),
        ],
        reasons=["llm: 2-mana 1/3 reach with Opus 1-burn trigger to opp"],
    )
)

# #135 Tome Blast {1}{R} Sorcery: 2 damage to any target. Flashback {4}{R}.
P(
    llm(
        "135",
        role_features={
            "removal_burn_damage": 2,
        },
        modes=[
            cast_mode("{1}{R}", [noop("burn_2_any_target")]),
        ],
        reasons=["llm: 2-mana burn 2 any target; flashback {4}{R} dropped"],
    )
)


# --- Green ---

# #140 Ambitious Augmenter {G} (1/1) — Increment + dies-trigger
P(
    llm(
        "140",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{G}", [etb()]),
        ],
        reasons=["llm: 1-mana 1/1 turtle with Increment + dies-trigger"],
    )
)

# #144 Efflorescence {2}{G} Instant: 2 +1/+1 counters + Infusion trample/indestructible EOT
# Counters on instant → combat trick per guide §3 (treat counter as +1/+1 plus rider keywords)
P(
    llm(
        "144",
        role_features={
            "combat_trick_power": 2,
            "combat_trick_toughness": 2,
            "combat_trick_granted_keywords": ["trample", "indestructible"],
        },
        modes=[
            cast_mode("{2}{G}", [noop("two_counters_plus_infusion_trample_indestructible")]),
        ],
        reasons=[
            "llm: 3-mana +2/+2 counters + Infusion conditional trample/indestructible; combat trick"
        ],
    )
)

# #148 Follow the Lumarets {1}{G} Sorcery: Infusion look at top 4 reveal land/creature → hand
P(
    llm(
        "148",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 3,
        },
        modes=[
            cast_mode("{1}{G}", [look_top(4, accepts_land=True, accepts_nonland=True)]),
        ],
        reasons=[
            "llm: 2-mana look at top 4 take 1 (or 2 if Infusion); cards_drawn=1, manipulated=3"
        ],
    )
)

# #150 Glorious Decay {1}{G} Instant: modal — destroy artifact / 4 to flyer / exile gy + draw a card
P(
    llm(
        "150",
        role_features={
            # Anti-flyer mode → removal_burn_damage. Anti-artifact → no role flag (non-creature destroy = is_other).
            # Card-draw mode → cards_drawn=1.
            "removal_burn_damage": 4,
            "cards_drawn": 1,
        },
        modes=[
            cast_mode("{1}{G}", [noop("modal_dest_artifact_burn_flyer_or_gy_draw")]),
        ],
        reasons=["llm: 2-mana modal — destroy artifact / 4 to flyer / exile gy card + draw"],
    )
)

# #153 Lumaret's Favor {1}{G} Instant: +2/+4 EOT + Infusion conditional copy
# Combat trick (instant pump)
P(
    llm(
        "153",
        role_features={
            "combat_trick_power": 2,
            "combat_trick_toughness": 4,
        },
        modes=[
            cast_mode("{1}{G}", [noop("pump_2_4_plus_infusion_copy")]),
        ],
        reasons=["llm: 2-mana +2/+4 EOT (combat trick) + Infusion conditional copy"],
    )
)

# #164 Thornfist Striker {2}{G} (3/3 Ward 1) — Infusion creatures get +1/+0 + trample/menace?
P(
    llm(
        "164",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{G}", [etb()]),
        ],
        reasons=["llm: 3-mana 3/3 ward 1 with Infusion conditional team buff"],
    )
)

# #165 Topiary Lecturer {2}{G} (1/2) — Increment + {T}: add G equal to power
# This is a mana dork! The Tap-add-G ability is conditional on power but base is 1/2 → produces G when activated.
# Encode as mana_ability with condition based on power.
# Conservative: encode as basic mana dork (T, add G). Per guide §9 conditional buffs encode unconditional baseline; here baseline is "amount of G equal to power" = 1 minimum.
P(
    llm(
        "165",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{G}", [etb()]),
        ],
        mana_abilities=[
            {
                "cost": cost("", tap=True),
                "produces": [["G"]],
                "condition": None,
            }
        ],
        reasons=[
            "llm: 3-mana 1/2 with Increment + T-add-G-per-power mana ability",
            "llm: encoded as basic mana dork (1 G); power buff via Increment is conditional",
        ],
    )
)


# --- Multicolor ---

# #178 Borrowed Knowledge {2}{R}{W} Sorcery: modal — discard hand, draw =opp's hand OR draw =discarded
P(
    llm(
        "178",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{2}{R}{W}", [noop("modal_wheel_self")]),
        ],
        reasons=["llm: 4-mana modal wheel-self; cards_drawn variable & conditional"],
    )
)

# #180 Colorstorm Stallion {1}{U}{R} (3/3 Haste Ward 1) — Opus pump + token-copy rider
P(
    llm(
        "180",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{U}{R}", [etb()]),
        ],
        reasons=["llm: 3-mana 3/3 haste ward with Opus pump + conditional copy-token rider"],
    )
)

# #183 Cuboid Colony {G}{U} (1/1 Flying Trample Flash) — Increment
P(
    llm(
        "183",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{G}{U}", [etb()]),
        ],
        reasons=["llm: 2-mana 1/1 flying trample flash; Increment self-grow"],
    )
)

# #184 Dina's Guidance {1}{B}{G} Instant: tutor for a creature card to hand or graveyard
# Per guide §10: tutors stay as is_other.
P(
    llm(
        "184",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{1}{B}{G}", [noop("tutor_creature_to_hand_or_gy")]),
        ],
        reasons=["llm: 3-mana tutor a creature to hand or gy; tutor → is_other per guide §10"],
    )
)

# #185 Elemental Mascot {1}{U}{R} (1/4 Flying Vigilance) — Opus pump/exile-and-cast
P(
    llm(
        "185",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{1}{U}{R}", [etb()]),
        ],
        reasons=["llm: 3-mana 1/4 flying vigilance with Opus pump + exile-cast rider"],
    )
)

# #196 Inkling Mascot {W}{B} (2/2) — Repartee flying + surveil 1 trigger
# Surveil 1 on a triggered ability — too conditional, leave off cards_manipulated.
P(
    llm(
        "196",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{W}{B}", [etb()]),
        ],
        reasons=[
            "llm: 2-mana 2/2 with Repartee flying + surveil trigger; conditional → no cards_manipulated"
        ],
    )
)

# #200 Lorehold Charm {R}{W} Instant: modal — each opp sacs artifact / return MV<=2 from gy / +1/+1 anthem EOT
# Aggregated across modes: anthem is the only mode that maps to a flag (combat trick).
# The artifact-sac and gy-reanimate modes have no role flags (no creature destroy, no creature creation).
P(
    llm(
        "200",
        role_features={
            "combat_trick_power": 1,
            "combat_trick_toughness": 1,
        },
        modes=[
            cast_mode("{R}{W}", [noop("modal_charm")]),
        ],
        reasons=["llm: 2-mana Lorehold modal Charm; aggregated — anthem mode → combat trick +1/+1"],
    )
)

# #202 Mind into Matter {X}{G}{U} Sorcery: draw X + may put a permanent MV<=X from hand to battlefield tapped
# Min X=1 → draw 1 + put a 1-mv permanent into play
P(
    llm(
        "202",
        role_features={
            "cards_drawn": 1,
        },
        modes=[
            cast_mode("{X}{G}{U}", [draw(1)]),
        ],
        reasons=["llm: X+GU draw X + cheat permanent MV<=X; min X=1 → draw 1"],
    )
)

# #203 Mind Roots {1}{B}{G} Sorcery: target player discards 2 + put up to 1 land into play tapped
# This is a discard + ramp for you. Encode as fetch_land — but the land comes from the opponent's discarded.
# The "put up to one land card discarded this way onto the battlefield tapped under your control" is a play-from-graveyard, not from library.
# For sim: this could effectively put a land into play (from opp's hand → discarded → battlefield).
# Conservative: this is mana ramp signal but conditional on opp having a land in hand. Leave it as is_other.
P(
    llm(
        "203",
        role_features={
            "is_other": True,
        },
        modes=[
            cast_mode("{1}{B}{G}", [noop("opp_discard_two_then_steal_a_land")]),
        ],
        reasons=["llm: 3-mana opp discards 2 + you steal a land to play tapped; conditional ramp"],
    )
)

# #204 Molten Note {X}{R}{W} Sorcery: damage = mana spent + untap your creatures. Flashback {6}{R}{W}.
P(
    llm(
        "204",
        role_features={
            "removal_burn_damage": 1,  # min damage at min X
        },
        modes=[
            cast_mode("{X}{R}{W}", [noop("burn_x_plus_untap_creatures")]),
        ],
        reasons=["llm: X+RW burn target + untap creatures; flashback {6}{R}{W} dropped"],
    )
)

# #211 Prismari Charm {U}{R} Instant: surveil 2 + draw / 1 to one or two targets / bounce nonland
# Modal Charm; per guide treat each mode separately.
P(
    llm(
        "211",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
            "removal_burn_damage": 1,
            "is_bounce": True,
        },
        modes=[
            cast_mode("{U}{R}", [noop("modal_charm")]),
        ],
        reasons=["llm: 2-mana Prismari Charm modal — surveil2+draw / 1-burn / bounce"],
    )
)

# #216 Pursue the Past {R}{W} Sorcery: gain 2 + may discard 1, draw 2. Flashback {2}{R}{W}.
# Net: discard 1 → draw 2 = +1 card. cards_manipulated += 2 (draw count).
P(
    llm(
        "216",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
        },
        modes=[
            cast_mode("{R}{W}", [draw(2), discard(1)]),
        ],
        reasons=["llm: 2-mana gain 2 + loot 2 (net +1); flashback {2}{R}{W} dropped"],
    )
)

# #217 Quandrix Charm {G}{U} Instant: counter unless 2 / destroy enchantment / target creature has base P/T 5/5 EOT
# Aggregated: counterspell + combat trick (5/5 base = ~+3/+3 over typical 2/2 creature; conservative +3/+3).
# Destroy-enchantment mode has no role flag (non-creature destroy = is_other-shape, but other flags fire).
P(
    llm(
        "217",
        role_features={
            "is_counterspell": True,
            "combat_trick_power": 3,
            "combat_trick_toughness": 3,
        },
        modes=[
            cast_mode("{G}{U}", [noop("modal_charm")]),
        ],
        reasons=[
            "llm: 2-mana Quandrix Charm modal — counter / destroy ench / 5/5 base EOT (combat trick approx +3/+3)"
        ],
    )
)

# #224 Scolding Administrator {W}{B} (2/2 Menace) — Repartee +1/+1 counter + ETB-related conditions
P(
    llm(
        "224",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{W}{B}", [etb()]),
        ],
        reasons=["llm: 2-mana 2/2 menace with Repartee self-counter trigger"],
    )
)

# #225 Silverquill Charm {W}{B} Instant: 2 +1/+1 counters on creature / exile creature with power 2 or less / drain 3
# Aggregated: combat trick (2 +1/+1 counters on instant = +2/+2 per guide §3) + removal_destroy_or_exile (exile mode).
P(
    llm(
        "225",
        role_features={
            "removal_destroy_or_exile": True,
            "combat_trick_power": 2,
            "combat_trick_toughness": 2,
        },
        modes=[
            cast_mode("{W}{B}", [noop("modal_charm")]),
        ],
        reasons=[
            "llm: 2-mana Silverquill Charm modal — +2/+2 counters / exile pow<=2 / drain 3 (aggregated)"
        ],
    )
)

# #227 Snooping Page {1}{W}{B} (2/3) — Repartee unblockable + combat-damage draw
# Per encoding guide §2: combat-damage-triggered draw is too conditional → leave off.
P(
    llm(
        "227",
        role_features={
            "is_creature": True,
            "cards_drawn": 0,
        },
        modes=[
            cast_mode("{1}{W}{B}", [etb()]),
        ],
        reasons=[
            "llm: 3-mana 2/3 with Repartee unblockable + combat-damage draw (conditional → off)"
        ],
    )
)

# #228 Social Snub {1}{W}{B} Sorcery: each player sac creature + opp loses 1, you gain 1
# Mass-ish removal but each player picks (edict-style).
P(
    llm(
        "228",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{1}{W}{B}", [noop("each_player_sac_creature_plus_drain")]),
        ],
        reasons=["llm: 3-mana symmetric edict + drain 1; encoded as removal"],
    )
)

# #231 Splatter Technique {1}{U}{U}{R}{R} Sorcery: modal — draw 4 OR 4 dmg to each creature/PW
# Aggregated: cards_drawn=4 + mass_removal + removal_destroy_or_exile + removal_burn_damage=4.
P(
    llm(
        "231",
        role_features={
            "cards_drawn": 4,
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
            "removal_burn_damage": 4,
        },
        modes=[
            cast_mode("{1}{U}{U}{R}{R}", [noop("modal_draw_4_or_sweeper_4")]),
        ],
        reasons=["llm: 5-mana modal — draw 4 OR 4 dmg sweeper (aggregated all modes)"],
    )
)

# #239 Traumatic Critique {X}{U}{R} Instant: deal X to any target + draw 2 discard 1
# Net cards = +1
P(
    llm(
        "239",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
            "removal_burn_damage": 1,
        },
        modes=[
            cast_mode("{X}{U}{R}", [draw(2), discard(1)]),
        ],
        reasons=["llm: X+UR burn X + loot 2 (net +1); min X=1 burn"],
    )
)

# #244 Witherbloom Charm {B}{G} Instant: sac for draw 2 / gain 5 / destroy nonland MV<=2
P(
    llm(
        "244",
        role_features={
            "cards_drawn": 2,
            "removal_destroy_or_exile": True,
        },
        modes=[
            cast_mode("{B}{G}", [noop("modal_charm")]),
        ],
        reasons=[
            "llm: 2-mana Witherbloom Charm modal — sac+draw 2 / gain 5 / destroy nonland MV<=2"
        ],
    )
)

# #245 Witherbloom, the Balancer {6}{B}{G} (5/5 Flying Deathtouch) — Affinity for creatures
# Affinity reduces cost. Encode printed cost; affinity is too situational to over-encode.
P(
    llm(
        "245",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{6}{B}{G}", [etb()]),
        ],
        reasons=[
            "llm: 8-mana 5/5 flying deathtouch with affinity-for-creatures cost reduction; printed cost encoded"
        ],
    )
)

# #259 Petrified Hamlet — Land
# When this land enters, choose a land card name. Lands with the chosen name get T:Add C. (and ability-restriction text)
# Effectively a land that taps for C (after the choose). Also makes other namesakes mana producers.
# For sim: encode as a basic-ish C-producing land.
P(
    llm(
        "259",
        role_features={},
        modes=[
            # Lands have no cast mode
        ],
        mana_abilities=[
            {
                "cost": cost("", tap=True),
                "produces": [["C"]],
                "condition": None,
            }
        ],
        reasons=[
            "llm: land — name-a-card on ETB; gives T:add C to lands with chosen name (incl. itself).",
            "llm: encoded as a basic colorless source for sim purposes",
        ],
    )
)


# ---------------------------------------------------------------------------
# SECTION 3: BONUS-SHEET REPRINTS (40)
# ---------------------------------------------------------------------------

# #bonus-soc-234 Abrade {1}{R} Instant: modal — 3 to creature OR destroy artifact
P(
    llm(
        "bonus-soc-234",
        role_features={
            "removal_burn_damage": 3,
        },
        modes=[cast_mode("{1}{R}", [noop("modal_burn_3_or_destroy_artifact")])],
        reasons=["llm bonus: Abrade modal burn 3 / destroy artifact"],
    )
)

# #bonus-tsr-4 Angel's Grace {W} Instant: split second; can't lose / damage to 1
P(
    llm(
        "bonus-tsr-4",
        role_features={"is_other": True},
        modes=[cast_mode("{W}", [noop("cannot_lose_this_turn")])],
        reasons=["llm bonus: Angel's Grace = no-lose protection (split second)"],
    )
)

# #bonus-bro-170 Awaken the Woods {X}{G}{G} Sorcery: create X 1/1 Forest Dryad lands
P(
    llm(
        "bonus-bro-170",
        role_features={
            # X=1 minimum → 1 token
            "creates_creatures": [body("1", "1", colors=["G"], subtypes=["Forest", "Dryad"])],
        },
        modes=[cast_mode("{X}{G}{G}", [noop("create_x_forest_dryad_tokens")])],
        reasons=["llm bonus: Awaken the Woods X-cost X Forest Dryad land tokens; min X=1"],
    )
)

# #bonus-cn2-175 Berserk {G} Instant: only before combat damage; trample + +X/+0 (X=power); EOT destroy
# Conditional + variable. Treat as combat trick +0/+0 with trample (rough).
P(
    llm(
        "bonus-cn2-175",
        role_features={
            "combat_trick_power": 0,  # variable; conservative
            "combat_trick_toughness": 0,
            "combat_trick_granted_keywords": ["trample"],
        },
        modes=[cast_mode("{G}", [noop("berserk_trample_plus_x")])],
        reasons=["llm bonus: Berserk = G grant trample + variable +X/+0 (combat trick)"],
    )
)

# #bonus-tdc-173 Bitter Triumph {1}{B} Instant: additional cost discard or 3 life. Destroy creature/PW.
P(
    llm(
        "bonus-tdc-173",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{1}{B}", [noop("destroy_creature_or_pw")])],
        reasons=[
            "llm bonus: Bitter Triumph 2-mana destroy creature/PW (additional discard-or-3-life)"
        ],
    )
)

# #bonus-vma-57 Brain Freeze {1}{U} Instant: target player mills 3 + storm
# Mill, no draw to us. is_other.
P(
    llm(
        "bonus-vma-57",
        role_features={"is_other": True},
        modes=[cast_mode("{1}{U}", [noop("opp_mill_3_storm")])],
        reasons=["llm bonus: Brain Freeze opp-mill 3 with storm; sim-irrelevant"],
    )
)

# #bonus-bro-128 Brotherhood's End {1}{R}{R} Sorcery: modal — 3 to each creature/PW OR destroy artifacts MV<=3
P(
    llm(
        "bonus-bro-128",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
            "removal_burn_damage": 3,
        },
        modes=[cast_mode("{1}{R}{R}", [noop("modal_sweeper_3_or_artifact_destroy")])],
        reasons=[
            "llm bonus: Brotherhood's End modal — 3 to each creature/PW OR destroy artifacts MV<=3"
        ],
    )
)

# #bonus-fdn-80 Bulk Up {1}{R} Instant: double target's power EOT. Flashback {4}{R}{R}.
# Variable pump → conservative combat trick +1/+1
P(
    llm(
        "bonus-fdn-80",
        role_features={
            "combat_trick_power": 1,
            "combat_trick_toughness": 0,
        },
        modes=[
            cast_mode("{1}{R}", [noop("double_target_power_eot")]),
        ],
        reasons=[
            "llm bonus: Bulk Up 2-mana double power EOT (combat trick); flashback {4}{R}{R} dropped"
        ],
    )
)

# #bonus-fdn-192 Burst Lightning {R} Instant: 2 dmg, kicker {4} → 4 dmg
# Kicker is paid from hand (legitimate alt cost). Aggregate: burn=4 (max across modes).
P(
    llm(
        "bonus-fdn-192",
        role_features={
            "removal_burn_damage": 4,
        },
        modes=[
            cast_mode("{R}", [noop("burn_2_any_target")]),
            cast_mode("{4}{R}", [noop("kicker:burn_4_any_target")]),
        ],
        reasons=["llm bonus: Burst Lightning R burn 2 / kicker 5R burn 4 (burn=max across modes)"],
    )
)

# #bonus-stx-253 Codie, Vociferous Codex 3-cost legendary artifact creature 1/4
# Activated ability: {4}, {T}: Add WUBRG. When you next cast a spell ... (long)
# Mana ability! Add WUBRG when activated.
P(
    llm(
        "bonus-stx-253",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{3}", [etb()]),
            activated_mode(
                "{4}",
                [produce_mana(["W", "U", "B", "R", "G"]), noop("codie_next_spell_dig")],
                tap=True,
            ),
        ],
        reasons=[
            "llm bonus: Codie 3-mana 1/4 with {4}{T} 5c-mana ability; can't cast permanent spells"
        ],
    )
)

# #bonus-stx-95 Crackle with Power {X}{X}{X}{R}{R} Sorcery: 5X dmg to each of up to X targets
P(
    llm(
        "bonus-stx-95",
        role_features={
            "removal_burn_damage": 5,  # min X=1 → 5 to 1 target
        },
        modes=[cast_mode("{X}{X}{X}{R}{R}", [noop("burn_5x_to_x_targets")])],
        reasons=["llm bonus: Crackle with Power X^3+RR — burn 5X to X targets; min X=1 → 5 burn"],
    )
)

# #bonus-dmr-154 Crop Rotation {G} Instant: additional sac a land. Search library for any land to battlefield.
# This is mana ramp. fetch_land: any to battlefield_untapped (the searched land enters untapped by default).
P(
    llm(
        "bonus-dmr-154",
        role_features={
            "is_other": True,  # net land neutral (sac one, get one)
        },
        modes=[
            cast_mode(
                "{G}",
                [
                    fetch_land(target="any", dest="battlefield_untapped"),
                ],
                sacrifice="land",
            ),
        ],
        reasons=["llm bonus: Crop Rotation G+sac-land tutor any land untapped; net land-neutral"],
    )
)

# #bonus-exo-55 Culling the Weak {B} Instant: additional sac a creature. Add {B}{B}{B}{B}.
P(
    llm(
        "bonus-exo-55",
        role_features={
            # Burst-mana — produces 4 black. Encode as produce_mana effect.
            "is_other": True,
        },
        modes=[
            cast_mode("{B}", [produce_mana(["B", "B", "B", "B"])], sacrifice="creature"),
        ],
        reasons=["llm bonus: Culling the Weak B+sac-creature add BBBB"],
    )
)

# #bonus-rvr-40 Cyclonic Rift {1}{U} Instant: bounce nonland you don't control. Overload {6}{U}.
P(
    llm(
        "bonus-rvr-40",
        role_features={
            "is_bounce": True,
        },
        modes=[
            cast_mode("{1}{U}", [noop("bounce_nonland")]),
            cast_mode("{6}{U}", [noop("overload:bounce_each_nonland")]),
        ],
        reasons=["llm bonus: Cyclonic Rift bounce nonland; overload 7-mana mass bounce"],
    )
)

# #bonus-mkc-205 Deflecting Palm {R}{W} Instant: prevent damage + redirect to source's controller
P(
    llm(
        "bonus-mkc-205",
        role_features={"is_other": True},
        modes=[cast_mode("{R}{W}", [noop("prevent_and_redirect")])],
        reasons=["llm bonus: Deflecting Palm prevent + redirect; sim-irrelevant"],
    )
)

# #bonus-mm2-79 Dismember {1}{B/P}{B/P} Instant: -5/-5 EOT
# Per guide -5/-5 → removal_destroy_or_exile
P(
    llm(
        "bonus-mm2-79",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{1}{B/P}{B/P}", [noop("removal_minus_5_minus_5")])],
        reasons=["llm bonus: Dismember -5/-5 EOT; removal"],
    )
)

# #bonus-tdm-10 Duty Beyond Death {1}{W} Instant: additional sac creature. All your creatures indestructible EOT + +1/+1 counters
P(
    llm(
        "bonus-tdm-10",
        role_features={
            "combat_trick_power": 1,
            "combat_trick_toughness": 1,
            "combat_trick_granted_keywords": ["indestructible"],
        },
        modes=[
            cast_mode("{1}{W}", [noop("team_indestructible_plus_counters")], sacrifice="creature")
        ],
        reasons=["llm bonus: Duty Beyond Death sac+all-creatures indestructible/counters"],
    )
)

# #bonus-soc-309 Expressive Iteration {U}{R} Sorcery: top 3 → 1 hand, 1 bottom, 1 exile (playable this turn)
# Effective +1 card with extra option to play exile this turn.
P(
    llm(
        "bonus-soc-309",
        role_features={
            "cards_drawn": 1,
            "cards_manipulated": 2,
        },
        modes=[cast_mode("{U}{R}", [look_top(3, accepts_land=True, accepts_nonland=True)])],
        reasons=["llm bonus: Expressive Iteration top 3 → 1 hand + 1 exile-castable; ~+2 cards"],
    )
)

# #bonus-ima-55 Flusterstorm {U} Instant: counter target instant/sorcery unless {1}; storm
P(
    llm(
        "bonus-ima-55",
        role_features={
            "is_counterspell": True,
        },
        modes=[cast_mode("{U}", [noop("soft_counter_with_storm")])],
        reasons=["llm bonus: Flusterstorm soft counter with storm; counterspell"],
    )
)

# #bonus-soc-310 Fracture {W}{B} Instant: destroy artifact, enchantment, or planeswalker
P(
    llm(
        "bonus-soc-310",
        role_features={"is_other": True},  # non-creature destroy → is_other per guide
        modes=[cast_mode("{W}{B}", [noop("destroy_artifact_ench_pw")])],
        reasons=["llm bonus: Fracture destroy artifact/ench/PW; non-creature → is_other"],
    )
)

# #bonus-chk-210 Glimpse of Nature {G} Sorcery: whenever you cast a creature this turn, draw a card
# Card-draw, but conditional on casting more creatures. Conservative: cards_drawn=1 (you draw at least 1 from next creature).
P(
    llm(
        "bonus-chk-210",
        role_features={
            "cards_drawn": 1,
        },
        modes=[cast_mode("{G}", [noop("creature_cast_draw_trigger")])],
        reasons=["llm bonus: Glimpse of Nature card-per-creature this turn; min draw 1"],
    )
)

# #bonus-clb-754 Grim Haruspex {2}{B} Creature 3/2 — Morph {B}; nontoken creature dies → draw
# Plain creature with morph. Morph cost {B} - encode as second cast mode.
P(
    llm(
        "bonus-clb-754",
        role_features={
            "is_creature": True,
        },
        modes=[
            cast_mode("{2}{B}", [etb()]),
            cast_mode("{3}", [etb()]),  # morph: cast face-down 2/2 for {3}
        ],
        reasons=["llm bonus: Grim Haruspex 3-mana 3/2 with morph {B} (face-down 2/2 for 3 mana)"],
    )
)

# #bonus-lci-17 Helping Hand {W} Sorcery: return creature MV<=3 from gy to battlefield tapped
# This is a reanimate effect. Sim-irrelevant for mulligan.
P(
    llm(
        "bonus-lci-17",
        role_features={"is_other": True},
        modes=[cast_mode("{W}", [noop("reanimate_mv_le_3_tapped")])],
        reasons=["llm bonus: Helping Hand reanimate MV<=3 tapped"],
    )
)

# #bonus-mkc-156 Jeska's Will {2}{R} Sorcery: choose one (commander mode = both) — add R per opp's hand OR exile top 3 cast this turn
# Conservative: is_other (mostly EDH-tier card).
P(
    llm(
        "bonus-mkc-156",
        role_features={"is_other": True},
        modes=[cast_mode("{2}{R}", [noop("modal_ramp_or_dig")])],
        reasons=["llm bonus: Jeska's Will modal mana/dig"],
    )
)

# #bonus-tsr-121 Living End — no mana cost (suspend 3 — {2}{B}{B})
# Suspend = alt-cost; no normal cast. Per guide suspend goes to LLM. Encode suspend as cast mode at suspend cost.
P(
    llm(
        "bonus-tsr-121",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{2}{B}{B}", [noop("suspend:living_end_reanimate_after_sweep")])],
        reasons=[
            "llm bonus: Living End no mana cost; suspend 4-mana — encoded as 4-mana cast mode"
        ],
    )
)

# #bonus-dft-95 Locust Spray {B} Instant: -1/-1 EOT. Cycling {B}.
P(
    llm(
        "bonus-dft-95",
        role_features={
            # -1/-1 single → is_other per guide §7
            "is_other": True,
        },
        modes=[
            cast_mode("{B}", [noop("debuff_minus_1")]),
            cycle_mode("{B}", draws=1),
        ],
        reasons=["llm bonus: Locust Spray B -1/-1 EOT; cycling B"],
    )
)

# #bonus-mh2-25 Prismatic Ending {X}{W} Sorcery: exile nonland MV<=#colors-spent-to-cast (Converge)
# Conservative: removal_destroy_or_exile (it kills small creatures regularly).
P(
    llm(
        "bonus-mh2-25",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{X}{W}", [noop("converge:exile_nonland_mv_le_colors")])],
        reasons=["llm bonus: Prismatic Ending X+W converge exile nonland; removal"],
    )
)

# #bonus-m11-153 Pyretic Ritual {1}{R} Instant: Add {R}{R}{R}
P(
    llm(
        "bonus-m11-153",
        role_features={"is_other": True},
        modes=[cast_mode("{1}{R}", [produce_mana(["R", "R", "R"])])],
        reasons=["llm bonus: Pyretic Ritual 2-mana add RRR (mana burst)"],
    )
)

# #bonus-ltr-26 Reprieve {1}{W} Instant: bounce target spell + draw a card
# Bouncing a spell is a soft counter; draw 1 card.
P(
    llm(
        "bonus-ltr-26",
        role_features={
            "is_counterspell": True,
            "cards_drawn": 1,
        },
        modes=[cast_mode("{1}{W}", [draw(1), noop("bounce_target_spell")])],
        reasons=["llm bonus: Reprieve 2-mana bounce a spell + draw 1; counter-ish"],
    )
)

# #bonus-otj-26 Requisition Raid {W} Sorcery: Spree — must pick at least one +1 mode.
# Per owner: encode base + cheapest mode = {1}{W}. The +1 modes are destroy artifact / destroy enchantment / anthem.
P(
    llm(
        "bonus-otj-26",
        role_features={"is_other": True},
        modes=[cast_mode("{1}{W}", [noop("spree:base_plus_one_mode")])],
        reasons=["llm bonus: Requisition Raid Spree (base W + 1 for cheapest mode = 1W)"],
    )
)

# #bonus-otj-142 Return the Favor {R}{R} Instant: Spree — must pick at least one +1 mode.
# Per owner: encode base + cheapest mode = {1}{R}{R}.
P(
    llm(
        "bonus-otj-142",
        role_features={"is_other": True},
        modes=[cast_mode("{1}{R}{R}", [noop("spree:base_plus_one_mode")])],
        reasons=["llm bonus: Return the Favor Spree (base RR + 1 = 1RR)"],
    )
)

# #bonus-m15-29 Return to the Ranks {X}{W}{W} Sorcery: Convoke; return X creatures with MV<=2 from gy to battlefield
P(
    llm(
        "bonus-m15-29",
        role_features={"is_other": True},
        modes=[cast_mode("{X}{W}{W}", [noop("convoke:reanimate_x_small_creatures")])],
        reasons=["llm bonus: Return to the Ranks X+WW Convoke X reanimates"],
    )
)

# #bonus-one-108 Sheoldred's Edict {1}{B} Instant: modal — opp sacs creature / token / planeswalker
P(
    llm(
        "bonus-one-108",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{1}{B}", [noop("modal_edict")])],
        reasons=["llm bonus: Sheoldred's Edict modal — opp sacs creature/token/PW; removal"],
    )
)

# #bonus-woe-67 Sleight of Hand {U} Sorcery: top 2 take 1, bottom 1
P(
    llm(
        "bonus-woe-67",
        role_features={
            "cards_drawn": 1,
        },
        modes=[cast_mode("{U}", [look_top(2, accepts_land=True, accepts_nonland=True)])],
        reasons=["llm bonus: Sleight of Hand top 2 take 1 to hand"],
    )
)

# #bonus-tsr-139 Smallpox {B}{B} Sorcery: each player loses 1 + discards + sacs creature + sacs land
P(
    llm(
        "bonus-tsr-139",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{B}{B}", [discard(1), noop("symmetric_smallpox")])],
        reasons=["llm bonus: Smallpox 2-mana symmetric edict + discard + land-sac"],
    )
)

# #bonus-blb-114 Stargaze {X}{B}{B} Sorcery: top 2X → X to hand, rest to gy. Lose X life.
P(
    llm(
        "bonus-blb-114",
        role_features={
            "cards_drawn": 1,  # min X=1
            "cards_manipulated": 2,  # min 2X=2
        },
        modes=[cast_mode("{X}{B}{B}", [noop("scry_2x_pick_x_to_hand")])],
        reasons=["llm bonus: Stargaze X+BB top 2X take X; min X=1 → draw 1, manipulated 2"],
    )
)

# #bonus-dft-67 Stock Up {2}{U} Sorcery: top 5 take 2, bottom 3
P(
    llm(
        "bonus-dft-67",
        role_features={
            "cards_drawn": 2,
            "cards_manipulated": 3,
        },
        modes=[cast_mode("{2}{U}", [draw(2)])],
        reasons=["llm bonus: Stock Up 3-mana top 5 take 2 (draw 2 + scry-3-equivalent)"],
    )
)

# #bonus-cn2-58 Subterranean Tremors {X}{R} Sorcery: X dmg to each creature without flying; if X>=4 destroy artifacts; X>=8 8/8 token
# Per guide variable-X tokens encode min case (X=1 → no Lizard); explicitly clear creates_creatures the parser set.
P(
    llm(
        "bonus-cn2-58",
        role_features={
            "is_mass_removal": True,
            "removal_destroy_or_exile": True,
            "removal_burn_damage": 2,  # mass burn → conservative X=2 per encoding guide §1
            "creates_creatures": [],
        },
        modes=[cast_mode("{X}{R}", [noop("mass_x_burn_no_flying_plus_riders")])],
        reasons=[
            "llm bonus: Subterranean Tremors X+R mass burn ground; conservative X=2; 8/8 only at X>=8 (off)"
        ],
    )
)

# #bonus-dmr-108 Vampiric Tutor {B} Instant: tutor any to top of library + lose 2 life
# Tutor → is_other per guide.
P(
    llm(
        "bonus-dmr-108",
        role_features={"is_other": True},
        modes=[cast_mode("{B}", [noop("vampiric_tutor")])],
        reasons=["llm bonus: Vampiric Tutor 1-mana top-tutor; is_other"],
    )
)

# #bonus-mh1-37 Winds of Abandon {1}{W} Sorcery: exile target creature you don't control + opp searches a basic
P(
    llm(
        "bonus-mh1-37",
        role_features={
            "removal_destroy_or_exile": True,
        },
        modes=[cast_mode("{1}{W}", [noop("exile_creature_opp_searches_basic")])],
        reasons=["llm bonus: Winds of Abandon 2-mana exile creature you don't control"],
    )
)


# ===========================================================================
# Save patches.
# ===========================================================================


def main() -> None:
    PATCHES_PATH.write_text(json.dumps(PATCHES, indent=2), encoding="utf-8")
    print(f"Wrote {len(PATCHES)} patches to {PATCHES_PATH}")


if __name__ == "__main__":
    main()
