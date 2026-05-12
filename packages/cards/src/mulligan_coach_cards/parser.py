"""Deterministic Scryfall card parser.

The entry point is ``parse_card(scryfall_dict)``. It returns a
``ParsedCard`` whose ``status`` is either ``AUTO`` (everything was
recognised) or ``NEEDS_LLM`` (something we can't classify deterministically).

The output is shaped for two consumers:

* The simulator gets ``modes`` (each with a structured ``Cost`` and a list
  of ``Effect``s), ``mana_abilities`` (for lands, mana dorks, mana rocks),
  and ``enter_condition`` (for lands with conditional ETB-tapped clauses).
* The XGBoost feature stage gets ``role_features`` — a flat per-card
  categorization (creature, removal, combat trick, equipment, …).

The deterministic rules are intentionally narrow. Anything ambiguous goes
to ``NEEDS_LLM`` — the LLM classifier is responsible for filling the
structured fields the parser couldn't. Better to over-flag than to
silently mis-classify; a miss here would corrupt downstream features.

Top-level structure:

* ``parse_card`` resolves identity and dispatches by type line.
* ``_parse_land``, ``_parse_creature``, ``_parse_spell``,
  ``_parse_enchantment``, ``_parse_artifact``, ``_parse_planeswalker``
  handle the type families.
* ``_match_*`` helpers encode the deterministic patterns. They live near
  the top of the file so adding a new pattern is easy to find.

Reminder text in parens is stripped before matching so patterns don't
trip on it.
"""

from __future__ import annotations

import re
from typing import Any

from .keywords import (
    ALT_COST_KEYWORDS,
    EVERGREEN_KEYWORDS,
    IGNORABLE_KEYWORD_LINES,
    SET_SPECIFIC_KEYWORDS,
)
from .mana import ManaCost, Pip, parse_mana_cost
from .models import (
    Cost,
    CreatureBody,
    DrawCardsEffect,
    Effect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    ManaAbility,
    Mode,
    NoopEffect,
    ParsedCard,
    ParseStatus,
    Predicate,
    RoleFeatures,
    SacrificeSpec,
    ScryEffect,
)

# ---------------------------------------------------------------------------
# Constants and small helpers.
# ---------------------------------------------------------------------------

# Word-to-number for "a / an / one / … / ten". Magic loves writing numbers
# in words ("Draw a card", "You gain two life") so we need both forms.
_NUMBER_WORDS: dict[str, int] = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

# Basic land types — used both in dispatch and in conditional-ETB detection.
_BASIC_TYPES: dict[str, str] = {
    "Plains": "W",
    "Island": "U",
    "Swamp": "B",
    "Mountain": "R",
    "Forest": "G",
    "Wastes": "C",
}


def _to_int(token: str) -> int | None:
    """Return the integer value of a numeric token, or ``None`` if unknown."""
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


# Reminder text appears in parentheses on Scryfall oracle text. We always
# strip it before matching — it's there for human readers, never for rules.
_REMINDER_RE = re.compile(r"\([^)]*\)")


def _strip_reminder(text: str) -> str:
    """Remove parenthetical reminder text and tidy whitespace."""
    cleaned = _REMINDER_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\s*\n\s*", "\n", cleaned)
    return cleaned.strip()


def _split_chunks(text: str) -> list[str]:
    """Split oracle text into one chunk per ability/sentence, keeping order."""
    return [line.strip() for line in text.split("\n") if line.strip()]


# Mechanic-label prefixes — once-per-game / conditional ability labels
# that act as a header before the actual cost/effect. Stripping them
# lets the standard activated / waterbend / triggered matchers fire on
# the body. New entries added as new sets ship.
_LABEL_PREFIX_RE = re.compile(
    r"^(?:exhaust|raid|fateful hour|delirium|metalcraft|landfall|morbid|"
    r"hellbent|threshold|formidable|coven|domain|battalion|vivid|"
    r"firebending|champion of dawn|champion of dusk|"
    r"alliance|disappear|kicker)"
    r"\s*[—\-]\s*",
    re.IGNORECASE,
)


def _strip_label_prefix(chunk: str) -> str:
    """Remove a leading mechanic-label prefix like 'Exhaust — ' or 'Raid — '.

    Idempotent; non-matching chunks are returned unchanged."""
    return _LABEL_PREFIX_RE.sub("", chunk, count=1)


def _empty_mana() -> ManaCost:
    """Build a fresh empty ManaCost. Pydantic models are not safely shared
    as defaults across instances, so we build a new one each time."""
    return ManaCost(raw="", pips=[], cmc=0, color_pips={})


# ---------------------------------------------------------------------------
# Type-line parsing.
#
# Scryfall type lines look like "Legendary Creature — Human Warrior Ally".
# Split on the em-dash to get supertypes+types on the left and subtypes on
# the right. Supertypes are a small fixed set; everything else on the left
# is a card type.
# ---------------------------------------------------------------------------

_SUPERTYPES: frozenset[str] = frozenset(
    {"Basic", "Legendary", "Snow", "Ongoing", "World", "Elite", "Token", "Tribal"}
)


def _parse_type_line(type_line: str) -> tuple[list[str], list[str], list[str]]:
    """Return (supertypes, types, subtypes).

    Handles the standard "Supertype Type — Subtype" form. Returns empty
    lists for any missing sections.
    """
    if "—" in type_line:
        left, right = type_line.split("—", 1)
    elif " - " in type_line:
        left, right = type_line.split(" - ", 1)
    else:
        left, right = type_line, ""

    left_tokens = left.split()
    supertypes = [t for t in left_tokens if t in _SUPERTYPES]
    types = [t for t in left_tokens if t not in _SUPERTYPES]
    subtypes = right.split()
    return supertypes, types, subtypes


# ---------------------------------------------------------------------------
# Cost parsing.
#
# Costs can include any combination of:
# * mana symbols ({2}{W}{U})
# * tap / untap ({T} / {Q})
# * sacrifice ("Sacrifice this creature", "Sacrifice an artifact")
# * discard self ("Discard this card") — for the cycling pattern
#
# Returns None if the cost includes any component we don't model
# (e.g. "Pay 2 life", "Exile two cards from your graveyard"). Callers
# treat None as "this Mode can't be deterministically built — flag the
# card NEEDS_LLM if this Mode mattered."
# ---------------------------------------------------------------------------

_MANA_RUN_RE = re.compile(r"^(?:\{[^{}]+\})+$")
_SACRIFICE_RE = re.compile(
    r"^sacrifice\s+(?:"
    r"this(?:\s+\w+)?"
    r"|"
    r"(?:an?|another)\s+(?P<type>creature|artifact|land|permanent|"
    r"artifact or creature|creature or artifact)"
    r")\.?$",
    re.IGNORECASE,
)
_DISCARD_SELF_RE = re.compile(r"^discard\s+this\s+card\.?$", re.IGNORECASE)


def _parse_cost_string(cost_str: str) -> Cost | None:
    """Parse a comma-separated cost string into a structured ``Cost``.

    Examples that work:
    * ``"{2}, {T}"`` → mana={2}, tap=True.
    * ``"{T}"`` → tap=True, no mana.
    * ``"{2}, {T}, Sacrifice this creature"`` → mana, tap, sacrifice(self).
    * ``"{R}, Discard this card"`` → mana, discard_self.

    Returns ``None`` if any segment is unrecognised — caller should treat
    as "can't build this Mode."
    """
    cost = Cost(mana=_empty_mana())
    accumulated_mana = ""

    for raw_part in cost_str.split(","):
        part = raw_part.strip()
        if not part:
            continue

        if part == "{T}":
            cost.tap = True
            continue
        if part == "{Q}":
            cost.untap = True
            continue

        if _DISCARD_SELF_RE.match(part):
            cost.discard_self = True
            continue

        if m := _SACRIFICE_RE.match(part):
            target_word = m.group("type")
            if target_word is None:
                # "Sacrifice this …" — sacrificing self.
                cost.sacrifice = SacrificeSpec(target="self")
            else:
                t = target_word.lower()
                # Map "permanent" / multi-target ("artifact or creature") to
                # "any" since the simulator doesn't distinguish.
                if t in ("permanent", "artifact or creature", "creature or artifact"):
                    mapped = "any"
                else:
                    mapped = t
                cost.sacrifice = SacrificeSpec(target=mapped)  # type: ignore[arg-type]
            continue

        # Mana run? Concatenate; we'll parse all the mana at the end.
        if _MANA_RUN_RE.match(part):
            accumulated_mana += part
            continue

        return None

    if accumulated_mana:
        try:
            cost.mana = parse_mana_cost(accumulated_mana)
        except ValueError:
            return None

    return cost


# ---------------------------------------------------------------------------
# Spell-effect patterns.
#
# Each ``_match_*`` returns an Effect (or list of effects, or None / structured
# result depending on the matcher). They share the regex namespace because
# they're applied across creatures (ETB triggers), spells, and activated
# abilities — same vocabulary in each context.
# ---------------------------------------------------------------------------

_DRAW_RE = re.compile(r"^Draw (\w+) cards?\b", re.IGNORECASE)
_SCRY_RE = re.compile(r"^Scry (\w+)\b", re.IGNORECASE)
# Loot: "Draw N cards, then discard N cards." Net draw is zero, but
# the player sees and manipulates new cards. role_features.cards_manipulated
# is the right home for this.
_LOOT_RE = re.compile(
    r"^Draw\s+(?P<draw>\w+)\s+cards?,?\s+then\s+discard\s+(?P<discard>\w+)\s+cards?\.?$",
    re.IGNORECASE,
)
# Generalised destroy / exile target X. The "X" group is captured so the
# matcher can route to removal_destroy_or_exile (creature / nonland
# permanent / permanent) vs is_other (artifact / enchantment / land / …).
# A trailing qualifier ("with mana value 3 or greater", "you don't control",
# "an opponent controls") is allowed before the period.
# Optional descriptor that can appear between "target" and the target
# word ("attacking", "blocking", "tapped", "untapped", etc.).
_TARGET_PRE_DESC = (
    r"(?:(?:attacking|blocking|attacking or blocking|tapped|untapped|"
    r"nonblack|nonwhite|nonblue|nonred|nongreen|nontoken|colorless)\s+)*"
)
_DESTROY_TARGET_RE = re.compile(
    r"^Destroy(?:\s+up to (?:one|two|three|\d+))?\s+target\s+"
    + _TARGET_PRE_DESC
    + r"(?P<target>creature|nonland permanent|permanent|artifact|enchantment|"
    r"land|artifact or enchantment)"
    r"(?:\s+[^.]*?)?(?:\.|$)",
    re.IGNORECASE,
)
_EXILE_TARGET_RE = re.compile(
    r"^Exile(?:\s+up to (?:one|two|three|\d+))?\s+target\s+"
    + _TARGET_PRE_DESC
    + r"(?P<target>creature|nonland permanent|permanent|artifact|enchantment|"
    r"land|artifact or enchantment)"
    r"(?:\s+[^.]*?)?(?:\.|$)",
    re.IGNORECASE,
)
_DAMAGE_CREATURE_RE = re.compile(
    r"^.+?\s+deals\s+(\d+)\s+damage to target "
    r"(?:attacking or blocking creature|attacking creature|blocking creature|creature)"
    r"\.",
    re.IGNORECASE,
)
_DAMAGE_ANY_RE = re.compile(
    r"^.+?\s+deals\s+(\d+)\s+damage to (any target|target creature or player)\.?$",
    re.IGNORECASE,
)
# Variable-amount damage: "<this> deals damage equal to ... to <target>".
# We can't record a specific damage number, so callers should NOT update
# role_features.removal_burn_damage from these — just mark is_other.
_DAMAGE_VARIABLE_RE = re.compile(
    r"deals damage equal to.+?to\s+(?:any target|target creature(?: or player)?)",
    re.IGNORECASE | re.DOTALL,
)
_LIFE_GAIN_RE = re.compile(r"^You gain (\w+) life\.?$", re.IGNORECASE)
_LIFE_LOSS_RE = re.compile(
    r"^(?:Target opponent|Each opponent) loses (\w+) life\.?$", re.IGNORECASE
)

# Bounce — return target creature / nonland permanent / permanent to hand.
# Trailing conditional text (e.g. "If you controlled that permanent, draw a
# card.") is allowed after the period — the bounce is the primary effect.
_BOUNCE_RE = re.compile(
    r"^Return\s+(?:up to (?:one|two|three|\d+)\s+)?target\s+"
    r"(?P<target>creature|nonland permanent|permanent)"
    r"\s+to (?:its owner's|your) hand\.",
    re.IGNORECASE,
)
# Tuck — put target creature / permanent on top of its owner's library.
_TOP_LIBRARY_RE = re.compile(
    r"^Put\s+target\s+(?P<target>creature|nonland permanent|permanent)"
    r"\s+on (?:top of|the top of)\s+(?:its owner's|your) library\.",
    re.IGNORECASE,
)

# Combat-trick patterns. The two halves run independently — a card can pump,
# grant keywords, or both. Both regexes require "until end of turn" so
# permanent enchantments don't false-match.
_COMBAT_TRICK_PUMP_RE = re.compile(
    r"\+(?P<p>\d+)/\+(?P<t>\d+).*?until end of turn",
    re.IGNORECASE | re.DOTALL,
)
# Lower-cased keywords we recognise as "granted" by combat tricks. Subset
# of EVERGREEN_KEYWORDS — we exclude defender / shroud / protection because
# they're rarely granted by combat tricks and the parsing is finicky.
_GRANTABLE_KEYWORDS: tuple[str, ...] = (
    "deathtouch",
    "double strike",
    "first strike",
    "flying",
    "haste",
    "hexproof",
    "indestructible",
    "lifelink",
    "menace",
    "reach",
    "trample",
    "vigilance",
)
_GRANT_KEYWORDS_BLOCK_RE = re.compile(
    r"gains?\s+("
    + r"(?:"
    + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS)
    + r")"
    + r"(?:[\s,]+(?:and\s+)?(?:"
    + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS)
    + r"))*"
    + r")",
    re.IGNORECASE,
)
_UNTIL_EOT_RE = re.compile(r"until end of turn", re.IGNORECASE)


# Backwards-compatible alias used by the old extractor — the new logic
# below decouples "gains <keywords>" from "until end of turn" so they
# can appear in either order in the oracle text.
_GRANT_KEYWORDS_RE = _GRANT_KEYWORDS_BLOCK_RE
# Combat tricks always target a creature. Used as a gate before the pump /
# grant matchers fire — without the "target creature" gate, static
# +N/+M-grant abilities on permanents would false-match.
_TARGETS_CREATURE_RE = re.compile(r"target\s+creature", re.IGNORECASE)
# Counter distribution on ETB — board-wide buff that's neither bounce
# nor combat trick. Marks the card as having a non-trivial ETB without
# claiming it's a creature pump.
_COUNTER_DISTRIBUTION_RE = re.compile(
    r"put a \+\d+/\+\d+ counter on each",
    re.IGNORECASE,
)
# Soft-removal: "exile target X until this leaves the battlefield" — the
# permanent comes back when our card leaves, but for a turn count purpose
# (which is what role_features cares about) it's removal. Common ETB shape
# on enchantments (Aang's Iceberg) and creatures (Earth Kingdom Jailer).
_EXILE_UNTIL_LEAVES_RE = re.compile(
    r"exile\s+(?:up to (?:one|two|three|\d+)\s+)?(?:other\s+)?target\s+"
    r"(?:nonland\s+|other\s+)*"
    r"(?:creature|permanent|artifact, creature,? or enchantment|"
    r"artifact or creature|artifact or enchantment)"
    r".*?until\s+(?:this|that|it)\s+\w*?\s*leaves",
    re.IGNORECASE | re.DOTALL,
)

# Spell counter: "Counter target spell" — the counterspell role.
_COUNTER_SPELL_RE = re.compile(
    r"^counter\s+target\s+(?:spell|creature spell|noncreature spell|"
    r"instant or sorcery spell|activated ability)\b",
    re.IGNORECASE,
)

# Single-target +1/+1 counter pump (different from the "on each" variant
# already covered by _COUNTER_DISTRIBUTION_RE).
_COUNTER_TARGET_RE = re.compile(
    r"^put\s+(?:a|one|two|three|\d+)\s+\+\d+/\+\d+ counters?\s+on\s+target\s+(?:creature|permanent)",
    re.IGNORECASE,
)

# Negative-counter pump on target — large -N/-N counters effectively kill
# the target (e.g. "Put four -1/-1 counters on target creature."). Three
# or more counters typically kills, so we treat as removal.
_NEG_COUNTER_TARGET_RE = re.compile(
    r"^put\s+(?P<count>a|one|two|three|four|five|six|\d+)\s+-\d+/-\d+ counters?\s+on\s+target\s+(?:creature|permanent)",
    re.IGNORECASE,
)

# Multi-target bounce: "Choose two target creatures controlled by different
# players. Return those creatures to their owners' hands." or "Return all
# creatures to their owners' hands."
_MULTI_BOUNCE_RE = re.compile(
    r"^Return\s+(?:all|each|those|the)?\s*(?:nonland\s+)?(?:creatures?|permanents?)"
    r".*?to (?:their owners'|its owner's|your) hands?",
    re.IGNORECASE | re.DOTALL,
)
# Or the modal-shape: choose targets first, then return them.
_CHOOSE_BOUNCE_RE = re.compile(
    r"^Choose\s+(?:up to )?(?:one|two|three|\d+)\s+target\s+creatures?\b"
    r".*?return (?:those|that) creatures? to (?:their owners'|its owner's|your) hands?",
    re.IGNORECASE | re.DOTALL,
)

# Static self-modifier on a creature: "This creature gets/has/'s power..."
# Lines matching this are considered "noise from the parser's perspective"
# — they don't change cast / castability / mana, so we ignore them rather
# than bail. Triggered abilities ("When this creature dies/enters") match
# _ETB_RE / _OTHER_TRIGGERED_RE first and are handled there.
_STATIC_SELF_MOD_RE = re.compile(
    r"^this creature(?:'s| has| gets|\s)",
    re.IGNORECASE,
)


_SELF_STATIC_VERB_RE = re.compile(
    r"^(?:can't|cannot|has|have|is|isn't|gets|gains?|doesn't|don't|are|"
    r"enters|becomes?|attacks?|blocks?|deals?|costs?)\b",
    re.IGNORECASE,
)


def _looks_like_self_static_modifier(chunk: str, name: str) -> bool:
    """True if a chunk reads like a static self-modifier referenced by name.

    Cards routinely write their own name where Magic rules would say
    "this creature" — e.g. "Sygg can't be blocked." or "Aang has flying."
    We accept any chunk whose leading token equals the card's first name
    word AND whose next token is a recognised static verb ("can't", "has",
    "is", "gets", …). The verb list keeps this from colliding with
    keyword-cost lines like "Cycling {2}" — important when the card name
    happens to share a word with a keyword.
    """
    if not name:
        return False
    first_token = name.split(",")[0].split(" //")[0].strip().split()
    if not first_token:
        return False
    head = first_token[0]
    if not head:
        return False
    m = re.match(rf"^{re.escape(head)}\s+(.+)$", chunk, re.IGNORECASE)
    if m is None:
        return False
    return bool(_SELF_STATIC_VERB_RE.match(m.group(1).strip()))


# Static / triggered ability tolerance.
#
# v1 rule (per project owner): we generally ignore static and triggered
# abilities when classifying — they don't change a card's mana cost,
# castability, or first-four-turns behaviour, which is what mulligan
# decisions hinge on. The two exceptions worth capturing are:
#
# 1. Triggered card draw: bumps `role_features.cards_drawn`.
# 2. Triggered mana production: noted as a breadcrumb only — the schema
#    doesn't have a clean home for "produces mana on a trigger" yet.
#
# Lines matched as ignorable are silently dropped rather than treated as
# blockers. Lines that DON'T look static/triggered (i.e. action lines
# describing the spell's own effect) still go through the normal matcher
# bank and bail to NEEDS_LLM if unrecognised.
_TRIGGERED_PREFIX_RE = re.compile(
    r"^(when|whenever|at the beginning of)\b",
    re.IGNORECASE,
)
# Static prose that doesn't describe a player-initiated action. Matches the
# common openers; we err on the side of including more rather than fewer
# — false positives just mean we don't bail on a card we already would
# have wanted to call AUTO.
_STATIC_PREFIXES: tuple[str, ...] = (
    "as long as ",
    "as this ",  # "As this artifact enters, choose a creature type." — replacement effect
    "if ",
    "you may ",
    "each player",
    "each opponent",
    "each turn",
    "each upkeep",
    "each combat",
    "your ",
    "creatures you control",
    "creature you control",
    "lands you control",
    "permanents you control",
    "all creatures",
    "creature spells",  # "Creature spells you cast have convoke."
    "instant spells",
    "sorcery spells",
    "spells you cast",
    "the next",
    "this creature",
    "this artifact",
    "this enchantment",
    "this land",
    "this card",
    "this spell",
    "untap this ",  # "Untap this artifact during each other player's untap step." (Bender's Waterskin)
    "cards in",
    "players can't",
    "creatures can't",
    "noncreature spells",
    # Anthem-style statics referencing other permanents.
    "other ",
    "creatures with",
    "tokens you control",
    # Equipment / aura buffs.
    "equipped ",
    "enchanted ",
    "the equipped ",
    # Conditional / replacement-effect prefixes.
    "the first ",
    "during ",
    "until ",
    "for each ",
    # TLA firebending — niche triggered ability we choose to ignore.
    "firebending",
)
# Inner card-draw inside a triggered/static line: "draw a card", "draw N cards".
_INNER_DRAW_RE = re.compile(
    r"\b(?:you (?:may )?)?draw (a|an|one|two|three|four|five|six|seven|\d+) cards?\b",
    re.IGNORECASE,
)
# Inner mana production inside a triggered/static line: "add {G}", "add {C}{C}".
_INNER_ADD_MANA_RE = re.compile(
    r"\badd ((?:\{[WUBRGC]\})+)",
    re.IGNORECASE,
)


def _is_likely_static_or_triggered(chunk: str) -> bool:
    """Heuristic: True if a chunk reads like a static or triggered ability
    rather than an action the player initiates on resolution.

    Used at the bail boundary in per-type parsers — when the regular spell
    matchers don't recognise a line, we ask "is this prose / a trigger
    we can safely ignore?" before deciding to flag the card NEEDS_LLM.
    """
    cl = chunk.strip().lower()
    if _TRIGGERED_PREFIX_RE.match(cl):
        return True
    return any(cl.startswith(p) for p in _STATIC_PREFIXES)


def _extract_triggered_signal(chunk: str, rf: RoleFeatures) -> None:
    """Scan an ignorable static/triggered chunk for card-draw and mana
    production and reflect them in role_features.

    Mana production currently has nowhere to go on the simulator side
    (ManaAbility models permanent-resident activated abilities, not
    triggered ones), so for now we just acknowledge it via a no-op and
    keep the parser from bailing. The XGBoost stage can pick this up
    later if we add a `triggered_mana` feature.

    Also picks up bending effects nested inside triggered abilities
    (e.g. "When this enters, airbend up to one target nonland permanent."
    sets `rf.is_bounce`; "When this dies, earthbend 2." appends to
    `rf.creates_creatures`).
    """
    for m in _INNER_DRAW_RE.finditer(chunk):
        n = _to_int(m.group(1))
        if n is not None:
            rf.cards_drawn += n
    # Mana production breadcrumb only — no role_features field today.
    _ = _INNER_ADD_MANA_RE.search(chunk)
    # Bending mechanics nested inside a triggered ability.
    if _AIRBEND_RE.search(chunk):
        rf.is_bounce = True
    for m in _EARTHBEND_RE.finditer(chunk):
        n = _to_int(m.group(1))
        if n is not None:
            rf.creates_creatures.append(
                CreatureBody(
                    power=str(n),
                    toughness=str(n),
                    colors=[],
                    subtypes=[],
                    keywords=[],
                )
            )


# ---------------------------------------------------------------------------
# TLA bending mechanics.
#
# Per the project owner's design call:
# * airbend     → de facto a bounce ("return to hand"). Set is_bounce=True.
# * earthbend N → turns a land you control into a 0/0 creature with N
#                 +1/+1 counters → effectively an N/N creature token.
# * waterbend N — <cost>, <activation>: <effect> — treated as an activated
#                 mode whose colored mana pips are demoted to generic
#                 (since waterbending in TLA can be paid with generic mana
#                 from any element / source for v1 simulator purposes).
# * firebending → triggered "Whenever this attacks/etc" damage ability.
#                 Niche; silently ignored via the static/triggered tolerance.
#
# These regexes intentionally allow leading whitespace / triggered prefixes
# so they fire in any context (spell body, ETB trigger, dies-trigger, etc.).
# ---------------------------------------------------------------------------

_AIRBEND_RE = re.compile(
    r"\bairbend(?:\s+up\s+to)?\s+"
    r"(?:one|two|three|four|\d+)?\s*(?:other\s+)?"
    r"target\s+(?:nonland\s+)?(?:creature|permanent)s?\b",
    re.IGNORECASE,
)
_EARTHBEND_RE = re.compile(r"\bearthbend\s+(\d+)\b", re.IGNORECASE)
# Waterbend line shape, supports both:
#   * "Waterbend N — {cost}, {T}: <effect>." (dashed form with element counter)
#   * "Waterbend {cost}: <effect>." (bare form where the cost follows directly)
_WATERBEND_LINE_RE = re.compile(
    r"^waterbend\s+(?:\d+\s*[—\-]\s*)?"
    r"(?P<cost>[^:]+?)\s*:\s*(?P<effect>.+?)\.?$",
    re.IGNORECASE,
)
# Firebending lines look like "Firebending {N} — Whenever this creature
# attacks, ..." — the leading "Firebending ..." word forms a labelled
# triggered ability we silently ignore.
_FIREBENDING_LINE_RE = re.compile(r"^firebending\b", re.IGNORECASE)


def _demote_cost_to_generic(cost: Cost) -> Cost:
    """Return a copy of ``cost`` with each colored mana pip replaced by
    a single generic ({1}). Used to model waterbending: the activation
    cost is treated as if only generic mana could be paid for it.

    Keeps {T}, sacrifice, discard_self, and untap unchanged. ``{X}`` /
    snow / colorless pips stay as-is."""
    if cost.mana.cmc == 0 and not cost.mana.pips:
        return cost
    new_pips: list[Pip] = []
    extra_generic = 0
    for pip in cost.mana.pips:
        if pip.kind == "color" or pip.kind == "hybrid" or pip.kind == "phyrexian":
            extra_generic += 1
        elif pip.kind == "two_or_color":
            extra_generic += pip.amount or 0
        else:
            # generic / x / snow / colorless — preserve.
            new_pips.append(pip)
    if extra_generic:
        # Find an existing generic pip to bump, else prepend one.
        bumped = False
        for i, pip in enumerate(new_pips):
            if pip.kind == "generic":
                new_pips[i] = pip.model_copy(update={"amount": (pip.amount or 0) + extra_generic})
                bumped = True
                break
        if not bumped:
            new_pips.insert(0, Pip(kind="generic", amount=extra_generic))
    new_cmc = sum(
        (p.amount or 0) if p.kind == "generic" else (1 if p.kind != "x" else 0) for p in new_pips
    )
    raw_bits = []
    for p in new_pips:
        if p.kind == "generic":
            raw_bits.append(f"{{{p.amount}}}")
        elif p.kind == "x":
            raw_bits.append("{X}")
        elif p.kind == "colorless":
            raw_bits.append("{C}")
        elif p.kind == "snow":
            raw_bits.append("{S}")
    new_mana = ManaCost(
        raw="".join(raw_bits),
        pips=new_pips,
        cmc=new_cmc,
        color_pips={},
        has_x=any(p.kind == "x" for p in new_pips),
        has_hybrid=False,
        has_phyrexian=False,
        has_colorless=any(p.kind == "colorless" for p in new_pips),
        has_snow=any(p.kind == "snow" for p in new_pips),
    )
    return Cost(
        mana=new_mana,
        tap=cost.tap,
        untap=cost.untap,
        sacrifice=cost.sacrifice,
        discard_self=cost.discard_self,
    )


def _match_bending_effect(chunk: str, rf: RoleFeatures | None = None) -> list[Effect] | None:
    """Recognise airbend / earthbend / firebending patterns and return the
    equivalent ``Effect`` list, side-effecting ``rf`` when supplied.

    Returns ``None`` if the chunk has no bending text. Returns ``[]`` if the
    chunk is recognised but produces no sim-relevant effects (firebending).
    Waterbending is handled separately because it produces an activated Mode,
    not effects on the cast Mode.
    """
    if _FIREBENDING_LINE_RE.match(chunk):
        return []
    matched_air = bool(_AIRBEND_RE.search(chunk))
    matched_earth = list(_EARTHBEND_RE.finditer(chunk))
    if not (matched_air or matched_earth):
        return None
    effects: list[Effect] = []
    if matched_air:
        if rf is not None:
            rf.is_bounce = True
        effects.append(NoopEffect(role_tag="bounce_creature"))
    for m in matched_earth:
        n = _to_int(m.group(1))
        if n is None:
            continue
        if rf is not None:
            rf.creates_creatures.append(
                CreatureBody(
                    power=str(n),
                    toughness=str(n),
                    colors=[],
                    subtypes=[],
                    keywords=[],
                )
            )
        effects.append(NoopEffect(role_tag="create_token"))
    return effects


def _try_build_waterbend_mode(chunk: str, rf: RoleFeatures | None) -> Mode | None:
    """If ``chunk`` is a waterbending activated ability, build the Mode.

    The colored mana pips in the activation cost are demoted to generic
    (per the project owner's call: simulator can treat waterbending as
    payable with any mana). Returns ``None`` only if the cost doesn't
    parse — if the cost parses but the effect doesn't, we still build a
    Mode with a placeholder NoopEffect rather than bailing the whole
    card. The simulator only cares about the cost for mulligan purposes."""
    m = _WATERBEND_LINE_RE.match(chunk)
    if m is None:
        return None
    cost_str = m.group("cost").strip()
    effect_str = m.group("effect").strip()
    cost = _parse_cost_string(cost_str)
    if cost is None:
        return None
    cost = _demote_cost_to_generic(cost)
    effects = _match_spell_effect(effect_str, rf=rf)
    if effects is None:
        effects = [NoopEffect(role_tag="waterbend_unknown")]
    return Mode(kind="activated", cost=cost, effects=effects)


def _extract_granted_keywords(text: str) -> list[str]:
    """Return any combat-trick-granted keywords found in ``text``.

    Requires both a "gains <keywords>" block and an "until end of turn"
    qualifier somewhere in the chunk. The two can appear in either order
    ("gains X until end of turn" vs "until end of turn, ... gains X")."""
    if not _UNTIL_EOT_RE.search(text):
        return []
    if m := _GRANT_KEYWORDS_BLOCK_RE.search(text):
        block = m.group(1)
        words = re.findall(
            r"\b(?:" + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS) + r")\b",
            block,
            re.IGNORECASE,
        )
        # Preserve order, lowercase.
        seen: list[str] = []
        for w in words:
            wl = w.lower()
            if wl not in seen:
                seen.append(wl)
        return seen
    return []


# Token-creation pattern. We capture power, toughness, color, and subtype
# words so we can build a CreatureBody. Plenty of variations exist; this
# covers the most common "create a/N P/T <color> <type> creature token(s)".
_CREATE_TOKEN_RE = re.compile(
    r"create (?P<count>a|an|one|two|three|four|five|\d+)\s+"
    r"(?P<power>\d+|\*)/(?P<toughness>\d+|\*)\s+"
    r"(?P<colors>(?:white|blue|black|red|green|colorless)(?:\s+and\s+(?:white|blue|black|red|green))*)?\s*"
    r"(?P<subtypes>(?:[A-Z][a-z]+\s*)+?)\s+creature tokens?",
    re.IGNORECASE,
)

# Land-fetch patterns. Three flavors: to-battlefield-tapped, to-battlefield-untapped,
# and to-hand. Each accepts "basic land", "Forest" / specific basic, or just
# "land" (any).
_FETCH_BATTLEFIELD_TAPPED_RE = re.compile(
    r"search your library for (?:a|up to one) (?P<filter>basic land|"
    r"plains|island|swamp|mountain|forest|land) card,?\s*"
    r"put (?:it|that card) onto the battlefield tapped,?\s*"
    r"(?:then shuffle)?",
    re.IGNORECASE,
)
_FETCH_BATTLEFIELD_UNTAPPED_RE = re.compile(
    r"search your library for (?:a|up to one) (?P<filter>basic land|"
    r"plains|island|swamp|mountain|forest|land) card,?\s*"
    r"put (?:it|that card) onto the battlefield(?!\s+tapped),?\s*"
    r"(?:then shuffle)?",
    re.IGNORECASE,
)
_FETCH_TO_HAND_RE = re.compile(
    r"search your library for (?:a|up to one) (?P<filter>basic land|"
    r"plains|island|swamp|mountain|forest|land) card,?\s*"
    r"(?:reveal it,?\s*)?put (?:it|that card) into your hand,?\s*"
    r"(?:then shuffle)?",
    re.IGNORECASE,
)


def _build_fetch_land(filter_word: str, destination: str) -> FetchLandEffect:
    """Convert a fetch-text "filter word" + destination into a ``FetchLandEffect``."""
    f = filter_word.lower()
    if f == "basic land":
        return FetchLandEffect(target_filter="basic", destination=destination)  # type: ignore[arg-type]
    if f == "land":
        return FetchLandEffect(target_filter="any", destination=destination)  # type: ignore[arg-type]
    # Specific basic by name.
    return FetchLandEffect(
        target_filter="specific_subtype",
        subtype=filter_word.title(),
        destination=destination,  # type: ignore[arg-type]
    )


def _match_fetch_land_effects(text: str) -> list[FetchLandEffect]:
    """Find all fetch-land patterns in ``text``. Order matters but the
    distinct patterns are independent.

    Returns a list (often empty, occasionally length 2 — Cultivate puts
    one onto the battlefield tapped AND one into hand)."""
    out: list[FetchLandEffect] = []
    # Order matters: try the "battlefield tapped" first, then untapped (so
    # the negative lookahead in untapped doesn't fight the tapped match).
    for m in _FETCH_BATTLEFIELD_TAPPED_RE.finditer(text):
        out.append(_build_fetch_land(m.group("filter"), "battlefield_tapped"))
    for m in _FETCH_BATTLEFIELD_UNTAPPED_RE.finditer(text):
        out.append(_build_fetch_land(m.group("filter"), "battlefield_untapped"))
    for m in _FETCH_TO_HAND_RE.finditer(text):
        out.append(_build_fetch_land(m.group("filter"), "hand"))
    return out


# Color-word ↔ WUBRG letter, used when parsing "create a 1/1 white …" tokens
# and "Add {G}" mana production.
_COLOR_WORD_TO_LETTER: dict[str, str] = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
    "colorless": "C",
}


def _match_token_creation(text: str) -> list[CreatureBody]:
    """Find token-creation patterns and return their bodies.

    One entry per distinct "create N P/T … creature token" phrase. The
    parent's `count` is implicit in the parent's effects — we record one
    body per phrase, not N bodies for "create three 1/1 tokens."
    """
    bodies: list[CreatureBody] = []
    for m in _CREATE_TOKEN_RE.finditer(text):
        power = m.group("power")
        toughness = m.group("toughness")
        colors_word = m.group("colors") or ""
        subtypes_word = (m.group("subtypes") or "").strip()
        colors = []
        for word in re.findall(r"\b(white|blue|black|red|green|colorless)\b", colors_word.lower()):
            letter = _COLOR_WORD_TO_LETTER[word]
            if letter in ("W", "U", "B", "R", "G"):
                colors.append(letter)
        subtypes = [w for w in subtypes_word.split() if w[:1].isupper()]
        bodies.append(
            CreatureBody(
                power=power,
                toughness=toughness,
                colors=colors,  # type: ignore[arg-type]
                subtypes=subtypes,
                keywords=[],
            )
        )
    return bodies


# Targets that count as "creature removal" for role_features. Nonland
# permanent / permanent removal kills creatures incidentally so we count
# them too. Anything else (artifact / enchantment / land / artifact-or-
# enchantment) is non-creature removal — bucket into role_features.is_other.
_CREATURE_TARGETS = frozenset({"creature", "nonland permanent", "permanent"})


def _match_spell_effect(chunk: str, rf: RoleFeatures | None = None) -> list[Effect] | None:
    """Match a single oracle-text chunk against the recognised spell effects.

    When ``rf`` is supplied, side-effect role-features fields that depend
    on the chunk's content (combat-trick P/T, granted keywords, bounce /
    top-library flags, removal target type). When ``rf`` is None (e.g.
    when called from inside an activated-ability or cycling Mode where
    role features shouldn't change), only the effect list is returned.

    Returns a list of Effects (one chunk can imply several — token
    creation + draw, etc.) or ``None`` if the chunk doesn't match any
    recognised pattern. The list is empty if the chunk is recognised but
    produces no simulator-relevant effects (pure removal spells become
    [NoopEffect(role_tag="…")]).
    """
    # Land-fetch — "Search your library for a basic land card, put it
    # onto the battlefield tapped, then shuffle." (Evolving Wilds-style).
    # Tried first so the activated-ability path on sac-fetch lands
    # produces a real FetchLandEffect rather than falling through to
    # the noop placeholder.
    if fetch := _match_fetch_land_effects(chunk):
        return list(fetch)

    # Loot — "Draw N, then discard N". Try this BEFORE plain draw so
    # the loot pattern wins over the leading "Draw a card" partial match.
    if m := _LOOT_RE.match(chunk):
        n_draw = _to_int(m.group("draw"))
        if n_draw is None:
            return None
        if rf is not None:
            rf.cards_manipulated += n_draw
        return [NoopEffect(role_tag="loot")]

    # Card draw.
    if m := _DRAW_RE.match(chunk):
        n = _to_int(m.group(1))
        if n is None:
            return None
        return [DrawCardsEffect(n=n)]

    # Scry.
    if m := _SCRY_RE.match(chunk):
        n = _to_int(m.group(1))
        if n is None:
            return None
        return [ScryEffect(n=n)]

    # Destroy / exile target X — generalised over creature / permanent /
    # artifact / enchantment / land.
    for pattern, base_tag in (
        (_DESTROY_TARGET_RE, "removal_destroy"),
        (_EXILE_TARGET_RE, "removal_exile"),
    ):
        if m := pattern.match(chunk):
            target = m.group("target").lower()
            if rf is not None:
                if target in _CREATURE_TARGETS:
                    rf.removal_destroy_or_exile = True
                else:
                    rf.is_other = True
            tag = base_tag if target == "creature" else f"{base_tag}_{target.replace(' ', '_')}"
            return [NoopEffect(role_tag=tag)]

    # Bounce — return target permanent to its owner's hand.
    if m := _BOUNCE_RE.match(chunk):
        if rf is not None:
            rf.is_bounce = True
        target = m.group("target").lower().replace(" ", "_")
        return [NoopEffect(role_tag=f"bounce_{target}")]

    # TLA bending mechanics: airbend (bounce) / earthbend (token creation) /
    # firebending (ignored). Waterbending produces a separate activated Mode
    # so it's handled at the chunk-loop level by per-type parsers, not here.
    bending = _match_bending_effect(chunk, rf=rf)
    if bending is not None:
        return bending

    # Counter target spell — counterspell role flag.
    if _COUNTER_SPELL_RE.match(chunk):
        if rf is not None:
            rf.is_counterspell = True
        return [NoopEffect(role_tag="counter_spell")]

    # Single-target +1/+1 counter pump — buff effect.
    if _COUNTER_TARGET_RE.match(chunk):
        if rf is not None:
            rf.is_other = True
        return [NoopEffect(role_tag="counter_target")]

    # Multi-target bounce.
    if _MULTI_BOUNCE_RE.match(chunk) or _CHOOSE_BOUNCE_RE.match(chunk):
        if rf is not None:
            rf.is_bounce = True
        return [NoopEffect(role_tag="bounce_multi")]

    # Negative-counter pump on target — treat as removal when 3+ counters,
    # otherwise as is_other (small debuff).
    if m := _NEG_COUNTER_TARGET_RE.match(chunk):
        n = _to_int(m.group("count"))
        if rf is not None:
            if n is not None and n >= 3:
                rf.removal_destroy_or_exile = True
            else:
                rf.is_other = True
        return [NoopEffect(role_tag="neg_counter_target")]

    # Soft removal: exile target permanent until <this> leaves the
    # battlefield. Treated as removal_destroy_or_exile because for the
    # next several turns the targeted creature is off the board.
    if _EXILE_UNTIL_LEAVES_RE.search(chunk):
        if rf is not None:
            rf.removal_destroy_or_exile = True
        return [NoopEffect(role_tag="exile_until_leaves")]

    # Tuck — put target on top of library.
    if m := _TOP_LIBRARY_RE.match(chunk):
        if rf is not None:
            rf.is_top_library = True
        target = m.group("target").lower().replace(" ", "_")
        return [NoopEffect(role_tag=f"top_library_{target}")]

    # Fixed-amount damage.
    if m := _DAMAGE_CREATURE_RE.match(chunk):
        n = int(m.group(1))
        if rf is not None:
            rf.removal_burn_damage = n
        return [NoopEffect(role_tag=f"removal_damage_creature_{n}")]
    if m := _DAMAGE_ANY_RE.match(chunk):
        n = int(m.group(1))
        if rf is not None:
            rf.removal_burn_damage = n
        return [NoopEffect(role_tag=f"removal_damage_any_{n}")]

    # Variable-amount damage to creature/any (Cat-Gator pattern). The
    # damage is some board-state expression, so we don't claim a
    # specific number — but we do mark the card as non-trivial.
    if _DAMAGE_VARIABLE_RE.search(chunk):
        if rf is not None:
            rf.is_other = True
        return [NoopEffect(role_tag="removal_damage_variable")]

    # Counter distribution on ETB ("put a +1/+1 counter on each Ally").
    if _COUNTER_DISTRIBUTION_RE.search(chunk):
        if rf is not None:
            rf.is_other = True
        return [NoopEffect(role_tag="etb_counter_distribution")]

    # Combat trick: requires "target creature" + (pump or granted keywords)
    # + "until end of turn". The PUMP and GRANT regexes both encode the
    # "until end of turn" gate so static permanents don't false-match.
    if _TARGETS_CREATURE_RE.search(chunk):
        pump_match = _COMBAT_TRICK_PUMP_RE.search(chunk)
        granted = _extract_granted_keywords(chunk)
        if pump_match is not None or granted:
            if rf is not None:
                if pump_match is not None:
                    rf.combat_trick_power = int(pump_match.group("p"))
                    rf.combat_trick_toughness = int(pump_match.group("t"))
                if granted:
                    # Extend rather than replace so multiple matches accrue.
                    for kw in granted:
                        if kw not in rf.combat_trick_granted_keywords:
                            rf.combat_trick_granted_keywords.append(kw)
            return [NoopEffect(role_tag="combat_trick")]

    # Life gain / loss — noop for sim, kept for downstream debug only.
    if _LIFE_GAIN_RE.match(chunk):
        return [NoopEffect(role_tag="life_gain")]
    if _LIFE_LOSS_RE.match(chunk):
        return [NoopEffect(role_tag="life_loss")]

    return None


# ---------------------------------------------------------------------------
# Land-specific helpers: mana production, ETB-tapped predicates.
# ---------------------------------------------------------------------------

# Color-pip producer: "{T}: Add {G}." / "{T}: Add {G} or {U}.".
# No end anchor — trailing prose after the period is tolerated so cards
# with a conditional buff (Raucous Audience: "{T}: Add {G}. If you
# control a creature with power 4 or greater, add {G}{G} instead.")
# match the unconditional baseline. Predicate has no
# "creature with power N+" kind, so we encode the baseline and drop
# the conditional half — the v1 simplification rule.
_TAP_FOR_RE = re.compile(
    r"^\{T\}:\s*Add\s+((?:\{[WUBRGC]\})(?:\s+or\s+\{[WUBRGC]\})*)\.",
    re.IGNORECASE,
)
# Prose any-color producer: "{T}: Add one mana of any color."
_TAP_FOR_ANY_RE = re.compile(
    r"^\{T\}:\s*Add\s+one\s+mana\s+of\s+any\s+color\.",
    re.IGNORECASE,
)
# Cost-prefix variants: "{N}, {T}: Add ..." for filter mana rocks.
# The captured cost group is fed back through _parse_cost_string so
# the resulting Cost.mana carries the {N}.
_COST_TAP_FOR_RE = re.compile(
    r"^(?P<cost>(?:\{[^{}]+\}\s*,\s*)+)\{T\}:\s*Add\s+"
    r"((?:\{[WUBRGC]\})(?:\s+or\s+\{[WUBRGC]\})*)\.",
    re.IGNORECASE,
)
_COST_TAP_FOR_ANY_RE = re.compile(
    r"^(?P<cost>(?:\{[^{}]+\}\s*,\s*)+)\{T\}:\s*Add\s+one\s+mana\s+of\s+any\s+color\.",
    re.IGNORECASE,
)
# Cost-only mana ability (no tap): "{N}: Add {color}." / "{N}: Add one
# mana of any color." Used by mana sources whose once-per-turn limit
# comes from a trailing "Activate only once each turn." line rather
# than a tap requirement (Barrels of Blasting Jelly). The negative
# lookahead rejects chunks starting with `{T}` so the tap-based patterns
# above keep precedence — necessary because `\{[^{}]+\}` would
# otherwise also match `{T}`.
_COST_ONLY_FOR_RE = re.compile(
    r"^(?!\{T\})(?P<cost>\{[^{}]+\}(?:\s*,\s*\{[^{}]+\})*):\s*Add\s+"
    r"((?:\{[WUBRGC]\})(?:\s+or\s+\{[WUBRGC]\})*)\.",
    re.IGNORECASE,
)
_COST_ONLY_FOR_ANY_RE = re.compile(
    r"^(?!\{T\})(?P<cost>\{[^{}]+\}(?:\s*,\s*\{[^{}]+\})*):\s*"
    r"Add\s+one\s+mana\s+of\s+any\s+color\.",
    re.IGNORECASE,
)

# Conditional ETB-tapped patterns.
_DEATHCAP_RE = re.compile(
    r"enters tapped unless you control\s+"
    r"(?P<count>two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"or more (?:other )?lands?",
    re.IGNORECASE,
)
_BASIC_ETB_RE = re.compile(
    r"enters tapped unless you control a (?:basic )?(?P<basic>Plains|Island|Swamp|Mountain|Forest)",
    re.IGNORECASE,
)
# "...unless you control a basic land" — any-basic check (#264 Agna Qel'a).
# Distinct from _BASIC_ETB_RE which names a specific basic type.
_BASIC_ANY_ETB_RE = re.compile(
    r"enters tapped unless you control a basic land",
    re.IGNORECASE,
)
_ENTERS_TAPPED_PLAIN = re.compile(
    r"enters(?:\s+the\s+battlefield)?\s+tapped(?!\s+unless)",
    re.IGNORECASE,
)


def _build_cost_with_tap(cost_prefix: str) -> Cost | None:
    """Build a Cost from a captured cost prefix plus an implicit {T}.

    ``cost_prefix`` is the text before the ``{T}`` in a mana-ability
    chunk, e.g. ``"{1}, "`` for a filter mana rock. Trailing commas and
    whitespace are stripped before delegating to ``_parse_cost_string``.
    Returns ``None`` if the prefix doesn't parse cleanly."""
    cleaned = cost_prefix.strip().rstrip(",").strip()
    if not cleaned:
        return Cost(mana=_empty_mana(), tap=True)
    return _parse_cost_string(f"{cleaned}, {{T}}")


def _extract_mana_ability(chunk: str) -> ManaAbility | None:
    """If ``chunk`` is a mana ability line, return the structured
    ``ManaAbility``. Recognises six shapes:

    * Plain          — ``{T}: Add {G}`` (or ``{G} or {U}``).
    * Prose any      — ``{T}: Add one mana of any color.``
    * Cost-prefix    — ``{N}, {T}: Add {G}`` (filter mana rocks).
    * Cost-prefix +  — ``{N}, {T}: Add one mana of any color.``
      prose any
    * Cost-only      — ``{N}: Add {G}.`` (no tap; the limit comes from a
      trailing "Activate only once each turn." line we don't model).
    * Cost-only +    — ``{N}: Add one mana of any color.``
      prose any

    Trailing prose after the colors (Raucous Audience's conditional
    "...add {G}{G} instead." clause, or the "Activate only once each
    turn." limiter) is tolerated; the baseline gets encoded. Returns
    ``None`` when the chunk isn't a mana ability."""
    if m := _TAP_FOR_RE.match(chunk):
        colors = re.findall(r"\{([WUBRGC])\}", m.group(1))
        return ManaAbility(
            cost=Cost(mana=_empty_mana(), tap=True),
            produces=[[c] for c in colors],
        )
    if _TAP_FOR_ANY_RE.match(chunk):
        return ManaAbility(
            cost=Cost(mana=_empty_mana(), tap=True),
            produces=[["any"]],
        )
    if m := _COST_TAP_FOR_RE.match(chunk):
        cost = _build_cost_with_tap(m.group("cost"))
        if cost is None:
            return None
        colors = re.findall(r"\{([WUBRGC])\}", m.group(2))
        return ManaAbility(
            cost=cost,
            produces=[[c] for c in colors],
        )
    if m := _COST_TAP_FOR_ANY_RE.match(chunk):
        cost = _build_cost_with_tap(m.group("cost"))
        if cost is None:
            return None
        return ManaAbility(cost=cost, produces=[["any"]])
    if m := _COST_ONLY_FOR_RE.match(chunk):
        cost = _parse_cost_string(m.group("cost"))
        if cost is None:
            return None
        colors = re.findall(r"\{([WUBRGC])\}", m.group(2))
        return ManaAbility(
            cost=cost,
            produces=[[c] for c in colors],
        )
    if m := _COST_ONLY_FOR_ANY_RE.match(chunk):
        cost = _parse_cost_string(m.group("cost"))
        if cost is None:
            return None
        return ManaAbility(cost=cost, produces=[["any"]])
    return None


def _match_etb_tapped_predicate(oracle_text: str) -> Predicate | None:
    """Detect an ETB-tapped predicate on a land's oracle text.

    Returns:
    * ``Predicate(kind="controls_lands_lt", n=N+1)`` for Deathcap-style
      "...unless you control N or more (other) lands" — the land enters
      tapped iff you control fewer than N+1 (i.e. N or fewer) other lands.
      We use ``controls_lands_lt`` so the simulator's evaluator only needs
      the strict-less-than comparator.
    * ``Predicate(kind="controls_basic", basic_type=X)`` for the
      "...unless you control a basic Plains" check-land family.
    * ``Predicate(kind="always")`` for plain unconditional "enters tapped".
    * ``None`` if the land has no ETB-tapped clause at all.
    """
    if m := _DEATHCAP_RE.search(oracle_text):
        n = _to_int(m.group("count"))
        if n is None:
            return None
        # "N or more lands" → tapped iff lands < N. Subtract one because
        # the predicate is strict-less-than.
        return Predicate(kind="controls_lands_lt", n=n)
    # Order matters: any-basic check before specific-basic so the
    # "basic land" generic phrasing wins over individual-basic regex.
    if _BASIC_ANY_ETB_RE.search(oracle_text):
        return Predicate(kind="controls_basic_any")
    if m := _BASIC_ETB_RE.search(oracle_text):
        return Predicate(kind="controls_basic", basic_type=m.group("basic").title())
    if _ENTERS_TAPPED_PLAIN.search(oracle_text):
        return Predicate(kind="always")
    return None


# ---------------------------------------------------------------------------
# Activated-ability and cycling parsing.
# ---------------------------------------------------------------------------

# An activated ability is "<cost>: <effect>". Cost cannot contain a colon
# in legal Magic, so the first colon splits it cleanly.
_ACTIVATED_RE = re.compile(r"^(?P<cost>[^:]{1,80}):\s*(?P<effect>.+?)\.?$")

# ETB trigger on the card itself: "When this creature enters, …" /
# "When <name> enters, …"
_ETB_RE = re.compile(
    r"^When\s+(?P<subject>.+?)\s+enters[,.]\s*(?P<effect>.+?)\.?$",
    re.IGNORECASE,
)
_OTHER_TRIGGERED_RE = re.compile(
    r"^(Whenever|At the beginning of|When)\b",
    re.IGNORECASE,
)

# Cycling line. Covers plain "Cycling {2}" plus type-cycling
# "Mountaincycling {2}" / "Plainscycling {1}{W}". Captures the type prefix
# (or empty for plain cycling) and the cost.
_CYCLING_RE = re.compile(
    r"^(?P<prefix>plains|island|swamp|mountain|forest|land|basiclands?|wastes?)?cycling\s+"
    r"(?P<cost>(?:\{[^{}]+\})+)\.?$",
    re.IGNORECASE,
)
# Channel ability line: "Channel — <cost>, Discard this card: <effect>"
_CHANNEL_RE = re.compile(r"^channel\s*[—-]\s*(?P<rest>.+)$", re.IGNORECASE)


# Vehicle / Equipment lines we ignore at the parser level.
# Examples: "Crew 2", "Equip {2}", "Equip 1".
_VEHICLE_EQUIPMENT_LINE_RE = re.compile(
    r"^(?:crew\s+\d+|equip(?:\s+\{[^{}]+\}|\s+\d+))\.?$",
    re.IGNORECASE,
)

# Planeswalker loyalty ability: starts with "+N:", "-N:", U+2212 N:,
# "0:", "+X:", or "-X:". Followed by the effect text. Per the v1 design
# rule, planeswalker abilities don't affect mulligan classification
# meaningfully — silently drop them so the planeswalker auto-classifies.
# Both the ASCII hyphen-minus and the unicode minus sign (U+2212) appear
# in real Scryfall oracle text; we include both in the character class.
_LOYALTY_ABILITY_RE = re.compile(
    "^[+\\-\u2212](\\d+|X)\\s*:",
    re.IGNORECASE,
)
# Set-specific keyword lines. Match "Airbend N", "Waterbend {2}", etc. —
# anything that starts with a set keyword from SET_SPECIFIC_KEYWORDS.
_SET_KEYWORD_LEADING_RE = re.compile(
    r"^(?P<kw>" + "|".join(re.escape(k) for k in SET_SPECIFIC_KEYWORDS) + r")\b",
    re.IGNORECASE,
)


def _has_set_keyword_in_text(oracle_text: str) -> str | None:
    """Return the first set-specific keyword found anywhere in the text.

    Used by per-type parsers to bail with a clean reason rather than a
    confusing low-level "activated cost not recognised" or "unrecognised
    line" trace. The check is substring-based — we only need to know
    that *some* set-mechanic is present.
    """
    if not SET_SPECIFIC_KEYWORDS:
        return None
    text_lower = oracle_text.lower()
    for kw in SET_SPECIFIC_KEYWORDS:
        # Word-boundary check to avoid partial matches inside other words.
        if re.search(rf"\b{re.escape(kw)}\b", text_lower):
            return kw
    return None


def _is_pure_keyword_line(chunk: str) -> bool:
    """True if a chunk consists only of evergreen / ignorable keyword names.

    Tolerates trailing cost on "ward" — "ward {1}" / "ward {2}{U}" still
    counts as a single keyword. Comma-separated lists work too:
    "flying, vigilance, ward {1}". Also accepts the ignorable-keyword
    set ("changeling", "convoke", etc.).
    """
    parts = [p.strip() for p in chunk.split(",")]
    if not parts:
        return False
    accept = EVERGREEN_KEYWORDS | IGNORABLE_KEYWORD_LINES
    for raw in parts:
        # Strip trailing punctuation and an optional "{cost}" tail (for ward).
        stripped = raw.rstrip(".").strip()
        # Drop a trailing mana-cost run.
        cleaned = re.sub(r"\s*(?:\{[^{}]+\})+\s*$", "", stripped).strip()
        if cleaned.lower() not in accept:
            return False
    return True


def _is_self_etb(subject: str, name: str) -> bool:
    """True if ``subject`` refers to the card itself."""
    s = subject.strip().lower()
    self_phrases = {
        "this",
        "this creature",
        "this artifact",
        "this enchantment",
        "this land",
        "this card",
    }
    if s in self_phrases:
        return True
    full = name.lower()
    short = name.split(",", 1)[0].strip().lower()
    return s in (full, short)


# ---------------------------------------------------------------------------
# Mode builders.
#
# Each returns a Mode (or None if it couldn't build one). The cast Mode is
# always built first when it applies; cycling/channel/activated come after.
# ---------------------------------------------------------------------------


def _build_cast_mode(
    mana_cost: ManaCost | None,
    extra_effects: list[Effect],
    is_permanent: bool,
) -> Mode | None:
    """Build the card's normal cast mode.

    For permanents, the cast Mode's effects start with
    ``EntersBattlefieldEffect`` followed by any ETB-trigger effects we
    parsed. For instants/sorceries, just the parsed effects.

    Returns ``None`` if there's no mana cost (lands, etc.)."""
    if mana_cost is None:
        return None
    cost = Cost(mana=mana_cost)
    effects: list[Effect] = []
    if is_permanent:
        effects.append(EntersBattlefieldEffect())
    effects.extend(extra_effects)
    return Mode(kind="cast", cost=cost, effects=effects)


def _build_cycling_mode(line: str) -> Mode | None:
    """If ``line`` is a cycling/landcycling line, build the corresponding Mode."""
    m = _CYCLING_RE.match(line)
    if m is None:
        return None
    prefix = (m.group("prefix") or "").lower()
    cost_str = m.group("cost")
    try:
        mana = parse_mana_cost(cost_str)
    except ValueError:
        return None
    cost = Cost(mana=mana, discard_self=True)

    effects: list[Effect]
    if not prefix:
        effects = [DrawCardsEffect(n=1)]
        return Mode(kind="cycle", cost=cost, effects=effects)
    if prefix == "land":
        effects = [
            FetchLandEffect(target_filter="any", destination="hand"),
        ]
        return Mode(kind="land_cycle", cost=cost, effects=effects)
    if prefix in ("basicland", "basiclands", "wastes", "waste"):
        # Wastecycling and basiclandcycling treat any basic.
        effects = [FetchLandEffect(target_filter="basic", destination="hand")]
        return Mode(kind="land_cycle", cost=cost, effects=effects)
    # Specific basic type — Plainscycling, Mountaincycling, etc.
    return Mode(
        kind="land_cycle",
        cost=cost,
        effects=[
            FetchLandEffect(
                target_filter="specific_subtype",
                subtype=prefix.title(),
                destination="hand",
            )
        ],
    )


def _build_channel_mode(line: str) -> Mode | None:
    """If ``line`` is a channel line, parse its activation cost + effect."""
    m = _CHANNEL_RE.match(line)
    if m is None:
        return None
    rest = m.group("rest").strip()
    # Channel reads as "<cost>: <effect>" after the em-dash.
    if ":" not in rest:
        return None
    cost_str, effect_str = rest.split(":", 1)
    cost = _parse_cost_string(cost_str.strip())
    if cost is None:
        return None
    effects = _match_spell_effect(effect_str.strip())
    if effects is None:
        return None
    return Mode(kind="channel", cost=cost, effects=effects)


def _build_activated_mode(
    line: str, rf: RoleFeatures | None = None
) -> tuple[Mode | None, str | None]:
    """If ``line`` is an activated ability "<cost>: <effect>", build a Mode.

    When ``rf`` is supplied, the effect's role-features side-effects fire
    (e.g. "Sacrifice this: Destroy target creature." sets
    ``removal_destroy_or_exile=True``). Activations are gated behind a
    cost but they still represent the card "doing" the effect, so the
    XGBoost stage benefits from counting them.

    Returns ``(mode, blocker_reason)``:
    * ``(Mode, None)`` if both cost and effect parse cleanly. If only the
      effect fails to parse, we still return a Mode with a placeholder
      noop effect — the simulator only needs the cost for mulligan
      castability checks.
    * ``(None, str)`` if the cost is unparseable AND the chunk is otherwise
      ambiguous. Per the v1 rule we lean toward silently dropping these
      since activated abilities with weird costs (set-mechanic costs like
      Blight N, "Exile this from your graveyard", etc.) rarely affect
      mulligan-relevant turns.
    * ``(None, None)`` if the line isn't activated-shaped at all.
    """
    m = _ACTIVATED_RE.match(line)
    if m is None:
        return None, None
    cost_str = m.group("cost").strip()
    effect_str = m.group("effect").strip()
    cost = _parse_cost_string(cost_str)
    if cost is None:
        # Cost we can't model (Blight N, exile-from-graveyard, behold,
        # etc.). Silently drop with no blocker — these activations are
        # rarely mulligan-relevant. We still capture token creation /
        # triggered-signal-style effects from the body for role_features.
        if rf is not None:
            for body in _match_token_creation(effect_str):
                rf.creates_creatures.append(body)
            _extract_triggered_signal(effect_str, rf)
        return None, None
    effects = _match_spell_effect(effect_str, rf=rf)
    if effects is None:
        # Cost parsed cleanly but we don't model the effect. Build a Mode
        # with a placeholder noop — the simulator can still evaluate
        # whether the player CAN activate the ability (cost-wise). For
        # role_features we still scan the body for token creation and
        # nested triggered signals.
        if rf is not None:
            for body in _match_token_creation(effect_str):
                rf.creates_creatures.append(body)
            _extract_triggered_signal(effect_str, rf)
        return Mode(
            kind="activated",
            cost=cost,
            effects=[NoopEffect(role_tag="activated_unknown")],
        ), None
    return Mode(kind="activated", cost=cost, effects=effects), None


# ---------------------------------------------------------------------------
# Per-type parsers.
# ---------------------------------------------------------------------------


def _parse_land(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    type_line = base["type_line"]
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)
    reasons: list[str] = []
    mana_abilities: list[ManaAbility] = []
    extra_modes: list[Mode] = []
    blockers: list[str] = []

    enter_condition = _match_etb_tapped_predicate(oracle_text)

    # Basic land: Scryfall lists basic types in the type line and the oracle
    # text is empty (or only reminder text).
    if "Basic" in type_line and not chunks:
        for type_name, color in _BASIC_TYPES.items():
            if type_name in type_line:
                mana_abilities.append(
                    ManaAbility(
                        cost=Cost(mana=_empty_mana(), tap=True),
                        produces=[[color]],  # type: ignore[list-item]
                    )
                )
                reasons.append(f"basic land tapping for {{{color}}}")
                return ParsedCard(
                    status=ParseStatus.AUTO,
                    mana_abilities=mana_abilities,
                    enter_condition=enter_condition,
                    role_features=role_features,
                    reasons=reasons,
                    **base,
                )
        blockers.append("basic land but no recognised land type")

    # Typed dual / triple lands (e.g. "Land — Swamp Mountain", aka shocklands
    # / fastlands / typed duals) inherit mana abilities from their basic
    # land subtypes. The actual mana ability isn't in oracle text — it's
    # implicit from the type line. Detect this and synthesize the abilities.
    typed_basic_subtypes = [s for s in base.get("subtypes", []) if s in _BASIC_TYPES]
    if typed_basic_subtypes and not mana_abilities:
        # Combine into a single tap-ability with multiple OR options
        # (matches the dual-land convention: pick one color per activation).
        produces: list[list[str]] = []
        for subtype in typed_basic_subtypes:
            color = _BASIC_TYPES[subtype]
            if [color] not in produces:
                produces.append([color])
        if produces:
            mana_abilities.append(
                ManaAbility(
                    cost=Cost(mana=_empty_mana(), tap=True),
                    produces=produces,  # type: ignore[arg-type]
                )
            )
            reasons.append(
                f"typed dual land — produces {' / '.join('{' + c[0] + '}' for c in produces)}"
            )

    # Non-basic lands: walk every chunk. Recognised shapes:
    #   * the "<land> enters tapped (unless …)" sentence (skipped — already
    #     factored into enter_condition),
    #   * a "{T}: Add {…}" mana-ability line,
    #   * a non-mana activated ability ("{2}{U}, {T}: Draw a card, then
    #     discard a card.") parsed via _build_activated_mode and added to
    #     ``modes``. This lets utility lands like Agna Qel'a auto-classify.
    for raw_chunk in chunks:
        chunk = _strip_label_prefix(raw_chunk)
        if "enters tapped" in chunk.lower() or "enters the battlefield tapped" in chunk.lower():
            continue
        if ab := _extract_mana_ability(chunk):
            mana_abilities.append(ab)
            continue
        # Pure-keyword line (Vigilance on a land, etc.).
        if _is_pure_keyword_line(chunk):
            continue
        # Triggered abilities on the land (ETB lifegain, scry, etc.) —
        # silently dropped per the v1 design rule.
        if _ETB_RE.match(chunk) or _OTHER_TRIGGERED_RE.match(chunk):
            _extract_triggered_signal(chunk, role_features)
            continue
        # Non-mana activated ability.
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
            continue
        if _ACTIVATED_RE.match(chunk):
            continue
        # Static / passive prose on a land — silently drop.
        if _is_likely_static_or_triggered(chunk):
            _extract_triggered_signal(chunk, role_features)
            continue
        blockers.append(f"unrecognised land text: {chunk!r}")

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=extra_modes,
            mana_abilities=mana_abilities,
            enter_condition=enter_condition,
            role_features=role_features,
            reasons=reasons + blockers,
            **base,
        )

    if not mana_abilities:
        # Lands with no tap-for-mana ability but at least one
        # recognised activated mode (Evolving Wilds-style sac-fetches,
        # or other utility-only lands) still auto-classify. The
        # simulator only needs the activated mode's structure to
        # evaluate castability / playability; it doesn't require
        # a mana ability to exist.
        if extra_modes:
            reasons.append(f"no tap-for-mana ability; {len(extra_modes)} activated ability(ies)")
            return ParsedCard(
                status=ParseStatus.AUTO,
                modes=extra_modes,
                mana_abilities=[],
                enter_condition=enter_condition,
                role_features=role_features,
                reasons=reasons,
                **base,
            )
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=extra_modes,
            mana_abilities=[],
            enter_condition=enter_condition,
            role_features=role_features,
            reasons=["land doesn't have a recognised mana ability"],
            **base,
        )

    color_summary = "/".join(
        opt for ab in mana_abilities for opt_list in ab.produces for opt in opt_list
    )
    tag = (
        " (always tapped)"
        if enter_condition and enter_condition.kind == "always"
        else (" (conditional ETB)" if enter_condition else "")
    )
    reasons.append(f"land taps for {color_summary}{tag}")
    if extra_modes:
        reasons.append(f"non-mana activated abilities: {len(extra_modes)}")
    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=extra_modes,
        mana_abilities=mana_abilities,
        enter_condition=enter_condition,
        role_features=role_features,
        reasons=reasons,
        **base,
    )


def _parse_creature(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    name: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)
    reasons: list[str] = ["creature stats parsed"]

    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    evergreens = [k for k in raw_keywords if k in EVERGREEN_KEYWORDS]

    # Bail on alt-cost mechanics we don't model.
    blocking_alt_costs = [k for k in raw_keywords if k in ALT_COST_KEYWORDS]
    if blocking_alt_costs:
        reasons.append(f"alternative-cost keyword(s) present: {', '.join(blocking_alt_costs)}")
        early_cast = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
        # Note: `evergreen_keywords` is already in `base` via parse_card().
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=[early_cast] if early_cast is not None else [],
            role_features=role_features,
            reasons=reasons,
            **base,
        )

    # Bail on set-specific keywords (airbend / waterbend / earthbend / …)
    # with a clean reason. Token-creation we already pulled out above
    # might still populate role_features for partial signal.
    if set_kw := _has_set_keyword_in_text(oracle_text):
        early_cast = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
        for body in _match_token_creation(_strip_reminder(oracle_text)):
            role_features.creates_creatures.append(body)
        reasons.append(f"set-specific keyword {set_kw!r} not modelled")
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=[early_cast] if early_cast is not None else [],
            role_features=role_features,
            reasons=reasons,
            **base,
        )

    etb_effects: list[Effect] = []
    extra_modes: list[Mode] = []
    mana_abilities: list[ManaAbility] = []
    blockers: list[str] = []

    for raw_chunk in chunks:
        # Strip leading mechanic-label prefixes ("Exhaust — ", "Raid — ", etc.)
        # so the body parses through the normal matchers.
        chunk = _strip_label_prefix(raw_chunk)
        # 1. Pure keyword line — already in `keywords`. Skip.
        if _is_pure_keyword_line(chunk):
            continue

        # 2. Static "<this creature> ..." self-modifier (variable P/T,
        # conditional pump, "<this> can't be blocked", etc). Doesn't affect
        # cast / castability so we ignore it rather than bail. Triggered
        # abilities ("When this creature dies/enters") match _ETB_RE /
        # _OTHER_TRIGGERED_RE before we get here, so this is safe.
        if _STATIC_SELF_MOD_RE.match(chunk):
            continue
        # Cards often spell their own name where rules text would otherwise
        # say "this creature" — accept that as a self-static modifier too.
        if _looks_like_self_static_modifier(chunk, name):
            continue

        # 3. Cycling / land-cycling on the creature.
        if cyc := _build_cycling_mode(chunk):
            extra_modes.append(cyc)
            continue
        if ch := _build_channel_mode(chunk):
            extra_modes.append(ch)
            continue
        # 3b. Waterbending activated mode (TLA).
        if wb := _try_build_waterbend_mode(chunk, role_features):
            extra_modes.append(wb)
            continue

        # 4. ETB on the creature itself.
        if m := _ETB_RE.match(chunk):
            if _is_self_etb(m.group("subject"), name):
                inner = m.group("effect").strip()
                effects = _match_spell_effect(inner, rf=role_features)
                if effects is not None:
                    etb_effects.extend(effects)
                    continue
                # Try token-creation as an ETB even if the broader chunk
                # doesn't parse as a clean spell effect.
                bodies = _match_token_creation(chunk)
                if bodies:
                    role_features.creates_creatures.extend(bodies)
                    etb_effects.append(NoopEffect(role_tag="create_token"))
                    continue
                # Per the v1 design rule: ETB triggers we can't parse are
                # silently dropped (they're triggered abilities). We still
                # extract embedded card-draw / bending signals via the
                # static/triggered scanner.
                _extract_triggered_signal(inner, role_features)
                continue
            # ETB-shaped but for some other permanent — ignored triggered ability.
            continue

        # 5. Other triggered abilities — ignored per design rules. We do
        # scan them for token creation, since cast-triggers / attack-
        # triggers that create tokens (e.g. Sokka) are worth recording
        # for role_features. Triggered card-draw is also captured.
        if _OTHER_TRIGGERED_RE.match(chunk):
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            _extract_triggered_signal(chunk, role_features)
            continue

        # 6. Activated abilities. Try mana ability first (it's a special
        # shape: "{T}: Add {…}." on a creature is a mana dork).
        if ab := _extract_mana_ability(chunk):
            mana_abilities.append(ab)
            continue

        # General activated ability — captured as a Mode regardless of cost.
        # The simulator decides whether to consider expensive ones.
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
            continue
        # If the chunk was activated-shaped but the helper silently dropped
        # it (unparseable cost like "Blight 2" or "Remove a counter from
        # this creature"), treat the chunk as handled per the v1 rule.
        if _ACTIVATED_RE.match(chunk):
            continue

        # 7. Static ability / triggered without "When/Whenever/At" prefix
        # (rare phrasings) / passive prose — silently drop per the v1
        # design rule. Capture any embedded draw / mana production.
        if _is_likely_static_or_triggered(chunk):
            _extract_triggered_signal(chunk, role_features)
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            continue

        # 8. Anything else — modal text, action verb we don't model. Bail.
        blockers.append(f"unrecognised line: {chunk!r}")

    cast_mode = _build_cast_mode(base.get("mana_cost"), etb_effects, is_permanent=True)
    modes: list[Mode] = []
    if cast_mode is not None:
        modes.append(cast_mode)
    modes.extend(extra_modes)

    # Update role_features from parsed effects.
    _populate_role_features_from_effects(role_features, modes)

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            mana_abilities=mana_abilities,
            role_features=role_features,
            reasons=reasons + blockers,
            **base,
        )

    if evergreens:
        reasons.append(f"evergreen keywords: {', '.join(evergreens)}")
    if etb_effects:
        reasons.append("ETB effects: " + ", ".join(_summarize_effect(e) for e in etb_effects))
    if mana_abilities:
        reasons.append("mana dork ability detected")
    if extra_modes:
        reasons.append(f"alternative modes: {', '.join(m.kind for m in extra_modes)}")

    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=modes,
        mana_abilities=mana_abilities,
        role_features=role_features,
        reasons=reasons,
        **base,
    )


def _parse_spell(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)
    reasons: list[str] = []

    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    blocking_alt_costs = [k for k in raw_keywords if k in ALT_COST_KEYWORDS]
    if blocking_alt_costs:
        reasons.append(f"alternative-cost keyword(s) present: {', '.join(blocking_alt_costs)}")
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            role_features=role_features,
            reasons=reasons,
            **base,
        )

    # Bail on set-specific keywords. We still try to capture token bodies
    # from the oracle text so role_features has partial signal.
    if set_kw := _has_set_keyword_in_text(oracle_text):
        for body in _match_token_creation(cleaned):
            role_features.creates_creatures.append(body)
        reasons.append(f"set-specific keyword {set_kw!r} not modelled")
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            role_features=role_features,
            reasons=reasons,
            **base,
        )

    spell_effects: list[Effect] = []
    blockers: list[str] = []

    # Land-fetch effects can span the whole text (multi-clause Cultivate-style).
    fetch_effects = _match_fetch_land_effects(cleaned)
    spell_effects.extend(fetch_effects)

    # Token creation on a spell.
    bodies = _match_token_creation(cleaned)
    if bodies:
        role_features.creates_creatures.extend(bodies)
        spell_effects.append(NoopEffect(role_tag="create_token"))

    # Per-chunk effect matching.
    extra_modes_spell: list[Mode] = []
    for raw_chunk in chunks:
        chunk = _strip_label_prefix(raw_chunk)
        # Single-word keyword lines (Convoke, Changeling, etc.) on a spell
        # — these are rules-text decorators, not blockers.
        if _is_pure_keyword_line(chunk):
            continue
        # Cost-reduction lines on the spell itself ("This spell costs {N}
        # less to cast for each X you control.") — silently drop. The
        # card's effective MV may be lower than its mana_cost suggests,
        # but for v1 we accept the headline cost.
        if _COST_REDUCTION_RE.search(chunk):
            continue
        # Skip "As an additional cost to cast this spell, …" — recognised
        # but unmodelled. Card stays NEEDS_LLM (caller adds blocker
        # below); the rest of the text still parses for role_features.
        if chunk.lower().startswith("as an additional cost to cast this spell"):
            blockers.append(f"additional cost not modelled: {chunk!r}")
            continue
        # Waterbending activated mode (TLA) on a spell — rare but possible.
        if wb := _try_build_waterbend_mode(chunk, role_features):
            extra_modes_spell.append(wb)
            continue
        # Skip chunks the fetch matcher already accounted for to avoid
        # double-flagging them as "unrecognised."
        if any(
            p.search(chunk)
            for p in (
                _FETCH_BATTLEFIELD_TAPPED_RE,
                _FETCH_BATTLEFIELD_UNTAPPED_RE,
                _FETCH_TO_HAND_RE,
            )
        ):
            continue
        # Skip chunks that are purely a token-creation phrase.
        if _CREATE_TOKEN_RE.search(chunk) and not re.search(
            r"\bdraw\b|\bdestroy\b|\bexile\b|\bdeals\b|\bgain\b|\bscry\b", chunk, re.IGNORECASE
        ):
            continue

        effects = _match_spell_effect(chunk, rf=role_features)
        if effects is None:
            # Static / triggered tolerance: silently drop the line if it
            # reads as passive prose or a trigger we don't model. Most
            # spells don't have static/triggered text, but cards like
            # "Until end of turn, ..." enchanting effects can fall here.
            if _is_likely_static_or_triggered(chunk):
                _extract_triggered_signal(chunk, role_features)
                continue
            blockers.append(f"unrecognised line: {chunk!r}")
        else:
            spell_effects.extend(effects)

    cast_mode = _build_cast_mode(base.get("mana_cost"), spell_effects, is_permanent=False)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []
    modes.extend(extra_modes_spell)

    _populate_role_features_from_effects(role_features, modes)

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            role_features=role_features,
            reasons=reasons + blockers,
            **base,
        )

    if spell_effects:
        reasons.append("effects: " + ", ".join(_summarize_effect(e) for e in spell_effects))
    else:
        reasons.append("no effects recognised (empty oracle text?)")
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            role_features=role_features,
            reasons=reasons,
            **base,
        )

    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=modes,
        role_features=role_features,
        reasons=reasons,
        **base,
    )


# ---------------------------------------------------------------------------
# Aura / Equipment / Vehicle helpers and parsers.
# ---------------------------------------------------------------------------

# Aura "removal" patterns — enchanted creature is rendered ineffective.
_AURA_REMOVAL_PATTERNS = (
    re.compile(r"enchanted creature can't attack(?:\s+or block)?", re.IGNORECASE),
    re.compile(r"enchanted creature doesn't untap", re.IGNORECASE),
    re.compile(r"enchanted creature is tapped", re.IGNORECASE),
    re.compile(r"enchanted creature has defender", re.IGNORECASE),
    re.compile(r"enchanted creature loses all abilities", re.IGNORECASE),
)
# Aura pump patterns — enchanted creature gets +N/+M and/or gains keywords.
_AURA_PUMP_RE = re.compile(
    r"enchanted creature gets\s+\+(?P<p>\d+)/\+(?P<t>\d+)",
    re.IGNORECASE,
)
_AURA_PUMP_GAINS_RE = re.compile(
    r"enchanted creature (?:gets[^.]*\band\s+gains|gains|has)\s+("
    + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS)
    + r"(?:[\s,]+(?:and\s+)?(?:"
    + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS)
    + r"))*)",
    re.IGNORECASE,
)


def _classify_aura(oracle_text: str, role_features: RoleFeatures) -> bool:
    """Set is_removal_aura / is_pump_aura on role_features based on the
    aura's text. Returns True if any aura classification was applied."""
    classified = False
    for pat in _AURA_REMOVAL_PATTERNS:
        if pat.search(oracle_text):
            role_features.is_removal_aura = True
            classified = True
            break
    if pump := _AURA_PUMP_RE.search(oracle_text):
        role_features.is_pump_aura = True
        role_features.aura_pump_power = int(pump.group("p"))
        role_features.aura_pump_toughness = int(pump.group("t"))
        classified = True
    if grants := _AURA_PUMP_GAINS_RE.search(oracle_text):
        # Reuse the granted-keyword extractor on just the matched group.
        kws = re.findall(
            r"\b(?:" + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS) + r")\b",
            grants.group(1),
            re.IGNORECASE,
        )
        if kws:
            role_features.is_pump_aura = True
            for k in kws:
                kl = k.lower()
                if kl not in role_features.aura_pump_granted_keywords:
                    role_features.aura_pump_granted_keywords.append(kl)
            classified = True
    return classified


def _parse_aura(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    """Parser branch for Aura enchantments.

    Detects removal-aura vs pump-aura via oracle text, scans for token
    creation (e.g. an aura that creates a token on activation), and
    accepts ignorable lines like "Enchant creature" plus activated
    abilities. The aura's static effect is sim-noop — the simulator just
    sees the cast Mode + EntersBattlefieldEffect.
    """
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)

    cast_mode = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []

    # Aura categorization (sets role_features.is_removal_aura / is_pump_aura).
    _classify_aura(oracle_text, role_features)

    # Token creation can fire on activation / ETB.
    for body in _match_token_creation(cleaned):
        role_features.creates_creatures.append(body)

    extra_modes: list[Mode] = []
    blockers: list[str] = []
    for raw_chunk in chunks:
        chunk = _strip_label_prefix(raw_chunk)
        cl = chunk.lower()
        # Single-word keywords (Flash, Convoke, etc.).
        if _is_pure_keyword_line(chunk):
            continue
        if cl.startswith("enchant ") or cl == "enchant creature":
            continue
        if cl.startswith("enchanted creature"):
            # Static aura effect — already classified above. Ignore.
            continue
        # Waterbending activated mode (TLA).
        if wb := _try_build_waterbend_mode(chunk, role_features):
            extra_modes.append(wb)
            continue
        # Activated abilities on the aura (e.g. sac-self → exile target).
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
            continue
        if _ACTIVATED_RE.match(chunk):
            continue
        # Triggered abilities on the aura.
        if _OTHER_TRIGGERED_RE.match(chunk) or _ETB_RE.match(chunk):
            _extract_triggered_signal(chunk, role_features)
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            continue
        # Static / passive prose — silently drop.
        if _is_likely_static_or_triggered(chunk):
            _extract_triggered_signal(chunk, role_features)
            continue
        blockers.append(f"unrecognised aura line: {chunk!r}")

    modes.extend(extra_modes)

    if not (role_features.is_removal_aura or role_features.is_pump_aura):
        # Couldn't classify the aura's static effect — bail with reason.
        blockers.append("aura static effect not classified")

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            role_features=role_features,
            reasons=blockers,
            **base,
        )

    label = "removal aura" if role_features.is_removal_aura else "pump aura"
    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=modes,
        role_features=role_features,
        reasons=[label],
        **base,
    )


def _parse_artifact_typed(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
    is_vehicle: bool,
    is_equipment: bool,
) -> ParsedCard:
    """Parser branch for Vehicle / Equipment artifacts.

    Both card types come with a small set of ignorable lines: "Crew N"
    (vehicles), "Equip {N}" / "Equip N" (equipment), evergreen-on-an-
    artifact, dies-triggers (which we ignore), and token creation. Cards
    that fit this shape auto-classify.
    """
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)

    cast_mode = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []

    # Token creation (Vehicle ETB pilot, Equipment with a stapled body, …).
    for body in _match_token_creation(cleaned):
        role_features.creates_creatures.append(body)

    extra_modes: list[Mode] = []
    blockers: list[str] = []
    for raw_chunk in chunks:
        chunk = _strip_label_prefix(raw_chunk)
        # Pure-keyword line (Reach, Trample on a vehicle).
        if _is_pure_keyword_line(chunk):
            continue
        # Crew N / Equip N.
        if _VEHICLE_EQUIPMENT_LINE_RE.match(chunk):
            continue
        # Triggered abilities — ignored (dies-trigger Clue, etc.).
        if _ETB_RE.match(chunk) or _OTHER_TRIGGERED_RE.match(chunk):
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            _extract_triggered_signal(chunk, role_features)
            continue
        # Waterbending activated mode (TLA).
        if wb := _try_build_waterbend_mode(chunk, role_features):
            extra_modes.append(wb)
            continue
        # Activated abilities on artifact.
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
            continue
        if _ACTIVATED_RE.match(chunk):
            continue
        # Static / passive prose — silently drop.
        if _is_likely_static_or_triggered(chunk):
            _extract_triggered_signal(chunk, role_features)
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            continue
        blockers.append(f"unrecognised line: {chunk!r}")

    modes.extend(extra_modes)
    label = "vehicle" if is_vehicle else "equipment"

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            role_features=role_features,
            reasons=[f"{label}: " + r for r in blockers] or [f"{label} blockers"],
            **base,
        )

    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=modes,
        role_features=role_features,
        reasons=[label],
        **base,
    )


def _parse_other_permanent(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
    type_word: str,
) -> ParsedCard:
    """Parser for Enchantments / Artifacts / Planeswalkers that aren't
    Creatures, Lands, Auras, Equipment, or Vehicles.

    Walks chunks the same way the artifact-typed parser does: pure-keyword
    lines and triggered abilities are silently dropped (with token-creation
    captured), activated abilities become Modes, static prose passes
    through the static/triggered tolerance, and only truly unrecognised
    action lines bail."""
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)

    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    blocking_alt_costs = [k for k in raw_keywords if k in ALT_COST_KEYWORDS]
    if blocking_alt_costs:
        early_cast = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=[early_cast] if early_cast is not None else [],
            role_features=role_features,
            reasons=[f"alternative-cost keyword(s) present: {', '.join(blocking_alt_costs)}"],
            **base,
        )

    if set_kw := _has_set_keyword_in_text(oracle_text):
        early_cast = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
        for body in _match_token_creation(cleaned):
            role_features.creates_creatures.append(body)
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=[early_cast] if early_cast is not None else [],
            role_features=role_features,
            reasons=[f"set-specific keyword {set_kw!r} not modelled"],
            **base,
        )

    cast_mode = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []

    # Token creation is captured per-chunk below; the previous top-level
    # ``_match_token_creation(cleaned)`` call was removed because it
    # double-counted bodies that the per-chunk branches already capture.

    if not chunks:
        return ParsedCard(
            status=ParseStatus.AUTO,
            modes=modes,
            role_features=role_features,
            reasons=[f"vanilla {type_word.lower()}"],
            **base,
        )

    extra_modes: list[Mode] = []
    mana_abilities: list[ManaAbility] = []
    blockers: list[str] = []
    for raw_chunk in chunks:
        chunk = _strip_label_prefix(raw_chunk)
        if _is_pure_keyword_line(chunk):
            continue
        # Planeswalker loyalty ability — silently dropped. Token creation
        # inside the ability still counts toward creates_creatures.
        if _LOYALTY_ABILITY_RE.match(chunk):
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            _extract_triggered_signal(chunk, role_features)
            continue
        if _ETB_RE.match(chunk) or _OTHER_TRIGGERED_RE.match(chunk):
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            _extract_triggered_signal(chunk, role_features)
            continue
        # Direct-action token creation: a chunk whose first verb is "Create
        # … creature token" — typical for saga chapters and immediate
        # cast-resolution effects on enchantments. Consume it so the loop
        # doesn't fall to "unrecognised line".
        if direct_bodies := _match_token_creation(chunk):
            role_features.creates_creatures.extend(direct_bodies)
            continue
        # Waterbending activated mode (TLA).
        if wb := _try_build_waterbend_mode(chunk, role_features):
            extra_modes.append(wb)
            continue
        # Mana ability on a non-creature artifact / enchantment — same
        # shapes the land and creature branches recognise.
        if ab := _extract_mana_ability(chunk):
            mana_abilities.append(ab)
            continue
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
            continue
        if _ACTIVATED_RE.match(chunk):
            continue
        if _is_likely_static_or_triggered(chunk):
            _extract_triggered_signal(chunk, role_features)
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            continue
        blockers.append(f"unrecognised line: {chunk!r}")

    modes.extend(extra_modes)

    # Non-creature, non-equipment, non-vehicle artifact with at least one
    # mana ability = "mana rock" (Sol Ring, Springleaf Drum, etc.). Routing
    # via `_parse_other_permanent` already excludes Equipment / Vehicle
    # (they go through `_parse_artifact_typed`) and creatures (which take
    # the Creature branch first), so the `type_word == "Artifact"` check is
    # the only filter needed here.
    if type_word == "Artifact" and mana_abilities:
        role_features.is_mana_rock = True

    if blockers:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=modes,
            mana_abilities=mana_abilities,
            role_features=role_features,
            reasons=[f"{type_word.lower()}: " + r for r in blockers],
            **base,
        )

    return ParsedCard(
        status=ParseStatus.AUTO,
        modes=modes,
        mana_abilities=mana_abilities,
        role_features=role_features,
        reasons=[type_word.lower()],
        **base,
    )


# ---------------------------------------------------------------------------
# RoleFeatures population.
# ---------------------------------------------------------------------------


def _populate_role_features_from_effects(rf: RoleFeatures, modes: list[Mode]) -> None:
    """Aggregate per-Mode effects into role_features fields.

    Reads through every Mode's effects and updates ``cards_drawn``,
    ``cards_manipulated``, ``removal_*`` etc. Idempotent — calling it
    twice doesn't double-count because we set absolute values from the
    cast Mode only (cycling counts as +1 net draw but its discard
    self-cancels, so we deliberately leave it out)."""
    for mode in modes:
        # Only the cast Mode contributes to cards_drawn / scry totals;
        # cycling and channel are conditional-use modes whose card-draw
        # contribution depends on whether you choose to use them. The
        # XGBoost feature stage downstream cares about "guaranteed" draw,
        # which is what's on the cast resolution.
        if mode.kind != "cast":
            continue
        for effect in mode.effects:
            if isinstance(effect, DrawCardsEffect):
                rf.cards_drawn += effect.n
            elif isinstance(effect, ScryEffect):
                rf.cards_manipulated += effect.n
            elif isinstance(effect, NoopEffect):
                tag = effect.role_tag or ""
                if tag in ("removal_destroy", "removal_exile"):
                    rf.removal_destroy_or_exile = True
                elif tag.startswith("removal_damage_"):
                    # Format: removal_damage_creature_3 / removal_damage_any_3.
                    parts = tag.rsplit("_", 1)
                    if len(parts) == 2 and parts[1].isdigit():
                        rf.removal_burn_damage = int(parts[1])


def _summarize_effect(e: Effect) -> str:
    """One-line description used in ``reasons`` so reports are readable."""
    bits: list[str] = [e.kind]
    if isinstance(e, DrawCardsEffect | ScryEffect):
        bits.append(f"x{e.n}")
    elif isinstance(e, FetchLandEffect):
        bits.append(f"->{e.destination}")
        if e.subtype:
            bits.append(f"({e.subtype})")
    elif isinstance(e, NoopEffect) and e.role_tag:
        bits.append(f"({e.role_tag})")
    return " ".join(bits)


# ---------------------------------------------------------------------------
# MV >= 4 fast-path.
#
# Per the project owner's design call: a card with mana value ≥ 4 that has
# no alternative casting modes and no cost-reduction effects can't realistically
# affect the first four turns of the game (turn-4 plays usually decide turn 5
# at earliest). For mulligan purposes the card is "fine to keep or mull"
# regardless of its exact effect, so we don't need LLM/human review to
# call it AUTO.
#
# Conditions to qualify for the fast-path:
# 1. The deterministic parser would otherwise have flagged the card NEEDS_LLM.
# 2. CMC ≥ 4. (X-cost cards have cmc=0 so they DON'T qualify — those need
#    closer attention because of the variable cost.)
# 3. No alt-cost keyword (kicker, flashback, escape, etc.) — these add a
#    cheaper cast mode that DOES affect turns 1-3.
# 4. No oracle-text patterns that reduce the card's cost (affinity, convoke,
#    delve, improvise, "costs N less to cast" / "costs less to cast for each ...").
# 5. No modal "Choose one — …" / "Choose two — …" structure — the modal
#    bullet structure indicates effects we can't fold into a single role
#    classification.
# ---------------------------------------------------------------------------

_COST_REDUCTION_RE = re.compile(
    r"costs?\s+\{?\d?\}?\s+less\s+to\s+cast"
    r"|"
    r"\b(?:affinity for|convoke|delve|improvise|undaunted|emerge)\b",
    re.IGNORECASE,
)
# Modal text — true modal cards offer the player a choice between bullet
# options. We exclude "Choose up to one target X" because that's a target
# specification, not a modal selection.
_MODAL_RE = re.compile(
    r"^choose\s+(?:one|two|three|one or both|one or more|any number)\s*[—\-]"
    r"|"
    r"^modal\b",
    re.IGNORECASE | re.MULTILINE,
)


def _qualifies_for_mv4_fast_path(card: dict[str, Any], parsed: ParsedCard) -> bool:
    """True if the NEEDS_LLM card's MV is ≥ 4 and it has no alt-cost or
    cost-reduction or modal text."""
    if parsed.mana_cost is None:
        return False
    if parsed.mana_cost.cmc < 4:
        return False
    if parsed.mana_cost.has_x:
        return False  # variable cost — doesn't qualify
    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    if any(k in ALT_COST_KEYWORDS for k in raw_keywords):
        return False
    oracle_text = str(card.get("oracle_text") or "")
    if _COST_REDUCTION_RE.search(oracle_text):
        return False
    return not _MODAL_RE.search(oracle_text)


def _ensure_role_other_catchall(parsed: ParsedCard) -> ParsedCard:
    """Set ``is_other`` if the card ended up with no role flag at all.

    The ``is_other`` flag is the design's catchall for cards that don't
    fit any specific category (see ``packages/cards/CLAUDE.md``). Most
    branches populate at least one flag implicitly — creatures get
    ``is_creature`` from the type seeder, lands get ``is_land``, etc.
    But a vanilla enchantment or oddball artifact with no recognised
    oracle pattern can fall through with everything default-False.
    This helper guarantees the invariant "every parsed card has at
    least one role classification" so the XGBoost stage never sees a
    blank role vector.
    """
    if not parsed.role_features.has_any_role_flag():
        parsed.role_features.is_other = True
    return parsed


def _maybe_apply_mv4_fast_path(card: dict[str, Any], parsed: ParsedCard) -> ParsedCard:
    """If the card was flagged NEEDS_LLM but qualifies for the MV ≥ 4
    fast-path, promote it to AUTO. The universal catchall in
    :func:`_ensure_role_other_catchall` (called after this) handles
    setting ``is_other`` when no other role flag is populated."""
    if parsed.status is not ParseStatus.NEEDS_LLM:
        return parsed
    if not _qualifies_for_mv4_fast_path(card, parsed):
        return parsed
    parsed.status = ParseStatus.AUTO
    # `_qualifies_for_mv4_fast_path` already returns False when mana_cost
    # is None, so it's non-None here — the assertion narrows for mypy.
    assert parsed.mana_cost is not None
    parsed.reasons.append(
        f"MV>={parsed.mana_cost.cmc} fast-path: card unlikely to affect mulligan-relevant turns"
    )
    return parsed


# ---------------------------------------------------------------------------
# Saga, Class, and transform-DFC handling.
#
# Per the project owner's design call:
# * Sagas: only encode chapter I deterministically. Set ``is_saga=True``
#   regardless. Higher chapters happen on subsequent turns and are out of
#   scope for v1 mulligan-relevant features.
# * Classes: only encode the always-on level-1 effect. Set ``is_class=True``.
#   Level 2 / 3 require activation and don't fire on cast.
# * Transform DFCs: if the back face has no mana cost (or is a land), the
#   back face never enters via cast — collapse to the front face for the
#   purposes of parsing. This catches both transform creatures whose back
#   side is a flipped form (Aang, Brigid, …) and transform sagas whose
#   back side is a creature minted by chapter III.
# ---------------------------------------------------------------------------

# Saga chapter prefix: "I — body" / "II — body" / "I, II — body" / etc.
# Roman numerals up to X cover any saga we'd reasonably see.
_SAGA_CHAPTER_PREFIX_RE = re.compile(
    r"^(?P<labels>(?:I+|IV|V|VI{0,3}|IX|X)(?:\s*,\s*(?:I+|IV|V|VI{0,3}|IX|X))*)"
    r"\s*[—\-]\s*(?P<body>.*)$"
)

# Class level-up cost line: "{1}{R}: Level 2", "{4}{G}: Level 3".
_CLASS_LEVEL_UP_RE = re.compile(
    r"^\{[^}]+\}(?:\s*,\s*\{[^}]+\})*\s*:\s*Level\s+\d+\s*$",
    re.IGNORECASE,
)


def _extract_saga_chapter_one(oracle_text: str) -> str:
    """Return only the chapter I body of a Saga's oracle text.

    Saga oracle text is shaped as a (parenthetical reminder) followed by
    one chapter-labelled line per chapter; some sagas combine chapters
    via "I, II — shared effect". This helper returns just the lines that
    belong to chapter I (whose label list contains "I"), preserving any
    unlabelled continuation lines. Returns an empty string if no chapter I
    is detectable.
    """
    cleaned = _strip_reminder(oracle_text)
    chapter_one_lines: list[str] = []
    in_chapter_one = False
    for line in cleaned.split("\n"):
        if not line.strip():
            continue
        m = _SAGA_CHAPTER_PREFIX_RE.match(line.strip())
        if m:
            labels = [lab.strip() for lab in m.group("labels").split(",")]
            if "I" in labels:
                in_chapter_one = True
                body = m.group("body").strip()
                if body:
                    chapter_one_lines.append(body)
            else:
                # Hit chapter II / III — chapter I is closed.
                break
        elif in_chapter_one:
            chapter_one_lines.append(line.strip())
    return "\n".join(chapter_one_lines)


def _extract_class_level_one(oracle_text: str) -> str:
    """Return only the level-1 (always-on) ability text of a Class.

    Class oracle text starts with a parenthetical reminder, then the
    always-on level-1 ability, then the first level-up cost line
    ("{1}{R}: Level 2"), then level-2 effects, then the next level-up
    cost, and so on. We return everything between the reminder and the
    first level-up cost line.
    """
    cleaned = _strip_reminder(oracle_text)
    level_one_lines: list[str] = []
    for line in cleaned.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if _CLASS_LEVEL_UP_RE.match(stripped):
            break
        level_one_lines.append(stripped)
    return "\n".join(level_one_lines)


def _back_face_is_castable(faces: list[dict[str, Any]]) -> bool:
    """True if the back face of a transform DFC is castable in its own right.

    "Castable" here means: it has a mana cost AND isn't a land. Lands and
    cost-less back faces (the trigger-flips, like Brigid's Doun's Mind or
    Avatar Yangchen) only enter via the front-face transform trigger and
    never affect their own cast Mode — so they're safe to ignore for
    parsing purposes.
    """
    if len(faces) < 2:
        return False
    back = faces[1]
    cost = str(back.get("mana_cost") or "")
    type_line = str(back.get("type_line") or "")
    if not cost:
        return False
    return "Land" not in type_line


def _synthesize_front_face_card(card: dict[str, Any]) -> dict[str, Any] | None:
    """If the back face is uncastable, return a card dict that looks like
    the front face alone.

    The recursed ``parse_card`` sees ``layout="normal"`` and dispatches by
    the front-face type line — so a transform Saga goes to the saga
    branch, a transform creature goes to the creature branch, etc.
    Returns ``None`` if the back face is castable in its own right (we
    can't safely collapse those yet — they need separate handling once
    Mode supports two cast modes).
    """
    faces = card.get("card_faces") or []
    if len(faces) < 2:
        return None
    if _back_face_is_castable(faces):
        return None
    front = faces[0]
    out = dict(card)
    out["mana_cost"] = front.get("mana_cost") or ""
    out["type_line"] = front.get("type_line") or out.get("type_line", "")
    out["oracle_text"] = front.get("oracle_text") or ""
    if "power" in front:
        out["power"] = front["power"]
    if "toughness" in front:
        out["toughness"] = front["toughness"]
    if "colors" in front:
        out["colors"] = front["colors"]
    if "keywords" in front:
        out["keywords"] = front["keywords"]
    # Bypass the layout check on recursion. The back face is intentionally
    # ignored from here on.
    out["layout"] = "normal"
    return out


def _parse_saga(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    """Parser branch for Enchantment — Saga.

    Sets ``is_saga=True`` and runs only chapter I's body through the
    standard enchantment chunk loop. Chapters II+ are deferred — they
    fire on subsequent turns and don't affect the mulligan-relevant
    turns 1-4 enough to justify modelling for v1.
    """
    role_features.is_saga = True
    chapter_one = _extract_saga_chapter_one(oracle_text)
    result = _parse_other_permanent(base, card, chapter_one, role_features, "Saga")
    # Replace the generic enchantment label with one that surfaces what we did.
    result.reasons = ["saga: encoded chapter I only"] + [
        r for r in result.reasons if r not in ("enchantment", "vanilla enchantment")
    ]
    return result


def _parse_class(
    base: dict[str, Any],
    card: dict[str, Any],
    oracle_text: str,
    role_features: RoleFeatures,
) -> ParsedCard:
    """Parser branch for Enchantment — Class.

    Sets ``is_class=True`` and runs only the always-on level-1 effect
    through the standard enchantment chunk loop. Levels 2 and 3 require
    activation on subsequent turns and aren't material for mulligan-time
    decisions.
    """
    role_features.is_class = True
    level_one = _extract_class_level_one(oracle_text)
    result = _parse_other_permanent(base, card, level_one, role_features, "Class")
    result.reasons = ["class: encoded level-1 effect only"] + [
        r for r in result.reasons if r not in ("enchantment", "vanilla enchantment")
    ]
    return result


# ---------------------------------------------------------------------------
# Top-level dispatch.
# ---------------------------------------------------------------------------


def parse_card(
    card: dict[str, Any],
    *,
    arena_id_index: dict[tuple[str, str], int] | None = None,
) -> ParsedCard:
    """Parse a single Scryfall card dictionary into a ``ParsedCard``.

    The input must be a dict shaped like a row from Scryfall's bulk
    ``oracle_cards`` JSON. Any input we don't fully understand returns a
    ``ParsedCard`` with ``status=NEEDS_LLM`` and reasons explaining why —
    never an exception.

    ``arena_id_index`` (optional) is a ``(oracle_id, set_code) -> mtga_id``
    map produced by :func:`mulligan_coach_cards.loader.load_arena_id_index`.
    When provided, the matching ``arena_id`` is set on the returned card.
    When None, ``arena_id`` is None — fine for unit tests and for sets
    MTGJSON hasn't ingested yet.
    """
    name = str(card.get("name", "<unknown>"))
    type_line = str(card.get("type_line", ""))
    oracle_text = str(card.get("oracle_text") or "")
    layout = str(card.get("layout", "normal"))
    oracle_id = str(card.get("oracle_id", ""))
    set_code = str(card.get("set", "")).upper()

    supertypes, types, subtypes = _parse_type_line(type_line)
    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    evergreens = [k for k in raw_keywords if k in EVERGREEN_KEYWORDS]

    # Resolve arena_id once at the top — every ParsedCard(**base) call
    # below picks it up automatically.
    arena_id: int | None = None
    if arena_id_index is not None and oracle_id and set_code:
        arena_id = arena_id_index.get((oracle_id, set_code))

    # Build the base dict fed to ParsedCard's constructor.
    base: dict[str, Any] = {
        "name": name,
        "set_code": set_code,
        "collector_number": str(card.get("collector_number", "")),
        "oracle_id": oracle_id,
        "arena_id": arena_id,
        "rarity": str(card.get("rarity", "")),
        "raw_oracle_text": oracle_text,
        "type_line": type_line,
        "types": types,
        "subtypes": subtypes,
        "supertypes": supertypes,
        "colors": list(card.get("colors") or []),
        "mana_cost": None,
        "power": card.get("power"),
        "toughness": card.get("toughness"),
        "evergreen_keywords": evergreens,
    }

    # Transform DFCs collapse to their front face when the back face is
    # uncastable (no mana cost, or a land). The recursive call sees
    # layout="normal" and dispatches normally on the front-face type line.
    if layout == "transform":
        synthetic = _synthesize_front_face_card(card)
        if synthetic is not None:
            result = parse_card(synthetic, arena_id_index=arena_id_index)
            # Preserve display fields from the original (full DFC) card so
            # the user still sees the joint name / type line in listings.
            result.name = name
            result.type_line = type_line
            if (
                "transform DFC: parsed front face only (back face has no cast cost)"
                not in result.reasons
            ):
                result.reasons.append(
                    "transform DFC: parsed front face only (back face has no cast cost)"
                )
            return result
        # Back face is castable — leave for human review.
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            reasons=["transform DFC with castable back face — both faces need encoding"],
            **base,
        )

    # Saga and Class share the enchantment branch but only encode their
    # always-on / first-chapter effect; let them through the layout gate.
    if layout not in {"normal", "saga", "class"}:
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            reasons=[f"non-normal layout {layout!r} — DFC/split/adventure not handled yet"],
            **base,
        )

    raw_cost = str(card.get("mana_cost") or "")
    if raw_cost:
        try:
            base["mana_cost"] = parse_mana_cost(raw_cost)
        except ValueError as exc:
            return ParsedCard(
                status=ParseStatus.NEEDS_LLM,
                reasons=[f"unparseable mana cost {raw_cost!r}: {exc}"],
                **base,
            )

    # Build a RoleFeatures from the type system. The per-type parsers may
    # add to it (creates_creatures, removal flags, etc.).
    role_features = _initial_role_features(types, subtypes)

    # Dispatch in order: Land > Creature > Instant/Sorcery > Enchantment >
    # Artifact > Planeswalker. Multi-typed cards (Artifact Creature) take
    # the Creature branch; their Artifact-ness is preserved in `types`.
    if "Land" in types:
        result = _parse_land(base, card, oracle_text, role_features)
    elif "Creature" in types:
        result = _parse_creature(base, card, oracle_text, name, role_features)
    elif "Instant" in types or "Sorcery" in types:
        result = _parse_spell(base, card, oracle_text, role_features)
    elif "Enchantment" in types:
        if "Saga" in subtypes:
            result = _parse_saga(base, card, oracle_text, role_features)
        elif "Class" in subtypes:
            result = _parse_class(base, card, oracle_text, role_features)
        elif "Aura" in subtypes:
            result = _parse_aura(base, card, oracle_text, role_features)
        else:
            result = _parse_other_permanent(base, card, oracle_text, role_features, "Enchantment")
    elif "Artifact" in types:
        if "Vehicle" in subtypes or "Equipment" in subtypes:
            result = _parse_artifact_typed(
                base,
                card,
                oracle_text,
                role_features,
                is_vehicle="Vehicle" in subtypes,
                is_equipment="Equipment" in subtypes,
            )
        else:
            result = _parse_other_permanent(base, card, oracle_text, role_features, "Artifact")
    elif "Planeswalker" in types:
        result = _parse_other_permanent(base, card, oracle_text, role_features, "Planeswalker")
    else:
        result = ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            role_features=role_features,
            reasons=[f"unsupported type line: {type_line!r}"],
            **base,
        )

    # MV >= 4 fast-path: promote NEEDS_LLM cards that can't realistically
    # affect mulligan-relevant turns 1-4 to AUTO.
    result = _maybe_apply_mv4_fast_path(card, result)
    # Universal catchall: anything with no role flag set lands on `is_other`.
    return _ensure_role_other_catchall(result)


def _initial_role_features(types: list[str], subtypes: list[str]) -> RoleFeatures:
    """Seed RoleFeatures with the type-derived flags.

    These are populated from the type line alone (no oracle-text parse
    needed): is_creature, is_planeswalker, is_equipment, is_vehicle. The
    parser's per-type branch later fills in role-specific fields
    (creates_creatures, combat_trick_*, etc.).
    """
    rf = RoleFeatures()
    if "Creature" in types:
        rf.is_creature = True
    if "Planeswalker" in types:
        rf.is_planeswalker = True
    if "Equipment" in subtypes:
        rf.is_equipment = True
    if "Vehicle" in subtypes:
        rf.is_vehicle = True
    if "Land" in types:
        rf.is_land = True
    return rf
