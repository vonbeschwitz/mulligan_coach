"""Resolve the two AUTO cards the named-token tripwire routes to review.

The named-token tripwire (``parser._flag_named_tokens``, added 2026-07-07)
demotes any card whose oracle text contains a "create <Name>, a … creature
token" phrase to NEEDS_LLM, because the count-anchored token matcher can't
parse the proper-noun form. Across all five sets only two currently-AUTO
cards carry that shape (the other named-token cards — Dark Depths, Falcon,
Ka-Zar — are already llm_encoded). Both are correctly resolved here so the
tripwire leaves the persisted data at 0 NEEDS_LLM:

* **White Tiger, Ava Ayala (MSH #196)** — the named token (The Tiger God)
  is created by a ``{5}{G}`` power-up. Per §19 an activated ability with
  cmc > 3 credits NO role_features, so the body is correctly absent.
  role_features unchanged (``is_creature`` only); just marked reviewed.

* **The Coming of Galactus (MSH #212)** — Saga. Chapter IV creates the
  named Galactus token; only chapter I is encoded (§6), so no body. But
  chapter I ("Destroy up to one target nonland permanent") IS removal and
  was being silently dropped: ``_parse_other_permanent``'s chunk loop (which
  the saga branch delegates to) doesn't run the destroy matcher on a direct
  action line, so the removal never got credited (a pre-existing gap,
  separate from named tokens). Encoded correctly here:
  ``removal_destroy_or_exile=True`` per §6, ``is_saga`` already set, no token.

Idempotent via the per-card fix marker.

Usage::

    uv run python scripts/audit/apply_named_token_reviews_20260707.py
"""

from __future__ import annotations

from mulligan_coach_cards import load_parsed_cards, save_parsed_cards
from mulligan_coach_cards.models import ParseStatus

FIX_MARKER = "named-token review 2026-07-07"


def main() -> None:
    cards = load_parsed_cards("MSH")
    by_name = {c.name: c for c in cards}
    patched = 0

    # White Tiger — token is in a {5}{G} power-up (cmc>3) → no body (§19).
    c = by_name["White Tiger, Ava Ayala"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: named token 'The Tiger God' is created by a {{5}}{{G}} power-up "
            "(cmc>3) → no creates_creatures per §19; is_creature only."
        )
        patched += 1

    # The Coming of Galactus — Saga: chapter I removal credited, chapter IV
    # named token not credited (§6).
    c = by_name["The Coming of Galactus"]
    if not any(FIX_MARKER in r for r in c.reasons):
        c.role_features.removal_destroy_or_exile = True
        c.status = ParseStatus.LLM_ENCODED
        c.reasons.append(
            f"{FIX_MARKER}: chapter I ('destroy up to one target nonland permanent') → "
            "removal_destroy_or_exile per §6 (the saga chunk loop doesn't run the destroy "
            "matcher, so it was silently dropped); chapter IV named 'Galactus' token → no "
            "body (later chapter, §6)."
        )
        patched += 1

    save_parsed_cards("MSH", cards)
    print(f"[MSH] patched {patched} card(s)")


if __name__ == "__main__":
    main()
