"""Monte Carlo goldfish simulator for MTG Limited mulligan decisions.

Public surface — keep small. Most types are package-private; only the
ones a downstream caller (`packages/model`, the website, the overlay)
needs are re-exported here.
"""

from .bottoming import OhWrLookup, bottom_card
from .monte_carlo import iter_traces, simulate
from .mulligan import post_mulligan_hand, simulate_mulligan_from_deck
from .smoother import draw_smoothed_hand
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
    "OhWrLookup",
    "ScryEvent",
    "SpellCastEvent",
    "TurnSnapshot",
    "aggregate_game_level",
    "bottom_card",
    "check_deck_encodings",
    "draw_smoothed_hand",
    "iter_traces",
    "post_mulligan_hand",
    "pretty_print",
    "simulate",
    "simulate_mulligan_from_deck",
]
