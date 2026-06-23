"""Configuration for which 17Lands datasets to download.

Two pieces of configuration:

1. **Event types.** The four MTG Arena Limited event types we care about.
   Cube is intentionally excluded — the Mulligan Coach project is scoped to
   Premier Draft and Sealed (and their traditional / best-of-3 variants).

2. **Set codes.** A list of MTG set codes (e.g. ``"FIN"``, ``"TDM"``,
   ``"DFT"``). The defaults below should be the three most-recent
   Premier-Draft-supported sets at the time of writing — they are deliberately
   easy for the user to override at the CLI with ``--sets``.

Pydantic models give us cheap validation at config-load time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The four event types we ingest. Tuple order is also the canonical order
# used in CLI defaults and in iteration.
EventType = Literal["PremierDraft", "TradDraft", "Sealed", "TradSealed"]

DEFAULT_EVENT_TYPES: tuple[EventType, ...] = (
    "PremierDraft",
    "TradDraft",
    "Sealed",
    "TradSealed",
)

# Default set codes — the most recent Premier-Draft-supported sets as of
# 2026-06-21. SHOULD be reviewed before each download run, since new sets
# release every few months. Override at the CLI with --sets.
#
# The three-letter set codes are the ones 17Lands uses in dataset filenames
# (e.g. ``game_data_public.TLA.PremierDraft.csv.gz``). MSH ("Marvel Super
# Heroes") releases on Arena 2026-06-23; its bonus sheet (Scryfall code MAR)
# is drafted as part of MSH packs and has no separate 17Lands dataset, so it
# is NOT listed here — see packages/cards' run-detector --sets MSH,MAR
# instead, which reads MAR cards straight from the Scryfall snapshot.
DEFAULT_SETS: tuple[str, ...] = ("TMT", "ECL", "TLA", "SOS", "MSH")


class DownloadSelection(BaseModel):
    """Validated selection of (sets x event_types) to download."""

    model_config = ConfigDict(frozen=True)

    sets: tuple[str, ...] = Field(default=DEFAULT_SETS, min_length=1)
    event_types: tuple[EventType, ...] = Field(default=DEFAULT_EVENT_TYPES, min_length=1)

    @field_validator("sets", mode="before")
    @classmethod
    def _normalize_sets(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = [s.strip() for s in value.split(",") if s.strip()]
        if not isinstance(value, (list, tuple)):
            raise TypeError("sets must be a string or sequence of strings")
        normalized: list[str] = []
        for s in value:
            if not isinstance(s, str):
                raise TypeError("set codes must be strings")
            code = s.strip().upper()
            if not code.isalnum():
                raise ValueError(f"invalid set code: {s!r}")
            normalized.append(code)
        return tuple(normalized)

    @field_validator("event_types", mode="before")
    @classmethod
    def _normalize_event_types(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, str):
            value = [s.strip() for s in value.split(",") if s.strip()]
        if not isinstance(value, (list, tuple)):
            raise TypeError("event_types must be a string or sequence of strings")
        normalized: list[str] = []
        for s in value:
            if not isinstance(s, str):
                raise TypeError("event types must be strings")
            normalized.append(s.strip())
        return tuple(normalized)

    def pairs(self) -> list[tuple[str, EventType]]:
        """All (set, event_type) combinations to download."""
        return [(s, e) for s in self.sets for e in self.event_types]


# 17Lands public dataset URLs. The project's public_datasets page documents
# these patterns; they have been stable for years.
SEVENTEENLANDS_GAME_URL = (
    "https://17lands-public.s3.amazonaws.com/analysis_data/game_data/"
    "game_data_public.{set_code}.{event_type}.csv.gz"
)
# Card-ratings JSON is served by the 17Lands site directly.
SEVENTEENLANDS_RATINGS_URL = (
    "https://www.17lands.com/card_ratings/data?expansion={set_code}&format={event_type}"
)

# Scryfall bulk-data API. We discover the actual download URL via this
# endpoint rather than hardcoding a daily-changing CDN URL.
SCRYFALL_BULK_DATA_URL = "https://api.scryfall.com/bulk-data"

# MTGJSON publishes a file specifically for cross-source ID mapping. It is
# much smaller than AllPrintings.json and is all we need for arena_id lookup.
MTGJSON_ALL_IDENTIFIERS_URL = "https://mtgjson.com/api/v5/AllIdentifiers.json"
