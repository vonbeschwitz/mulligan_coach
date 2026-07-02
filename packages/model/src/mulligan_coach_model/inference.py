"""Inference entry points: predict P(win) + keep/mull recommendation.

Two public surfaces:

* :func:`predict_win_probability` — single-hand prediction through
  the full sim -> features -> baseline -> XGBoost pipeline. Returns
  a calibrated probability in ``[0, 1]``.
* :func:`recommend` — compares ``P(win | keep)`` against
  ``P(win | mulligan-to-(N+1))`` via a Monte Carlo over fresh
  draws from the deck. Returns a typed :class:`Recommendation`.

The website (PR 6) and overlay (PR 7) both consume this module as
their only model-side interface. The overlay is the latency-
sensitive path: at the default ``n_sims=1000`` /
``n_mulligan_samples=30`` it should complete well under a second
on a modern laptop.

No post-hoc calibrator
----------------------

The booster trained with ``binary:logistic`` + ``base_margin`` is
already well calibrated at the relevant data scale (test ECE
~0.005). An earlier version of the pipeline applied an isotonic
post-hoc step; we removed it after benchmarking confirmed it (a)
gave no log-loss improvement, (b) introduced a saturation bug at
the tails where rare calibration-split runs of all-loss or all-win
got pinned to exactly 0 or 1. See :mod:`train` for details.

Baseline cancellation
---------------------

At inference time we never have the user's 17Lands buckets — we
don't query 17Lands at runtime. The baseline's ``margin(None,
None, on_play, opp_mull)`` path uses the precomputed population
marginal for the on-play cell. Both arms of the keep-vs-mull
comparison see the SAME baseline margin (same user info set,
same on_play, same opp_mull), so the baseline cancels in the
comparison and the verdict reflects only the XGBoost-learned
delta.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np
import xgboost as xgb
from mulligan_coach_cards import ParsedCard
from mulligan_coach_features import (
    CardZScores,
    ShrunkWinRates,
    build_feature_row,
)
from mulligan_coach_simulation import simulate

from .baseline import BaselineModel
from .feature_matrix import _library_from_deck
from .train import TrainResult, load_train_result
from .versioning import compute_version_warning

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ModelBundle: container for the artifacts inference needs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelBundle:
    """Loaded model artifacts for inference.

    Built by :meth:`load` from a model directory (the output of
    :func:`save_train_result`) or directly from a
    :class:`TrainResult` (the in-process path tests use).

    The ``feature_names`` tuple drives column ordering at inference
    time — a re-trained model with a different feature vocabulary
    just slots into the same call sites, with missing features
    defaulting to ``NaN`` (XGBoost's native missing-value path).

    ``version_warning`` is a non-fatal note set at load time when the
    model's recorded pipeline versions don't match the live simulator +
    feature code (or are absent, for pre-Step-1 models). It never blocks
    loading — frozen-EXE users legitimately run skewed versions; surfacing
    it in the UI is a later roadmap step.
    """

    booster: xgb.Booster
    baseline: BaselineModel
    feature_names: tuple[str, ...]
    best_iteration: int
    version_warning: str | None = None

    @classmethod
    def load(cls, model_dir: Path) -> ModelBundle:
        """Load a fitted model from ``model_dir``.

        The directory must contain the three files written by
        :func:`save_train_result`: ``baseline.json``,
        ``xgboost.json``, ``metadata.json``.
        """
        result = load_train_result(model_dir)
        bundle = cls.from_train_result(result)
        if bundle.version_warning is not None:
            log.warning("ModelBundle.load(%s): %s", model_dir, bundle.version_warning)
        return bundle

    @classmethod
    def from_train_result(cls, result: TrainResult) -> ModelBundle:
        """Construct a bundle from an in-memory :class:`TrainResult`.

        Useful in tests and during interactive experimentation:
        bypasses the disk round-trip when the artifacts are
        already in process.
        """
        return cls(
            booster=result.booster,
            baseline=result.baseline,
            feature_names=result.metadata.feature_names,
            best_iteration=result.metadata.best_iteration,
            version_warning=compute_version_warning(result.metadata.pipeline_versions),
        )


# ---------------------------------------------------------------------------
# Recommendation: result type for the keep / mull verdict
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Recommendation:
    """Result of :func:`recommend` — keep vs. mulligan verdict.

    ``verdict`` is a literal string so callers can pattern-match.
    ``delta`` is the keep-minus-mulligan calibrated win
    probability; positive means keep is better, negative means
    mull is. Tiny positive deltas (e.g. ``0.001``) probably mean
    "the model can't tell" — the website surface displays both
    probabilities so the user can judge magnitude themselves.
    """

    verdict: Literal["keep", "mulligan"]
    keep_win_probability: float
    mulligan_win_probability: float
    delta: float
    mulligan_number_from: int
    mulligan_number_to: int


# ---------------------------------------------------------------------------
# Internal: feature row -> XGBoost prediction
# ---------------------------------------------------------------------------


def _predict_proba(
    bundle: ModelBundle,
    row: dict[str, float],
    base_margin: float,
) -> float:
    """Run one already-built feature dict through baseline + XGBoost.

    The bundle's ``feature_names`` order drives column layout.
    Missing features default to ``np.nan`` so a new build_feature_row
    column (introduced in a future feature spec) doesn't crash an
    older model — XGBoost treats NaN as missing and routes it
    according to the trained tree splits.
    """
    values = np.array(
        [[row.get(name, np.nan) for name in bundle.feature_names]],
        dtype=float,
    )
    dm = xgb.DMatrix(
        values,
        base_margin=np.array([base_margin], dtype=float),
        feature_names=list(bundle.feature_names),
    )
    iter_range = (0, bundle.best_iteration + 1)
    proba = bundle.booster.predict(dm, iteration_range=iter_range)
    return float(proba[0])


# ---------------------------------------------------------------------------
# Public: predict_win_probability
# ---------------------------------------------------------------------------


def predict_win_probability(
    bundle: ModelBundle,
    *,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    on_the_play: bool,
    mulligan_number: int,
    opp_mulligan_number: int | None,
    event_type: str,
    set_code: str,
    shrunk: dict[int, ShrunkWinRates],
    zscores: dict[int, CardZScores],
    n_sims: int = 1000,
    seed: int | None = None,
) -> float:
    """Predict ``P(win)`` for a single ``(hand, deck, context)`` row.

    Calls the same chain training used:
    :func:`mulligan_coach_simulation.simulate`
    -> :func:`mulligan_coach_features.build_feature_row`
    -> :meth:`BaselineModel.margin`
    -> ``bundle.booster.predict``.

    User-skill buckets are intentionally not passed — at runtime we
    don't query 17Lands per-player stats. The baseline falls back
    to its population marginal for the ``on_the_play`` cell.

    Parameters
    ----------
    bundle:
        Loaded :class:`ModelBundle`.
    hand, deck:
        The 7-card opening hand and the 40-card deck (including
        the hand). 17Lands London-mulligan convention: the hand is
        the pre-bottom 7-card draw regardless of mulligan number.
    on_the_play:
        True if the player is on the play; affects the simulator
        draw step and the baseline cell lookup.
    mulligan_number:
        Number of mulligans the player has already taken (0..6).
    opp_mulligan_number:
        The opponent's mulligan count when known (only on the draw);
        ``None`` on the play uses the baseline's population mean.
    event_type, set_code:
        Format identifiers. Passed through to the feature builder's
        one-hot context columns.
    shrunk, zscores:
        Per-format 17Lands shrunk WR / z-score dicts, keyed by
        arena_id. Built once per format via
        :func:`mulligan_coach_features.shrink_stats` etc.
    n_sims:
        Monte Carlo replications inside the simulator. 1000 is the
        deploy default — enough to keep per-card castability
        probabilities stable.
    seed:
        Forwarded to :func:`simulate` for reproducibility. ``None``
        uses fresh entropy each call.
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
    # Conditional opp_mulligan feature: known on the draw, NaN
    # (XGBoost missing-value path) on the play.
    if on_the_play or opp_mulligan_number is None:
        row["opp_mulligan_count_if_known"] = float("nan")
    else:
        row["opp_mulligan_count_if_known"] = float(opp_mulligan_number)

    base_margin = bundle.baseline.margin(
        user_wr_bucket=None,
        user_n_games_bucket=None,
        on_the_play=on_the_play,
        opp_mulligan_number=opp_mulligan_number,
    )
    return _predict_proba(bundle, row, base_margin)


# ---------------------------------------------------------------------------
# Public: recommend (keep vs. mulligan)
# ---------------------------------------------------------------------------


def recommend(
    bundle: ModelBundle,
    *,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    on_the_play: bool,
    mulligan_number: int,
    opp_mulligan_number: int | None,
    event_type: str,
    set_code: str,
    shrunk: dict[int, ShrunkWinRates],
    zscores: dict[int, CardZScores],
    n_sims: int = 1000,
    n_mulligan_samples: int = 30,
    seed: int | None = None,
) -> Recommendation:
    """Compare P(win | keep) vs P(win | mulligan-to-(N+1)).

    The mulligan arm is a Monte Carlo over fresh 7-card draws from
    the (shuffled) deck. Each draw runs through the same prediction
    pipeline at ``mulligan_number + 1``; the average predicted
    probability is the mulligan-arm estimate.

    The London mulligan convention is preserved: even at higher
    mulligan numbers the player draws 7 cards (and would bottom
    ``mulligan_number + 1`` after the fact, which we don't model
    because 17Lands doesn't record which cards were bottomed).
    """
    if mulligan_number >= 6:
        raise ValueError(f"cannot mulligan from mulligan_number={mulligan_number}; max is 6")
    if len(hand) != 7:
        raise ValueError(f"expected hand=7 cards (London mulligan); got {len(hand)}")
    # Training pipeline accepts 40-42-card decks (the +1/+2 lists players
    # sometimes run when torn between strong cards). Match that here so the
    # inference API doesn't reject decks the model was trained to score.
    if not (40 <= len(deck) <= 42):
        raise ValueError(f"expected deck=40-42 cards (Limited); got {len(deck)}")

    rng = random.Random(seed)
    base_seed = rng.randint(0, 2**31 - 1)

    # Keep arm.
    p_keep = predict_win_probability(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=on_the_play,
        mulligan_number=mulligan_number,
        opp_mulligan_number=opp_mulligan_number,
        event_type=event_type,
        set_code=set_code,
        shrunk=shrunk,
        zscores=zscores,
        n_sims=n_sims,
        seed=base_seed,
    )

    # Mulligan arm: average over n_mulligan_samples freshly drawn hands.
    mull_probs: list[float] = []
    for _ in range(n_mulligan_samples):
        indices = rng.sample(range(len(deck)), 7)
        mull_hand = [deck[i] for i in indices]
        p = predict_win_probability(
            bundle,
            hand=mull_hand,
            deck=deck,
            on_the_play=on_the_play,
            mulligan_number=mulligan_number + 1,
            opp_mulligan_number=opp_mulligan_number,
            event_type=event_type,
            set_code=set_code,
            shrunk=shrunk,
            zscores=zscores,
            n_sims=n_sims,
            seed=rng.randint(0, 2**31 - 1),
        )
        mull_probs.append(p)
    p_mull = float(np.mean(mull_probs))

    delta = p_keep - p_mull
    verdict: Literal["keep", "mulligan"] = "keep" if delta >= 0 else "mulligan"
    return Recommendation(
        verdict=verdict,
        keep_win_probability=p_keep,
        mulligan_win_probability=p_mull,
        delta=delta,
        mulligan_number_from=mulligan_number,
        mulligan_number_to=mulligan_number + 1,
    )
