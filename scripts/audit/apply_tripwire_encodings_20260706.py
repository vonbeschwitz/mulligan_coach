"""Hand-encode the cards surfaced by the 2026-07-06 parser fixes.

The parser round that added the unknown-keyword tripwire, the death-trigger
skip, the activated-ability cmc<=3 crediting gate, the "Choose one." modal
form, and token-keyword capture (see ``CARD_ENCODING_GUIDE.md`` §19) flipped
15 cards to ``needs_llm`` on the 2026-07-06 ``run-detector`` rerun, and the
rerun also surfaced 5 pre-existing ``needs_llm`` ECL bonus-sheet stragglers
that had never been encoded. This script encodes all 20 per the owner's
rulings (recorded in §19):

* **ETB connive = loot** (guide §2): A.I.M. Scientists (MSH #44), Red Room
  Recruit (#110), Madame Masque (#104) get ``cards_manipulated=1`` and
  ``DrawCardsEffect(1) + DiscardCardEffect(1)`` on the cast mode.
* **M.O.D.O.K.** (MSH #106): "Mental Organism — Pay 3 life: connives" is
  assumed to fire once on arrival (pay-life costs are reliably payable,
  §9) → same loot encoding as ETB connive.
* **Recurring / attack-trigger connives stay unencoded** (§16): Leader
  Super-Genius (#64), Swordsman (#116), Kang Temporal Tyrant (#217).
* **Trickster's Stratagem** (MSH #81): tuck → ``is_top_library`` (Lost
  Days / Swat Away precedent, §1) + the connive rider as loot.
* **Villainous Hideout** (MSH #276): the {3},{T} connive activation is
  deliberately ignored — expensive land activations aren't modelled
  (owner ruling 2026-07-06).
* **Teamwork cards** (§16): Atlantis Attacks (#46) aggregates its modal
  outcomes (6/5 Leviathan token + bounce); Repulsor Blast (#150) keeps
  the 5-damage burn (the teamwork rider is face damage); Earth's
  Mightiest Heroes (#165) is a 6-mana battlefield tutor → ``is_other``.
* **TMT Bebop (#59) / Rocksteady (#131)**: flipped by the (pre-existing)
  discard-self fast-path guard because their typecycling reminder text
  contains "Discard this card:". Their parsed encoding (cast +
  land_cycle) is already correct; the lord statics aren't modelled.
* **SOS Akroma's Will**: period-form modal caught by the fixed
  ``_MODAL_RE``; mass-protection instants are ``is_other`` (§16).
* **ECL bonus-sheet stragglers**: Heat Shimmer (copy token — body
  unmodelable → is_other), Manamorphose (mana-neutral cantrip →
  ``cards_drawn=1`` + DrawCardsEffect; the mana production is NOT wired
  as a sim effect — ProduceManaEffect on cast modes has no established
  precedent), Dolmen Gate (combat static → is_other), Painter's Servant
  (color static → plain creature), Idyllic Tutor (tutor → is_other, §10
  Splinter's Technique precedent).

All patched cards become ``LLM_ENCODED`` so subsequent detector runs
preserve them. Idempotent: cards already carrying the fix marker are
skipped.

Usage::

    uv run python scripts/audit/apply_tripwire_encodings_20260706.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import (
    CreatureBody,
    DiscardCardEffect,
    DrawCardsEffect,
    NoopEffect,
    ParsedCard,
    ParseStatus,
)

FIX_MARKER = "tripwire encode 2026-07-06"


def _mark(card: ParsedCard, note: str) -> None:
    card.status = ParseStatus.LLM_ENCODED
    card.reasons.append(f"{FIX_MARKER}: {note}")


def _cast_mode(card: ParsedCard):
    for mode in card.modes:
        if mode.kind == "cast":
            return mode
    raise AssertionError(f"{card.name}: no cast mode")


def _add_etb_loot(card: ParsedCard) -> None:
    """Wire draw-1/discard-1 onto the cast mode and credit the loot.

    Loot rule (guide §2): gross draws feed ``cards_manipulated``, net
    (0 here) feeds ``cards_drawn``.
    """
    mode = _cast_mode(card)
    kinds = [e.kind for e in mode.effects]
    if "draw_cards" not in kinds:
        mode.effects.append(DrawCardsEffect(n=1))
        mode.effects.append(DiscardCardEffect(n=1))
    card.role_features.cards_manipulated = 1
    card.role_features.cards_drawn = 0


def fix_msh(cards: list[ParsedCard]) -> int:
    by_name = {c.name: c for c in cards}
    n = 0

    for name, note in [
        ("A.I.M. Scientists", "ETB connive = loot (guide §2/§19)"),
        ("Red Room Recruit", "ETB connive = loot (guide §2/§19)"),
        (
            "Madame Masque",
            "ETB connive = loot; draw-two token trigger kept (recurring-trigger token, Sokka precedent)",
        ),
    ]:
        c = by_name[name]
        if any(FIX_MARKER in r for r in c.reasons):
            continue
        _add_etb_loot(c)
        _mark(c, note)
        n += 1

    c = by_name["M.O.D.O.K."]
    if not any(FIX_MARKER in r for r in c.reasons):
        _add_etb_loot(c)
        _mark(
            c,
            "owner ruling: 'Pay 3 life: connives' assumed to fire once on arrival "
            "(pay-life is reliably payable, §9); -1/-1 opponent static not modelled",
        )
        n += 1

    for name, note in [
        (
            "Leader, Super-Genius",
            "recurring connive engine (replacement + begin-combat trigger) stays unencoded per §16",
        ),
        ("Swordsman, Sharp Scoundrel", "attack-trigger connive stays unencoded per §16"),
        ("Kang, Temporal Tyrant", "attack-trigger connive stays unencoded per §16"),
    ]:
        c = by_name[name]
        if any(FIX_MARKER in r for r in c.reasons):
            continue
        c.role_features.cards_drawn = 0
        c.role_features.cards_manipulated = 0
        _mark(c, note)
        n += 1

    c = by_name["Trickster's Stratagem"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.is_top_library = True
        c.role_features.is_other = False
        mode = _cast_mode(c)
        if not any(e.kind == "draw_cards" for e in mode.effects):
            mode.effects.append(DrawCardsEffect(n=1))
            mode.effects.append(DiscardCardEffect(n=1))
        c.role_features.cards_manipulated = 1
        _mark(
            c,
            "tuck ('second from the top or on the bottom' ~ Lost Days, §1) + connive "
            "rider as loot ('up to one target creature you control' — Limited decks "
            "reliably have a creature by the time this casts)",
        )
        n += 1

    c = by_name["Villainous Hideout"]
    if not any(FIX_MARKER in r for r in c.reasons):
        _mark(
            c,
            "owner ruling 2026-07-06: the {3},{T} connive activation is ignored — "
            "expensive land activations aren't modelled; mana abilities kept as parsed",
        )
        n += 1

    c = by_name["Atlantis Attacks"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.creates_creatures = [
            CreatureBody(
                power="6",
                toughness="5",
                colors=["U"],
                subtypes=["Leviathan"],
                keywords=["hexproof"],
            )
        ]
        c.role_features.is_bounce = True
        c.role_features.is_other = False
        mode = _cast_mode(c)
        if not mode.effects:
            mode.effects.append(NoopEffect(role_tag="modal_token_or_bounce"))
        _mark(
            c,
            "modal aggregation (§12): 'Target player creates' is self-targetable "
            "(Shredder's Revenge precedent, §18) → token body + is_bounce; teamwork "
            "chooses-both rider needs no extra flags",
        )
        n += 1

    c = by_name["Repulsor Blast"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.removal_burn_damage = 5
        c.role_features.removal_destroy_or_exile = False
        c.role_features.is_other = False
        _mark(
            c,
            "5 damage to target creature; teamwork rider adds face damage only, so "
            "the creature-burn value is unchanged (§16 teamwork = encode paid outcome)",
        )
        n += 1

    # Batch-2 finding: Deadly Dispute's fields contradicted its own
    # documented §16 ruling ("Kept is_other", no draw credit — the
    # sac-additional-cost isn't reliably payable per §9/Sewer-veillance
    # precedent). The 2026-06-23 encode wrote the reason but left the
    # parser-populated cards_drawn=2 + DrawCardsEffect in place. Align
    # the fields with the ruling.
    c = by_name["Deadly Dispute"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.cards_drawn = 0
        c.role_features.is_other = True
        mode = _cast_mode(c)
        mode.effects = [e for e in mode.effects if e.kind != "draw_cards"]
        if not mode.effects:
            mode.effects.append(NoopEffect(role_tag="sac_gated_draw_unencoded"))
        _mark(
            c,
            "fields aligned with the §16 ruling the reason already recorded "
            "(is_other, no draw credit — sac additional cost not reliably payable)",
        )
        n += 1

    c = by_name["Earth's Mightiest Heroes"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.is_other = True
        mode = _cast_mode(c)
        if not mode.effects:
            mode.effects.append(NoopEffect(role_tag="battlefield_tutor"))
        _mark(
            c,
            "6-mana reveal-8 battlefield tutor — outside the mulligan window; the "
            "revealed cards go to battlefield/graveyard, not hand, so no "
            "LookAtTopEffect (§15 is hand-fetch only)",
        )
        n += 1

    return n


def fix_tmt(cards: list[ParsedCard]) -> int:
    by_name = {c.name: c for c in cards}
    n = 0
    for name in ["Bebop, Warthog Warrior", "Rocksteady, Crash Courser"]:
        c = by_name[name]
        if any(FIX_MARKER in r for r in c.reasons):
            continue
        # Parsed encoding (cast + land_cycle fetch-to-hand) is already right;
        # the tribal lord statics aren't modelled. The card only reached
        # review because its typecycling reminder text trips the
        # discard-self fast-path guard.
        _mark(c, "lord static not modelled; cast + typecycling modes kept as parsed")
        n += 1
    return n


def fix_sos(cards: list[ParsedCard]) -> int:
    by_name = {c.name: c for c in cards}
    c = by_name["Akroma's Will"]
    if any(FIX_MARKER in r for r in c.reasons):
        return 0
    c.role_features.is_other = True
    mode = _cast_mode(c)
    if not mode.effects:
        mode.effects.append(NoopEffect(role_tag="mass_protection"))
    _mark(
        c,
        "mass-protection instant → is_other (§16 Heroic Intervention / Teferi's "
        "Protection precedent); caught by the fixed period-form modal regex",
    )
    return 1


def fix_ecl(cards: list[ParsedCard]) -> int:
    by_name = {c.name: c for c in cards}
    n = 0

    specs: list[tuple[str, str, bool]] = [
        # (name, note, is_other)
        (
            "Heat Shimmer",
            "copy-token body is unmodelable (no fixed P/T) → is_other; temporary "
            "haste token not worth a CreatureBody guess",
            True,
        ),
        ("Dolmen Gate", "combat-damage-prevention static → is_other", True),
        ("Painter's Servant", "color-changing static not modelled; plain creature", False),
        ("Idyllic Tutor", "tutor → is_other (§10 Splinter's Technique precedent)", True),
    ]
    for name, note, other in specs:
        c = by_name[name]
        if any(FIX_MARKER in r for r in c.reasons):
            continue
        if other:
            c.role_features.is_other = True
        mode = _cast_mode(c)
        if not mode.effects:
            mode.effects.append(NoopEffect(role_tag="unmodelled"))
        _mark(c, note)
        n += 1

    c = by_name["Manamorphose"]
    if not any(FIX_MARKER in r for r in c.reasons):
        mode = _cast_mode(c)
        if not any(e.kind == "draw_cards" for e in mode.effects):
            mode.effects.append(DrawCardsEffect(n=1))
        c.role_features.cards_drawn = 1
        c.role_features.is_other = False
        _mark(
            c,
            "mana-neutral cantrip: draw wired; the 2-mana production is NOT encoded "
            "(ProduceManaEffect on cast modes has no sim precedent — revisit if a "
            "ritual ever matters at mulligan time)",
        )
        n += 1

    return n


def main() -> None:
    total = 0
    for set_code, fixer in [
        ("MSH", fix_msh),
        ("TMT", fix_tmt),
        ("SOS", fix_sos),
        ("ECL", fix_ecl),
    ]:
        cards = load_parsed_cards(set_code)
        n = fixer(cards)
        if n:
            save_parsed_cards(set_code, cards)
        print(f"[{set_code}] patched {n} card(s)")
        total += n
    print(f"Done — {total} card(s) patched.")


if __name__ == "__main__":
    main()
