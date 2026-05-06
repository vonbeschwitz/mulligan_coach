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
        # TLA (Avatar: The Last Airbender)
        "airbend",
        "waterbend",
        "earthbend",
        "firebending",
        # Vehicles / Equipment have their own dedicated parser branches now,
        # but "crew N" and "equip N" lines on those cards are recognised as
        # ignorable rather than bail-worthy. Handled in parser.py directly.
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
