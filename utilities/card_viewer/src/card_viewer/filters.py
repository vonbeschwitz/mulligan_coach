"""Search/filter logic for the card viewer.

The :class:`Filter` model is bound from the HTMX form's query string by
FastAPI; :func:`apply_filter` walks the in-memory store and applies each
selected constraint. Multiple values within a single filter group (e.g.
two role flags) combine via AND — ticking ``is_equipment`` and
``is_vehicle`` shows artifact-creature vehicles that are also equipment.
That matches the "narrow down" mental model the verification workflow
needs; if you want OR semantics you use multiple successive searches.

Result limit: 200 cards. With ~740 cards loaded by default that's plenty
of headroom for any realistic filter combination, and the page stays fast
even before the browser starts lazy-loading images.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from mulligan_coach_cards import ParsedCard, RoleFeatures
from pydantic import BaseModel, Field

from .data import CardEntry, CardStore

# Hard cap so the results grid doesn't fetch hundreds of images at once.
RESULT_LIMIT = 200

# Keys used in the HTML form. Kept as plain strings so the template can
# iterate over them without importing the module.
ROLE_FLAG_KEYS: list[str] = [
    "is_creature",
    "is_planeswalker",
    "is_equipment",
    "is_vehicle",
    "is_punch_fight",
    "is_pump_aura",
    "is_removal_aura",
    "is_bounce",
    "is_top_library",
    "removal_destroy_or_exile",
    "removal_burn_damage_gt0",
    "cards_drawn_gt0",
    "cards_manipulated_gt0",
    "creates_creatures",
]

# Each role-flag key is satisfied by a one-line predicate over RoleFeatures.
# Splitting the bool flags from the numeric/list ones keeps each lambda a
# trivial accessor — easier to eyeball for correctness than a single big
# match statement.
ROLE_FLAG_PREDICATES: dict[str, Callable[[RoleFeatures], bool]] = {
    "is_creature": lambda r: r.is_creature,
    "is_planeswalker": lambda r: r.is_planeswalker,
    "is_equipment": lambda r: r.is_equipment,
    "is_vehicle": lambda r: r.is_vehicle,
    "is_punch_fight": lambda r: r.is_punch_fight,
    "is_pump_aura": lambda r: r.is_pump_aura,
    "is_removal_aura": lambda r: r.is_removal_aura,
    "is_bounce": lambda r: r.is_bounce,
    "is_top_library": lambda r: r.is_top_library,
    "removal_destroy_or_exile": lambda r: r.removal_destroy_or_exile,
    "removal_burn_damage_gt0": lambda r: (r.removal_burn_damage or 0) > 0,
    "cards_drawn_gt0": lambda r: r.cards_drawn > 0,
    "cards_manipulated_gt0": lambda r: r.cards_manipulated > 0,
    "creates_creatures": lambda r: bool(r.creates_creatures),
}

# Primary types we expose in the dropdown. Lands and Planeswalker are
# included even though they're rarer in Limited — verification still needs
# to be able to single them out.
PRIMARY_TYPES: list[str] = [
    "Creature",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Land",
    "Planeswalker",
    "Battle",
]

MODE_KINDS: list[str] = ["cast", "cycle", "land_cycle", "channel", "activated"]

# 'C' is our shorthand for "colorless" (the card has no colors). It isn't a
# Scryfall color symbol; we translate it to "no entries in colors" below.
ColorChoice = Literal["W", "U", "B", "R", "G", "C"]


class Filter(BaseModel):
    """All search/filter inputs from the index form.

    Defaults are "no constraint" so an empty form (the page-load HTMX
    trigger) returns everything in the store.
    """

    q: str = Field(default="", description="Case-insensitive substring of the card name.")
    status: Literal["", "auto", "needs_llm"] = ""
    sets: list[str] = Field(
        default_factory=list,
        description="Restrict to these set codes. Empty = all loaded sets.",
    )
    colors: list[ColorChoice] = Field(
        default_factory=list,
        description=(
            "WUBRG colors that must all be present on the card. 'C' matches "
            "colorless cards (cards with empty `colors`)."
        ),
    )
    primary_type: str = ""
    role_flags: list[str] = Field(default_factory=list)
    mode_kinds: list[str] = Field(default_factory=list)
    has_mana_abilities: Literal["", "yes", "no"] = ""


def _color_match(parsed: ParsedCard, wanted: set[ColorChoice]) -> bool:
    """True iff the card satisfies the WUBRG/colorless constraint.

    Treating 'C' as a real choice (rather than its own filter) keeps the
    UI single-checkbox-row clean. The semantics: if 'C' is selected, the
    card must be colorless; remaining WUBRG selections must all appear on
    the card. Selecting both 'C' and any color is a contradiction (returns
    no results), which is fine — the user will notice and correct.
    """
    card_colors: set[str] = {c for c in parsed.colors}
    needs_colorless = "C" in wanted
    needed_colors = wanted - {"C"}

    if needs_colorless and card_colors:
        return False
    if not needed_colors:
        return True
    return needed_colors <= card_colors


def apply_filter(store: CardStore, f: Filter) -> tuple[list[CardEntry], int]:
    """Apply a Filter to the store.

    Returns ``(top-N entries sorted by name, total matched count)`` so the
    template can show "showing 200 of 537". The total is computed BEFORE
    truncation, on purpose.
    """
    out: list[CardEntry] = list(store.entries)

    if f.q:
        needle = f.q.casefold()
        out = [e for e in out if needle in e.parsed.name.casefold()]

    if f.status:
        out = [e for e in out if e.parsed.status.value == f.status]

    if f.sets:
        wanted_sets = {s.upper() for s in f.sets}
        out = [e for e in out if e.parsed.set_code.upper() in wanted_sets]

    if f.colors:
        wanted_colors: set[ColorChoice] = set(f.colors)
        out = [e for e in out if _color_match(e.parsed, wanted_colors)]

    if f.primary_type:
        out = [e for e in out if f.primary_type in e.parsed.types]

    for flag in f.role_flags:
        pred = ROLE_FLAG_PREDICATES.get(flag)
        if pred is None:
            # Silently skip unknown flags rather than 400 — keeps the UI
            # forgiving if the form layout drifts ahead of this list.
            continue
        out = [e for e in out if pred(e.parsed.role_features)]

    if f.mode_kinds:
        wanted_kinds = set(f.mode_kinds)
        out = [e for e in out if any(m.kind in wanted_kinds for m in e.parsed.modes)]

    if f.has_mana_abilities == "yes":
        out = [e for e in out if e.parsed.mana_abilities]
    elif f.has_mana_abilities == "no":
        out = [e for e in out if not e.parsed.mana_abilities]

    total = len(out)
    out.sort(key=lambda e: e.parsed.name.casefold())
    return out[:RESULT_LIMIT], total


# ---------------------------------------------------------------------------
# Template helpers — exposed via the FastAPI template context so Jinja2 can
# call them inline. Kept here (rather than in app.py) so the predicate map
# is the single source of truth for "what counts as an active flag".
# ---------------------------------------------------------------------------


def active_role_flags(parsed: ParsedCard) -> list[str]:
    """Return every role-flag key whose predicate is true for this card.

    Iterates `ROLE_FLAG_KEYS` in declaration order so the chip layout in
    the UI stays stable across cards.
    """
    rf: RoleFeatures = parsed.role_features
    return [k for k in ROLE_FLAG_KEYS if ROLE_FLAG_PREDICATES[k](rf)]


def active_mode_kinds(parsed: ParsedCard) -> list[str]:
    """Distinct mode kinds present on the card, in encounter order.

    Most cards have a single 'cast' mode plus zero-or-one extras (cycling,
    activated). Deduplicating means we don't render two 'activated' chips
    just because a card has two activated abilities.
    """
    seen: list[str] = []
    for mode in parsed.modes:
        if mode.kind not in seen:
            seen.append(mode.kind)
    return seen


def display_role_flag(flag: str) -> str:
    """Trim the verbose suffix off chip labels for visual density.

    `is_creature` -> `creature`, `removal_burn_damage_gt0` -> `burn`,
    `cards_drawn_gt0` -> `card draw`, etc. Kept narrow and explicit
    rather than generic so future renames are obvious.
    """
    pretty = {
        "removal_burn_damage_gt0": "burn",
        "cards_drawn_gt0": "card draw",
        "cards_manipulated_gt0": "scry/loot",
        "creates_creatures": "creates tokens",
        "removal_destroy_or_exile": "removal",
    }
    if flag in pretty:
        return pretty[flag]
    if flag.startswith("is_"):
        return flag[len("is_") :].replace("_", " ")
    return flag.replace("_", " ")
