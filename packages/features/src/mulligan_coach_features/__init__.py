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
from .stats_join import fold_card_name, stats_for_card

# ---------------------------------------------------------------------------
# Feature semantics version
# ---------------------------------------------------------------------------
#
# Bump this integer in the SAME PR as any change to the *values* or the
# *column set* that ``build_feature_row`` emits for a fixed set of inputs.
# That includes:
#   * adding / removing / renaming feature columns,
#   * changing how any existing feature is computed (the deck-wide
#     castability change is the canonical example — same column names,
#     shifted distribution),
#   * changing ``DEFAULT_KNOWN_SETS`` / ``DEFAULT_KNOWN_EVENT_TYPES`` (the
#     one-hot context vocabulary is part of the row's meaning).
#
# Like ``SIMULATION_SEMANTICS_VERSION``, this is stamped into the
# feature-cache ``_meta.json`` sidecars and the model ``metadata.json`` so
# stale caches and train/serve skew are caught rather than silently
# corrupting predictions.
#
# Version history:
#   1 -> 2 (roadmap Step 2): appended SOS + MSH to ``DEFAULT_KNOWN_SETS``.
#     Adds two ``set_code_*`` columns and changes SOS rows' one-hot values
#     (SOS was previously the all-zero reference category), so it is a
#     value/column-set change and must bump.
#   2 -> 3 (roadmap Step 5): 17Lands stats join re-keyed from arena_id
#     (MTGJSON-dependent) to folded card name. Per-card WR / z-score
#     features now populate for every card with a ratings row; under v2
#     they were zero whenever MTGJSON lacked the printing's arena_id (all
#     main-set cards of TMT/ECL/TLA/SOS at materialisation time). The
#     column set is unchanged, but the *values* of the ``avg_*_wr_*`` /
#     ``max_*_wr_*`` / Z-bucket / castability ``*_high_oh_*`` features
#     shift, so it is a value change and must bump.
#   3 -> 4 (HOB rotation, 2026-08): appended HOB to ``DEFAULT_KNOWN_SETS``.
#     Adds one ``set_code_HOB`` column (all-zero in every existing cache —
#     no 17Lands HOB data exists yet), so it is a column-set change and
#     must bump. Existing v3 caches are upgraded in place by the
#     ``set_onehots_v2`` migration in ``mulligan_coach_model.cache_patch``
#     (no re-simulation needed).
FEATURES_SEMANTICS_VERSION: int = 4

__all__ = [
    "DEFAULT_KNOWN_EVENT_TYPES",
    "DEFAULT_KNOWN_SETS",
    "DEFAULT_K_BASE",
    "DEFAULT_N_BINS",
    "FEATURES_SEMANTICS_VERSION",
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
    "fold_card_name",
    "shrink_stats",
    "stats_for_card",
    "zscore_stats",
]
