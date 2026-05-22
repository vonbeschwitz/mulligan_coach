"""Arena ID → ``ParsedCard`` lookup for the overlay.

The log tailer emits raw arena_ids (the Arena ``grpId`` Arena assigns
to each printing). The recommender wants typed
:class:`mulligan_coach_cards.ParsedCard` instances. This module is the
join: it loads every persisted ``ParsedCard`` for the sets we care
about, indexes them by ``arena_id``, and exposes a single
:meth:`ArenaCardIndex.find` method.

We deliberately don't reuse the website's :class:`CardStore` —
``CardStore`` indexes by name and ``(set, collector_number)`` because
that's what an MTGA-pasted decklist gives you. The overlay only ever
sees arena_ids, so a much smaller dedicated index is the right
abstraction. Both indexes load from the same on-disk
``parsed_cards/<SET>.json`` artefacts; the work isn't duplicated, just
the keyed views.

Two arena_id sources are merged:

1. ``ParsedCard.arena_id`` — populated from MTGJSON at parse time. Reliable
   for older sets, mostly empty for the current Premier Draft rotation
   (MTGJSON lags weeks behind a set's Arena release).
2. The local MTGA client's ``Raw_CardDatabase`` SQLite — authoritative
   and always-current, but requires the MTGA client to be installed on
   the same machine the overlay runs on (which is the canonical use
   case). See :mod:`mulligan_coach_overlay.arena_card_db`.

The Arena-DB source covers art-treatment variants (each gets its own
grpId in Arena), so this is the only way to resolve, e.g., a showcase
printing the user drafted. Both sources flow into the same
``by_arena_id`` dict.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from mulligan_coach_cards import Cost, ManaAbility, ParsedCard, ParseStatus, load_parsed_cards
from mulligan_coach_cards.models import ManaOption
from mulligan_coach_cards.store import parsed_cards_dir

from .arena_card_db import default_card_database_path, load_arena_id_pairs

log = logging.getLogger(__name__)

# Basic-land name → produced color. Used to synthesise basics for
# arena_ids whose printing's set isn't in our parsed_cards (Arena ships
# basics from a dozen old sets; we don't keep parsed_cards for most).
_BASICS: tuple[tuple[str, ManaOption], ...] = (
    ("Plains", "W"),
    ("Island", "U"),
    ("Swamp", "B"),
    ("Mountain", "R"),
    ("Forest", "G"),
)


@dataclass(frozen=True)
class ArenaCardIndex:
    """Arena ID → ``ParsedCard`` lookup.

    Build once at process start via :meth:`build`. Lookups are O(1)
    dict access; the structure is immutable after construction.

    ``n_mtgjson_resolved`` and ``n_arena_db_resolved`` are diagnostic
    counts the headless / GUI layers surface in their startup banner
    so the user can see which source contributed what — useful when
    debugging "the overlay can't see my card".
    """

    by_arena_id: dict[int, ParsedCard]
    loaded_sets: list[str]
    n_mtgjson_resolved: int = 0
    n_arena_db_resolved: int = 0
    n_basic_resolved: int = 0
    arena_db_path: Path | None = None

    @classmethod
    def build(
        cls,
        set_codes: Iterable[str] | None = None,
        *,
        arena_db_path: Path | None = None,
    ) -> ArenaCardIndex:
        """Load every persisted card across ``set_codes`` and index by arena_id.

        ``set_codes=None`` means "every set with a persisted JSON on
        disk". ``arena_db_path=None`` triggers auto-detection of the
        local MTGA CardDatabase; pass an explicit path for tests, or
        to point at a captured copy of the file.
        """
        set_codes = _discover_sets() if set_codes is None else list(set_codes)

        # First load every set's parsed_cards. Keep a (SET, COLLECTOR_NUMBER)
        # index alongside the arena_id index — the Arena-DB augmentation
        # below joins by (set, collector_number).
        by_set_num: dict[tuple[str, str], ParsedCard] = {}
        by_arena_id: dict[int, ParsedCard] = {}
        loaded: list[str] = []
        for set_code in set_codes:
            try:
                cards = load_parsed_cards(set_code)
            except FileNotFoundError:
                log.info("no parsed_cards for %s; skipping", set_code)
                continue
            loaded.append(set_code)
            for card in cards:
                key = (card.set_code.upper(), card.collector_number)
                by_set_num.setdefault(key, card)
                if card.arena_id is not None:
                    by_arena_id.setdefault(card.arena_id, card)

        n_mtgjson = len(by_arena_id)

        # Synthesise the 5 basic lands. Arena's basics come from any of
        # ~20 sets (the user's collection determines which printing they
        # see); we'd need parsed_cards for every one of those sets to
        # cover them via (set, collector_number) lookup. Instead, build
        # one canonical ParsedCard per basic name and join the Arena DB
        # to it by name below.
        basics_by_name = {name: _synth_basic(name, color) for name, color in _BASICS}

        # Augment from Arena's local CardDatabase. setdefault preserves
        # whatever MTGJSON gave us; the Arena DB only fills gaps. This
        # ordering means we don't accidentally clobber a known-good
        # mapping with one that — in theory — could differ across data
        # sources (alt-art printings have different grpIds in Arena,
        # and MTGJSON might point at a different art treatment than the
        # one our (set, collector_number) join hits).
        db_path = arena_db_path or default_card_database_path()
        n_db_added = 0
        n_basic_added = 0
        if db_path is not None:
            try:
                records = load_arena_id_pairs(db_path)
            except Exception:
                log.exception("failed to read Arena CardDatabase at %s", db_path)
                records = []
            for set_code, collector_number, grpid, name in records:
                if grpid in by_arena_id:
                    continue  # Already indexed (MTGJSON wins).
                hit = by_set_num.get((set_code, collector_number))
                if hit is not None:
                    # Stamp the grpid onto a copy of the ParsedCard so
                    # downstream code (the recommender's per-card OH WR
                    # lookup, the feature builder's shrunk-stats join)
                    # has a usable arena_id even on new sets where
                    # MTGJSON hasn't ingested the printing yet.
                    # Always model_copy rather than mutate in place —
                    # the same `hit` instance can be the join target
                    # for multiple grpids (alt-art variants).
                    by_arena_id[grpid] = hit.model_copy(update={"arena_id": grpid})
                    n_db_added += 1
                    continue
                # Name-match fallback for basic lands. Any of the 20+
                # basic-land printings Arena ships maps to the one
                # synthesized basic for that name. Stamp the grpid the
                # same way so the recommender sees a populated
                # arena_id (in practice basics don't have shrunk OH
                # stats, but consistency keeps the invariant simple).
                basic = basics_by_name.get(name)
                if basic is not None:
                    by_arena_id[grpid] = basic.model_copy(update={"arena_id": grpid})
                    n_basic_added += 1
        else:
            log.info("no MTGA CardDatabase found; arena_id coverage limited to MTGJSON")

        return cls(
            by_arena_id=by_arena_id,
            loaded_sets=loaded,
            n_mtgjson_resolved=n_mtgjson,
            n_arena_db_resolved=n_db_added,
            n_basic_resolved=n_basic_added,
            arena_db_path=db_path,
        )

    def find(self, arena_id: int) -> ParsedCard | None:
        """Return the ``ParsedCard`` for an Arena ID, or ``None``."""
        return self.by_arena_id.get(arena_id)

    def resolve_all(self, arena_ids: Iterable[int]) -> tuple[list[ParsedCard], list[int]]:
        """Resolve a list of arena_ids. Returns (resolved, unresolved_ids).

        Used by the headless / GUI layer when handling a
        :class:`MulliganDecisionRequest`: if any hand card fails to
        resolve, the coordinator should surface that to the user
        ("missing encoding for card X") rather than silently dropping
        it from the recommendation input.
        """
        resolved: list[ParsedCard] = []
        missing: list[int] = []
        for aid in arena_ids:
            card = self.by_arena_id.get(aid)
            if card is None:
                missing.append(aid)
            else:
                resolved.append(card)
        return resolved, missing


def _discover_sets() -> list[str]:
    """List every set with persisted parsed_cards on disk."""
    directory = parsed_cards_dir()
    if not directory.exists():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if not p.stem.endswith("_needs_llm"))


def _synth_basic(name: str, color: ManaOption) -> ParsedCard:
    """Synthesise a basic-land ParsedCard.

    Mirrors the website's :func:`synthetic_basics` (``packages/website/
    src/mulligan_coach_website/data.py``). Same fields, same shape —
    deliberately duplicated rather than imported so the overlay doesn't
    take a FastAPI dep just to reach the synthesiser. If a third
    consumer ever needs the same, lift this into ``mulligan_coach_cards``.
    """
    return ParsedCard(
        name=name,
        # ``BASIC`` isn't a real set code, but nothing reads ``set_code``
        # off these — the recommender only looks at the gameplay fields.
        set_code="BASIC",
        collector_number=name,
        # Fixed all-zero UUIDs differing only in the last byte. Within
        # a single simulated game the engine assigns its own
        # ``instance_id`` per copy, so oracle_id collisions across
        # printings of the same basic don't matter.
        oracle_id=f"00000000-0000-0000-0000-{ord(color):012d}",
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land — {name}",
        types=["Land"],
        subtypes=[name],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )
