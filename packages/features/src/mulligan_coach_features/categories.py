"""Card-classification predicates for the XGBoost feature stage.

The feature builder counts hand and deck cards across many overlapping
categories (creatures, removal, ramp, pump, multi-modal, etc.) and across
mana-value buckets. This module collects the predicate definitions so
the builder can stay focused on the bookkeeping.

A few categories are not first-class flags on :class:`RoleFeatures` and
need an adapter:

* **Ramp** (``is_ramp``) — mana dork, mana rock, or any mode that fetches
  a land into play. Built from ``mana_abilities`` plus
  :class:`FetchLandEffect` destinations.
* **Card manipulation** (``is_card_manipulation``) — anything that draws
  or filters cards. ``cards_drawn > 0`` or ``cards_manipulated > 0`` on
  the role features.
* **Has alternative mode** (``has_alt_mode``) — ``len(card.modes) > 1``.
  Catches cycling / land-cycling / channel / evoke / flashback / madness
  / jump-start / aftermath as those land as extra modes on the card.
  Adventure / MDFC / Split / modal "choose one" are NOT counted today
  (parser limitation documented in CLAUDE.md).
* **Removal (broad)** (``is_removal``) — union of destroy/exile, burn,
  bounce, top-of-library, removal aura, and counterspell. Matches the
  spec's removal definition for both deck/hand counts and the per-turn
  castability bucket.
* **Pump (broad)** (``is_pump_broad``) — combat trick or pump aura.
  Both require a creature in play; grouped together for the per-turn
  castability "pump" bucket.

Two creature predicates exist:

* :func:`is_creature_strict` — the card's printed type includes Creature.
  Used by the hand- and deck-level counts where "creature" means an
  actual creature card.
* :func:`is_creature_for_castability` — strict, OR the card creates
  creature tokens on resolution (token-makers). Used by the per-turn
  castability "creature" bucket, per the spec note that "creature ...
  includes anything with a creature on cast — token-makers, etc.".

Mana-value buckets used downstream:

* ``mv_le_2`` — CMC ≤ 2.
* ``mv_eq_3`` — CMC == 3.
* ``mv_4_5`` — 4 ≤ CMC ≤ 5.
* ``mv_ge_6`` — CMC ≥ 6.

Plus the role-by-MV section uses a coarser split:

* ``mv_0_2`` — CMC 0..2.
* ``mv_eq_3`` — CMC == 3.
* ``mv_4_5`` — CMC 4..5.

(no ``mv_ge_6`` bucket in role-by-MV per the spec).

And the turn-3/turn-4 castability sections introduce open-ended buckets:

* ``mv_3_plus`` — CMC ≥ 3.
* ``mv_4_plus`` — CMC ≥ 4.

X-cost cards (``mana_cost.has_x``) keep the printed CMC the mana parser
reports. The simulator treats X = 1 for castability; for static feature
bucketing the printed CMC is the more honest answer (an X spell with
``{X}{R}`` printed CMC 1 lives in mv_le_2 bucket).
"""

from __future__ import annotations

from mulligan_coach_cards import FetchLandEffect, ParsedCard

# ---------------------------------------------------------------------------
# Card type tests
# ---------------------------------------------------------------------------


def is_land(card: ParsedCard) -> bool:
    """True iff the card's printed type includes Land.

    Mirrors ``role_features.is_land`` but reads the type line directly
    so the predicate works on LLM_ENCODED cards where role_features may
    have been partially set.
    """
    return "Land" in card.types


def is_spell(card: ParsedCard) -> bool:
    """True iff the card is a spell (any non-land card).

    Includes creatures, instants, sorceries, enchantments, artifacts,
    planeswalkers. The XGBoost feature spec uses "spell" for this set.
    """
    return not is_land(card)


def is_nonbasic_land(card: ParsedCard) -> bool:
    """True iff the card is a land but not a Basic land."""
    return is_land(card) and "Basic" not in card.supertypes


def cmc(card: ParsedCard) -> int:
    """Converted mana cost. Returns 0 for cards with no mana cost (lands).

    Mirrors ``ParsedCard.mana_cost.cmc`` so callers don't have to write
    the ``is not None`` dance for lands every time.
    """
    return card.mana_cost.cmc if card.mana_cost is not None else 0


# ---------------------------------------------------------------------------
# Role predicates
# ---------------------------------------------------------------------------


def is_creature_strict(card: ParsedCard) -> bool:
    """The card's printed type is Creature.

    Doesn't include token-makers — see :func:`is_creature_for_castability`
    for the looser version used by the per-turn castability buckets.
    """
    return card.role_features.is_creature


def is_creature_for_castability(card: ParsedCard) -> bool:
    """Strict creature OR creates creature tokens on resolution.

    The per-turn castability buckets group these together: a sorcery
    that makes a 2/2 fills the "creature on board this turn" role the
    same way an actual creature spell does.
    """
    return is_creature_strict(card) or bool(card.role_features.creates_creatures)


def is_removal(card: ParsedCard) -> bool:
    """Broad removal: destroy/exile, burn, bounce, tuck, removal aura,
    counterspell.

    Counterspell joined the family in PR #12 (``is_counterspell`` flag
    on RoleFeatures).
    """
    rf = card.role_features
    return (
        rf.removal_destroy_or_exile
        or rf.removal_burn_damage is not None
        or rf.is_bounce
        or rf.is_top_library
        or rf.is_removal_aura
        or rf.is_counterspell
    )


def is_combat_trick(card: ParsedCard) -> bool:
    """Combat trick — instant pump effects.

    The role-by-MV section lists this as a separate role from pump aura.
    """
    rf = card.role_features
    return (
        rf.combat_trick_power is not None
        or rf.combat_trick_toughness is not None
        or bool(rf.combat_trick_granted_keywords)
    )


def is_pump_broad(card: ParsedCard) -> bool:
    """Combat trick or pump aura. Used by the per-turn castability
    "pump" bucket, which conflates the two (both want a creature in
    play to be useful)."""
    return is_combat_trick(card) or card.role_features.is_pump_aura


def is_equipment_or_vehicle(card: ParsedCard) -> bool:
    """Equipment or Vehicle. Grouped per the per-turn castability spec."""
    rf = card.role_features
    return rf.is_equipment or rf.is_vehicle


def is_card_manipulation(card: ParsedCard) -> bool:
    """Draws cards or filters them (scry / loot / surveil).

    Adapter over ``role_features.cards_drawn`` and
    ``role_features.cards_manipulated``. Either > 0 counts.
    """
    rf = card.role_features
    return rf.cards_drawn > 0 or rf.cards_manipulated > 0


def has_alt_mode(card: ParsedCard) -> bool:
    """The card has at least one alternative mode beyond its base cast.

    Operational definition: ``len(card.modes) > 1``. Catches cycling,
    land-cycling, channel, and the alt-cost-cast family (evoke /
    flashback / madness / jump-start / aftermath) — these all land as
    a second Mode on the parsed card.

    Does NOT catch Adventure / MDFC / Split / modal "choose one"
    cards, which bail to NEEDS_LLM in the parser today (known v1
    limitation).
    """
    return len(card.modes) > 1


def has_mana_ability(card: ParsedCard) -> bool:
    """The card has at least one permanent-resident mana ability.

    A creature with a mana ability is a mana dork; a non-equipment,
    non-vehicle artifact with one is a mana rock. Both feed the
    ``is_ramp`` adapter.
    """
    return bool(card.mana_abilities)


def has_fetch_to_battlefield(card: ParsedCard) -> bool:
    """Any mode fetches a land directly onto the battlefield.

    Captures Cultivate-style ramp ({2}{G}, fetches a land tapped),
    Three Visits ({2}{G}, fetches a Forest untapped), Evolving Wilds
    (sac fetches a basic tapped). Hand-fetch effects (Environmental
    Scientist, landcycling) are NOT counted — they don't accelerate
    mana the way a battlefield fetch does.
    """
    for mode in card.modes:
        for effect in mode.effects:
            if isinstance(effect, FetchLandEffect) and effect.destination in (
                "battlefield_untapped",
                "battlefield_tapped",
            ):
                return True
    return False


def is_ramp(card: ParsedCard) -> bool:
    """Mana dork, mana rock, or any mode that fetches a land into play.

    The spec's definition. ``has_mana_ability`` covers dorks/rocks;
    ``has_fetch_to_battlefield`` covers Cultivate-family ramp.
    """
    return has_mana_ability(card) or has_fetch_to_battlefield(card)


def is_other(card: ParsedCard) -> bool:
    """Catch-all role flag — set when no other category applies.

    Set by the parser (and re-validated at save time in store.py) as
    the complement of every other role flag. Read straight off the
    role features here; we don't re-derive it.
    """
    return card.role_features.is_other


# ---------------------------------------------------------------------------
# Mana-value buckets
# ---------------------------------------------------------------------------


def mv_le_2(card: ParsedCard) -> bool:
    """CMC ≤ 2. Used by both deck-level percentage and hand counts."""
    return cmc(card) <= 2


def mv_eq_3(card: ParsedCard) -> bool:
    return cmc(card) == 3


def mv_4_5(card: ParsedCard) -> bool:
    """4 ≤ CMC ≤ 5."""
    return 4 <= cmc(card) <= 5


def mv_ge_6(card: ParsedCard) -> bool:
    """CMC ≥ 6."""
    return cmc(card) >= 6


# Coarser buckets used by the role-by-MV section (one cell per
# (role, bucket) pair, no mv_ge_6 by spec).


def mv_0_2(card: ParsedCard) -> bool:
    """CMC 0..2. Alias for ``mv_le_2`` kept under a different name to
    match the role-by-MV section's labelling in features_list.md."""
    return cmc(card) <= 2


# Open-ended buckets used by the per-turn castability sections.


def mv_3_plus(card: ParsedCard) -> bool:
    """CMC ≥ 3 — turn 3's any-spell / creature broad bucket."""
    return cmc(card) >= 3


def mv_4_plus(card: ParsedCard) -> bool:
    """CMC ≥ 4 — turn 4's high-MV broad bucket."""
    return cmc(card) >= 4
