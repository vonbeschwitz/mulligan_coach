"""Monte Carlo goldfish simulator for MTG Limited mulligan decisions.

Public surface — keep small. Most types are package-private; only the
ones a downstream caller (`packages/model`, the website, the overlay)
needs are re-exported here.
"""

from .monte_carlo import iter_traces, simulate
from .stats import AggregateStats, CardStats, GameLevelStats, ModeStats, aggregate_game_level
from .trace import (
    ActionEvent,
    CastabilityRecord,
    DrawEvent,
    GameTrace,
    InstanceOutcome,
    LandDropEvent,
    ModeFirstTurn,
    ScryEvent,
    SpellCastEvent,
    TurnSnapshot,
    pretty_print,
)
from .validate import DeckEncodingError, check_deck_encodings

__all__ = [
    "ActionEvent",
    "AggregateStats",
    "CardStats",
    "CastabilityRecord",
    "DeckEncodingError",
    "DrawEvent",
    "GameLevelStats",
    "GameTrace",
    "InstanceOutcome",
    "LandDropEvent",
    "ModeFirstTurn",
    "ModeStats",
    "ScryEvent",
    "SpellCastEvent",
    "TurnSnapshot",
    "aggregate_game_level",
    "check_deck_encodings",
    "iter_traces",
    "pretty_print",
    "simulate",
]
