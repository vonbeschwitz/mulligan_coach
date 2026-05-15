"""Model-loading and recommendation orchestration.

The website's :mod:`app` calls into this module at three points:

1. At startup (inside the FastAPI ``lifespan``), :func:`load_service`
   assembles a :class:`RecommendationService` — loads the trained
   model bundle, builds per-format shrunk-WR / z-score dicts, and
   spins up a small thread-pool + mulligan-arm cache.
2. Per ``POST /validate`` request, the route calls
   :meth:`RecommendationService.prefetch_mulligan` to fire-and-forget
   compute the mulligan arm for the freshly-pasted deck in the
   background. Result lands in the cache; the user's eventual
   click on *Keep or mulligan?* awaits a Future that's usually
   already done.
3. Per ``POST /recommend`` request, the route calls
   :meth:`RecommendationService.recommend_asymmetric` which runs the
   keep arm inline while pulling the mulligan arm from the cache
   (computing it on the spot if no prefetch covered this case).

Why asymmetric sims
-------------------

The keep arm evaluates a single specific hand; precision (per-hand
``n_sims``) is the only dial. The mulligan arm averages predicted
P(win) over ``n_mulligan_samples`` independent freshly-drawn hands;
*between-hand* variance dominates the *within-hand* sampling noise.
So a budget of M total mulligan-arm simulations is better spent on
many samples x few sims-per-sample than the reverse. Defaults below
reflect that: keep=1000 sims for one hand, mulligan=50 hands x 40
sims each (=2000 sims total, 2x the keep budget, but spent on
sample diversity rather than per-hand precision).

Loading is best-effort: if the model directory doesn't exist (fresh
checkout) or the ratings parquet is missing for a set, the affected
piece is omitted and a clear human message is surfaced via
:class:`ServiceStatus`. The route layer then renders that message
instead of crashing — the website is usable for deck-paste / hand-
building even before the model is trained.
"""

from __future__ import annotations

import hashlib
import logging
import os
import random
import threading
from collections import Counter, OrderedDict
from collections.abc import Iterable
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
from mulligan_coach_model import ModelBundle, Recommendation, predict_win_probability, recommend
from mulligan_coach_model.feature_matrix import _library_from_deck
from mulligan_coach_model.inference import _predict_proba
from mulligan_coach_simulation import simulate

log = logging.getLogger(__name__)

# Default model directory — checked relative to the workspace root
# (the parent of `packages/`). Override via env var.
_DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[4] / "models" / "tla_v2"
_DEFAULT_EVENT_TYPE = "PremierDraft"

# Default sim budgets for the asymmetric path. The keep arm runs once;
# the mulligan arm averages over many independent draws so precision
# benefits more from samples than from per-sample sim depth (see the
# module docstring). 50x40 = 2k total mulligan sims is ~3x the keep
# arm by wall clock without ballooning user wait.
DEFAULT_N_SIMS_KEEP = 1000
DEFAULT_N_SIMS_PER_MULLIGAN = 40
DEFAULT_N_MULLIGAN_SAMPLES = 50

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
class AsymmetricRecommendation:
    """Result of :meth:`RecommendationService.recommend_asymmetric`.

    Same shape as :class:`mulligan_coach_model.Recommendation` plus
    diagnostic fields about the sim budget used, the deeper-mulligan
    floor adjustment, and whether the mulligan arm came from the
    prefetch cache.
    """

    verdict: Literal["keep", "mulligan"]
    keep_win_probability: float
    mulligan_win_probability: float
    delta: float
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
        compute_fn,
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
        p_keep = self._compute_keep_arm(
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
        verdict: Literal["keep", "mulligan"] = "keep" if delta >= 0 else "mulligan"
        return AsymmetricRecommendation(
            verdict=verdict,
            keep_win_probability=p_keep,
            mulligan_win_probability=p_mull,
            delta=delta,
            mulligan_number_from=mulligan_number,
            mulligan_number_to=mulligan_number + 1,
            n_sims_keep=n_sims_keep,
            n_sims_per_mulligan=n_sims_per_mulligan,
            n_mulligan_samples=n_mulligan_samples,
            mulligan_arm_was_cached=cached,
            mulligan_arm_raw_mean=mull_result.raw_mean,
            mulligan_arm_floor=mull_result.floor_value,
            n_samples_below_floor=mull_result.n_samples_below_floor,
        )

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
    ) -> float:
        """Single :func:`predict_win_probability` call for the kept hand."""
        assert self.bundle is not None  # checked by caller
        stats = self.stats_by_set.get(set_code)
        return predict_win_probability(
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
            seed=seed,
        )

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
        p_target: list[float] = []
        p_deeper: list[float] = []
        for _ in range(n_mulligan_samples):
            indices = rng.sample(range(len(deck)), 7)
            sample_hand = [deck[i] for i in indices]
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
    """Resolve the model directory from env var or the default path."""
    raw = os.environ.get("MULLIGAN_COACH_MODEL_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_MODEL_DIR


def load_service(set_codes: list[str]) -> RecommendationService:
    """Build the :class:`RecommendationService` for the given sets.

    Best-effort: missing model directory → ``bundle=None`` and the
    status reflects that. Missing per-set ratings parquet → that set
    is omitted from ``stats_by_set`` and listed in
    ``status.formats_missing_stats``. The website still loads either
    way; ``/recommend`` returns a clear error message when ``bundle``
    is None.
    """
    model_dir = _model_dir()
    bundle: ModelBundle | None = None
    err: str | None = None
    if model_dir.exists():
        try:
            bundle = ModelBundle.load(model_dir)
            log.info("loaded model bundle from %s", model_dir)
        except Exception as exc:
            err = f"Failed to load model from {model_dir}: {exc}"
            log.exception("model load failed")
    else:
        err = (
            f"Model directory {model_dir} does not exist. "
            "Train a model or set MULLIGAN_COACH_MODEL_DIR."
        )
        log.warning("%s", err)

    stats_by_set: dict[str, FormatStats] = {}
    missing: list[str] = []
    for set_code in set_codes:
        stats = _try_load_format_stats(set_code)
        if stats is not None:
            stats_by_set[set_code] = stats
        else:
            missing.append(set_code)

    status = ServiceStatus(
        model_loaded=bundle is not None,
        model_dir=model_dir,
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
        stats_by_set=stats_by_set,
        status=status,
        executor=executor,
        mulligan_cache=_MulliganArmCache(),
    )
