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

from .mana import ManaCost, Pip, parse_mana_cost
from .models import (
    Cost,
    CreatureBody,
    DrawCardsEffect,
    Effect,
    EntersBattlefieldEffect,
    FetchLandEffect,
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

__all__ = [
    "Cost",
    "CreatureBody",
    "DrawCardsEffect",
    "Effect",
    "EntersBattlefieldEffect",
    "FetchLandEffect",
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
    "parse_card",
    "parse_mana_cost",
]
