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

from .keywords import ALT_COST_KEYWORDS, EVERGREEN_KEYWORDS, SET_SPECIFIC_KEYWORDS
from .mana import ManaCost, parse_mana_cost
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
    r"^sacrifice\s+(?:this(?:\s+\w+)?|an?\s+(?P<type>creature|artifact|land|permanent))\.?$",
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
                # Map "permanent" → "any" since the sim doesn't distinguish.
                cost.sacrifice = SacrificeSpec(
                    target="any" if t == "permanent" else t  # type: ignore[arg-type]
                )
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

_DRAW_RE = re.compile(r"^Draw (\w+) cards?\.?$", re.IGNORECASE)
_SCRY_RE = re.compile(r"^Scry (\w+)\.?$", re.IGNORECASE)
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
_DESTROY_TARGET_RE = re.compile(
    r"^Destroy(?:\s+up to (?:one|two|three|\d+))?\s+target\s+"
    r"(?P<target>creature|nonland permanent|permanent|artifact|enchantment|"
    r"land|artifact or enchantment)\.?$",
    re.IGNORECASE,
)
_EXILE_TARGET_RE = re.compile(
    r"^Exile(?:\s+up to (?:one|two|three|\d+))?\s+target\s+"
    r"(?P<target>creature|nonland permanent|permanent|artifact|enchantment|"
    r"land|artifact or enchantment)\.?$",
    re.IGNORECASE,
)
_DAMAGE_CREATURE_RE = re.compile(
    r"^.+?\s+deals\s+(\d+)\s+damage to target creature\.?$", re.IGNORECASE
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
_BOUNCE_RE = re.compile(
    r"^Return\s+(?:up to (?:one|two|three|\d+)\s+)?target\s+"
    r"(?P<target>creature|nonland permanent|permanent)"
    r"\s+to (?:its owner's|your) hand\.?$",
    re.IGNORECASE,
)
# Tuck — put target creature / permanent on top of its owner's library.
_TOP_LIBRARY_RE = re.compile(
    r"^Put\s+target\s+(?P<target>creature|nonland permanent|permanent)"
    r"\s+on (?:top of|the top of)\s+(?:its owner's|your) library\.?$",
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
    "deathtouch", "double strike", "first strike", "flying", "haste",
    "hexproof", "indestructible", "lifelink", "menace", "reach",
    "trample", "vigilance",
)
_GRANT_KEYWORDS_RE = re.compile(
    r"gains?\s+("
    + r"(?:" + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS) + r")"
    + r"(?:[\s,]+(?:and\s+)?(?:" + "|".join(re.escape(k) for k in _GRANTABLE_KEYWORDS) + r"))*"
    + r").*?until end of turn",
    re.IGNORECASE | re.DOTALL,
)
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
# Static self-modifier on a creature: "This creature gets/has/'s power..."
# Lines matching this are considered "noise from the parser's perspective"
# — they don't change cast / castability / mana, so we ignore them rather
# than bail. Triggered abilities ("When this creature dies/enters") match
# _ETB_RE / _OTHER_TRIGGERED_RE first and are handled there.
_STATIC_SELF_MOD_RE = re.compile(
    r"^this creature(?:'s| has| gets|\s)",
    re.IGNORECASE,
)


def _extract_granted_keywords(text: str) -> list[str]:
    """Return any combat-trick-granted keywords found in ``text``."""
    if m := _GRANT_KEYWORDS_RE.search(text):
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
    for pattern, base_tag in ((_DESTROY_TARGET_RE, "removal_destroy"),
                              (_EXILE_TARGET_RE, "removal_exile")):
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

# "{T}: Add {<color>}." — possibly with multiple "or {…}" alternatives.
_TAP_FOR_RE = re.compile(
    r"^\{T\}:\s*Add\s+((?:\{[WUBRGC]\})(?:\s+or\s+\{[WUBRGC]\})*)\.?$",
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


def _extract_taps_for(chunk: str) -> list[str] | None:
    """If ``chunk`` is a plain "{T}: Add {…}" line, return the colors.

    Returns ``None`` if the line isn't a plain mana ability."""
    match = _TAP_FOR_RE.match(chunk)
    if not match:
        return None
    colors_part = match.group(1)
    return list(re.findall(r"\{([WUBRGC])\}", colors_part))


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
    """True if a chunk consists only of evergreen keyword names.

    Tolerates trailing cost on "ward" — "ward {1}" / "ward {2}{U}" still
    counts as a single keyword. Comma-separated lists work too:
    "flying, vigilance, ward {1}".
    """
    parts = [p.strip() for p in chunk.split(",")]
    if not parts:
        return False
    for raw in parts:
        # Strip trailing punctuation and an optional "{cost}" tail (for ward).
        stripped = raw.rstrip(".").strip()
        # Drop a trailing mana-cost run.
        cleaned = re.sub(r"\s*(?:\{[^{}]+\})+\s*$", "", stripped).strip()
        if cleaned.lower() not in EVERGREEN_KEYWORDS:
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
    * ``(Mode, None)`` if both cost and effect parse cleanly.
    * ``(None, str)`` if it looks like an activated ability but we can't
      classify the effect — caller flags NEEDS_LLM with the reason.
    * ``(None, None)`` if the line isn't activated-shaped at all.
    """
    m = _ACTIVATED_RE.match(line)
    if m is None:
        return None, None
    cost_str = m.group("cost").strip()
    effect_str = m.group("effect").strip()
    cost = _parse_cost_string(cost_str)
    if cost is None:
        return None, f"activated cost not recognised: {cost_str!r}"
    effects = _match_spell_effect(effect_str, rf=rf)
    if effects is None:
        return None, f"activated effect not recognised: {effect_str!r}"
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

    # Non-basic lands: walk every chunk. Recognised shapes:
    #   * the "<land> enters tapped (unless …)" sentence (skipped — already
    #     factored into enter_condition),
    #   * a "{T}: Add {…}" mana-ability line,
    #   * a non-mana activated ability ("{2}{U}, {T}: Draw a card, then
    #     discard a card.") parsed via _build_activated_mode and added to
    #     ``modes``. This lets utility lands like Agna Qel'a auto-classify.
    for chunk in chunks:
        if "enters tapped" in chunk.lower() or "enters the battlefield tapped" in chunk.lower():
            continue
        if colors := _extract_taps_for(chunk):
            mana_abilities.append(
                ManaAbility(
                    cost=Cost(mana=_empty_mana(), tap=True),
                    produces=[[c] for c in colors],  # type: ignore[list-item]
                )
            )
            continue
        # Non-mana activated ability.
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
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
        return ParsedCard(
            status=ParseStatus.NEEDS_LLM,
            modes=extra_modes,
            mana_abilities=[],
            enter_condition=enter_condition,
            role_features=role_features,
            reasons=["land doesn't have a recognised mana ability"],
            **base,
        )

    color_summary = "/".join(opt for ab in mana_abilities for opt_list in ab.produces for opt in opt_list)
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

    for chunk in chunks:
        # 1. Pure keyword line — already in `keywords`. Skip.
        if _is_pure_keyword_line(chunk):
            continue

        # 2. Static "<this creature> ..." self-modifier (variable P/T,
        # conditional pump). Doesn't affect cast / castability so we
        # ignore it rather than bail. Triggered abilities ("When this
        # creature dies/enters") match _ETB_RE / _OTHER_TRIGGERED_RE
        # before we get here, so this is safe.
        if _STATIC_SELF_MOD_RE.match(chunk):
            continue

        # 3. Cycling / land-cycling on the creature.
        if cyc := _build_cycling_mode(chunk):
            extra_modes.append(cyc)
            continue
        if ch := _build_channel_mode(chunk):
            extra_modes.append(ch)
            continue

        # 4. ETB on the creature itself.
        if m := _ETB_RE.match(chunk):
            if _is_self_etb(m.group("subject"), name):
                effects = _match_spell_effect(m.group("effect").strip(), rf=role_features)
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
                blockers.append(f"ETB effect not recognised: {m.group('effect').strip()!r}")
                continue
            # ETB-shaped but for some other permanent — ignored triggered ability.
            continue

        # 5. Other triggered abilities — ignored per design rules. We do
        # scan them for token creation, since cast-triggers / attack-
        # triggers that create tokens (e.g. Sokka) are worth recording
        # for role_features.
        if _OTHER_TRIGGERED_RE.match(chunk):
            for body in _match_token_creation(chunk):
                role_features.creates_creatures.append(body)
            continue

        # 6. Activated abilities. Try mana ability first (it's a special
        # shape: "{T}: Add {…}." on a creature is a mana dork).
        if mana_colors := _extract_taps_for(chunk):
            mana_abilities.append(
                ManaAbility(
                    cost=Cost(mana=_empty_mana(), tap=True),
                    produces=[[c] for c in mana_colors],  # type: ignore[list-item]
                )
            )
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

        # 7. Anything else — static ability, modal text, prose. Bail.
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
    for chunk in chunks:
        # Skip "As an additional cost to cast this spell, …" — recognised
        # but unmodelled. Card stays NEEDS_LLM (caller adds blocker
        # below); the rest of the text still parses for role_features.
        if chunk.lower().startswith("as an additional cost to cast this spell"):
            blockers.append(f"additional cost not modelled: {chunk!r}")
            continue
        # Skip chunks the fetch matcher already accounted for to avoid
        # double-flagging them as "unrecognised."
        if any(p.search(chunk) for p in (
            _FETCH_BATTLEFIELD_TAPPED_RE,
            _FETCH_BATTLEFIELD_UNTAPPED_RE,
            _FETCH_TO_HAND_RE,
        )):
            continue
        # Skip chunks that are purely a token-creation phrase.
        if _CREATE_TOKEN_RE.search(chunk) and not re.search(
            r"\bdraw\b|\bdestroy\b|\bexile\b|\bdeals\b|\bgain\b|\bscry\b", chunk, re.IGNORECASE
        ):
            continue

        effects = _match_spell_effect(chunk, rf=role_features)
        if effects is None:
            blockers.append(f"unrecognised line: {chunk!r}")
        else:
            spell_effects.extend(effects)

    cast_mode = _build_cast_mode(base.get("mana_cost"), spell_effects, is_permanent=False)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []

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
    for chunk in chunks:
        cl = chunk.lower()
        if cl.startswith("enchant ") or cl == "enchant creature":
            continue
        if cl.startswith("enchanted creature"):
            # Static aura effect — already classified above. Ignore.
            continue
        # Activated abilities on the aura (e.g. sac-self → exile target).
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
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
    for chunk in chunks:
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
            continue
        # Activated abilities on artifact.
        act, blocker = _build_activated_mode(chunk, rf=role_features)
        if act is not None:
            extra_modes.append(act)
            continue
        if blocker is not None:
            blockers.append(blocker)
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

    For v1 we always emit a cast Mode (with EntersBattlefieldEffect), set
    the appropriate role_features flags from subtypes, and partially parse
    oracle text for token-creation. The rest goes to NEEDS_LLM unless the
    text is empty (vanilla permanent — e.g. a wall of stats with no
    abilities, rare but legal)."""
    cleaned = _strip_reminder(oracle_text)
    chunks = _split_chunks(cleaned)

    cast_mode = _build_cast_mode(base.get("mana_cost"), [], is_permanent=True)
    modes: list[Mode] = [cast_mode] if cast_mode is not None else []

    # Token creation can appear on any permanent.
    bodies = _match_token_creation(cleaned)
    if bodies:
        role_features.creates_creatures.extend(bodies)

    if not chunks:
        # Vanilla permanent — accept.
        return ParsedCard(
            status=ParseStatus.AUTO,
            modes=modes,
            role_features=role_features,
            reasons=[f"vanilla {type_word.lower()}"],
            **base,
        )

    # Anything with text needs the LLM until we extend the parser.
    return ParsedCard(
        status=ParseStatus.NEEDS_LLM,
        modes=modes,
        role_features=role_features,
        reasons=[f"{type_word} oracle text not deterministically parsed yet"],
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
# Top-level dispatch.
# ---------------------------------------------------------------------------


def parse_card(card: dict[str, Any]) -> ParsedCard:
    """Parse a single Scryfall card dictionary into a ``ParsedCard``.

    The input must be a dict shaped like a row from Scryfall's bulk
    ``oracle_cards`` JSON. Any input we don't fully understand returns a
    ``ParsedCard`` with ``status=NEEDS_LLM`` and reasons explaining why —
    never an exception.
    """
    name = str(card.get("name", "<unknown>"))
    type_line = str(card.get("type_line", ""))
    oracle_text = str(card.get("oracle_text") or "")
    layout = str(card.get("layout", "normal"))

    supertypes, types, subtypes = _parse_type_line(type_line)
    raw_keywords = [str(k).lower() for k in card.get("keywords") or []]
    evergreens = [k for k in raw_keywords if k in EVERGREEN_KEYWORDS]

    # Build the base dict fed to ParsedCard's constructor.
    base: dict[str, Any] = {
        "name": name,
        "set_code": str(card.get("set", "")).upper(),
        "collector_number": str(card.get("collector_number", "")),
        "oracle_id": str(card.get("oracle_id", "")),
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

    if layout != "normal":
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
        return _parse_land(base, card, oracle_text, role_features)
    if "Creature" in types:
        return _parse_creature(base, card, oracle_text, name, role_features)
    if "Instant" in types or "Sorcery" in types:
        return _parse_spell(base, card, oracle_text, role_features)
    if "Enchantment" in types:
        if "Aura" in subtypes:
            return _parse_aura(base, card, oracle_text, role_features)
        return _parse_other_permanent(base, card, oracle_text, role_features, "Enchantment")
    if "Artifact" in types:
        if "Vehicle" in subtypes or "Equipment" in subtypes:
            return _parse_artifact_typed(
                base, card, oracle_text, role_features,
                is_vehicle="Vehicle" in subtypes,
                is_equipment="Equipment" in subtypes,
            )
        return _parse_other_permanent(base, card, oracle_text, role_features, "Artifact")
    if "Planeswalker" in types:
        return _parse_other_permanent(base, card, oracle_text, role_features, "Planeswalker")

    return ParsedCard(
        status=ParseStatus.NEEDS_LLM,
        role_features=role_features,
        reasons=[f"unsupported type line: {type_line!r}"],
        **base,
    )


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
    return rf
