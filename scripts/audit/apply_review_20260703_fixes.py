"""Apply the 2026-07-03 random-commons spot-check fixes to the parsed cards.

A review of 20 random TLA/TMT/SOS commons (seed 20260703, sampled from
``data/processed/parsed_cards``) found three encoding issues plus two
cosmetic ones, and the owner additionally decided that modal spells with
an unconditionally-available draw mode should have that draw wired into
the simulator (previously modal cards only carried the draw in
``role_features``). See ``CARD_ENCODING_GUIDE.md`` §18 for the settled
conventions; this script applies them:

* **Triggered draw cleared** (guide §2 / §16 — April, Reporter of the
  Weird precedent): Oroku Saki (TMT #68, combat-damage trigger) and
  April O'Neil, Hacktivist (TMT #29, end-step trigger) lose
  ``cards_drawn=1``.
* **Graveyard-resident activation stats cleared**: Stone Docent
  (SOS #36) loses ``cards_manipulated=1`` — its surveil lives on a
  "{W}, Exile this card from your graveyard:" ability that can't fire
  in the mulligan window.
* **Shredder's Revenge** (TMT #76): the "target player draws two cards
  and loses 2 life" mode is self-targetable (Sign in Blood templating),
  so per the §12 modal aggregation it gains ``cards_drawn=2`` — plus a
  ``DrawCardsEffect(n=2)`` under the new modal-draw sim rule.
* **Modal-draw sim wiring** (new §18 rule): Splatter Technique
  (SOS #231, draw 4), Prismari Charm (SOS #211, surveil 2 + draw 1),
  Return of the Wildspeaker (TLA #bonus-ecc-115, variable draw min 1),
  Ashling's Command (ECL #205, draw 2), Sygg's Command (ECL #244,
  draw 1). Gated draw modes stay unwired: Witherbloom Charm (sac cost)
  and Glorious Decay (needs a graveyard target).
* **Visionary's Dance** (SOS #242): its discard-self channel filter is
  net-0 on hand size, so ``cards_drawn`` 1 -> 0 (consistent with how
  cycling and Gristle Glutton are encoded); ``cards_manipulated=1``
  stays.
* **Firebending Lesson** (TLA #138): stale reason string still said
  "conservative removal_burn_damage=2" after §17 correctly bumped the
  value to the kicked 5. Reason text refreshed; no field changes.

Modified ``auto`` cards are bumped to ``llm_encoded`` so the fixes
survive subsequent ``run-detector`` invocations (same mechanism as
``apply_flagged_fixes.py``). The script is idempotent: re-running it on
an already-fixed file reports zero changes.

Usage::

    uv run python scripts/audit/apply_review_20260703_fixes.py
"""

from __future__ import annotations

from collections.abc import Callable

from mulligan_coach_cards import (
    DrawCardsEffect,
    ParseStatus,
    ScryEffect,
    load_parsed_cards,
    save_parsed_cards,
)
from mulligan_coach_cards.models import ParsedCard

FIX_MARKER = "fix 2026-07-03"


def _cast_mode(card: ParsedCard):
    """First cast mode, or None. Every card patched here has exactly one
    cast mode except Firebending Lesson (whose fix is reason-text only)."""
    for mode in card.modes:
        if mode.kind == "cast":
            return mode
    return None


def _has_draw_effect(card: ParsedCard) -> bool:
    mode = _cast_mode(card)
    return mode is not None and any(e.kind == "draw_cards" for e in mode.effects)


def _bump_to_llm_encoded(card: ParsedCard) -> None:
    """Fixes to ``auto`` cards would be reverted by the next detector run
    (only LLM_ENCODED / NEEDS_HUMAN entries are preserved), so flip them."""
    if card.status == ParseStatus.AUTO:
        card.status = ParseStatus.LLM_ENCODED


def _note(card: ParsedCard, text: str) -> None:
    card.reasons.append(f"{FIX_MARKER}: {text}")


# ---------------------------------------------------------------------------
# Per-card fixes. Each returns True iff it changed the card.
# ---------------------------------------------------------------------------


def _clear_triggered_draw(card: ParsedCard, why: str) -> bool:
    if card.role_features.cards_drawn == 0:
        return False
    card.role_features.cards_drawn = 0
    _bump_to_llm_encoded(card)
    _note(card, why)
    return True


def _fix_oroku_saki(card: ParsedCard) -> bool:
    return _clear_triggered_draw(
        card,
        "combat-damage-triggered draw cleared per guide §2/§16 "
        "(April, Reporter of the Weird precedent)",
    )


def _fix_april_hacktivist(card: ParsedCard) -> bool:
    return _clear_triggered_draw(
        card, "end-step-triggered draw cleared per guide §16 (recurring triggers not modeled)"
    )


def _fix_stone_docent(card: ParsedCard) -> bool:
    if card.role_features.cards_manipulated == 0:
        return False
    card.role_features.cards_manipulated = 0
    _bump_to_llm_encoded(card)
    _note(
        card,
        "surveil lives on a graveyard-resident activation ('{W}, Exile this card "
        "from your graveyard:') that can't fire in the mulligan window — "
        "cards_manipulated cleared (Sewer-veillance Cam conservatism, guide §2)",
    )
    return True


def _fix_shredders_revenge(card: ParsedCard) -> bool:
    changed = False
    if card.role_features.cards_drawn != 2:
        card.role_features.cards_drawn = 2
        changed = True
    if not _has_draw_effect(card):
        _cast_mode(card).effects.append(DrawCardsEffect(n=2))
        changed = True
    if changed:
        _note(
            card,
            "'target player draws two cards and loses 2 life' is self-targetable "
            "(Sign in Blood templating) — cards_drawn=2 per §12 aggregation, "
            "DrawCardsEffect(2) per §18 modal-draw sim rule",
        )
    return changed


def _wire_modal_draw(card: ParsedCard, effects: list, why: str) -> bool:
    """Append sim effects for an unconditionally-available modal draw mode."""
    if _has_draw_effect(card):
        return False
    _cast_mode(card).effects.extend(effects)
    _note(card, f"{why} — wired per §18 modal-draw sim rule")
    return True


def _fix_visionarys_dance(card: ParsedCard) -> bool:
    if card.role_features.cards_drawn == 0:
        return False
    card.role_features.cards_drawn = 0
    _note(
        card,
        "discard-self channel filter is net-0 on hand size — cards_drawn 1 -> 0 "
        "(consistent with cycling / Gristle Glutton); cards_manipulated=1 stays",
    )
    return True


_STALE_FIREBENDING = (
    "llm: 1-mana 2-damage to creature (5 if kicked). Has kicker alt-cost; "
    "conservative removal_burn_damage=2"
)


def _fix_firebending_lesson(card: ParsedCard) -> bool:
    """Reason-text refresh only — §17 already fixed removal_burn_damage to 5."""
    if _STALE_FIREBENDING not in card.reasons:
        return False
    card.reasons[card.reasons.index(_STALE_FIREBENDING)] = (
        "llm: 1-mana 2-damage to creature (5 if kicked); "
        "removal_burn_damage=5 = kicked value per guide §12/§17"
    )
    return True


FIXES: dict[str, dict[str, Callable[[ParsedCard], bool]]] = {
    "TMT": {
        "29": _fix_april_hacktivist,
        "68": _fix_oroku_saki,
        "76": _fix_shredders_revenge,
    },
    "SOS": {
        "36": _fix_stone_docent,
        "211": lambda c: _wire_modal_draw(
            c, [ScryEffect(n=2), DrawCardsEffect(n=1)], "surveil-2-then-draw mode"
        ),
        "231": lambda c: _wire_modal_draw(c, [DrawCardsEffect(n=4)], "draw-4 mode"),
        "242": _fix_visionarys_dance,
    },
    "TLA": {
        "138": _fix_firebending_lesson,
        "bonus-ecc-115": lambda c: _wire_modal_draw(
            c, [DrawCardsEffect(n=1)], "variable-draw mode (min 1 per §9)"
        ),
    },
    "ECL": {
        "205": lambda c: _wire_modal_draw(c, [DrawCardsEffect(n=2)], "draw-2 mode"),
        "244": lambda c: _wire_modal_draw(c, [DrawCardsEffect(n=1)], "draw-1 mode"),
    },
}


def main() -> None:
    for set_code, fixes in FIXES.items():
        cards = load_parsed_cards(set_code)
        if not cards:
            print(f"{set_code}: no parsed cards found — skipped")
            continue
        touched: list[str] = []
        for card in cards:
            fix = fixes.get(card.collector_number)
            if fix is not None and fix(card):
                touched.append(f"#{card.collector_number} {card.name}")
        missing = set(fixes) - {c.collector_number for c in cards}
        if missing:
            print(f"{set_code}: WARNING — collector numbers not found: {sorted(missing)}")
        if touched:
            save_parsed_cards(set_code, cards)
            print(f"{set_code}: fixed {len(touched)} card(s):")
            for line in touched:
                print(f"   {line}")
        else:
            print(f"{set_code}: nothing to change (already applied)")


if __name__ == "__main__":
    main()
