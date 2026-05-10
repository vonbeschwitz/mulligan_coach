"""Derived per-card and hand-level features for the Mulligan Coach pipeline.

Public surface kept small. Sample-size shrinkage of 17Lands per-card
win rates lives in :mod:`seventeenlands_shrinkage`; the matching
z-score computation in :mod:`seventeenlands_zscores`. The 196-feature
XGBoost row builder lives in :mod:`feature_builder` (with per-card
classification predicates in :mod:`categories`).
"""

from __future__ import annotations

from . import categories
from .feature_builder import (
    DEFAULT_KNOWN_EVENT_TYPES,
    DEFAULT_KNOWN_SETS,
    build_context_features,
    build_deck_features,
    build_feature_row,
    build_hand_features,
    build_simulation_features,
)
from .seventeenlands_shrinkage import (
    DEFAULT_K_BASE,
    DEFAULT_N_BINS,
    FormatPriors,
    PlayRateBins,
    ShrunkWinRates,
    compute_format_priors,
    shrink_stats,
)
from .seventeenlands_zscores import (
    CardZScores,
    FormatWRDistribution,
    compute_format_wr_distribution,
    zscore_stats,
)

__all__ = [
    "DEFAULT_KNOWN_EVENT_TYPES",
    "DEFAULT_KNOWN_SETS",
    "DEFAULT_K_BASE",
    "DEFAULT_N_BINS",
    "CardZScores",
    "FormatPriors",
    "FormatWRDistribution",
    "PlayRateBins",
    "ShrunkWinRates",
    "build_context_features",
    "build_deck_features",
    "build_feature_row",
    "build_hand_features",
    "build_simulation_features",
    "categories",
    "compute_format_priors",
    "compute_format_wr_distribution",
    "shrink_stats",
    "zscore_stats",
]
