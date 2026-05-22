"""Model-loading and recommendation orchestration.

Production path (overlay + website)
-----------------------------------

The shipped verdict comes from the **choice model** (``models/choice_v6``
by default), via :meth:`RecommendationService.recommend_choice`. The
choice model predicts ``P(skilled player would keep | hand)``; we
bucket that into four levels using fixed thresholds
(``CHOICE_*_THRESHOLD``):

* ``p_keep > 0.75``   -> ``clear_keep``
* ``p_keep > 0.50``   -> ``marginal_keep``
* ``p_keep > 0.25``   -> ``marginal_mulligan``
* otherwise           -> ``clear_mulligan``

Each call runs one simulate + build_feature_row + predict pass. The
resulting :class:`ChoiceRecommendation` carries the verdict, the
raw ``p_keep``, and the playability explanation built from the same
simulator aggregate.

Legacy win-model path
---------------------

The older :meth:`recommend_asymmetric` / :class:`AsymmetricRecommendation`
flow is retained for two callers:

1. A few analysis scripts under ``packages/model/scripts`` that
   reach into the cache machinery to benchmark the win-model.
2. Any in-process consumer that hasn't migrated.

It is **not** wired to the overlay or website any more.
:func:`load_service` still loads the win bundle best-effort
alongside the choice bundle so those scripts keep working; absence
of either bundle is reported via :class:`ServiceStatus`.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import threading
from collections import Counter, OrderedDict
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
from mulligan_coach_cards import (
    ParsedCard,
    SeventeenLandsStats,
    load_premier_draft_stats,
    ratings_parquet_path,
)
from mulligan_coach_features import (
    CardZScores,
    ShrunkWinRates,
    build_feature_row,
    shrink_stats,
    zscore_stats,
)
from mulligan_coach_features.categories import cmc as _card_cmc
from mulligan_coach_features.categories import is_land as _card_is_land
from mulligan_coach_model import (
    ChoiceModelBundle,
    ModelBundle,
    Recommendation,
    predict_keep_probability_from_feature_row,
    recommend,
)
from mulligan_coach_model.feature_matrix import _library_from_deck
from mulligan_coach_model.inference import _predict_proba
from mulligan_coach_simulation import AggregateStats, draw_smoothed_hand, simulate
from mulligan_coach_simulation.runtime import Card

log = logging.getLogger(__name__)

# Default model directories — checked relative to the workspace root
# (the parent of `packages/`). Override via env var.
#
# Two models, two paths:
#
# * ``choice_v6`` is the production keep/mull recommender — XGBoost
#   trained on 17Lands replay-data mulligan decisions across
#   PremierDraft + TradDraft, filtered to competent players. The
#   overlay and website both read its P(keep) prediction and bucket
#   into the four-level verdict (clear / marginal x keep / mull).
#   This is the only model required for normal operation.
# * ``all3_v2`` is the legacy win-model bundle (P(win | hand)).
#   Optional and only consulted by a couple of analysis scripts
#   (replay-mulligan benchmarks etc.) — the production verdict is
#   choice-model only. Load is best-effort; missing is fine.
_DEFAULT_CHOICE_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "choice_v6"
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "all3_v2"
_DEFAULT_EVENT_TYPE = "PremierDraft"

# Choice-model verdict thresholds, applied to p_keep:
#
#   p_keep > 0.75            -> clear_keep         (mull% < 25)
#   0.50 < p_keep <= 0.75    -> marginal_keep
#   0.25 < p_keep <= 0.50    -> marginal_mulligan
#   p_keep <= 0.25           -> clear_mulligan     (mull% >= 75)
#
# The thresholds map directly onto bucket boundaries the user picked
# (clear keep > 75% chance of keeping, clear mull < 25%, two marginal
# bands of equal width between). The displayed number in the overlay
# is the *mulligan* percentage = round(100 * (1 - p_keep)), so a
# `clear_mulligan` hand shows >=75% and a `clear_keep` hand shows <25%.
CHOICE_CLEAR_KEEP_THRESHOLD = 0.75
CHOICE_MARGINAL_KEEP_THRESHOLD = 0.50
CHOICE_MARGINAL_MULL_THRESHOLD = 0.25

# Default sims-per-decision for the choice-model recommendation. The
# choice model reads simulator-derived playability features the same
# way the win model does, so per-hand precision matters. 1000 matches
# the website's historical keep-arm budget; the overlay overrides to
# a lower value to keep latency under ~250 ms.
DEFAULT_N_SIMS_CHOICE = 1000

# Default sim budgets for the asymmetric path. The keep arm runs once;
# the mulligan arm averages over many independent draws so precision
# benefits more from samples than from per-sample sim depth (see the
# module docstring). 50x40 = 2k total mulligan sims is ~3x the keep
# arm by wall clock without ballooning user wait.
DEFAULT_N_SIMS_KEEP = 1000
DEFAULT_N_SIMS_PER_MULLIGAN = 40
DEFAULT_N_MULLIGAN_SAMPLES = 50

# Empirical bias added to the mulligan arm before the keep-vs-mull
# comparison. Calibrated against ~9.7k replay-data decisions by
# skilled players (n_games>=100, WR>=0.58): the un-biased model
# flagged mulligan on only 3.7% of hands vs 8.7% actual, and the
# marginal hands the model adds at each percentage point of bias
# have WR < format-average out to ~5pp. Picking 4pp lines the
# website's marginal mulligan frequency up with skilled-player
# behaviour while keeping the marginal-band hands clearly weaker
# than typical keeps. See ``replay_mulligan_benchmark.log``.
MULLIGAN_BIAS = 0.04

# Width of the "marginal" band around the verdict cutoff. If
# ``|p_keep - (p_mull + MULLIGAN_BIAS)| < MARGIN_THRESHOLD`` the
# verdict is qualified as "marginal" rather than "clear" — the
# arms are close enough that game-context (matchup, mana base,
# opponent's known plan) should drive the call.
MARGIN_THRESHOLD = 0.03

# Highest mulligan number the model can accept. Mulligan-to-0 (mulled
# six times, hand of one card) is the deepest a player can keep in
# Limited. Mulligan-from-6 raises in mulligan_coach_model.recommend()
# because there's no deeper level to go to.
_MAX_MULLIGAN_NUMBER = 6


# ---------------------------------------------------------------------------
# Per-format stats: shrunk WR + z-scores
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatStats:
    """Shrunk WR + z-score dicts for one (set, event_type) format.

    Both dicts are keyed by 17Lands' ``mtga_id`` — the same id
    ``ParsedCard.arena_id`` carries (when MTGJSON has caught up).
    Cards whose arena_id is missing from these dicts produce zero
    contribution to the per-card-stat features at inference time;
    that's expected and the model is robust to it.
    """

    shrunk: dict[int, ShrunkWinRates]
    zscores: dict[int, CardZScores]

    @classmethod
    def build(cls, stats: Iterable[SeventeenLandsStats]) -> FormatStats:
        """Build both dicts from one format's worth of raw stats rows."""
        shrunk = shrink_stats(stats)
        zscores = zscore_stats(shrunk.values())
        return cls(shrunk=shrunk, zscores=zscores)


def _try_load_format_stats(set_code: str) -> FormatStats | None:
    """Load and shrink one set's Premier-Draft stats, or return ``None``."""
    path = ratings_parquet_path(set_code, event_type=_DEFAULT_EVENT_TYPE)
    if not path.exists():
        log.info("ratings parquet not found for %s at %s; skipping", set_code, path)
        return None
    try:
        lookup = load_premier_draft_stats(set_code)
    except Exception:
        log.exception("failed to load ratings parquet for %s at %s", set_code, path)
        return None
    return FormatStats.build(lookup.by_arena_id.values())


# ---------------------------------------------------------------------------
# Status reporting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceStatus:
    """Human-readable summary of which pieces of the service loaded."""

    model_loaded: bool
    model_dir: Path | None
    formats_with_stats: list[str]
    formats_missing_stats: list[str]
    error: str | None = None

    @property
    def ready(self) -> bool:
        """True iff at least the model is loaded."""
        return self.model_loaded


# ---------------------------------------------------------------------------
# Asymmetric recommendation result + cache
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MulliganArmResult:
    """Mulligan-arm prediction with the deeper-mulligan floor applied.

    For each drawn 7-card sample we predict P(win) at *two* mulligan
    levels — the target one (the level the player would be at after
    mulliganing once more) and the next level deeper. The deeper
    level's average is the floor: if a sample at the target level
    predicts lower than that floor, the player would just mulligan
    again, so the sample's effective P(win) is at least the floor.

    ``floored_mean`` is what the recommendation reports;
    ``raw_mean`` and the floor diagnostics let the template explain
    the adjustment. ``floor_value`` is ``None`` when the floor isn't
    applicable — currently only ``mulligan_number_to == 6`` (no
    deeper level exists for the model to evaluate).
    """

    floored_mean: float
    raw_mean: float
    floor_value: float | None
    n_samples: int
    n_samples_below_floor: int


@dataclass(frozen=True)
class HandCardPlayability:
    """Per-hand-card castability summary surfaced in the explanation panel.

    Lands populate ``p_castable_by_turn`` with zeros — the simulator
    plays lands, it doesn't "cast" them — and the template renders
    them with a dash. ``mana_value`` is ``None`` for lands as well.

    ``mana_cost`` is the printed Scryfall cost string (e.g. ``"{1}{G}"``)
    for spells; ``None`` for lands. The overlay renders a pretty form
    (``"1G"``); the website is free to render the Scryfall mana-symbol
    SVGs from the same raw string.

    ``opening_hand_win_rate`` is the *shrunk* 17Lands OH WR for the
    card (pulled from the same shrunk table the choice model reads).
    ``None`` when the card has no 17Lands stats for the current format
    (new printing, or arena_id not yet in the parquet).
    """

    name: str
    is_land: bool
    mana_value: int | None
    # P(card castable on turn T | drawn by turn T), averaged across
    # the keep arm's Monte Carlo runs. Length-4 tuple for T = 1..4.
    p_castable_by_turn: tuple[float, float, float, float]
    mana_cost: str | None = None
    opening_hand_win_rate: float | None = None


@dataclass(frozen=True)
class RecommendationExplanation:
    """Playability stats from the keep arm's Monte Carlo run.

    Surfaced under the verdict so the user can see *why* the model
    rated the hand the way it did. All values come straight from the
    same ``AggregateStats`` and feature row the model consumed — no
    extra simulation cost.

    Three sections, picked to cover the top of the trained model's
    feature-importance ranking (see ``models/all3_v2`` feature
    importance dump) while staying readable to a non-technical user:

    * **Mana base** — ``p_land_drop_by_turn_{2,3,4}`` plus
      ``expected_mana_count_turn_4``. Captures land screw / flood.
    * **Curve hits** — five "did you have something to do on this
      turn?" probabilities pulled directly from the castability
      grid: T1 play, T2 creature, T2 removal, T3 small creature
      (the #2 feature overall by gain), and a T4 3-drop slot.
    * **Color fixing** — ``avg_pct_hand_spells_with_colored_mana_by_turn_4``,
      the share of hand spells whose colored cost is castable by T4.
    * **Per-card playability** — one row per hand card with its
      per-turn castability. Lets the user see which cards anchor the
      keep value and which sit dead in hand.
    """

    p_make_2nd_land_by_t2: float
    p_make_3rd_land_by_t3: float
    p_make_4th_land_by_t4: float
    expected_mana_at_t4: float
    p_cast_any_spell_t1: float
    p_cast_any_creature_t2: float
    p_cast_any_removal_t2: float
    p_cast_small_creature_by_t3: float
    p_cast_3drop_by_t4: float
    color_fix_by_t4: float
    hand_cards: tuple[HandCardPlayability, ...]
    # Per-turn aggregates surfaced in the overlay's 4x3 stats table.
    # All four turns; T1 land is conventionally 1.0 — a 0-land hand
    # is rare enough in Premier Draft that hard-coding it is honest.
    # Each tuple is (T1, T2, T3, T4). Read from the same feature-row
    # the model consumed, so what the UI shows is exactly what
    # entered the prediction.
    p_make_land_drop_by_turn: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    p_cast_any_creature_by_turn: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    p_cast_any_spell_by_turn: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)


def _build_explanation(
    *,
    hand: list[ParsedCard],
    aggregate: AggregateStats,
    feature_row: dict[str, float],
    shrunk: dict[int, ShrunkWinRates] | None = None,
) -> RecommendationExplanation:
    """Pull the explanatory subset out of the per-card stats + feature row.

    The feature row is the canonical source of truth for the aggregate
    castability numbers — it's the exact dict the model consumed, so
    showing values from it guarantees the user sees the same signal
    the prediction was built on. Per-card castability comes from the
    aggregate directly because the feature row aggregates across cards
    and loses the per-card breakdown.

    ``shrunk`` (when provided) is the same ``ShrunkWinRates`` dict the
    choice/win model reads — keyed by ``mtga_id`` (a.k.a. arena_id).
    We pull the shrunk OH WR per hand card so the overlay's per-card
    table can colour-code it the same way the model weights it.
    Passing ``None`` (or a card whose arena_id isn't in the table)
    just leaves the per-card OH WR field as ``None``.
    """
    shrunk = shrunk or {}
    per_card: list[HandCardPlayability] = []
    for card in hand:
        cs = aggregate.by_card_name.get(card.name)
        if cs is None:
            ps: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
        else:
            ps = (
                float(cs.p_castable_by_turn[0]),
                float(cs.p_castable_by_turn[1]),
                float(cs.p_castable_by_turn[2]),
                float(cs.p_castable_by_turn[3]),
            )
        is_land = _card_is_land(card)
        # Per-card OH WR lookup. Arena_id is the canonical join key
        # the shrunk table uses; cards without an arena_id (e.g. a
        # printing MTGJSON hasn't ingested yet) just get None and
        # the renderer shows a dash.
        oh_wr: float | None = None
        if card.arena_id is not None:
            entry = shrunk.get(card.arena_id)
            if entry is not None:
                oh_wr = entry.shrunk_opening_hand_win_rate
        mana_cost_raw = card.mana_cost.raw if card.mana_cost is not None else None
        per_card.append(
            HandCardPlayability(
                name=card.name,
                is_land=is_land,
                mana_value=None if is_land else int(_card_cmc(card)),
                p_castable_by_turn=ps,
                mana_cost=mana_cost_raw,
                opening_hand_win_rate=oh_wr,
            )
        )
    # Per-turn aggregates for the overlay's 4x3 table. Lands T1 is
    # hard-coded to 1.0 because the feature row doesn't track it (a
    # 0-land hand is rare enough in Premier Draft that pinning it to
    # 100% reads more honestly than showing 0).
    p_land = (
        1.0,
        float(feature_row.get("p_land_drop_by_turn_2", 0.0)),
        float(feature_row.get("p_land_drop_by_turn_3", 0.0)),
        float(feature_row.get("p_land_drop_by_turn_4", 0.0)),
    )
    p_creature = tuple(float(feature_row.get(f"p_any_creature_t{t}", 0.0)) for t in range(1, 5))
    p_spell = tuple(float(feature_row.get(f"p_any_any_spell_t{t}", 0.0)) for t in range(1, 5))
    return RecommendationExplanation(
        p_make_2nd_land_by_t2=float(feature_row.get("p_land_drop_by_turn_2", 0.0)),
        p_make_3rd_land_by_t3=float(feature_row.get("p_land_drop_by_turn_3", 0.0)),
        p_make_4th_land_by_t4=float(feature_row.get("p_land_drop_by_turn_4", 0.0)),
        expected_mana_at_t4=float(feature_row.get("expected_mana_count_turn_4", 0.0)),
        p_cast_any_spell_t1=float(feature_row.get("p_any_any_spell_t1", 0.0)),
        p_cast_any_creature_t2=float(feature_row.get("p_any_creature_t2", 0.0)),
        p_cast_any_removal_t2=float(feature_row.get("p_any_removal_t2", 0.0)),
        p_cast_small_creature_by_t3=float(feature_row.get("p_any_creature_mv_0_2_t3", 0.0)),
        p_cast_3drop_by_t4=float(feature_row.get("p_any_any_spell_mv_3_t4", 0.0)),
        color_fix_by_t4=float(
            feature_row.get("avg_pct_hand_spells_with_colored_mana_by_turn_4", 0.0)
        ),
        hand_cards=tuple(per_card),
        p_make_land_drop_by_turn=p_land,
        p_cast_any_creature_by_turn=(p_creature[0], p_creature[1], p_creature[2], p_creature[3]),
        p_cast_any_spell_by_turn=(p_spell[0], p_spell[1], p_spell[2], p_spell[3]),
    )


@dataclass(frozen=True)
class AsymmetricRecommendation:
    """Result of :meth:`RecommendationService.recommend_asymmetric`.

    Same shape as :class:`mulligan_coach_model.Recommendation` plus
    diagnostic fields about the sim budget used, the deeper-mulligan
    floor adjustment, and whether the mulligan arm came from the
    prefetch cache.

    Verdict semantics
    -----------------
    The comparison adds :data:`MULLIGAN_BIAS` to the mulligan arm
    before deciding (an empirical correction; see the constant's
    docstring). ``adjusted_delta = p_keep - (p_mull + MULLIGAN_BIAS)``
    is what the verdict reads:

    * ``adjusted_delta >= MARGIN_THRESHOLD``  -> ``"clear_keep"``
    * ``0 <= adjusted_delta < MARGIN_THRESHOLD`` -> ``"marginal_keep"``
    * ``-MARGIN_THRESHOLD < adjusted_delta < 0`` -> ``"marginal_mulligan"``
    * ``adjusted_delta <= -MARGIN_THRESHOLD`` -> ``"clear_mulligan"``

    ``delta`` is kept as the raw ``p_keep - p_mull`` so callers and
    the template can show both the unadjusted comparison and the
    adjusted decision.
    """

    verdict: Literal["clear_keep", "marginal_keep", "marginal_mulligan", "clear_mulligan"]
    keep_win_probability: float
    mulligan_win_probability: float
    delta: float
    adjusted_delta: float
    mulligan_bias: float
    margin_threshold: float
    mulligan_number_from: int
    mulligan_number_to: int
    n_sims_keep: int
    n_sims_per_mulligan: int
    n_mulligan_samples: int
    mulligan_arm_was_cached: bool
    # Floor diagnostics — surfaced in the result panel.
    mulligan_arm_raw_mean: float
    mulligan_arm_floor: float | None
    n_samples_below_floor: int
    # Why-the-verdict panel: playability stats from the keep arm's
    # sim, surfaced under the verdict on the website.
    explanation: RecommendationExplanation


@dataclass(frozen=True)
class ChoiceRecommendation:
    """Result of :meth:`RecommendationService.recommend_choice`.

    The production keep/mull verdict, driven by the choice model
    (``models/choice_v6``). The model predicts ``P(skilled player
    would keep | hand)``; we bucket that into four levels using
    fixed thresholds (see :data:`CHOICE_CLEAR_KEEP_THRESHOLD` etc.):

    * ``p_keep > 0.75``            -> ``"clear_keep"``
    * ``0.50 < p_keep <= 0.75``    -> ``"marginal_keep"``
    * ``0.25 < p_keep <= 0.50``    -> ``"marginal_mulligan"``
    * ``p_keep <= 0.25``           -> ``"clear_mulligan"``

    ``mulligan_percent`` is the *display* number — the recommender
    surfaces a single "should-mulligan" percentage in 0..100 rather
    than two separate arms, since the choice model already collapses
    the decision into one signal.
    """

    verdict: Literal["clear_keep", "marginal_keep", "marginal_mulligan", "clear_mulligan"]
    p_keep: float
    mulligan_number_from: int
    mulligan_number_to: int
    n_sims: int
    explanation: RecommendationExplanation

    @property
    def mulligan_percent(self) -> float:
        """``round(100 * (1 - p_keep))`` — the number shown to the user."""
        return (1.0 - self.p_keep) * 100.0


def _classify_choice_verdict(
    p_keep: float,
) -> Literal["clear_keep", "marginal_keep", "marginal_mulligan", "clear_mulligan"]:
    """Map a ``p_keep`` in ``[0, 1]`` to one of the four verdict tags.

    Boundaries are inclusive at the upper edge of each band: a value
    of exactly 0.75 lands in ``marginal_keep`` (not ``clear_keep``)
    so the bucket whose visible label is "75% or more" requires the
    model to be strictly above 75%.
    """
    if p_keep > CHOICE_CLEAR_KEEP_THRESHOLD:
        return "clear_keep"
    if p_keep > CHOICE_MARGINAL_KEEP_THRESHOLD:
        return "marginal_keep"
    if p_keep > CHOICE_MARGINAL_MULL_THRESHOLD:
        return "marginal_mulligan"
    return "clear_mulligan"


@dataclass(frozen=True)
class _MulliganCacheKey:
    """Cache key for one mulligan-arm computation.

    Includes everything the model sees: the deck's content (via a
    stable signature), context, and sim budget. Hand is NOT part of
    the key because the mulligan arm doesn't depend on the kept hand —
    it samples fresh hands from the deck.
    """

    deck_signature: tuple[str, ...]
    on_the_play: bool
    mulligan_number_to: int
    opp_mulligan_number: int | None
    n_sims_per_mulligan: int
    n_mulligan_samples: int


def _classify_verdict(
    adjusted_delta: float,
) -> Literal["clear_keep", "marginal_keep", "marginal_mulligan", "clear_mulligan"]:
    """Map ``adjusted_delta`` -> one of four verdict labels.

    Ties (``adjusted_delta == 0`` exactly) resolve to
    ``"marginal_keep"`` — keeping is the lower-variance choice when
    the arms are within rounding of each other.
    """
    if adjusted_delta >= MARGIN_THRESHOLD:
        return "clear_keep"
    if adjusted_delta >= 0:
        return "marginal_keep"
    if adjusted_delta > -MARGIN_THRESHOLD:
        return "marginal_mulligan"
    return "clear_mulligan"


def _deck_signature(deck: list[ParsedCard]) -> tuple[str, ...]:
    """Stable, set-aware signature of a 40-card deck for the cache key.

    Uses ``set:collector_number`` per copy and sorts. Two decks with
    the same multiset of printings hash to the same signature —
    different printings of the same card name don't collide (the
    model sees per-set features). Sort is for order independence.
    """
    return tuple(sorted(f"{c.set_code.upper()}:{c.collector_number}" for c in deck))


def _stable_seed(key: _MulliganCacheKey) -> int:
    """Derive a deterministic 32-bit seed from the cache key.

    Determinism is what makes caching the *result* meaningful: a
    second click on the same deck reproduces the same mulligan-hand
    sample, so the same average. Without this, every call would
    sample different hands and the cache would silently lie about
    matching previous output.
    """
    h = hashlib.sha256(repr(key).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big")


class _MulliganArmCache:
    """Process-wide LRU cache of mulligan-arm prediction futures.

    Holds :class:`concurrent.futures.Future` objects (not bare floats)
    so a concurrent ``/validate`` prefetch and ``/recommend`` lookup
    don't race — the second caller awaits the first's computation
    rather than duplicating ~2 s of work.

    The LRU is process-local. ``max_entries=32`` is plenty for a
    single-user dev session; promoting to a multi-user deploy would
    need a shared cache (Redis) and we'd revisit then.
    """

    def __init__(self, max_entries: int = 32) -> None:
        self._futures: OrderedDict[_MulliganCacheKey, Future[MulliganArmResult]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_entries = max_entries

    def get_or_submit(
        self,
        key: _MulliganCacheKey,
        compute_fn: Callable[[], MulliganArmResult],
        executor: ThreadPoolExecutor,
    ) -> tuple[Future[MulliganArmResult], bool]:
        """Return the future for *key*; submit *compute_fn* on miss.

        Second return value is ``True`` if *key* was already in the
        cache (hit), ``False`` if we just kicked off a new
        computation. Useful for the diagnostic flag on
        :class:`AsymmetricRecommendation`.
        """
        with self._lock:
            existing = self._futures.get(key)
            if existing is not None:
                # Cache hit; mark as recently used (LRU tail).
                self._futures.move_to_end(key)
                return existing, True

            future = executor.submit(compute_fn)
            self._futures[key] = future
            # Evict the oldest entries when over capacity. We don't
            # cancel running futures — a soon-to-be-evicted prefetch
            # might still complete and be useful via lru-cached
            # functions downstream. The Future object simply drops
            # out of the table.
            while len(self._futures) > self._max_entries:
                self._futures.popitem(last=False)
            return future, False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@dataclass
class RecommendationService:
    """Bundles the model, per-format stats, executor and cache.

    Held on ``app.state.service`` for the request handlers. Most
    fields are read-only after startup; the cache and executor are
    the two mutable pieces — both internally synchronised.

    Note: not ``frozen=True`` because :class:`_MulliganArmCache` and
    :class:`ThreadPoolExecutor` are stateful. The handler code still
    treats the service as effectively immutable (it only calls
    methods, never reassigns fields).
    """

    bundle: ModelBundle | None
    stats_by_set: dict[str, FormatStats] = field(default_factory=dict)
    status: ServiceStatus = field(
        default_factory=lambda: ServiceStatus(
            model_loaded=False,
            model_dir=None,
            formats_with_stats=[],
            formats_missing_stats=[],
        )
    )
    executor: ThreadPoolExecutor | None = None
    mulligan_cache: _MulliganArmCache = field(default_factory=_MulliganArmCache)
    # Choice-model bundle — predicts P(skilled player would keep this
    # hand). The production overlay + website verdict come from this
    # model; the win-model ``bundle`` above is legacy and only used by
    # a few analysis scripts. ``status.model_loaded`` reflects the
    # choice bundle's load state (since that's the production model).
    choice_bundle: ChoiceModelBundle | None = None

    @staticmethod
    def primary_set_of(deck: list[ParsedCard]) -> str | None:
        """Pick the deck's "format set" — the most common non-BASIC printing.

        Returns ``None`` only if the deck is *all* basics, which the
        route layer treats as "can't recommend".
        """
        counts = Counter(card.set_code.upper() for card in deck if card.set_code.upper() != "BASIC")
        if not counts:
            return None
        return counts.most_common(1)[0][0]

    def shutdown(self) -> None:
        """Tear down the background executor at app shutdown."""
        if self.executor is not None:
            self.executor.shutdown(wait=False, cancel_futures=True)

    # -----------------------------------------------------------------
    # Original symmetric recommend (kept for tests + any direct caller)
    # -----------------------------------------------------------------

    def recommend(
        self,
        *,
        hand: list[ParsedCard],
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number: int,
        opp_mulligan_number: int | None,
        n_sims: int = 1000,
        n_mulligan_samples: int = 30,
        seed: int | None = None,
    ) -> Recommendation:
        """Symmetric keep / mulligan recommendation (legacy path).

        Kept around for callers that don't need the prefetch cache or
        the asymmetric sim split. The website's ``/recommend`` route
        prefers :meth:`recommend_asymmetric`.
        """
        if self.bundle is None:
            raise RuntimeError(
                "Model bundle not loaded. Train a model (see packages/model/CLAUDE.md) "
                "and set MULLIGAN_COACH_MODEL_DIR to its directory."
            )
        set_code = self.primary_set_of(deck)
        if set_code is None:
            raise ValueError("Deck has no non-basic cards — cannot pick a format.")
        stats = self.stats_by_set.get(set_code)
        return recommend(
            self.bundle,
            hand=hand,
            deck=deck,
            on_the_play=on_the_play,
            mulligan_number=mulligan_number,
            opp_mulligan_number=opp_mulligan_number,
            event_type=_DEFAULT_EVENT_TYPE,
            set_code=set_code,
            shrunk=stats.shrunk if stats is not None else {},
            zscores=stats.zscores if stats is not None else {},
            n_sims=n_sims,
            n_mulligan_samples=n_mulligan_samples,
            seed=seed,
        )

    # -----------------------------------------------------------------
    # Asymmetric recommend + prefetch
    # -----------------------------------------------------------------

    def prefetch_mulligan(
        self,
        *,
        deck: list[ParsedCard],
        on_the_play: bool = True,
        mulligan_number: int = 0,
        opp_mulligan_number: int | None = None,
        n_sims_per_mulligan: int = DEFAULT_N_SIMS_PER_MULLIGAN,
        n_mulligan_samples: int = DEFAULT_N_MULLIGAN_SAMPLES,
    ) -> Future[MulliganArmResult] | None:
        """Fire-and-forget compute of the mulligan arm; cache the Future.

        Called from ``POST /validate`` whenever the freshly-pasted
        deck parses to 40 cards. Idempotent: re-validates of the
        same deck (e.g. user types another character, debounced
        validate fires again) find the existing Future and don't
        duplicate work.

        Returns ``None`` if the service isn't ready (no model bundle)
        or the deck is unusable (all basics / picks no set). The
        caller doesn't need to inspect the Future — the
        ``/recommend`` route picks it up via the cache on demand.
        """
        if self.bundle is None or self.executor is None:
            return None
        set_code = self.primary_set_of(deck)
        if set_code is None:
            return None

        key = _MulliganCacheKey(
            deck_signature=_deck_signature(deck),
            on_the_play=on_the_play,
            mulligan_number_to=mulligan_number + 1,
            opp_mulligan_number=opp_mulligan_number,
            n_sims_per_mulligan=n_sims_per_mulligan,
            n_mulligan_samples=n_mulligan_samples,
        )
        # Snapshot a closure that doesn't capture self mutably.
        deck_snapshot = list(deck)
        seed = _stable_seed(key)

        def _compute() -> MulliganArmResult:
            return self._compute_mulligan_arm(
                deck=deck_snapshot,
                on_the_play=on_the_play,
                mulligan_number_to=key.mulligan_number_to,
                opp_mulligan_number=opp_mulligan_number,
                set_code=set_code,
                n_sims_per_mulligan=n_sims_per_mulligan,
                n_mulligan_samples=n_mulligan_samples,
                seed=seed,
            )

        future, _hit = self.mulligan_cache.get_or_submit(key, _compute, self.executor)
        return future

    def recommend_asymmetric(
        self,
        *,
        hand: list[ParsedCard],
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number: int,
        opp_mulligan_number: int | None,
        n_sims_keep: int = DEFAULT_N_SIMS_KEEP,
        n_sims_per_mulligan: int = DEFAULT_N_SIMS_PER_MULLIGAN,
        n_mulligan_samples: int = DEFAULT_N_MULLIGAN_SAMPLES,
    ) -> AsymmetricRecommendation:
        """Run keep arm inline, pull mulligan arm from cache (or compute).

        The mulligan arm is requested through the cache: if a
        :meth:`prefetch_mulligan` covering the same key has been
        running since deck submission, we await its result; otherwise
        the cache submits a fresh job and we await that. Either way,
        the keep arm runs in the caller's thread while the mulligan
        arm runs in the executor — both arms get the wall-clock
        concurrency benefit.
        """
        if self.bundle is None or self.executor is None:
            raise RuntimeError(
                "Model bundle not loaded. Train a model (see packages/model/CLAUDE.md) "
                "and set MULLIGAN_COACH_MODEL_DIR to its directory."
            )
        if mulligan_number >= 6:
            raise ValueError(f"cannot mulligan from mulligan_number={mulligan_number}; max is 6")
        if len(hand) != 7:
            raise ValueError(f"expected hand=7 cards (London mulligan); got {len(hand)}")
        if len(deck) != 40:
            raise ValueError(f"expected deck=40 cards (Limited); got {len(deck)}")
        set_code = self.primary_set_of(deck)
        if set_code is None:
            raise ValueError("Deck has no non-basic cards — cannot pick a format.")

        # Submit / fetch the mulligan-arm future BEFORE running the
        # keep arm so the two run concurrently in the typical case
        # (no prefetch yet, or prefetch from a different context).
        key = _MulliganCacheKey(
            deck_signature=_deck_signature(deck),
            on_the_play=on_the_play,
            mulligan_number_to=mulligan_number + 1,
            opp_mulligan_number=opp_mulligan_number,
            n_sims_per_mulligan=n_sims_per_mulligan,
            n_mulligan_samples=n_mulligan_samples,
        )
        seed = _stable_seed(key)
        deck_snapshot = list(deck)

        def _compute_mull() -> MulliganArmResult:
            return self._compute_mulligan_arm(
                deck=deck_snapshot,
                on_the_play=on_the_play,
                mulligan_number_to=key.mulligan_number_to,
                opp_mulligan_number=opp_mulligan_number,
                set_code=set_code,
                n_sims_per_mulligan=n_sims_per_mulligan,
                n_mulligan_samples=n_mulligan_samples,
                seed=seed,
            )

        mulligan_future, cached = self.mulligan_cache.get_or_submit(
            key, _compute_mull, self.executor
        )

        # Keep arm inline. Same fixed seed-pattern as the legacy
        # recommend() — uses the cache key's stable seed so the keep
        # arm is also reproducible for a given (deck, hand) request.
        p_keep, keep_aggregate, keep_feature_row = self._compute_keep_arm(
            hand=hand,
            deck=deck,
            on_the_play=on_the_play,
            mulligan_number=mulligan_number,
            opp_mulligan_number=opp_mulligan_number,
            set_code=set_code,
            n_sims=n_sims_keep,
            seed=seed ^ 0xDEAD_BEEF,  # decouple seed streams between arms
        )

        # Await the mulligan arm. If the prefetch is still warming
        # we block until it's done.
        mull_result = mulligan_future.result()
        p_mull = mull_result.floored_mean

        delta = p_keep - p_mull
        adjusted_delta = delta - MULLIGAN_BIAS
        verdict = _classify_verdict(adjusted_delta)
        # Look up shrunk OH WRs for the explanation's per-card panel.
        # The keep arm's feature build used the same dict, so this is
        # just a re-read — no extra computation.
        keep_shrunk: dict[int, ShrunkWinRates] = {}
        stats_keep = self.stats_by_set.get(set_code)
        if stats_keep is not None:
            keep_shrunk = stats_keep.shrunk
        explanation = _build_explanation(
            hand=hand,
            aggregate=keep_aggregate,
            feature_row=keep_feature_row,
            shrunk=keep_shrunk,
        )
        return AsymmetricRecommendation(
            verdict=verdict,
            keep_win_probability=p_keep,
            mulligan_win_probability=p_mull,
            delta=delta,
            adjusted_delta=adjusted_delta,
            mulligan_bias=MULLIGAN_BIAS,
            margin_threshold=MARGIN_THRESHOLD,
            mulligan_number_from=mulligan_number,
            mulligan_number_to=mulligan_number + 1,
            n_sims_keep=n_sims_keep,
            n_sims_per_mulligan=n_sims_per_mulligan,
            n_mulligan_samples=n_mulligan_samples,
            mulligan_arm_was_cached=cached,
            mulligan_arm_raw_mean=mull_result.raw_mean,
            mulligan_arm_floor=mull_result.floor_value,
            n_samples_below_floor=mull_result.n_samples_below_floor,
            explanation=explanation,
        )

    # -----------------------------------------------------------------
    # Choice-model recommendation (production path)
    # -----------------------------------------------------------------

    def recommend_choice(
        self,
        *,
        hand: list[ParsedCard],
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number: int,
        opp_mulligan_number: int | None,
        n_sims: int = DEFAULT_N_SIMS_CHOICE,
        seed: int | None = None,
    ) -> ChoiceRecommendation:
        """Run the choice model on a single (hand, deck, context) input.

        One ``simulate -> build_feature_row`` pass feeds both the
        choice prediction and the playability-explanation panel —
        no separate mulligan-arm simulation, unlike
        :meth:`recommend_asymmetric`. Returns a verdict + p_keep +
        explanation; the overlay / website display a single mulligan
        percentage derived from ``1 - p_keep``.
        """
        if self.choice_bundle is None:
            raise RuntimeError(
                "Choice model not loaded. Train models/choice_v6 "
                "(see packages/model/CLAUDE.md) or set "
                "MULLIGAN_COACH_CHOICE_MODEL_DIR to its directory."
            )
        if mulligan_number >= 7:
            raise ValueError(f"cannot mulligan from mulligan_number={mulligan_number}; max is 6")
        if len(hand) != 7:
            raise ValueError(f"expected hand=7 cards (London mulligan); got {len(hand)}")
        if len(deck) != 40:
            raise ValueError(f"expected deck=40 cards (Limited); got {len(deck)}")
        set_code = self.primary_set_of(deck)
        if set_code is None:
            raise ValueError("Deck has no non-basic cards — cannot pick a format.")

        # Reuse the seed derivation pattern from the asymmetric path so
        # repeated calls on the same hand are reproducible if the
        # caller doesn't pass an explicit seed. The keep arm only sees
        # the hand-specific seed; no cache to bypass here.
        if seed is None:
            sig_key = _MulliganCacheKey(
                deck_signature=_deck_signature(deck),
                on_the_play=on_the_play,
                mulligan_number_to=mulligan_number,  # not actually a "to" — just keying
                opp_mulligan_number=opp_mulligan_number,
                n_sims_per_mulligan=n_sims,
                n_mulligan_samples=0,
            )
            seed = _stable_seed(sig_key)

        p_keep, aggregate, feature_row = self._compute_choice_arm(
            hand=hand,
            deck=deck,
            on_the_play=on_the_play,
            mulligan_number=mulligan_number,
            opp_mulligan_number=opp_mulligan_number,
            set_code=set_code,
            n_sims=n_sims,
            seed=seed,
        )
        verdict = _classify_choice_verdict(p_keep)
        choice_shrunk: dict[int, ShrunkWinRates] = {}
        stats_choice = self.stats_by_set.get(set_code)
        if stats_choice is not None:
            choice_shrunk = stats_choice.shrunk
        explanation = _build_explanation(
            hand=hand,
            aggregate=aggregate,
            feature_row=feature_row,
            shrunk=choice_shrunk,
        )
        return ChoiceRecommendation(
            verdict=verdict,
            p_keep=p_keep,
            mulligan_number_from=mulligan_number,
            mulligan_number_to=mulligan_number + 1,
            n_sims=n_sims,
            explanation=explanation,
        )

    def _compute_choice_arm(
        self,
        *,
        hand: list[ParsedCard],
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number: int,
        opp_mulligan_number: int | None,
        set_code: str,
        n_sims: int,
        seed: int,
    ) -> tuple[float, AggregateStats, dict[str, float]]:
        """Run simulate + feature build once, predict P(keep) from the row.

        Returns the prediction plus the upstream aggregate and feature
        row so the caller can build the explanation panel without a
        second simulation pass.
        """
        assert self.choice_bundle is not None  # checked by caller
        stats = self.stats_by_set.get(set_code)
        shrunk = stats.shrunk if stats is not None else {}
        zscores = stats.zscores if stats is not None else {}

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
            event_type=_DEFAULT_EVENT_TYPE,
            set_code=set_code,
        )
        # Same conditional-feature shape the choice cache used during
        # training: NaN on the play / when opp's count is unknown,
        # the count itself on the draw.
        if on_the_play or opp_mulligan_number is None:
            row["opp_mulligan_count_if_known"] = float("nan")
        else:
            row["opp_mulligan_count_if_known"] = float(opp_mulligan_number)

        p_keep = predict_keep_probability_from_feature_row(self.choice_bundle, row)
        return p_keep, aggregate, row

    # -----------------------------------------------------------------
    # Internal compute helpers (called from cache workers + inline)
    # -----------------------------------------------------------------

    def _compute_keep_arm(
        self,
        *,
        hand: list[ParsedCard],
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number: int,
        opp_mulligan_number: int | None,
        set_code: str,
        n_sims: int,
        seed: int,
    ) -> tuple[float, AggregateStats, dict[str, float]]:
        """Predict P(win) for the kept hand and return the sim aggregate too.

        Inlines the body of :func:`predict_win_probability` so we can
        keep the ``AggregateStats`` and the assembled feature row —
        the explanation panel reads playability stats from them and
        we don't want to pay for a second simulate just to surface
        the same numbers. Same chain (simulate -> build_feature_row
        -> baseline margin -> XGBoost) as ``predict_win_probability``;
        :func:`mulligan_coach_model.tests.test_inference` covers the
        chain end-to-end.
        """
        assert self.bundle is not None  # checked by caller
        stats = self.stats_by_set.get(set_code)
        shrunk = stats.shrunk if stats is not None else {}
        zscores = stats.zscores if stats is not None else {}

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
            event_type=_DEFAULT_EVENT_TYPE,
            set_code=set_code,
        )
        # Conditional opp_mulligan feature: NaN on the play / when
        # unknown, value on the draw — matches predict_win_probability.
        if on_the_play or opp_mulligan_number is None:
            row["opp_mulligan_count_if_known"] = float("nan")
        else:
            row["opp_mulligan_count_if_known"] = float(opp_mulligan_number)

        base_margin = self.bundle.baseline.margin(
            user_wr_bucket=None,
            user_n_games_bucket=None,
            on_the_play=on_the_play,
            opp_mulligan_number=opp_mulligan_number,
        )
        p_win = _predict_proba(self.bundle, row, base_margin)
        return p_win, aggregate, row

    def _compute_mulligan_arm(
        self,
        *,
        deck: list[ParsedCard],
        on_the_play: bool,
        mulligan_number_to: int,
        opp_mulligan_number: int | None,
        set_code: str,
        n_sims_per_mulligan: int,
        n_mulligan_samples: int,
        seed: int,
    ) -> MulliganArmResult:
        """Average mulligan-arm predictions with a deeper-mulligan floor.

        For each drawn 7-card sample we predict P(win) at *two*
        mulligan-number levels using one shared :func:`simulate` call
        (mulligan_number is a context one-hot — same playability
        features, different baseline predictions). The floor then
        clamps each target-level sample at the average prediction of
        the deeper level: if the player's actual mulligan-to-N hand
        scores worse than the average mulligan-to-(N+1) outcome,
        they'd just mulligan again rather than keep that hand.

        At the boundary (``mulligan_number_to == 6``) the model can't
        evaluate the deeper level, so the floor is skipped and the
        raw mean is reported directly.
        """
        assert self.bundle is not None  # checked by caller
        stats = self.stats_by_set.get(set_code)
        shrunk = stats.shrunk if stats is not None else {}
        zscores = stats.zscores if stats is not None else {}

        # Determine whether a floor level is valid. The model's
        # `predict_win_probability` accepts `mulligan_number` up to
        # _MAX_MULLIGAN_NUMBER inclusive; anything beyond is out of
        # the training distribution and we don't trust it.
        deeper_level = mulligan_number_to + 1
        apply_floor = deeper_level <= _MAX_MULLIGAN_NUMBER

        rng = random.Random(seed)
        # Smoother input is the ``Card`` runtime wrapper (it needs the
        # is_land flag). Wrap each ParsedCard once and reuse the
        # wrappers across mulligan samples — re-wrapping per sample is
        # the same instances just with new ids, and we only care about
        # is_land for smoother weighting, never about instance_id at
        # this layer.
        deck_cards = [Card(instance_id=i, parsed=p) for i, p in enumerate(deck)]
        p_target: list[float] = []
        p_deeper: list[float] = []
        for _ in range(n_mulligan_samples):
            # Arena BO1 smoother: select one of N shuffles weighted by
            # how close the hand's land fraction is to the deck's.
            # This matches how 17Lands opening hands were drawn, so
            # the model is in-distribution. Plain ``rng.sample`` would
            # over-represent land-flooded / land-screwed hands and
            # bias p_mull downward.
            hand_cards, _library_cards = draw_smoothed_hand(deck_cards, rng)
            sample_hand = [c.parsed for c in hand_cards]
            sample_seed = rng.randint(0, 2**31 - 1)
            if apply_floor:
                pt, pd = _predict_levels_for_hand(
                    self.bundle,
                    hand=sample_hand,
                    deck=deck,
                    on_the_play=on_the_play,
                    opp_mulligan_number=opp_mulligan_number,
                    set_code=set_code,
                    shrunk=shrunk,
                    zscores=zscores,
                    mulligan_levels=(mulligan_number_to, deeper_level),
                    n_sims=n_sims_per_mulligan,
                    seed=sample_seed,
                )
                p_target.append(pt)
                p_deeper.append(pd)
            else:
                # Boundary case: still need the target-level
                # prediction, but skip the deeper one.
                (pt,) = _predict_levels_for_hand(
                    self.bundle,
                    hand=sample_hand,
                    deck=deck,
                    on_the_play=on_the_play,
                    opp_mulligan_number=opp_mulligan_number,
                    set_code=set_code,
                    shrunk=shrunk,
                    zscores=zscores,
                    mulligan_levels=(mulligan_number_to,),
                    n_sims=n_sims_per_mulligan,
                    seed=sample_seed,
                )
                p_target.append(pt)

        raw_mean = float(np.mean(p_target))
        if not apply_floor:
            return MulliganArmResult(
                floored_mean=raw_mean,
                raw_mean=raw_mean,
                floor_value=None,
                n_samples=n_mulligan_samples,
                n_samples_below_floor=0,
            )

        floor_value = float(np.mean(p_deeper))
        n_below = sum(1 for p in p_target if p < floor_value)
        floored_values = [max(p, floor_value) for p in p_target]
        floored_mean = float(np.mean(floored_values))
        return MulliganArmResult(
            floored_mean=floored_mean,
            raw_mean=raw_mean,
            floor_value=floor_value,
            n_samples=n_mulligan_samples,
            n_samples_below_floor=n_below,
        )


# ---------------------------------------------------------------------------
# Shared-simulate two-level prediction helper
# ---------------------------------------------------------------------------


def _predict_levels_for_hand(
    bundle: ModelBundle,
    *,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    on_the_play: bool,
    opp_mulligan_number: int | None,
    set_code: str,
    shrunk: dict[int, ShrunkWinRates],
    zscores: dict[int, CardZScores],
    mulligan_levels: tuple[int, ...],
    n_sims: int,
    seed: int,
) -> tuple[float, ...]:
    """Predict P(win) at multiple mulligan levels for the *same* hand.

    The simulator output and the baseline margin don't depend on
    ``mulligan_number`` — it's a context one-hot the model reads from
    the feature row plus a passthrough on the baseline (and even
    there the baseline cancels in the keep-vs-mull comparison).
    Sharing the simulate call between levels is ~2x faster than
    invoking :func:`predict_win_probability` once per level because
    simulate dominates the per-call cost (see the timing breakdown
    in CLAUDE.md).

    Inlines the body of :func:`mulligan_coach_model.predict_win_probability`
    deliberately — we import its two private helpers
    (``_library_from_deck`` and ``_predict_proba``) to keep behaviour
    identical without paying for a redundant simulate per level.
    """
    library = _library_from_deck(tuple(hand), tuple(deck))
    aggregate = simulate(
        list(hand),
        list(library),
        on_the_play=on_the_play,
        n_runs=n_sims,
        seed=seed,
    )
    base_margin = bundle.baseline.margin(
        user_wr_bucket=None,
        user_n_games_bucket=None,
        on_the_play=on_the_play,
        opp_mulligan_number=opp_mulligan_number,
    )
    # Same opp_mulligan_count_if_known computation predict_win_probability
    # does — conditional on on-the-play info-set.
    if on_the_play or opp_mulligan_number is None:
        opp_value: float = float("nan")
    else:
        opp_value = float(opp_mulligan_number)

    out: list[float] = []
    for mn in mulligan_levels:
        row = build_feature_row(
            hand=list(hand),
            deck=list(deck),
            aggregate_stats=aggregate,
            shrunk=shrunk,
            zscores=zscores,
            on_the_play=on_the_play,
            mulligan_number=mn,
            event_type=_DEFAULT_EVENT_TYPE,
            set_code=set_code,
        )
        row["opp_mulligan_count_if_known"] = opp_value
        out.append(_predict_proba(bundle, row, base_margin))
    return tuple(out)


# ---------------------------------------------------------------------------
# Public entry: build the service at startup
# ---------------------------------------------------------------------------


def _model_dir() -> Path:
    """Resolve the legacy win-model directory from env var or default."""
    raw = os.environ.get("MULLIGAN_COACH_MODEL_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_MODEL_DIR


def _choice_model_dir() -> Path:
    """Resolve the production choice-model directory from env var or default."""
    raw = os.environ.get("MULLIGAN_COACH_CHOICE_MODEL_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_CHOICE_MODEL_DIR


def load_service(set_codes: list[str]) -> RecommendationService:
    """Build the :class:`RecommendationService` for the given sets.

    The **choice model** (``models/choice_v6`` by default) is the
    production load target — ``status.model_loaded`` reflects whether
    that bundle came up. The legacy **win model** (``models/all3_v2``)
    is loaded best-effort alongside it but its absence doesn't fail
    the service: the production recommendation path
    (:meth:`RecommendationService.recommend_choice`) only needs the
    choice bundle.

    Missing per-set ratings parquet → that set is omitted from
    ``stats_by_set`` and listed in ``status.formats_missing_stats``.
    """
    # Choice model — the one the overlay and website actually call.
    choice_model_dir = _choice_model_dir()
    choice_bundle: ChoiceModelBundle | None = None
    err: str | None = None
    if choice_model_dir.exists():
        try:
            choice_bundle = ChoiceModelBundle.load(choice_model_dir)
            log.info("loaded choice-model bundle from %s", choice_model_dir)
        except Exception as exc:
            err = f"Failed to load choice model from {choice_model_dir}: {exc}"
            log.exception("choice model load failed")
    else:
        err = (
            f"Choice-model directory {choice_model_dir} does not exist. "
            "Train models/choice_v6 or set MULLIGAN_COACH_CHOICE_MODEL_DIR."
        )
        log.warning("%s", err)

    # Legacy win model — optional, only consulted by analysis scripts.
    # We swallow load failures silently here; an analysis script that
    # needs the win bundle can re-check status / model_dir.
    model_dir = _model_dir()
    bundle: ModelBundle | None = None
    if model_dir.exists():
        try:
            bundle = ModelBundle.load(model_dir)
            log.info("loaded legacy win-model bundle from %s", model_dir)
        except Exception:
            log.exception("legacy win-model load failed (continuing without it)")

    stats_by_set: dict[str, FormatStats] = {}
    missing: list[str] = []
    for set_code in set_codes:
        stats = _try_load_format_stats(set_code)
        if stats is not None:
            stats_by_set[set_code] = stats
        else:
            missing.append(set_code)

    status = ServiceStatus(
        model_loaded=choice_bundle is not None,
        model_dir=choice_model_dir,
        formats_with_stats=sorted(stats_by_set.keys()),
        formats_missing_stats=sorted(missing),
        error=err,
    )
    # Two-worker pool: one for the keep arm, one for the mulligan
    # prefetch / arm. Threading is enough because the simulator
    # releases the GIL through numpy and XGBoost predict is C-side.
    # Process pool would parallelise more cleanly but adds pickle /
    # spawn overhead that's not worth it at this scale.
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mc-rec")
    return RecommendationService(
        bundle=bundle,
        choice_bundle=choice_bundle,
        stats_by_set=stats_by_set,
        status=status,
        executor=executor,
        mulligan_cache=_MulliganArmCache(),
    )
