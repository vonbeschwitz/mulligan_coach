"""Derived per-card and hand-level features for the Mulligan Coach pipeline.

Public surface kept small. Sample-size shrinkage of 17Lands per-card
win rates lives in :mod:`seventeenlands_shrinkage`; the matching
z-score computation in :mod:`seventeenlands_zscores`. Hand-level
features land here as they're built.
"""

from __future__ import annotations

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
    "DEFAULT_K_BASE",
    "DEFAULT_N_BINS",
    "CardZScores",
    "FormatPriors",
    "FormatWRDistribution",
    "PlayRateBins",
    "ShrunkWinRates",
    "compute_format_priors",
    "compute_format_wr_distribution",
    "shrink_stats",
    "zscore_stats",
]
