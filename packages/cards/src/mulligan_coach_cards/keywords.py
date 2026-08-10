"""Keyword tables.

Three static sets used by the deterministic parser:

* ``EVERGREEN_KEYWORDS`` — abilities Wizards considers "evergreen" plus a
  few others (Flash, Defender, Indestructible, Protection, Shroud) that
  appear on permanents often enough to be worth deterministic recognition.
  If a creature's only abilities are from this set, the parser can record
  them and call the card auto-classified.

* ``MODE_EMITTING_KEYWORDS`` — alt-cost mechanics that the parser knows
  how to convert into additional ``Mode``s on the card. Cards with these
  do NOT bail to ``NEEDS_LLM``; they get a separate Mode emitted alongside
  their cast Mode. Currently: cycling, landcycling-by-type, channel.

* ``ALT_COST_KEYWORDS`` — alt-cost mechanics the parser still bails on.
  Adventure, Flashback, Kicker, Escape, Mutate, Bestow, Disturb, etc. —
  these need richer parsing than v1 supports and go to the LLM.

Names are matched case-insensitively against ``card['keywords']`` and
against text scanned out of oracle text. Storing them lowercase keeps the
comparison straightforward.
"""

from __future__ import annotations

from typing import Final

# Deliberately a tuple (immutable, ordered for tests) and exposed as a
# frozenset for fast membership checks.
EVERGREEN_KEYWORD_LIST: Final[tuple[str, ...]] = (
    "deathtouch",
    "defender",
    "double strike",
    "first strike",
    "flash",
    "flying",
    "haste",
    "hexproof",
    "indestructible",
    "lifelink",
    "menace",
    "protection",
    # Prowess is "deciduous" — Wizards uses it routinely on most aggressive
    # sets since the 2024 update. Counted as evergreen for our purposes.
    "prowess",
    "reach",
    "shroud",
    "trample",
    "vigilance",
    "ward",
)
EVERGREEN_KEYWORDS: Final[frozenset[str]] = frozenset(EVERGREEN_KEYWORD_LIST)


# Keywords introduced by a single set's mechanic. They look like activated
# abilities or alt-cost lines but behave in set-specific ways the parser
# can't model. Detecting them lets the parser bail with a clean reason
# ("set keyword X not modelled") instead of producing misleading
# "activated cost not recognised" / "unrecognised line" messages.
#
# Add new entries when a new set introduces a custom keyword. Removing one
# means the parser is now expected to handle it.
SET_SPECIFIC_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # TLA (Avatar: The Last Airbender) bending mechanics are now handled
        # deterministically in parser.py:
        #   * airbend     → treated as bounce (returns target to hand)
        #   * earthbend N → treated as creating an N/N creature token
        #   * waterbend   → treated as an activated ability whose colored
        #                   mana cost is demoted to generic
        #   * firebending → triggered ability, silently ignored
        # Add a new entry here only when a future set ships a custom
        # mechanic the parser hasn't been taught yet.
    }
)


# Single-word ability lines we choose to silently ignore when they appear
# alone on a line. These keywords either modify how the card interacts
# with other rules in ways that don't affect mulligan classification
# (Changeling, Convoke is treated as cost-reduction noise) or signal
# alt-modes whose details we defer to higher-MV review.
#
# Adding a keyword here means: a chunk consisting solely of this word
# (case-insensitive) is not a parse blocker. The card's other text still
# decides AUTO vs NEEDS_LLM.
IGNORABLE_KEYWORD_LINES: Final[frozenset[str]] = frozenset(
    {
        "changeling",
        "convoke",
        "conspire",
        "soulshift",
        "modular",
        "amplify",
        "battle cry",
        "delirium",
        "metalcraft",
        "fading",
        "scavenge",
        "buyback",
        # TMT (Mutant Ninja Turtles) — alt-cost keyword whose details we
        # defer to LLM/human review. Listing it here lets cards with
        # only "Sneak {N}" as their non-effect text auto-classify on the
        # rest of their oracle text.
        "sneak",
        # HOB (The Hobbit) — "Storied" on its own line only tells you how to
        # switch on an enduring story; it grants nothing by itself. Per the
        # owner ruling (2026-08-09) we always assume the story is NOT yet
        # assembled, so the line is pure noise and the abilities gated on it
        # are stripped in parser.py (_drop_enduring_story_text).
        "storied",
    }
)


# Alt-cost keywords the parser knows how to emit as additional ``Mode``s.
# Detection is by presence in ``card['keywords']`` plus a regex on the
# oracle line that names the cost. Any of these in keywords does NOT
# disqualify the card from AUTO classification — it just means we have
# extra modes to parse.
#
# Cycling family covers basic cycling, type-cycling (e.g. mountaincycling,
# plainscycling), and the {1}/{2}/{3} variants which are all still spelled
# with the word "cycling" in oracle text.
MODE_EMITTING_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "cycling",
        "channel",
        # Type-cycling variants Scryfall lists as discrete keywords.
        "plainscycling",
        "islandcycling",
        "swampcycling",
        "mountaincycling",
        "forestcycling",
        "wastelandcycling",
        # Generic land-cycling for any-land-type variants.
        "landcycling",
        "basiclandcycling",
        "typecycling",
    }
)


# Anything that makes the card playable on different timings or for a
# different cost than its mana cost AND that we haven't (yet) modelled as
# a separate Mode. Auto-classification gives up when any of these are
# present. We cast the net wide on purpose: a false positive here just
# sends the card to the LLM, which is correct behaviour.
#
# Note: cycling / channel / landcycling are NOT in this list — they're in
# MODE_EMITTING_KEYWORDS instead. As we extend the parser to handle more
# alt-cost mechanics, names move from here to MODE_EMITTING_KEYWORDS.
ALT_COST_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        "adventure",
        "awaken",
        "bargain",
        "bestow",
        "blitz",
        "boast",
        "buyback",
        "casualty",
        "cleave",
        "compleated",
        "conspire",
        "dash",
        "delve",
        "demonstrate",
        "disturb",
        "embalm",
        "emerge",
        "encore",
        "entwine",
        "epic",
        "escalate",
        "escape",
        "eternalize",
        "evoke",
        "flashback",
        "forecast",
        "foretell",
        "fortify",
        "fuse",
        "harmonize",
        "hideaway",
        "impulse",
        "jump-start",
        "kicker",
        "level up",
        "madness",
        "miracle",
        "morph",
        "multikicker",
        "mutate",
        "myriad",
        "ninjutsu",
        "offering",
        "outlast",
        "overload",
        "plot",
        "prowl",
        "rebound",
        "reconfigure",
        "replicate",
        "retrace",
        "scavenge",
        "spectacle",
        "splice",
        "suspend",
        "transfigure",
        "transmute",
        "unearth",
        "warp",
    }
)


# Scryfall ``keywords`` entries the parser either handles elsewhere or
# deliberately tolerates; any keyword on a card that is in none of the known
# sets routes the card to LLM review — that's the tripwire that catches
# brand-new mechanics.
#
# These are the residual keywords that appear on cards in the five current
# Premier-Draft sets (TMT / ECL / TLA / SOS / MSH) but aren't already listed
# in EVERGREEN_KEYWORDS / MODE_EMITTING_KEYWORDS / ALT_COST_KEYWORDS /
# IGNORABLE_KEYWORD_LINES / SET_SPECIFIC_KEYWORDS. They were grandfathered in
# by a full scan of those sets so the unknown-keyword tripwire in parser.py
# only fires on genuinely new mechanics.
#
# Deliberately EXCLUDES "connive" and "teamwork" (both MSH): those are the
# canonical examples the tripwire must catch, so they stay out of every known
# set and route their cards to review.
KNOWN_KEYWORDS_EXTRA: Final[frozenset[str]] = frozenset(
    {
        "prepared",
        "surveil",
        "mill",
        "equip",
        "enchant",
        "scry",
        "transform",
        "treasure",
        "food",
        "vivid",
        "behold",
        "repartee",
        "infusion",
        "opus",
        "increment",
        "paradigm",
        "fight",
        "crew",
        "converge",
        "alliance",
        "airbend",
        "earthbend",
        "waterbend",
        "firebending",
        # HOB — handled deterministically in parser.py (_apply_hob_mechanics),
        # treated as creating an N/N black <type> Army token.
        "amass",
        "power-up",
        "disappear",
        "double",
        "triple",
        "exhaust",
        "landfall",
        "basic landcycling",
        "affinity",
        "enrage",
        "spree",
        "raid",
        "storm",
        "investigate",
        "role token",
        "champion",
        "cascade",
        "improvise",
        "graft",
        "wither",
        "proliferate",
        "partner",
        "cumulative upkeep",
        "grandeur",
        "ferocious",
        "magecraft",
        "split second",
        "extort",
        "populate",
        "shadow",
        "devour",
        "mine vibranium",
        "survey the realm",
        "throw ...",
        "... catch",
        "genius industrialist",
        "ceaseless tempest",
        "unrivaled lethality",
        "blight",
    }
)
