"""In-memory card store for the website.

Mirrors :mod:`simulation_viewer.data` — load every ``ParsedCard`` we
have on disk for the configured Premier-Draft sets, build name + id
indices, and synthesise the five basic lands (which the parser never
emits, but every Limited decklist references). The website is in
`packages/`, not `utilities/`, so we duplicate the small amount of
code rather than reach into a sibling utility. The duplication is
intentional and cheap; if the patterns diverge later we can refactor
into a shared module under `packages/cards/`.

The store is built once at app startup (inside the FastAPI lifespan)
and stashed on ``app.state`` for the request handlers to read.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field

from mulligan_coach_cards import (
    Cost,
    ManaAbility,
    ParsedCard,
    ParseStatus,
    load_parsed_cards,
)
from mulligan_coach_cards.models import ManaOption
from mulligan_coach_cards.store import parsed_cards_dir

# ---------------------------------------------------------------------------
# Set discovery
# ---------------------------------------------------------------------------


def _sets_from_env() -> list[str] | None:
    """Return the explicit override list, or ``None`` to mean "auto-detect".

    ``MULLIGAN_COACH_WEBSITE_SETS=TLA`` (or ``TMT,ECL,TLA,SOS``) lets
    the user narrow which sets get loaded — useful when iterating on
    one specific format. Empty / unset → auto-detect every set with a
    persisted ``data/processed/parsed_cards/<SET>.json``.
    """
    raw = os.environ.get("MULLIGAN_COACH_WEBSITE_SETS", "").strip()
    if not raw:
        return None
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def discover_sets() -> list[str]:
    """List every set whose parsed cards have been persisted.

    Returns ``[]`` on a fresh checkout where ``run-detector`` hasn't
    been invoked yet — the caller can then surface a helpful "no
    cards loaded" state instead of crashing.
    """
    override = _sets_from_env()
    if override is not None:
        return override

    directory = parsed_cards_dir()
    if not directory.exists():
        return []
    # Skip the `_needs_llm` companion JSONs the detector emits — they're
    # the not-yet-encoded subset of a set, indexed under the same stem
    # plus a suffix.
    return sorted(p.stem for p in directory.glob("*.json") if not p.stem.endswith("_needs_llm"))


# ---------------------------------------------------------------------------
# Basic-land synthesis
# ---------------------------------------------------------------------------


_BASICS: tuple[tuple[str, ManaOption, str], ...] = (
    ("Plains", "W", "Plains"),
    ("Island", "U", "Island"),
    ("Swamp", "B", "Swamp"),
    ("Mountain", "R", "Mountain"),
    ("Forest", "G", "Forest"),
)


def _basic(name: str, color: ManaOption, subtype: str) -> ParsedCard:
    """Construct a basic-land ``ParsedCard``.

    Mirrors ``packages/simulation/tests/_factories.py:_basic`` so the
    simulator sees exactly the shape it knows how to handle: one
    untapped tap-for-color mana ability, no cast cost, no modes,
    ``status=AUTO``.
    """
    return ParsedCard(
        name=name,
        # ``BASIC`` isn't a real set code, but the basics live in
        # ``by_name`` only — nothing here ever gets indexed by id.
        set_code="BASIC",
        collector_number=name,
        # Fixed all-zero UUIDs differing only in the last byte. Within
        # a single simulated game the engine assigns its own
        # ``instance_id`` per copy, so oracle_id collisions across
        # printings of the same basic don't matter.
        oracle_id=f"00000000-0000-0000-0000-{ord(color):012d}",
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land — {subtype}",
        types=["Land"],
        subtypes=[subtype],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def synthetic_basics() -> list[ParsedCard]:
    """Return the five English basic lands as fully-encoded ``ParsedCard``s."""
    return [_basic(name, color, subtype) for name, color, subtype in _BASICS]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def _name_keys(name: str) -> Iterable[str]:
    """Yield every lower-cased lookup key for a card name.

    For DFCs, Scryfall's joint name (``"X // Y"``) is what
    ``ParsedCard.name`` carries, but MTGA exports often use just the
    front face. Indexing both forms means either spelling resolves.
    """
    yield name.lower()
    if " // " in name:
        front, _, _ = name.partition(" // ")
        front = front.strip()
        if front:
            yield front.lower()


@dataclass(frozen=True)
class CardStore:
    """All loaded cards plus the two indices the website needs.

    ``entries`` is the flat list (used by the validation panel to count
    cards). ``by_id`` resolves MTGA's ``(SET) NUMBER`` suffix exactly.
    ``by_name`` is the fallback for older exports without the suffix
    and for the autocomplete card-name input.
    """

    entries: list[ParsedCard]
    by_id: dict[tuple[str, str], ParsedCard] = field(default_factory=dict)
    by_name: dict[str, ParsedCard] = field(default_factory=dict)
    loaded_sets: list[str] = field(default_factory=list)

    @classmethod
    def build(cls, set_codes: list[str] | None = None) -> CardStore:
        """Load every persisted card in *set_codes* plus synthetic basics.

        ``set_codes=None`` means "discover from disk" via
        :func:`discover_sets`. The discovered list is preserved in
        ``loaded_sets`` so the index page can show the user what's
        available.
        """
        if set_codes is None:
            set_codes = discover_sets()

        entries: list[ParsedCard] = []
        for set_code in set_codes:
            entries.extend(load_parsed_cards(set_code))
        entries.extend(synthetic_basics())

        by_id: dict[tuple[str, str], ParsedCard] = {}
        by_name: dict[str, ParsedCard] = {}
        for card in entries:
            # Skip indexing synthetic basics by id — their fake "BASIC"
            # set_code shouldn't shadow real (TLA, 270) lookups for
            # actual basic-land printings on disk.
            if card.set_code != "BASIC":
                by_id.setdefault(
                    (card.set_code.upper(), card.collector_number),
                    card,
                )
            for key in _name_keys(card.name):
                # ``setdefault`` so the first set we load wins on name
                # collisions across reprints — deterministic but
                # otherwise unimportant for our use case.
                by_name.setdefault(key, card)

        return cls(
            entries=entries,
            by_id=by_id,
            by_name=by_name,
            loaded_sets=list(set_codes),
        )

    def find(
        self,
        *,
        name: str,
        set_code: str | None = None,
        collector_number: str | None = None,
    ) -> ParsedCard | None:
        """Resolve one decklist line to a ``ParsedCard``, or ``None``.

        Lookup order:

        1. If both ``set_code`` and ``collector_number`` are given,
           try the exact id index. Hit returns immediately.
        2. Otherwise (or on miss): the name index, with the DFC
           front-face fallback handled in :func:`_name_keys`.

        The name match is case-insensitive; whitespace is *not*
        normalised, so a stray double-space in the pasted decklist
        will miss. The decklist parser strips trailing whitespace
        before calling this.
        """
        if set_code and collector_number:
            hit = self.by_id.get((set_code.upper(), collector_number))
            if hit is not None:
                return hit
        return self.by_name.get(name.lower())
