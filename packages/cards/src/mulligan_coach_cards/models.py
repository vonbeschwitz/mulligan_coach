"""Pydantic models for parsed cards.

The cards package serves two downstream consumers:

* The **simulator** (``packages/simulation``) needs a structural view of
  each card's mana abilities, casting modes, costs (mana + tap + sacrifice
  + discard-self), effects (produce mana, fetch land, draw cards, scry,
  enters-battlefield, noop), and ETB-tapped predicates on lands. It
  evaluates castability by walking these structures.
* The **XGBoost feature stage** (``packages/model``, downstream) needs a
  flat per-card categorization: is this removal? a combat trick? does it
  create creature tokens? how many cards does it draw or scry through?
  The simulator ignores all of this; the model ignores the simulator-side
  shape and reads ``role_features`` plus 17Lands stats.

We split these concerns into two sub-models on ``ParsedCard``:

* ``modes`` + ``mana_abilities`` + ``enter_condition`` → simulator side.
* ``role_features`` → XGBoost side.

The parser (deterministic + LLM fallback) populates both from a single
oracle-text pass; many ``role_features`` fields are derivable from
simulator-side effects (e.g. a ``DrawCardsEffect(n=2)`` implies
``cards_drawn=2``), but having them broken out as their own field keeps
downstream code simple and explicit.

Four ``ParseStatus`` values matter:

* ``ParseStatus.AUTO`` — the deterministic parser fully understood the
  card. ``modes``, ``mana_abilities``, ``role_features`` etc. are
  populated and trustworthy.
* ``ParseStatus.NEEDS_LLM`` — the parser couldn't fully classify the
  card. ``reasons`` lists what blocked it; an LLM should fill in the
  structured fields. Partial fills are fine — the deterministic parser
  populates whatever it could before giving up.
* ``ParseStatus.LLM_ENCODED`` — an LLM (Claude) reviewed the card and
  hand-encoded the structured fields. Treated as authoritative by
  downstream consumers; the deterministic parser preserves these on
  re-runs (won't overwrite).
* ``ParseStatus.NEEDS_HUMAN`` — Claude reviewed the card but was
  uncertain enough that human judgement is required. Downstream code
  should treat the structured fields as best-effort. The deterministic
  parser also preserves these on re-runs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .mana import ManaCost

# ---------------------------------------------------------------------------
# Shared scalar / enum types.
# ---------------------------------------------------------------------------

# WUBRG colors as a Literal so JSON round-trips work and IDEs autocomplete
# correctly. Strings rather than an Enum to avoid serialization friction.
Color = Literal["W", "U", "B", "R", "G"]

# A "mana option" — what a single mana ability can produce per use. Lists
# of ManaOption appear inside ProduceManaEffect.options and
# ManaAbility.produces, where the outer list is OR (pick one) and the
# inner list is AND (all of these are produced together).
#
# 'C' = colorless mana. 'any' = any color (for filter lands and similar).
ManaOption = Literal["W", "U", "B", "R", "G", "C", "any"]


class ParseStatus(StrEnum):
    """Whether the deterministic parser fully classified the card.

    Cards flow through these states in roughly this order:
    1. ``NEEDS_LLM`` — the parser bailed; awaiting review.
    2. ``LLM_ENCODED`` or ``NEEDS_HUMAN`` — Claude reviewed it and either
       hand-encoded the fields or marked it for human attention.
    3. ``AUTO`` — the parser was extended to handle the card and now
       classifies it deterministically.
    """

    AUTO = "auto"
    NEEDS_LLM = "needs_llm"
    LLM_ENCODED = "llm_encoded"
    NEEDS_HUMAN = "needs_human"


# ---------------------------------------------------------------------------
# Predicates — a small closed enum, NOT a generic expression language.
#
# Used by:
# * ``ParsedCard.enter_condition`` — "this land enters tapped iff <pred>".
# * ``ManaAbility.condition`` — "this ability can be activated iff <pred>".
# * ``ProduceManaEffect.condition`` — same idea on triggered/spell-side
#   mana production.
#
# The simulator evaluates predicates against its game state. New patterns
# are added one at a time as new sets surface them — keeping this small
# means the simulator's evaluator stays a one-page switch statement.
# ---------------------------------------------------------------------------

PredicateKind = Literal[
    "always",  # always true; the default when no condition is set
    "controls_lands_ge",  # number of lands you control >= n
    "controls_lands_lt",  # number of lands you control <  n  (Deathcap pattern)
    "controls_basic",  # you control at least one basic of the named type
    "controls_basic_any",  # you control at least one basic land of any type
    "controls_color",  # you control at least one source of the named color
]


class Predicate(BaseModel):
    """Closed-enum predicate used by the simulator to evaluate conditions."""

    kind: PredicateKind
    n: int | None = Field(default=None, description="Threshold for *_ge / *_lt kinds.")
    color: Color | None = Field(default=None, description="Color for controls_color.")
    basic_type: str | None = Field(
        default=None,
        description="Basic land type for controls_basic — 'Plains' / 'Island' / 'Swamp' / 'Mountain' / 'Forest'.",
    )


# ---------------------------------------------------------------------------
# Costs — the complete cost of a Mode or ManaAbility.
#
# Mana lives in ``mana: ManaCost`` (which itself can be empty, e.g. for a
# {T}-only mana ability). Non-mana components are flags / sub-models on
# the same Cost. Per the v1 design decision, we model:
#
# * tap                — {T}
# * untap              — {Q}  (rare but cheap to support)
# * sacrifice          — Sacrifice this <type> or any type
# * discard_self       — discard this card (cycling mode)
#
# Pay-N-life is deferred to v2; it rarely affects Limited castability.
# ---------------------------------------------------------------------------


class SacrificeSpec(BaseModel):
    """Description of a sacrifice cost component.

    ``target`` describes WHAT must be sacrificed. Most activated abilities
    sacrifice the card itself ("self"); some sacrifice creatures or
    artifacts more generally.
    """

    target: Literal["self", "creature", "artifact", "land", "any"]


class Cost(BaseModel):
    """The full cost of a Mode or ManaAbility.

    All fields default to "no cost contribution" — a default-constructed
    ``Cost()`` represents the free / impossible-to-pay-without-something cost.
    The mana sub-cost is a real ``ManaCost`` (which itself can be empty
    for things like {T}-only abilities).
    """

    mana: ManaCost = Field(
        default_factory=lambda: ManaCost(raw="", pips=[], cmc=0, color_pips={}),
        description="Mana component of the cost. Empty ManaCost means no mana required.",
    )
    tap: bool = Field(default=False, description="Includes the {T} symbol in the cost.")
    untap: bool = Field(default=False, description="Includes the {Q} symbol in the cost.")
    sacrifice: SacrificeSpec | None = Field(
        default=None,
        description="Non-None if the cost includes a sacrifice component.",
    )
    discard_self: bool = Field(
        default=False,
        description="True if paying the cost includes discarding this card (the cycling pattern).",
    )


# ---------------------------------------------------------------------------
# Effects — what happens on resolution. Discriminated union over ``kind``.
#
# The simulator cares about: produce_mana, fetch_land, draw_cards, scry,
# enters_battlefield. Everything else (removal, damage, lifegain, combat
# tricks, token creation, …) is ``noop`` from the simulator's perspective —
# it doesn't change game state in a way that affects mana / castability /
# cards-in-hand / lands-in-play.
#
# The XGBoost side reads ``role_features`` rather than walking ``effects``,
# so we don't need to subclass NoopEffect for every category — a single
# ``role_tag`` string is enough breadcrumb for debugging.
# ---------------------------------------------------------------------------


class ProduceManaEffect(BaseModel):
    """A spell or triggered ability that produces mana on resolution.

    Distinct from ``ManaAbility`` (which lives on permanents and is paid
    with structured Costs): this is the spell-side / one-shot version,
    e.g. an instant that says "Add {G}{G}". Rarer than activated mana
    abilities but worth modeling.

    ``options`` is shaped like ``ManaAbility.produces``: outer list = OR
    (the controller picks one), inner list = AND (all members produced
    together). For "Add {G}{G}." this is ``[["G", "G"]]``.
    """

    kind: Literal["produce_mana"] = "produce_mana"
    options: list[list[ManaOption]]
    condition: Predicate | None = None


class FetchLandEffect(BaseModel):
    """Search-your-library-for-a-land effect.

    Three independent axes:

    * ``target_filter`` — what kinds of lands match.
        * ``"basic"`` — any basic land (Cultivate, Evolving Wilds).
        * ``"any"`` — any land at all (rare; mostly fixed-format effects).
        * ``"specific_subtype"`` — a named subtype (e.g. ``subtype="Forest"``
          for Three Visits; ``subtype="Plains"`` for Plainscycling).
    * ``destination`` — where the fetched land ends up.
        * ``"battlefield_untapped"`` — Three Visits (best case).
        * ``"battlefield_tapped"`` — Cultivate's putting-onto-battlefield half,
          Evolving Wilds (a sac-fetch produces a tapped land).
        * ``"hand"`` — Environmental Scientist, Strixhaven Skycoach,
          land-cycling (the discard-and-fetch mode goes to hand).
    * ``count`` — how many lands. Default 1; Cultivate is 1 to battlefield
      tapped + 1 to hand (modelled as two FetchLandEffect entries on the
      same Mode).

    The TRIGGER axis (ETB / cast / activated / sac) lives on the enclosing
    Mode's ``kind`` rather than here — same effect, different timing.
    """

    kind: Literal["fetch_land"] = "fetch_land"
    target_filter: Literal["basic", "any", "specific_subtype"]
    subtype: str | None = Field(
        default=None,
        description="Named subtype when target_filter == 'specific_subtype'. 'Forest', 'Plains', etc.",
    )
    destination: Literal["battlefield_untapped", "battlefield_tapped", "hand"]
    count: int = Field(default=1, ge=1, description="Number of lands fetched by this effect.")


class DrawCardsEffect(BaseModel):
    """Draw N cards. Cycling and looting are modelled as separate Modes
    whose effects include this one (cycling: discard-self + draw 1)."""

    kind: Literal["draw_cards"] = "draw_cards"
    n: int = Field(ge=1)


class ScryEffect(BaseModel):
    """Scry N — look at the top N cards and reorder / bottom them."""

    kind: Literal["scry"] = "scry"
    n: int = Field(ge=1)


class EntersBattlefieldEffect(BaseModel):
    """The card itself enters the battlefield as a permanent.

    Set on the cast Mode of permanents (creatures, artifacts, enchantments,
    lands, planeswalkers). The simulator uses this to know "after I cast
    this card, it's on the battlefield" — e.g. a mana dork enters and is
    available for mana on subsequent turns.
    """

    kind: Literal["enters_battlefield"] = "enters_battlefield"


class NoopEffect(BaseModel):
    """Anything the simulator doesn't care about — removal, damage, life
    gain, combat tricks, modal "choose one — …" outcomes, etc.

    ``role_tag`` is an informal breadcrumb for debugging and for cases
    where downstream code wants a quick categorization without looking at
    ``role_features``. The XGBoost feature stage should prefer
    ``role_features``; ``role_tag`` is for parser logs and ad-hoc inspection.
    """

    kind: Literal["noop"] = "noop"
    role_tag: str | None = None


# Discriminated union over ``kind``. Pydantic 2 picks the right concrete
# class on parse based on the discriminator string.
Effect = Annotated[
    ProduceManaEffect
    | FetchLandEffect
    | DrawCardsEffect
    | ScryEffect
    | EntersBattlefieldEffect
    | NoopEffect,
    Field(discriminator="kind"),
]


# ---------------------------------------------------------------------------
# Modes and ManaAbility.
# ---------------------------------------------------------------------------


class Mode(BaseModel):
    """One legal way to play / activate the card.

    Most cards have a single ``kind="cast"`` mode (their normal cast).
    Cards with cycling, channel, land-cycling, or activated abilities
    add additional modes. The simulator's castability check evaluates
    each mode independently — a card may be "castable as a cycle" but
    not "castable as a cast" on a given turn.
    """

    kind: Literal["cast", "cycle", "land_cycle", "channel", "activated"]
    cost: Cost
    effects: list[Effect] = Field(
        default_factory=list,
        description=(
            "Effects in order of appearance. The cast Mode of a permanent "
            "starts with EntersBattlefieldEffect and may include ETB-trigger "
            "effects after it."
        ),
    )


class ManaAbility(BaseModel):
    """Permanent-resident mana ability. Lives on lands, mana dorks (creatures
    that tap for mana), mana rocks (non-creature artifacts), and filter
    lands ({1} cost to produce any color).

    ``produces`` mirrors ``ProduceManaEffect.options``:
    * Outer list = OR — the controller picks one inner option per activation.
    * Inner list = AND — all members produced together.

    Examples:
    * Basic Forest: ``cost=Cost(tap=True)``, ``produces=[["G"]]``.
    * Boros Guildgate: ``cost=Cost(tap=True)``, ``produces=[["R"], ["W"]]``.
    * Filter land "{1}, {T}: Add any color": ``cost=Cost(tap=True, mana=ManaCost("{1}"))``,
      ``produces=[["any"]]``.
    """

    cost: Cost
    produces: list[list[ManaOption]]
    condition: Predicate | None = None


# ---------------------------------------------------------------------------
# XGBoost feature side: role categorization.
# ---------------------------------------------------------------------------


class CreatureBody(BaseModel):
    """Mechanical body of a creature.

    Used both for the parent card itself (when it's a creature) and for
    creature tokens the card creates / brings along (Equipment with a
    stapled body, Vehicle with a pilot token, sorceries that make tokens).
    """

    power: str = Field(
        description="Raw power string ('2', '*', '1+*', etc.). Strings to preserve oddities."
    )
    toughness: str
    colors: list[Color] = Field(default_factory=list)
    subtypes: list[str] = Field(
        default_factory=list,
        description="Creature subtypes, e.g. ['Human', 'Soldier'] or ['Ally'] for tokens.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Lower-cased keyword abilities ('flying', 'lifelink', …).",
    )


class RoleFeatures(BaseModel):
    """Per-card categorization for the XGBoost feature stage.

    A card may set multiple flags — an Equipment that ships with a
    creature token sets ``is_equipment=True`` AND populates
    ``creates_creatures``. The categories are not mutually exclusive.

    The deterministic parser populates as many of these as it can; the
    LLM classifier fills in the rest. ``is_other`` is set iff none of
    the other flags / sub-fields are populated — i.e. the card doesn't
    fit any of categories 1-10 from the design.
    """

    # Primary type / role flags ------------------------------------------------
    is_creature: bool = Field(
        default=False, description="The card itself is a creature (or artifact creature, etc.)."
    )
    is_planeswalker: bool = False
    is_equipment: bool = False
    is_vehicle: bool = False
    is_saga: bool = Field(
        default=False,
        description=(
            "The card is an Enchantment — Saga (or transform-DFC whose front face is a Saga). "
            "Set whenever the parser saw a Saga; we only encode chapter I deterministically."
        ),
    )
    is_class: bool = Field(
        default=False,
        description=(
            "The card is an Enchantment — Class. Set whenever the parser saw a Class; we only "
            "encode the always-on level-1 effect deterministically."
        ),
    )
    is_punch_fight: bool = Field(
        default=False,
        description="Punch (one-sided fight) and fight spells, grouped per design decision.",
    )
    is_other: bool = Field(
        default=False,
        description="Catchall flag — set iff no other category applies.",
    )

    # Token creation -----------------------------------------------------------
    creates_creatures: list[CreatureBody] = Field(
        default_factory=list,
        description=(
            "Creature tokens this card brings into play, from any source: "
            "ETB, cast trigger, equipment-with-body, vehicle-with-pilot, etc. "
            "One entry per distinct token (count via the parent card's number "
            "of activations / triggers — most cards just create one token, once)."
        ),
    )

    # Removal ------------------------------------------------------------------
    removal_destroy_or_exile: bool = Field(
        default=False,
        description="Creature-targeted destroy or exile (grouped together per design decision).",
    )
    removal_burn_damage: int | None = Field(
        default=None,
        description=(
            "Set iff the card deals direct damage to creatures (Lightning Bolt-style). "
            "Records the damage amount. None for non-burn or face-only burn."
        ),
    )

    # Soft removal -------------------------------------------------------------
    is_bounce: bool = Field(
        default=False,
        description=(
            "Returns target creature / nonland permanent / permanent to its "
            "owner's hand. Independent from removal_destroy_or_exile — a card "
            "can theoretically be both."
        ),
    )
    is_top_library: bool = Field(
        default=False,
        description=(
            "Puts target creature / permanent on top of its owner's library "
            "(tuck effects). Softer than bounce because the owner re-draws it."
        ),
    )

    # Combat trick (instants only) --------------------------------------------
    combat_trick_power: int | None = None
    combat_trick_toughness: int | None = None
    combat_trick_granted_keywords: list[str] = Field(default_factory=list)

    # Aura specifics — exactly one of removal_aura / pump_aura is true on Auras.
    is_removal_aura: bool = False
    is_pump_aura: bool = False
    aura_pump_power: int | None = None
    aura_pump_toughness: int | None = None
    aura_pump_granted_keywords: list[str] = Field(default_factory=list)

    # Card draw / filter -------------------------------------------------------
    cards_drawn: int = Field(
        default=0,
        ge=0,
        description="Net new cards added to hand (Divination=2, cycling-cantrip=1 net since you discard one).",
    )
    cards_manipulated: int = Field(
        default=0,
        ge=0,
        description="Cards seen but not necessarily drawn — scry / loot / surveil count.",
    )


# ---------------------------------------------------------------------------
# Top-level ParsedCard.
# ---------------------------------------------------------------------------


class ParsedCard(BaseModel):
    """The parser's output for a single card.

    Identifying fields and the type system are always populated. The
    simulator-side fields (``modes``, ``mana_abilities``, ``enter_condition``)
    and the XGBoost-side field (``role_features``) are populated by the
    parser branch that handled the card's type. Cards flagged
    ``status=NEEDS_LLM`` may have partial fills — whatever the parser
    could extract before bailing.
    """

    # Identity ---------------------------------------------------------------
    name: str
    set_code: str
    collector_number: str
    oracle_id: str
    arena_id: int | None = Field(
        default=None,
        description=(
            "MTGA card ID. The stable join key for 17Lands stats and Arena log "
            "events. Populated from MTGJSON's AllIdentifiers.json at parse time; "
            "None when MTGJSON hasn't yet ingested the printing (typical for "
            "very new sets — fall back to name-based matching in that case)."
        ),
    )
    rarity: str
    raw_oracle_text: str = Field(
        description="The original oracle text, preserved for the LLM and for human review."
    )

    # Type system ------------------------------------------------------------
    type_line: str = Field(
        description="Full Scryfall type line, preserved as-is for display / debugging."
    )
    types: list[str] = Field(
        default_factory=list,
        description="Card types, e.g. ['Creature'], ['Artifact','Creature'], ['Land'], ['Sorcery'].",
    )
    subtypes: list[str] = Field(
        default_factory=list,
        description="Card subtypes, e.g. ['Human','Druid'], ['Aura'], ['Vehicle'].",
    )
    supertypes: list[str] = Field(
        default_factory=list,
        description="Supertypes: 'Basic', 'Legendary', 'Snow', 'World'.",
    )

    # Cost / colors ----------------------------------------------------------
    mana_cost: ManaCost | None = Field(
        default=None,
        description="Parsed cast cost. ``None`` for cards with no mana cost (lands, etc.).",
    )
    colors: list[Color] = Field(
        default_factory=list,
        description="Card's colors per Scryfall, in WUBRG order. Empty for colorless / lands.",
    )

    # Creature stats (only meaningful when 'Creature' in types) -------------
    power: str | None = None
    toughness: str | None = None
    evergreen_keywords: list[str] = Field(
        default_factory=list,
        description="Lower-cased evergreen keywords detected on the card.",
    )

    # Simulator side: modes + permanent mana abilities + ETB-tapped condition.
    modes: list[Mode] = Field(
        default_factory=list,
        description=(
            "All castable / activatable modes. The cast mode (if any) is first; "
            "alternative-cost modes (cycling, channel, etc.) and activated "
            "abilities follow."
        ),
    )
    mana_abilities: list[ManaAbility] = Field(
        default_factory=list,
        description=(
            "Permanent-resident mana abilities. Populated for lands, mana dorks, "
            "mana rocks, and filter lands. Empty for everything else."
        ),
    )
    enter_condition: Predicate | None = Field(
        default=None,
        description=(
            "Land-only. ``None`` means no ETB-tapped clause (the land enters "
            "untapped). A predicate means the land enters tapped iff the "
            "predicate is true at ETB time. Use Predicate(kind='always') for "
            "lands that always enter tapped."
        ),
    )

    # XGBoost side: role categorization.
    role_features: RoleFeatures = Field(
        default_factory=RoleFeatures,
        description="Per-card categorization for the XGBoost feature engineering stage.",
    )

    # Outcome ---------------------------------------------------------------
    status: ParseStatus
    reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Free-text bullet points: what the parser recognised AND/OR what "
            "blocked auto-classification. Always populated to make review easy."
        ),
    )
