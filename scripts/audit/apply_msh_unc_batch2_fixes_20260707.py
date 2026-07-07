"""Apply the MSH uncommons batch-2 (cards 21-60 alphabetically) audit fixes.

Findings from the 2026-07-07 review of MSH uncommons #21 Dark Deed
through #60 Okoye (audit log: ``scripts/audit/MSH_commons_recheck.md``).
Four cards, all missed by deterministic parser gaps (named-token
templating and qualifier-heavy bounce/Role text):

1. **Falcon, Winged Wonder (MSH #52)** — "When Falcon enters, create
   Redwing, a legendary 1/1 blue Bird Scout creature token with flying
   …". Self-ETB token (§4 counts it), but the *named*-token templating
   ("create Redwing, a legendary 1/1 …") doesn't match
   ``_CREATE_TOKEN_RE`` (which anchors on "create a|an|one|… N/N …").
   Record ``creates_creatures=[1/1 U Bird Scout, flying]``. The token's
   own attack-surveil ability is not an evergreen keyword, so only
   flying lands in ``keywords``.

2. **Justice, Vance Astrovik (MSH #61)** — "When Justice enters, return
   up to one target nonland, nontoken permanent to its owner's hand."
   Self-ETB bounce (§1 is_bounce), but the "nonland, **nontoken**
   permanent" qualifier (and the comma) breaks ``_BOUNCE_RE`` (which
   allows "nonland permanent" but not the nontoken form). Set
   ``is_bounce=True``.

3. **Ka-Zar of the Savage Land (MSH #174)** — "When Ka-Zar enters,
   create Zabu, a legendary 2/2 green Cat creature token …". Same
   named-token gap as Falcon. Record ``creates_creatures=[2/2 G Cat]``
   (Zabu's landfall ability is not an evergreen keyword → no keywords).

4. **Monstrous Rage (MSH #bonus-woe-142)** — instant, "Target creature
   gets +2/+0 until end of turn. Create a Monster Role token attached
   to it. (… Enchanted creature gets +1/+1 and has trample.)". The
   parser captured only the explicit "+2/+0 until end of turn" pump and
   missed the Monster Role's +1/+1 + trample (a WOE Role mechanic it
   doesn't model). Fold the Role into the combat trick per the
   Saved-by-the-Shell counter-as-pump logic (§3): total this combat is
   +3/+1 and trample → ``combat_trick_power=3, combat_trick_toughness=1,
   combat_trick_granted_keywords=['trample']``.

The other 36 cards in the batch were clean. Notable holds worth a
breadcrumb (no change): Killmonger's ETB destroy is sac-gated (General
Traag precedent, §1 — kept off); Madame Hydra / Madame Masque's
recurring token triggers correctly credit nothing (§4); Light of
Promise stays ``is_other`` (recurring value-engine aura, Super
Intelligence precedent §16); Kang's attack-trigger connive stays
unencoded (§19).

Idempotent via the per-card fix marker.

Usage::

    uv run python scripts/audit/apply_msh_unc_batch2_fixes_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import CreatureBody, ParseStatus

FIX_MARKER = "unc-batch2 fix 2026-07-07"


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}
    patched = 0

    # 1. Falcon, Winged Wonder — self-ETB named token (parser named-token gap).
    c = by_name["Falcon, Winged Wonder"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.creates_creatures = [
            CreatureBody(
                power="1",
                toughness="1",
                colors=["U"],
                subtypes=["Bird", "Scout"],
                keywords=["flying"],
            )
        ]
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: self-ETB token 'Redwing' (1/1 U Bird Scout, flying) recorded "
            "per §4; named-token templating ('create Redwing, a legendary 1/1 …') slips "
            "past _CREATE_TOKEN_RE. Redwing's attack-surveil is not an evergreen keyword."
        )
        patched += 1

    # 2. Justice, Vance Astrovik — self-ETB bounce (nontoken qualifier gap).
    c = by_name["Justice, Vance Astrovik"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.is_bounce = True
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: self-ETB bounce ('return up to one target nonland, nontoken "
            "permanent to its owner's hand') → is_bounce per §1; the 'nonland, nontoken' "
            "qualifier breaks _BOUNCE_RE."
        )
        patched += 1

    # 3. Ka-Zar of the Savage Land — self-ETB named token (same gap as Falcon).
    c = by_name["Ka-Zar of the Savage Land"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.creates_creatures = [
            CreatureBody(power="2", toughness="2", colors=["G"], subtypes=["Cat"], keywords=[])
        ]
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: self-ETB token 'Zabu' (2/2 G Cat) recorded per §4; named-token "
            "templating slips past _CREATE_TOKEN_RE. Zabu's landfall ability is not an "
            "evergreen keyword."
        )
        patched += 1

    # 4. Monstrous Rage — fold the Monster Role (+1/+1, trample) into the trick.
    c = by_name["Monstrous Rage"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.combat_trick_power = 3
        c.role_features.combat_trick_toughness = 1
        c.role_features.combat_trick_granted_keywords = ["trample"]
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: fold the Monster Role token (+1/+1, trample) into the combat "
            "trick per the Saved-by-the-Shell counter-as-pump logic (§3): +2/+0 (EOT) + Role "
            "+1/+1 trample → combat_trick 3/1 ['trample']. The WOE Role mechanic isn't parsed."
        )
        patched += 1

    save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {patched} card(s)")


if __name__ == "__main__":
    main()
