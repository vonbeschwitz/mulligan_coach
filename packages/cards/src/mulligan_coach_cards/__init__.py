"""Card representation package.

Public surface, kept deliberately small so downstream packages (simulation,
model, overlay) only need to know about a handful of names:

* ``parse_card`` — turn a Scryfall card dict into a typed ``ParsedCard``.
* ``ParsedCard`` / ``ParseStatus`` — the result type and its enum.
* ``ManaCost`` / ``Pip`` — parsed mana-cost representation.
* The simulator-facing models: ``Cost``, ``Mode``, ``Effect`` discriminants,
  ``ManaAbility``, ``Predicate``.
* The XGBoost-facing models: ``RoleFeatures``, ``CreatureBody``.

The deterministic parser handles the easy cases and explicitly flags
anything complex with ``ParseStatus.NEEDS_LLM`` plus a list of reasons.
A future LLM classifier will pick up the slack for those cards; for now
we just record which cards need it.
"""

from __future__ import annotations

from .loader import load_all_cards, load_arena_id_index
from .mana import ManaCost, Pip, parse_mana_cost
from .models import (
    Cost,
    CreatureBody,
    DiscardCardEffect,
    DrawCardsEffect,
    Effect,
    EntersBattlefieldEffect,
    FetchLandEffect,
    LookAtTopEffect,
    ManaAbility,
    Mode,
    NoopEffect,
    ParsedCard,
    ParseStatus,
    Predicate,
    ProduceManaEffect,
    RoleFeatures,
    SacrificeSpec,
    ScryEffect,
)
from .parser import parse_card
from .seventeenlands_stats import (
    SeventeenLandsStats,
    StatsLookup,
    load_premier_draft_stats,
    ratings_parquet_path,
)
from .store import (
    cards_by_status,
    load_parsed_cards,
    merge_detector_run,
    parsed_cards_path,
    save_parsed_cards,
    status_histogram,
    update_parsed_card,
)

__all__ = [
    "Cost",
    "CreatureBody",
    "DiscardCardEffect",
    "DrawCardsEffect",
    "Effect",
    "EntersBattlefieldEffect",
    "FetchLandEffect",
    "LookAtTopEffect",
    "ManaAbility",
    "ManaCost",
    "Mode",
    "NoopEffect",
    "ParseStatus",
    "ParsedCard",
    "Pip",
    "Predicate",
    "ProduceManaEffect",
    "RoleFeatures",
    "SacrificeSpec",
    "ScryEffect",
    "SeventeenLandsStats",
    "StatsLookup",
    "cards_by_status",
    "load_all_cards",
    "load_arena_id_index",
    "load_parsed_cards",
    "load_premier_draft_stats",
    "merge_detector_run",
    "parse_card",
    "parse_mana_cost",
    "parsed_cards_path",
    "ratings_parquet_path",
    "save_parsed_cards",
    "status_histogram",
    "update_parsed_card",
]
