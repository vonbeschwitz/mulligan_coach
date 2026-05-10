"""Derived per-card and hand-level features for the Mulligan Coach pipeline.

Public surface kept small. The first inhabitant is sample-size shrinkage
of 17Lands per-card win rates; hand-level features land here as they're
built.
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

__all__ = [
    "DEFAULT_K_BASE",
    "DEFAULT_N_BINS",
    "FormatPriors",
    "PlayRateBins",
    "ShrunkWinRates",
    "compute_format_priors",
    "shrink_stats",
]
