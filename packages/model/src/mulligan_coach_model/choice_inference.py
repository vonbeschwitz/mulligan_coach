"""Inference for the choice (keep/mull) model.

Sister of :mod:`inference`. The win model predicts P(win | hand);
this module predicts P(keep | hand) — i.e. how likely a *good
player* (per the upstream skill filter) is to keep this hand.

The two probabilities answer different questions:

* P(win) — "would keeping this hand result in winning the game?"
  Useful for raw expected-value reasoning.
* P(keep) — "what would a competent player decide to do here?"
  Useful as a sanity check, an ensemble component, or a primary
  signal when downstream callers care about decision similarity
  rather than outcome.

Both share the same upstream simulate -> build_feature_row pipeline,
so calling them together for the same hand requires only one
simulation pass. The shared service composing both lives in
``packages/recommend``; this module exposes the choice-side
prediction primitives that service consumes.

No baseline residualization
----------------------------

Unlike :func:`inference.predict_win_probability`, this entry point
has no baseline term. The choice model is fit on raw XGBoost
(see :mod:`choice_train`), so inference is a direct
booster.predict call with no per-row margin shift.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import xgboost as xgb
from mulligan_coach_cards import ParsedCard
from mulligan_coach_features import (
    CardZScores,
    ShrunkWinRates,
    build_feature_row,
)
from mulligan_coach_simulation import simulate

from .choice_train import ChoiceTrainResult, load_choice_train_result
from .feature_matrix import _library_from_deck
from .versioning import compute_version_warning

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChoiceModelBundle:
    """Loaded artifacts for choice-model inference.

    Built by :meth:`load` from a model directory (output of
    :func:`save_choice_train_result`) or from an in-memory
    :class:`ChoiceTrainResult` (the tests path).

    ``version_warning`` is a non-fatal note set at load time when the
    model's recorded pipeline versions don't match the live simulator +
    feature code (or are absent, for pre-Step-1 models). It never blocks
    loading.
    """

    booster: xgb.Booster
    feature_names: tuple[str, ...]
    best_iteration: int
    version_warning: str | None = None

    @classmethod
    def load(cls, model_dir: Path) -> ChoiceModelBundle:
        """Load the fitted choice model from ``model_dir``.

        Expects ``xgboost.json`` + ``metadata.json``. No baseline.json
        — the choice model doesn't use one.
        """
        result = load_choice_train_result(model_dir)
        bundle = cls.from_train_result(result)
        if bundle.version_warning is not None:
            log.warning("ChoiceModelBundle.load(%s): %s", model_dir, bundle.version_warning)
        return bundle

    @classmethod
    def from_train_result(cls, result: ChoiceTrainResult) -> ChoiceModelBundle:
        return cls(
            booster=result.booster,
            feature_names=result.metadata.feature_names,
            best_iteration=result.metadata.best_iteration,
            version_warning=compute_version_warning(result.metadata.pipeline_versions),
        )


# ---------------------------------------------------------------------------
# Internal: feature dict -> P(keep)
# ---------------------------------------------------------------------------


def _predict_proba(bundle: ChoiceModelBundle, row: dict[str, float]) -> float:
    """Run one already-built feature dict through XGBoost.

    Missing columns default to ``np.nan`` (XGBoost native missing
    path), so an older feature builder against a newer model still
    runs — same forward-compatibility story as the win-model
    inference path.
    """
    values = np.array(
        [[row.get(name, np.nan) for name in bundle.feature_names]],
        dtype=float,
    )
    dm = xgb.DMatrix(values, feature_names=list(bundle.feature_names))
    iter_range = (0, bundle.best_iteration + 1)
    proba = bundle.booster.predict(dm, iteration_range=iter_range)
    return float(proba[0])


# ---------------------------------------------------------------------------
# Public: predict_keep_probability
# ---------------------------------------------------------------------------


def predict_keep_probability(
    bundle: ChoiceModelBundle,
    *,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    on_the_play: bool,
    mulligan_number: int,
    opp_mulligan_number: int | None,
    event_type: str,
    set_code: str,
    shrunk: dict[str, ShrunkWinRates],
    zscores: dict[str, CardZScores],
    n_sims: int = 1000,
    seed: int | None = None,
) -> float:
    """Predict ``P(skilled player would keep this hand)``.

    Parameters mirror :func:`inference.predict_win_probability` so the
    two models can share a single feature-build pass at the call site.
    Returns a probability in ``[0, 1]``.
    """
    library = _library_from_deck(tuple(hand), tuple(deck))
    aggregate = simulate(
        list(hand),
        list(library),
        on_the_play=on_the_play,
        n_runs=n_sims,
        seed=seed,
    )
    row = build_feature_row(
        hand=list(hand),
        deck=list(deck),
        aggregate_stats=aggregate,
        shrunk=shrunk,
        zscores=zscores,
        on_the_play=on_the_play,
        mulligan_number=mulligan_number,
        event_type=event_type,
        set_code=set_code,
    )
    # Conditional opp_mulligan feature — same shape the choice-model
    # cache used during training. NaN on the play, value on the draw.
    if on_the_play or opp_mulligan_number is None:
        row["opp_mulligan_count_if_known"] = float("nan")
    else:
        row["opp_mulligan_count_if_known"] = float(opp_mulligan_number)

    return _predict_proba(bundle, row)


def predict_keep_probability_from_feature_row(
    bundle: ChoiceModelBundle,
    row: dict[str, float],
) -> float:
    """Shortcut when the caller has already built the feature row.

    Composing the choice model with the win model in one
    :func:`simulate` + :func:`build_feature_row` call saves a sim per
    hand. Pre-condition: ``row`` must already include
    ``opp_mulligan_count_if_known`` (the call site that builds the row
    is responsible for that, as in :func:`predict_keep_probability`).
    """
    return _predict_proba(bundle, row)
