"""Shared keep/mulligan recommendation service.

Two consumer groups: the FastAPI website (``packages/website``) and
the PyQt6 overlay (``packages/overlay``). The model package's
analysis scripts also reach in here for the asymmetric / cache
machinery.

Public surface kept stable across the move out of
``mulligan_coach_website.recommendation`` — callers update their
import path and nothing else.
"""

from __future__ import annotations

from .service import (
    CHOICE_CLEAR_KEEP_THRESHOLD,
    CHOICE_MARGINAL_KEEP_THRESHOLD,
    CHOICE_MARGINAL_MULL_THRESHOLD,
    DEFAULT_N_MULLIGAN_SAMPLES,
    DEFAULT_N_SIMS_CHOICE,
    DEFAULT_N_SIMS_KEEP,
    DEFAULT_N_SIMS_PER_MULLIGAN,
    MARGIN_THRESHOLD,
    MULLIGAN_BIAS,
    AsymmetricRecommendation,
    ChoiceRecommendation,
    FormatStats,
    HandCardPlayability,
    MulliganArmResult,
    RecommendationExplanation,
    RecommendationService,
    ServiceStatus,
    _deck_signature,
    _MulliganCacheKey,
    _stable_seed,
    load_service,
)

__all__ = [
    "CHOICE_CLEAR_KEEP_THRESHOLD",
    "CHOICE_MARGINAL_KEEP_THRESHOLD",
    "CHOICE_MARGINAL_MULL_THRESHOLD",
    "DEFAULT_N_MULLIGAN_SAMPLES",
    "DEFAULT_N_SIMS_CHOICE",
    "DEFAULT_N_SIMS_KEEP",
    "DEFAULT_N_SIMS_PER_MULLIGAN",
    "MARGIN_THRESHOLD",
    "MULLIGAN_BIAS",
    "AsymmetricRecommendation",
    "ChoiceRecommendation",
    "FormatStats",
    "HandCardPlayability",
    "MulliganArmResult",
    "RecommendationExplanation",
    "RecommendationService",
    "ServiceStatus",
    # Semi-public: see CLAUDE.md "Privately re-exported helpers".
    "_MulliganCacheKey",
    "_deck_signature",
    "_stable_seed",
    "load_service",
]
