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

# ---------------------------------------------------------------------------
# Simulator semantics version
# ---------------------------------------------------------------------------
#
# Bump this integer in the SAME PR as ANY change that alters ``simulate()``
# output for a fixed ``(hand, library, on_the_play, seed)`` — i.e. anything
# that would make the equivalence harness
# (``packages/simulation/scripts/equivalence_harness.py``) report a diff:
# policy changes, effect resolution, new mechanics, or changes to how the
# RNG is consumed.
#
# Do NOT bump for formatting / performance changes that keep bit-identical
# output — those are exactly the changes the equivalence harness exempts
# (verify with ``--save`` before / ``--check`` after; if every aggregate
# field and trace hash matches, no bump).
#
# This constant is stamped into the feature-cache ``_meta.json`` sidecars
# (see ``mulligan_coach_model.versioning``) so a training run can refuse to
# stitch two simulator semantics into one model, and warned about at bundle
# load when the running code no longer matches the trained model.
SIMULATION_SEMANTICS_VERSION: int = 1

__all__ = [
    "SIMULATION_SEMANTICS_VERSION",
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
