"""Apply per-card fixes from FLAGGED_feedback.md to the parsed-cards JSON.

The audit at ``scripts/audit/FLAGGED_feedback.md`` flagged 150+ cards
whose ``role_features`` (and in a handful of cases, simulator-side
effects) disagreed with what the project owner thinks they should be.
This script applies those fixes directly to
``data/processed/parsed_cards/{TMT,ECL,TLA}.json`` and bumps the
status of any modified ``auto`` card to ``llm_encoded`` so the fix
survives subsequent ``run-detector`` invocations.

The script is intentionally idempotent: re-running it on top of an
already-fixed file produces no further changes (modulo a final
sort/normalise pass via ``save_parsed_cards``).

Categories implemented (and the matching feedback theme number):

* Theme 4 — loot draws populate ``cards_manipulated`` (and the cast
  Mode's effects switch from a noop to ``DrawCardsEffect(n)`` +
  ``DiscardCardEffect(n)`` so the simulator actually loots).
* Theme 5 — surveil populates ``cards_manipulated`` and the cast Mode
  gets a ``ScryEffect(n)`` so the simulator manipulates the top of the
  library exactly like scry.
* Theme 6 — look-at-top-N patterns gain a ``LookAtTopEffect(n)`` on the
  cast Mode so the simulator's S2 hand-fetch policy can find a land.
* Theme 7 — duplicate ``creates_creatures`` entries are kept (one per
  token created) per the owner's design change.
* Theme 8 / 10 — token-body / aura-pump granted keywords are added.
* Theme 9 — variable-X token bodies get a single CreatureBody entry
  (assume X=1).
* Theme 11 — "draw N then discard M" gets the net delta on
  ``cards_drawn`` plus full ``cards_manipulated``.
* Theme 12 — saga chapter-I sweepers (and Karai's Technique-style
  Sorcery removal+combat-trick) gain ``is_mass_removal`` /
  ``removal_destroy_or_exile``.
* Theme 14 — permanents whose ETB destroys/exiles a creature gain
  ``removal_destroy_or_exile``.

Plus a long list of per-card hand fixes from the audit. Each fix is
keyed by ``(set_code, collector_number)`` so reading the script against
the audit file is straightforward.

Usage::

    uv run python scripts/audit/apply_flagged_fixes.py

Prints a summary of how many entries were touched per set.
"""

from __future__ import annotations

from pathlib import Path

from mulligan_coach_cards import (
    CreatureBody,
    DiscardCardEffect,
    DrawCardsEffect,
    LookAtTopEffect,
    ParseStatus,
    ScryEffect,
    load_parsed_cards,
    save_parsed_cards,
)
from mulligan_coach_cards.models import ParsedCard, RoleFeatures

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Effect-list helpers
# ---------------------------------------------------------------------------


def _cast_mode_effects(card: ParsedCard) -> list:
    """Return the cast mode's effects list (mutable), or None if no cast."""
    for mode in card.modes:
        if mode.kind == "cast":
            return mode.effects
    return []


def _add_effect_to_cast(card: ParsedCard, effect) -> None:
    """Append an effect to the cast mode's effects, if a cast mode exists.

    We append rather than insert so the ``EntersBattlefieldEffect`` (when
    present on a permanent's cast Mode) stays at the head — the engine
    relies on that ordering.
    """
    for mode in card.modes:
        if mode.kind == "cast":
            mode.effects.append(effect)
            return


def _ensure_etb_first(card: ParsedCard) -> None:
    """No-op — kept for explicit intent. EntersBattlefieldEffect ordering
    isn't disturbed by our append-only additions."""


# ---------------------------------------------------------------------------
# Generic per-card fixes
# ---------------------------------------------------------------------------


def _bump_to_llm_encoded(card: ParsedCard) -> None:
    """Bump status from auto to llm_encoded so the fix persists across
    detector re-runs."""
    if card.status == ParseStatus.AUTO:
        card.status = ParseStatus.LLM_ENCODED


def _clear_combat_trick(rf: RoleFeatures) -> None:
    rf.combat_trick_power = None
    rf.combat_trick_toughness = None
    rf.combat_trick_granted_keywords = []


def _scry_n(card: ParsedCard, n: int) -> None:
    """Idempotently set cards_manipulated >= n and emit one ScryEffect(n)
    on the cast mode if not already present."""
    rf = card.role_features
    rf.cards_manipulated = max(rf.cards_manipulated, n)
    for mode in card.modes:
        if mode.kind == "cast":
            for fx in mode.effects:
                if isinstance(fx, ScryEffect) and fx.n == n:
                    return
            mode.effects.append(ScryEffect(n=n))
            return


def _loot(card: ParsedCard, n_draw: int, n_discard: int) -> None:
    """Apply loot semantics: cards_manipulated += n_draw, net to cards_drawn,
    and emit DrawCardsEffect + DiscardCardEffect on the cast mode."""
    rf = card.role_features
    rf.cards_manipulated += n_draw
    net = max(0, n_draw - n_discard)
    rf.cards_drawn = rf.cards_drawn + net  # already includes prior gross-misattribution
    _add_effect_to_cast(card, DrawCardsEffect(n=n_draw))
    _add_effect_to_cast(card, DiscardCardEffect(n=n_discard))


def _look_at_top(
    card: ParsedCard,
    n: int,
    *,
    accepts_land: bool = True,
    accepts_nonland: bool = True,
    cards_manipulated_credit: int = 0,
) -> None:
    """Idempotently emit a LookAtTopEffect on the cast mode and set
    cards_manipulated >= credit.

    cards_manipulated_credit is typically ``n - 1`` — the player sees N
    cards and takes one, so manipulation amounts to bottoming the rest.
    """
    if cards_manipulated_credit:
        card.role_features.cards_manipulated = max(
            card.role_features.cards_manipulated, cards_manipulated_credit
        )
    for mode in card.modes:
        if mode.kind == "cast":
            for fx in mode.effects:
                if isinstance(fx, LookAtTopEffect) and fx.n == n:
                    return
            mode.effects.append(
                LookAtTopEffect(n=n, accepts_land=accepts_land, accepts_nonland=accepts_nonland)
            )
            return


def _replace_creates_creatures(rf: RoleFeatures, bodies: list[CreatureBody]) -> None:
    rf.creates_creatures = list(bodies)


def _basic_body(power: str, toughness: str, **kw) -> CreatureBody:
    return CreatureBody(power=power, toughness=toughness, **kw)


# ---------------------------------------------------------------------------
# Per-card fix table.
#
# Each entry's value is a callable ``(card) -> None`` that mutates the
# card in place. The key is ``(set_code, collector_number)``. We don't
# call ``_bump_to_llm_encoded`` here — the apply loop does it for every
# card the fix touches.
# ---------------------------------------------------------------------------


def _ann(card: ParsedCard, reason: str) -> None:
    """Append an audit-style note to the card's reasons list so future
    readers know the entry was hand-adjusted by this script."""
    if reason not in card.reasons:
        card.reasons.append(reason)


# Themed fix builders — return a callable for a per-card lambda body.


def _fix_loot(n_draw: int, n_discard: int, *, scry_first: int = 0):
    """Return an idempotent fix that:

    * Sets cards_drawn = net (gross draw - discard).
    * Sets cards_manipulated >= gross draw count (clamped at the
      existing max so multi-effect cards don't lose ground).
    * Adds DrawCardsEffect / DiscardCardEffect / ScryEffect to the
      cast mode if not already present (loot pattern → simulator
      draws + discards).
    """

    def _has_effect_kind(card: ParsedCard, klass: type, n: int | None = None) -> bool:
        for mode in card.modes:
            if mode.kind != "cast":
                continue
            for fx in mode.effects:
                if isinstance(fx, klass) and (n is None or getattr(fx, "n", None) == n):
                    return True
        return False

    def _apply(card: ParsedCard) -> None:
        rf = card.role_features
        rf.cards_drawn = max(0, n_draw - n_discard)
        rf.cards_manipulated = max(rf.cards_manipulated, n_draw)
        if scry_first:
            rf.cards_manipulated = max(rf.cards_manipulated, n_draw + scry_first)
            if not _has_effect_kind(card, ScryEffect, scry_first):
                _add_effect_to_cast(card, ScryEffect(n=scry_first))
        if not _has_effect_kind(card, DrawCardsEffect, n_draw):
            _add_effect_to_cast(card, DrawCardsEffect(n=n_draw))
        if not _has_effect_kind(card, DiscardCardEffect, n_discard):
            _add_effect_to_cast(card, DiscardCardEffect(n=n_discard))
        _ann(card, "fix: loot — cards_manipulated, sim wired with draw+discard")

    return _apply


def _fix_scry(n: int):
    def _apply(card: ParsedCard) -> None:
        _scry_n(card, n)
        _ann(card, f"fix: scry {n} captured on ETB")

    return _apply


def _fix_surveil(n: int):
    """Idempotent: sets cards_manipulated >= n; appends one ScryEffect(n)
    to the cast mode if not already present. Lands have no cast mode,
    so for ETB-surveil lands the ScryEffect can't ride the cast resolution
    — the simulator's land-play step doesn't fire scry today, so for
    those cards the simulator-side modelling is incomplete (cards_manipulated
    still surfaces the signal to the model)."""

    def _apply(card: ParsedCard) -> None:
        rf = card.role_features
        rf.cards_manipulated = max(rf.cards_manipulated, n)
        for mode in card.modes:
            if mode.kind == "cast":
                for fx in mode.effects:
                    if isinstance(fx, ScryEffect) and fx.n == n:
                        return
                mode.effects.append(ScryEffect(n=n))
                _ann(card, f"fix: surveil {n} → cards_manipulated; sim treats as scry")
                return
        _ann(card, f"fix: surveil {n} → cards_manipulated (land — sim noop)")

    return _apply


def _fix_look_at_top(n: int, *, accepts_land: bool = True, accepts_nonland: bool = True):
    def _apply(card: ParsedCard) -> None:
        _look_at_top(
            card,
            n=n,
            accepts_land=accepts_land,
            accepts_nonland=accepts_nonland,
            cards_manipulated_credit=max(0, n - 1),
        )
        _ann(card, f"fix: look-at-top-{n} wired into simulator")

    return _apply


def _fix_set_removal_destroy(reason: str = "fix: ETB destroy/exile → removal_destroy_or_exile"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.removal_destroy_or_exile = True
        _ann(card, reason)

    return _apply


def _fix_set_bounce(reason: str = "fix: ETB bounce → is_bounce"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_bounce = True
        _ann(card, reason)

    return _apply


def _fix_set_punch(reason: str = "fix: fight/punch → is_punch_fight"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_punch_fight = True
        _ann(card, reason)

    return _apply


def _fix_strip_combat_trick(reason: str):
    def _apply(card: ParsedCard) -> None:
        _clear_combat_trick(card.role_features)
        _ann(card, reason)

    return _apply


def _fix_set_burn(damage: int, *, reason: str | None = None):
    def _apply(card: ParsedCard) -> None:
        card.role_features.removal_burn_damage = damage
        _ann(card, reason or f"fix: deals {damage} damage to creature/any → burn")

    return _apply


def _fix_set_counterspell(reason: str = "fix: counters spells → is_counterspell"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_counterspell = True
        _ann(card, reason)

    return _apply


def _fix_set_mass_removal(*, also_destroy: bool = True):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_mass_removal = True
        if also_destroy:
            card.role_features.removal_destroy_or_exile = True
        _ann(card, "fix: chapter-I / sweeper mass removal")

    return _apply


def _fix_set_mana_rock(reason: str = "fix: artifact with mana ability → is_mana_rock"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_mana_rock = True
        _ann(card, reason)

    return _apply


def _fix_set_top_library(reason: str = "fix: tuck pattern → is_top_library"):
    def _apply(card: ParsedCard) -> None:
        card.role_features.is_top_library = True
        _ann(card, reason)

    return _apply


def _fix_clear_creates_creatures(reason: str):
    def _apply(card: ParsedCard) -> None:
        card.role_features.creates_creatures = []
        _ann(card, reason)

    return _apply


def _fix_replace_creates_creatures(bodies: list[CreatureBody], reason: str):
    def _apply(card: ParsedCard) -> None:
        card.role_features.creates_creatures = list(bodies)
        _ann(card, reason)

    return _apply


def _compose(*fixes):
    def _apply(card: ParsedCard) -> None:
        for f in fixes:
            f(card)

    return _apply


def _fix_set_combat_trick(
    *,
    power: int | None = None,
    toughness: int | None = None,
    keywords: list[str] | None = None,
    reason: str = "fix: instant pump/keyword → combat_trick",
):
    """Idempotent combat-trick setter. Power/toughness use max() so a
    re-run doesn't double; keywords are appended only when not already
    present."""

    def _apply(card: ParsedCard) -> None:
        rf = card.role_features
        if power is not None:
            existing_p = rf.combat_trick_power or 0
            rf.combat_trick_power = max(existing_p, power)
        if toughness is not None:
            existing_t = rf.combat_trick_toughness or 0
            rf.combat_trick_toughness = max(existing_t, toughness)
        if keywords:
            for kw in keywords:
                if kw not in rf.combat_trick_granted_keywords:
                    rf.combat_trick_granted_keywords.append(kw)
        _ann(card, reason)

    return _apply


# ---------------------------------------------------------------------------
# The per-card table.
#
# Roughly mirrors FLAGGED_feedback.md in order. Cards are bumped to
# llm_encoded by the apply loop so the fixes survive run-detector.
# ---------------------------------------------------------------------------

PER_CARD_FIXES: dict[tuple[str, str], callable] = {  # type: ignore[valid-type]
    # === TMT ===
    # #4 Dimensional Exile — owner says current encoding is correct (it is
    #     a land aura that removes a creature). NO change.
    # #8 Hamato Guardian Stance — add cards_manipulated=1 (Scry 1).
    ("TMT", "8"): _fix_scry(1),
    # #9 High-Flying Ace — creature with activated ability; strip combat_trick.
    ("TMT", "9"): _fix_strip_combat_trick("fix: creature activated ability is not a combat trick"),
    # #11 Koya — ETB exile target creature → removal.
    ("TMT", "11"): _fix_set_removal_destroy(),
    # #19 Lita — Food token isn't a creature; drop the body.
    ("TMT", "19"): _fix_clear_creates_creatures("fix: Food token is an artifact, not a creature"),
    # #24 Sally Pride — variable-X creature token; assume X=1.
    ("TMT", "24"): _fix_replace_creates_creatures(
        [CreatureBody(power="2", toughness="2", colors=["R"], subtypes=["Mutant"])],
        "fix: variable-X token body recorded (assume X=1)",
    ),
    # #25 Triceraton Commander — variable-X.
    ("TMT", "25"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="2",
                toughness="2",
                colors=["W"],
                subtypes=["Dinosaur", "Soldier"],
            )
        ],
        "fix: variable-X token body recorded (assume X=1)",
    ),
    # #28 Uneasy Alliance — duplicate Ninja entry; owner now wants the
    #     full list, so per the new rule "all tokens" we leave entries
    #     reflecting actual numbers. Card creates 1 Ninja → 1 entry.
    ("TMT", "28"): _fix_replace_creates_creatures(
        [CreatureBody(power="1", toughness="1", colors=["B"], subtypes=["Ninja"])],
        "fix: dedupe — card creates one Ninja on sac",
    ),
    # #31 Bespoke Bō — ETB bounce nonland permanent.
    ("TMT", "31"): _fix_set_bounce(),
    # #44 Metalhead — ETB bounce artifact or creature.
    ("TMT", "44"): _fix_set_bounce(),
    # #50 Renet — flash ETB mass bounce-back permanents. Mark as bounce.
    ("TMT", "50"): _fix_set_bounce("fix: ETB mass bounce (this-turn permanents)"),
    # #52 Return to the Sewers — owner's choice top or bottom; treat as is_top_library.
    ("TMT", "52"): _fix_set_top_library("fix: tuck-or-deeper → is_top_library"),
    # #57 Anchovy & Banana Pizza — ETB destroys creature → removal.
    ("TMT", "57"): _fix_set_removal_destroy(),
    # #58 Armaggon — ETB destroy up to three creatures.
    ("TMT", "58"): _fix_set_removal_destroy(),
    # #62 Dream Beavers — ETB scry 1.
    ("TMT", "62"): _fix_scry(1),
    # #65 Lord Dregg — token missing flying keyword.
    ("TMT", "65"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="1",
                toughness="1",
                colors=["B"],
                subtypes=["Insect", "Warrior"],
                keywords=["flying"],
            )
        ],
        "fix: token keyword 'flying' added",
    ),
    # #87 Casey Jones — ETB look at top 4, may take an artifact.
    ("TMT", "87"): _fix_look_at_top(4, accepts_land=False),
    # #89 Cool but Rude — Class loot trigger → cards_manipulated.
    ("TMT", "89"): _compose(
        _fix_strip_combat_trick("(no combat_trick — Class loot)"),
        _fix_loot(1, 1),
    ),
    # #94 Manhole Missile — rummage rider; +1 cards_manipulated.
    ("TMT", "94"): _fix_loot(1, 1),
    # #98 Null Group — attack-trigger loot.
    ("TMT", "98"): _fix_loot(1, 1),
    # #109 Spicy Oatmeal Pizza — ETB 4 to any target → burn.
    ("TMT", "109"): _fix_set_burn(4, reason="fix: ETB deals 4 to any target"),
    # #122 Michelangelo's Technique — look at top 8.
    ("TMT", "122"): _fix_look_at_top(8, accepts_land=False),
    # #132 Saved by the Shell — instant +1/+1 counter + keyword grant.
    # User: treat as a pump combat trick (the counter is permanent but
    # functionally +1/+1 the turn it's cast; the keyword grant is the
    # primary combat-trick payload).
    ("TMT", "132"): _compose(
        _fix_set_combat_trick(
            power=1,
            toughness=1,
            keywords=["trample", "hexproof", "indestructible"],
            reason="fix: instant +1/+1 + trample/hexproof/indestructible → combat trick",
        ),
        # Clear is_other now that we have a specific category.
        lambda c: setattr(c.role_features, "is_other", False),
    ),
    # #133 Tenderize — punch.
    ("TMT", "133"): _fix_set_punch(),
    # #152 Karai's Technique — sorcery with Sneak; user says mark as
    #     combat_trick + add removal (the -3/-3 mode is removal).
    ("TMT", "152"): _compose(
        _fix_set_removal_destroy("fix: -3/-3 mode is creature removal"),
        # Re-add combat_trick (parser now suppresses on sorceries). The
        # +3/+3 mode is the combat-trick body; user notes Sneak makes
        # it instant-speed castable.
        lambda c: setattr(c.role_features, "combat_trick_power", 3),
        lambda c: setattr(c.role_features, "combat_trick_toughness", 3),
    ),
    # #154 Last Ronin — saga chapter I = destroy all creatures.
    ("TMT", "154"): _fix_set_mass_removal(),
    # #161 Nobody — ETB scry 1.
    ("TMT", "161"): _fix_scry(1),
    # #170 Tainted Treats — destroys artifact OR creature; creature branch fires.
    ("TMT", "170"): _fix_set_removal_destroy(),
    # #180 Turtle Blimp — dedupe to single 2/2 R Mutant.
    ("TMT", "180"): _fix_replace_creates_creatures(
        [CreatureBody(power="2", toughness="2", colors=["R"], subtypes=["Mutant"])],
        "fix: dedupe to one Mutant token",
    ),
    # #bonus-dsc-113 Brainstorm — gross 3, net 1.
    ("TMT", "bonus-dsc-113"): _compose(
        lambda c: setattr(c.role_features, "cards_drawn", 1),
        lambda c: setattr(c.role_features, "cards_manipulated", 2),
        lambda c: _ann(c, "fix: Brainstorm net 1 cards_drawn + 2 manipulated"),
    ),
    # #bonus-mkm-270 Undercity Sewers — ETB surveil 1 land.
    ("TMT", "bonus-mkm-270"): _fix_surveil(1),
    # === ECL ===
    # #10 Clachan Festival — already correct count? Card creates two on
    #     ETB and one per activation. We record both ETB tokens.
    ("ECL", "10"): _fix_replace_creates_creatures(
        [
            CreatureBody(power="1", toughness="1", colors=["G", "W"], subtypes=["Kithkin"]),
            CreatureBody(power="1", toughness="1", colors=["G", "W"], subtypes=["Kithkin"]),
        ],
        "fix: ETB makes two Kithkin; record both per new design",
    ),
    # #15 Evershrike's Gift — pump aura missing 'flying'.
    ("ECL", "15"): lambda c: (
        c.role_features.aura_pump_granted_keywords.append("flying")
        if "flying" not in c.role_features.aura_pump_granted_keywords
        else None,
        _ann(c, "fix: aura pump grants flying"),
    )[-1],
    # #23 Kithkeeper — variable-X tokens.
    ("ECL", "23"): _fix_replace_creates_creatures(
        [CreatureBody(power="1", toughness="1", colors=["G", "W"], subtypes=["Kithkin"])],
        "fix: variable-X token body recorded (assume X=1)",
    ),
    # #24 Liminal Hold — ETB exile nonland permanent.
    ("ECL", "24"): _fix_set_removal_destroy(),
    # #28 Personify — blinks own creature; not removal. Token missing changeling.
    ("ECL", "28"): _compose(
        lambda c: setattr(c.role_features, "removal_destroy_or_exile", False),
        _fix_replace_creates_creatures(
            [
                CreatureBody(
                    power="1",
                    toughness="1",
                    subtypes=["Shapeshifter"],
                    keywords=["changeling"],
                )
            ],
            "fix: own-creature blink + token keyword 'changeling'",
        ),
    ),
    # #34 Shore Lurker — ETB surveil 1.
    ("ECL", "34"): _fix_surveil(1),
    # #47 Disruptor of Currents — ETB bounce nonland permanent.
    ("ECL", "47"): _fix_set_bounce(),
    # #52 Glen Elendra's Answer — counterspell + token missing flying.
    ("ECL", "52"): _compose(
        _fix_set_counterspell(),
        _fix_replace_creates_creatures(
            [
                CreatureBody(
                    power="1",
                    toughness="1",
                    colors=["U", "B"],
                    subtypes=["Faerie"],
                    keywords=["flying"],
                )
            ],
            "fix: token keyword 'flying'",
        ),
    ),
    # #58 Lofty Dreams — aura pump missing flying.
    ("ECL", "58"): lambda c: (
        c.role_features.aura_pump_granted_keywords.append("flying")
        if "flying" not in c.role_features.aura_pump_granted_keywords
        else None,
        _ann(c, "fix: aura pump grants flying"),
    )[-1],
    # #66 Rimekin Recluse — ETB bounce creature.
    ("ECL", "66"): _fix_set_bounce(),
    # #70 Silvergill Peddler — tap-trigger loot.
    ("ECL", "70"): _fix_loot(1, 1),
    # #68 Shinestriker — Vivid ETB draws N cards (N = colors among
    # permanents). User: at least 1 card draw (Shinestriker itself
    # counts as a colored permanent on ETB).
    ("ECL", "68"): lambda c: (
        setattr(c.role_features, "cards_drawn", max(1, c.role_features.cards_drawn)),
        _ann(c, "fix: variable ETB draw, encoded as >=1"),
    )[-1],
    # #72 Stratosoarer — creature ETB pump; not flash. Strip combat_trick.
    ("ECL", "72"): _fix_strip_combat_trick("fix: creature ETB pump, not flash"),
    # #74 Sunderflock — ETB mass bounce non-Elementals.
    ("ECL", "74"): _fix_set_bounce("fix: ETB mass bounce non-Elementals"),
    # #79 Thirst for Identity — draw 3 then discard 2 (or 0 if you discard creature).
    ("ECL", "79"): _compose(
        lambda c: setattr(c.role_features, "cards_drawn", 1),
        lambda c: setattr(c.role_features, "cards_manipulated", 2),
        lambda c: _ann(c, "fix: net 1 cards_drawn + 2 manipulated"),
    ),
    # #80 Unexpected Assistance — draw 3, discard 1 → net 2.
    ("ECL", "80"): _compose(
        lambda c: setattr(c.role_features, "cards_drawn", 2),
        lambda c: setattr(c.role_features, "cards_manipulated", 3),
        lambda c: _ann(c, "fix: net 2 cards_drawn + 3 manipulated"),
    ),
    # #83 Wanderwine Farewell — bounce 1-2 nonland permanents.
    ("ECL", "83"): _fix_set_bounce("fix: bounces 1-2 nonland permanents"),
    # #75 Swat Away — owner: target spell or creature → also counterspell.
    #     is_top_library is already set; add is_counterspell.
    ("ECL", "75"): _fix_set_counterspell("fix: targets spell or creature — also counterspell"),
    # #97 Darkness Descends — parser now handles "Put two -1/-1 counters
    #     on each creature" as is_mass_removal + removal_destroy_or_exile.
    #     Migration script clears is_other in case a stale entry has it.
    ("ECL", "97"): _compose(
        lambda c: setattr(c.role_features, "is_mass_removal", True),
        lambda c: setattr(c.role_features, "removal_destroy_or_exile", True),
        lambda c: setattr(c.role_features, "is_other", False),
        lambda c: _ann(c, "fix: two -1/-1 counters on each creature → mass removal"),
    ),
    # #119 Scarblade's Malice — instant grants deathtouch+lifelink → combat trick.
    ("ECL", "119"): lambda c: (
        [
            c.role_features.combat_trick_granted_keywords.append(k)
            for k in ("deathtouch", "lifelink")
            if k not in c.role_features.combat_trick_granted_keywords
        ],
        _ann(c, "fix: instant grants deathtouch + lifelink → combat trick"),
    )[-1],
    # #122 Twilight Diviner — ETB surveil 2.
    ("ECL", "122"): _fix_surveil(2),
    # #124 Ashling — ETB loot trigger.
    ("ECL", "124"): _fix_loot(1, 1),
    # #142 Goatnap — sorcery threaten + (Goat) +3/+0. Not a combat trick.
    ("ECL", "142"): _fix_strip_combat_trick("fix: threaten effect, not combat trick"),
    # #144 Gristle Glutton — activated loot.
    ("ECL", "144"): _fix_loot(1, 1),
    # #146 Impolite Entrance — sorcery; drop combat_trick.
    ("ECL", "146"): _fix_strip_combat_trick("fix: sorcery — combat_trick suppressed"),
    # #157 Soulbright Seeker — activated ability; not combat trick.
    ("ECL", "157"): _fix_strip_combat_trick("fix: activated ability — not combat trick"),
    # #162 Tweeze — loot rider.
    ("ECL", "162"): _fix_loot(1, 1),
    # #164 Assert Perfection — sorcery punch; drop combat_trick, add punch.
    ("ECL", "164"): _compose(
        _fix_strip_combat_trick("fix: sorcery — combat_trick suppressed"),
        _fix_set_punch("fix: punch effect added"),
    ),
    # #177 Gilt-Leaf's Embrace — aura pump missing 'indestructible'.
    ("ECL", "177"): lambda c: (
        c.role_features.aura_pump_granted_keywords.append("indestructible")
        if "indestructible" not in c.role_features.aura_pump_granted_keywords
        else None,
        _ann(c, "fix: aura grants indestructible (already had trample)"),
    )[-1],
    # #181 Lys Alana Informant — ETB surveil 1.
    # #187 Pitiless Fists — aura with ETB-fight trigger. Mark as
    #     is_punch_fight (in addition to existing is_pump_aura).
    ("ECL", "187"): _fix_set_punch("fix: aura ETB fight → is_punch_fight"),
    ("ECL", "181"): _fix_surveil(1),
    # #182 Midnight Tilling — mill 4, may take a permanent. Wire as look-at-top.
    ("ECL", "182"): _fix_look_at_top(4),
    # #192 Sapling Nursery — dedupe + reach keyword.
    ("ECL", "192"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="3",
                toughness="4",
                colors=["G"],
                subtypes=["Treefolk"],
                keywords=["reach"],
            )
        ],
        "fix: dedupe + token keyword 'reach'",
    ),
    # #196 Surly Farrier — creature activated ability; not combat trick.
    ("ECL", "196"): _fix_strip_combat_trick("fix: activated ability — not combat trick"),
    # #197 Tend the Sprigs — token missing 'reach'.
    ("ECL", "197"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="3",
                toughness="4",
                colors=["G"],
                subtypes=["Treefolk"],
                keywords=["reach"],
            )
        ],
        "fix: token keyword 'reach'",
    ),
    # #207 Bre of Clan Stoutarm — creature activated ability; not combat trick.
    ("ECL", "207"): _fix_strip_combat_trick("fix: activated ability — not combat trick"),
    # #208 Brigid's Command — sorcery; drop combat_trick (punch stays).
    ("ECL", "208"): _fix_strip_combat_trick("fix: sorcery — combat_trick suppressed"),
    # #209 Catharsis — ETB makes two of the same token. Per new design, record both.
    ("ECL", "209"): _fix_replace_creates_creatures(
        [
            CreatureBody(power="1", toughness="1", colors=["G", "W"], subtypes=["Kithkin"]),
            CreatureBody(power="1", toughness="1", colors=["G", "W"], subtypes=["Kithkin"]),
        ],
        "fix: ETB makes two Kithkin",
    ),
    # #217-221 Eclipsed cycle — look at top 4.
    ("ECL", "217"): _fix_look_at_top(4),
    ("ECL", "218"): _fix_look_at_top(4),
    ("ECL", "219"): _fix_look_at_top(4),
    ("ECL", "220"): _fix_look_at_top(4),
    ("ECL", "221"): _fix_look_at_top(4),
    # #225 Flaring Cinder — conditional loot.
    ("ECL", "225"): _fix_loot(1, 1),
    # #256 Foraging Wickermaw — ETB surveil 1.
    ("ECL", "256"): _fix_surveil(1),
    # #260 Springleaf Drum — owner says NO change for mana rocks (per
    #     theme 13). Skip.
    # #261 Stalactite Dagger — single Shapeshifter with changeling.
    ("ECL", "261"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="1",
                toughness="1",
                subtypes=["Shapeshifter"],
                keywords=["changeling"],
            )
        ],
        "fix: dedupe + token keyword 'changeling'",
    ),
    # #bonus-2x2-69 Bitterblossom — token missing 'flying'.
    ("ECL", "bonus-2x2-69"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="1",
                toughness="1",
                colors=["B"],
                subtypes=["Faerie", "Rogue"],
                keywords=["flying"],
            )
        ],
        "fix: token keyword 'flying'",
    ),
    # === TLA ===
    # #3 Zuko's Exile — exiles target artifact/creature/enchantment.
    ("TLA", "3"): _fix_set_removal_destroy(),
    # #18 Enter the Avatar State — missing 'hexproof'.
    ("TLA", "18"): lambda c: (
        c.role_features.combat_trick_granted_keywords.append("hexproof")
        if "hexproof" not in c.role_features.combat_trick_granted_keywords
        else None,
        _ann(c, "fix: combat-trick grants hexproof"),
    )[-1],
    # #20 Gather the White Lotus — scry 2.
    ("TLA", "20"): _fix_scry(2),
    # #27 The Legend of Yangchen — chapter-I exile opp permanents → removal.
    ("TLA", "27"): _fix_set_mass_removal(),
    # #28 Master Piandao — look at top 4 (attack trigger).
    ("TLA", "28"): _fix_look_at_top(4, accepts_land=False),
    # #34 Sandbenders' Storm — earthbend doesn't create a token. Drop body.
    ("TLA", "34"): _fix_clear_creates_creatures(
        "fix: earthbend doesn't create token (sandbenders Storm)"
    ),
    # #39 United Front — variable-X tokens.
    ("TLA", "39"): _fix_replace_creates_creatures(
        [CreatureBody(power="1", toughness="1", colors=["W"], subtypes=["Ally"])],
        "fix: variable-X token (assume X=1)",
    ),
    # #42 Water Tribe Rallier — waterbend look-at-top 4.
    ("TLA", "42"): _fix_look_at_top(4, accepts_land=False),
    # #54 Gran-Gran — tap-trigger loot.
    ("TLA", "54"): _fix_loot(1, 1),
    # #57 Invasion Submersible — ETB bounce nonland permanent.
    ("TLA", "57"): _fix_set_bounce(),
    # #59 Katara, Bending Prodigy — activated draw 1 (waterbend). Set
    #     cards_drawn = 1 idempotently.
    ("TLA", "59"): lambda c: (
        setattr(c.role_features, "cards_drawn", max(1, c.role_features.cards_drawn)),
        _ann(c, "fix: activated draw 1 captured"),
    )[-1],
    # #61 The Legend of Kuruk — saga chapter I/II: scry 2 + draw 1.
    ("TLA", "61"): lambda c: (
        setattr(c.role_features, "cards_drawn", max(1, c.role_features.cards_drawn)),
        setattr(c.role_features, "cards_manipulated", max(2, c.role_features.cards_manipulated)),
        _ann(c, "fix: saga chapter I/II — draw 1 + scry 2"),
    )[-1],
    # #62 Lost Days — tuck (target creature or enchantment goes second-from-top
    #     or bottom). Treat as is_top_library.
    ("TLA", "62"): _fix_set_top_library("fix: tuck-or-deeper for creatures"),
    # #74 Teo, Spirited Glider — flying-attack loot trigger.
    ("TLA", "74"): _fix_loot(1, 1),
    # #80 Waterbending Lesson — draw 3, conditional discard 1 → net 2.
    ("TLA", "80"): _compose(
        lambda c: setattr(c.role_features, "cards_drawn", 2),
        lambda c: setattr(c.role_features, "cards_manipulated", 3),
        lambda c: _ann(c, "fix: net 2 cards_drawn + 3 manipulated"),
    ),
    # #93 Dai Li Indoctrination — earthbend doesn't create token.
    ("TLA", "93"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #97 Fatal Fissure — conditional removal; earthbend doesn't create token.
    ("TLA", "97"): _compose(
        _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
        _fix_set_removal_destroy("fix: conditional creature removal"),
    ),
    # #98 The Fire Nation Drill — conditional ETB destroy.
    ("TLA", "98"): _fix_set_removal_destroy("fix: ETB conditional destroy ≤4 power"),
    # #100 Fire Navy Trebuchet — token missing 'flying'.
    ("TLA", "100"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="2",
                toughness="1",
                subtypes=["Construct"],
                keywords=["flying"],
            )
        ],
        "fix: token keyword 'flying'",
    ),
    # #107 Koh, the Face Stealer — ETB exile target creature.
    ("TLA", "107"): _fix_set_removal_destroy(),
    # #117 The Rise of Sozin — chapter I destroys all creatures.
    ("TLA", "117"): _fix_set_mass_removal(),
    # #128 Combustion Technique — variable burn (2 + lessons in grave).
    #     User: encode as 2 damage burn assuming no Lessons in graveyard.
    ("TLA", "128"): _compose(
        _fix_set_burn(2, reason="fix: variable burn — encoded as 2 (X=0 Lessons)"),
        lambda c: setattr(c.role_features, "is_other", False),
    ),
    # #129 Crescent Island Temple — dedupe to ONE 1/1 R Monk prowess.
    ("TLA", "129"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="1",
                toughness="1",
                colors=["R"],
                subtypes=["Monk"],
                keywords=["prowess"],
            )
        ],
        "fix: dedupe + token keyword 'prowess'",
    ),
    # #133 Fire Nation Attacks — two tokens with firebending 1.
    ("TLA", "133"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="2",
                toughness="2",
                colors=["R"],
                subtypes=["Soldier"],
                keywords=["firebending 1"],
            ),
            CreatureBody(
                power="2",
                toughness="2",
                colors=["R"],
                subtypes=["Soldier"],
                keywords=["firebending 1"],
            ),
        ],
        "fix: two tokens with firebending 1",
    ),
    # #137 Firebender Ascension — token missing firebending 1.
    ("TLA", "137"): _fix_replace_creates_creatures(
        [
            CreatureBody(
                power="2",
                toughness="2",
                colors=["R"],
                subtypes=["Soldier"],
                keywords=["firebending 1"],
            )
        ],
        "fix: token keyword 'firebending 1'",
    ),
    # #161 Yuyan Archers — conditional ETB loot.
    ("TLA", "161"): _fix_loot(1, 1),
    # Earthbend dropouts — drop creates_creatures entries that the parser
    # added incorrectly.
    ("TLA", "166"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "167"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "173"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "174"): _compose(
        _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
        _fix_set_punch("fix: fight effect added"),
    ),
    ("TLA", "175"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "176"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "177"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "182"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "191"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #193 Rocky Rebuke — punch.
    ("TLA", "193"): _fix_set_punch(),
    ("TLA", "198"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #203 Aang at the Crossroads — look at top 5 (creature filter).
    ("TLA", "203"): _fix_look_at_top(5, accepts_land=False),
    # #205 Abandon Attachments — net 1 cards_drawn.
    ("TLA", "205"): _compose(
        lambda c: setattr(c.role_features, "cards_drawn", 1),
        lambda c: setattr(c.role_features, "cards_manipulated", 2),
        lambda c: _ann(c, "fix: net 1 cards_drawn + 2 manipulated"),
    ),
    ("TLA", "210"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "211"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #214 Dai Li Agents — earthbend doubled.
    ("TLA", "214"): _fix_clear_creates_creatures("fix: earthbend doesn't create token (x2)"),
    ("TLA", "219"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #223 Guru Pathik — look at top 5.
    ("TLA", "223"): _fix_look_at_top(5),
    # #238 Professor Zei — activated loot.
    ("TLA", "238"): _fix_loot(1, 1),
    # #240 Sokka — ETB loot up to 2.
    ("TLA", "240"): _fix_loot(2, 2),
    ("TLA", "246"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    ("TLA", "247"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #257 Kyoshi Battle Fan — dedupe to one Ally token.
    ("TLA", "257"): _fix_replace_creates_creatures(
        [CreatureBody(power="1", toughness="1", colors=["W"], subtypes=["Ally"])],
        "fix: dedupe to one Ally token",
    ),
    # #258 Meteor Sword — ETB destroy target permanent.
    ("TLA", "258"): _fix_set_removal_destroy(),
    # #266 Ba Sing Se — earthbend on a land.
    ("TLA", "266"): _fix_clear_creates_creatures("fix: earthbend doesn't create token"),
    # #277 Rumble Arena — ETB scry 1 on a land.
    ("TLA", "277"): _fix_scry(1),
    # #bonus-bro-233 Cityscape Leveler — cast + attack trigger destroys nonland.
    ("TLA", "bonus-bro-233"): _fix_set_removal_destroy(),
    # #bonus-dmu-235 Meteorite — ETB 2 damage to any target (mana rock + burn).
    ("TLA", "bonus-dmu-235"): _fix_set_burn(2, reason="fix: ETB 2 damage to any target"),
    # #bonus-cmm-294 The Great Henge — user: encode as mana rock, NOT
    #     card draw. The "draw a card" is conditional on a nontoken
    #     creature entering — too situational to count.
    ("TLA", "bonus-cmm-294"): _compose(
        _fix_set_mana_rock("fix: artifact with mana ability → is_mana_rock"),
        lambda c: setattr(c.role_features, "cards_drawn", 0),
        lambda c: _ann(c, "fix: cards_drawn=0 — draw is conditional on ETB trigger"),
    ),
    # #bonus-ecc-71 Black Sun's Zenith — "Put X -1/-1 counters on each
    #     creature." Parser now handles X (assumed = 2) → mass removal.
    #     Migration confirms the flag in case a stale entry is encountered.
    ("TLA", "bonus-ecc-71"): _compose(
        lambda c: setattr(c.role_features, "is_mass_removal", True),
        lambda c: setattr(c.role_features, "removal_destroy_or_exile", True),
        lambda c: setattr(c.role_features, "is_other", False),
        lambda c: _ann(c, "fix: X -1/-1 on each creature → mass removal"),
    ),
    # #bonus-dtk-150 Rending Volley — 4 to W/U creature.
    ("TLA", "bonus-dtk-150"): _fix_set_burn(4, reason="fix: 4 to color-restricted creature"),
    # #bonus-otc-170 Humble Defector — activated draw 2.
    ("TLA", "bonus-otc-170"): lambda c: (
        setattr(c.role_features, "cards_drawn", max(2, c.role_features.cards_drawn)),
        _ann(c, "fix: activated draw 2 captured"),
    )[-1],
    # #bonus-soc-238 Blasphemous Act — mass burn 13.
    ("TLA", "bonus-soc-238"): _compose(
        _fix_set_mass_removal(also_destroy=True),
        _fix_set_burn(13, reason="fix: 13 to each creature (mass burn)"),
    ),
    # #bonus-tdc-191 Noxious Gearhulk — ETB destroy another creature.
    ("TLA", "bonus-tdc-191"): _fix_set_removal_destroy(),
}


# Cards the project owner reviewed and explicitly said "keep as-is".
# Surfaced in the NEEDS_HUMAN_REVIEW.md output so the rationale stays
# easy to find when the same card gets re-flagged in a future audit.
INTENTIONALLY_DEFERRED: list[tuple[str, str, str]] = [
    (
        "TMT",
        "4",
        "Dimensional Exile — owner: keep current encoding (land aura that removes a creature; existing flag is right).",
    ),
    (
        "ECL",
        "260",
        "Springleaf Drum — owner: mana rock with conditional cost (tap a creature) — too situational to encode.",
    ),
    (
        "TLA",
        "262",
        "White Lotus Tile — owner: mana rock with conditional cost (creature-type X) — too situational to encode.",
    ),
    (
        "TMT",
        "30",
        "April, Reporter of the Weird — owner: loot only on combat damage; too conditional to encode as cards_manipulated.",
    ),
    ("TMT", "53", "Sewer-veillance Cam — owner: activated draw on sac, kept as-is for simplicity."),
    ("TMT", "80", "Splinter's Technique — owner: tutor adds 1 card, kept as-is for simplicity."),
    ("TMT", "82", "Stomped by the Foot — owner: kept as-is, the existing removal flag is right."),
    (
        "TMT",
        "90",
        "General Traag, Heart of Stone — owner: conditional removal (sac an artifact to deal 4) too situational to encode.",
    ),
    ("TMT", "96", "Mouser Foundry — owner: don't encode sac-effects that cost mana."),
    (
        "TMT",
        "182",
        "Weather Maker — owner: keep only as mana rock; activated burn is too situational.",
    ),
    ("TMT", "bonus-shm-73", "Plague of Vermin — owner: variable-X token w/ life-pay; kept as-is."),
    (
        "ECL",
        "27",
        "Morningtide's Light — owner: temporary exile isn't removal (creatures return at end step).",
    ),
    (
        "ECL",
        "113",
        "Nameless Inversion — owner: confirmed both combat_trick + removal flags are correct.",
    ),
    ("ECL", "198", "Thoughtweft Charge — owner: don't encode the conditional card-draw rider."),
]


# ---------------------------------------------------------------------------
# Apply pass
# ---------------------------------------------------------------------------


def main() -> int:
    data_root = REPO_ROOT / "data"
    per_set_changes: dict[str, int] = {}
    not_found: list[tuple[str, str]] = []

    for set_code in ("TMT", "ECL", "TLA"):
        cards = load_parsed_cards(set_code, data_root)
        by_cn: dict[str, ParsedCard] = {c.collector_number: c for c in cards}
        touched = 0
        for (s, cn), fix in PER_CARD_FIXES.items():
            if s != set_code:
                continue
            card = by_cn.get(cn)
            if card is None:
                not_found.append((s, cn))
                continue
            before = card.model_dump(mode="json")
            fix(card)
            _bump_to_llm_encoded(card)
            after = card.model_dump(mode="json")
            if before != after:
                touched += 1
        save_parsed_cards(set_code, cards, data_root=data_root)
        per_set_changes[set_code] = touched

    print("Per-set fixes applied:")
    for s, n in per_set_changes.items():
        print(f"  {s}: {n}")
    if not_found:
        print("\nCollector numbers in fix table but not in JSON:")
        for s, cn in not_found:
            print(f"  {s} #{cn}")

    # Write the NEEDS_HUMAN_REVIEW.md output with full context per entry.
    out_path = REPO_ROOT / "scripts" / "audit" / "NEEDS_HUMAN_REVIEW.md"
    lines: list[str] = [
        "# Cards still needing a judgment call from the project owner\n",
        "\n",
        "These cards were flagged in `FLAGGED_feedback.md` as ",
        "`debatable` *and* the systematic fixes in `apply_flagged_fixes.py` ",
        "didn't take a position. For each entry below: the current ",
        "encoding is shown, plus what the audit thought might be worth ",
        "changing. Decide whether to:\n",
        "\n",
        "1. Accept current — no action.\n",
        "2. Add a fix — extend `apply_flagged_fixes.py` with a new ",
        "PER_CARD_FIXES entry.\n",
        "3. Defer — re-flag in `FLAGGED_feedback.md` with the rationale.\n",
        "\n",
        "Most of these are activated-burn / activated-draw, conditional ",
        "triggers, or shape mismatches where the schema doesn't have a ",
        "clean home for what the card does.\n",
        "\n",
        "---\n",
        "\n",
    ]
    # Reload after the fix pass so the displayed encoding reflects any
    # cross-card mutations (none today, but harmless).
    by_set_cn: dict[tuple[str, str], ParsedCard] = {}
    for s in ("TMT", "ECL", "TLA"):
        for c in load_parsed_cards(s, data_root):
            by_set_cn[(s, c.collector_number)] = c

    for s, cn, note in INTENTIONALLY_DEFERRED:
        card = by_set_cn.get((s, cn))
        lines.append(f"## {s} #{cn}")
        if card is not None:
            lines.append(f" {card.name}\n")
            lines.append(f"- type: {card.type_line}\n")
            cost = card.mana_cost.raw if card.mana_cost else "—"
            lines.append(f"- cost: {cost}\n")
            lines.append(f"- oracle: {card.raw_oracle_text}\n")
            rf = card.role_features
            flag_pairs: list[str] = []
            for fn, fv in rf.model_dump().items():
                if fv in (False, None, 0, []):
                    continue
                flag_pairs.append(f"{fn}={fv!r}")
            lines.append(
                "- current role_features: "
                + (", ".join(flag_pairs) if flag_pairs else "(none)")
                + "\n"
            )
            lines.append(f"- status: {card.status.value}\n")
        else:
            lines.append("\n  (card not found in parsed_cards.json)\n")
        lines.append(f"- audit note: {note}\n\n")

    out_path.write_text("".join(lines), encoding="utf-8")
    print(f"\nWrote {out_path.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
