"""Sample-size shrinkage for 17Lands per-card win rates.

The 17Lands per-card win rates (OH WR, GD WR, GIH WR) are noisy for
cards with few observations. This module shrinks them toward a sensible
prior so that downstream features (the XGBoost model's hand- and
deck-level inputs) get stable estimates even on rare or just-released
cards.

Two design choices, made together, resolve the natural tension between
"low N is noise → shrink" and "low N might mean the card is bad → don't
shrink upward":

1. **N = ``pick_count``.** The shrinkage weight is
   ``w = pick_count / (pick_count + K_BASE)``. Selection is informative
   — a card picked many times but played in only a handful of games
   has lots of "people don't want to play this" evidence, and using
   ``pick_count`` (not the per-WR ``*_game_count``) keeps ``w`` high in
   that case so the raw WR is trusted.
2. **Prior = play-rate-conditional mean.** Cards with similar
   ``play_rate`` have similar typical quality. The prior for a card
   with ``play_rate ≈ 0.05`` is the mean WR of the lowest-decile
   sideboard tier — much lower than the format-wide mean. So even when
   shrinkage is applied to a sideboard card, it's pulled toward
   "typical sideboard WR," not toward 0.53.

Formula, applied independently per WR field
(``opening_hand_win_rate`` / ``drawn_win_rate`` / ``ever_drawn_win_rate``):

::

    prior   = conditional_mean(card.play_rate)   if play_rate is not None
            = overall_set_mean(WR)               otherwise
    w       = pick_count / (pick_count + K_BASE)
    shrunk  = w * raw + (1 - w) * prior          if raw is not None
            = prior                              if raw is None    (no-stats imputation)

The conditional-mean prior is built once per (set, event_type) by
sorting the format's cards on ``play_rate``, splitting into 10 deciles,
and recording each decile's mean WR. Lookup is a binary search over
the 9 inner cut points. Decile binning is deliberate: transparent
(no isotonic / kernel-smoothing dependency), and ~25 cards per bin in
a Premier Draft set is plenty for stable means.

The single ``weight`` reported by :class:`ShrunkWinRates` is the same
``w`` for all three WRs because ``pick_count`` is per-card, not
per-WR-field.
"""

from __future__ import annotations

import bisect
import warnings
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from mulligan_coach_cards import SeventeenLandsStats

from .stats_join import fold_card_name

# Default prior strength. With pick_count typically in the thousands for
# non-mythic cards in a Premier Draft format on 17Lands, k=500 gives
# w ≈ 0.85+ for commons (mild shrinkage) and w ≈ 0.3-0.5 for mythics
# (heavy shrinkage). Tunable via the `k_base` parameter on shrink_stats.
DEFAULT_K_BASE: float = 500.0

# Number of equal-count play_rate bins for the conditional-mean lookup.
# 10 deciles, ~25 cards per bin (Premier Draft sets have ~250 stats
# rows) is enough for stable bin means without flattening the
# play_rate -> WR slope.
DEFAULT_N_BINS: int = 10

# WR fields we shrink. Names match the underlying SeventeenLandsStats
# attributes so a single `getattr(stats, field)` works for both data
# and prior lookup.
_WR_FIELDS: tuple[str, ...] = (
    "opening_hand_win_rate",
    "drawn_win_rate",
    "ever_drawn_win_rate",
)


@dataclass(frozen=True)
class PlayRateBins:
    """Decile-binned play_rate → mean WR lookup table.

    ``edges`` holds the play_rate values at the boundaries between
    consecutive bins (so ``len(edges) == len(means) - 1``). ``means``
    holds the mean WR within each bin. ``lookup(play_rate)`` returns
    the bin's mean WR via binary search.

    An empty table (``means == ()``) means there were no cards with both
    a non-None play_rate and the relevant WR — ``lookup`` then returns
    ``None`` and callers fall back to the overall set mean.
    """

    edges: tuple[float, ...]
    means: tuple[float, ...]

    def lookup(self, play_rate: float) -> float | None:
        """Return the mean WR for the bin containing ``play_rate``.

        Uses ``bisect_right``: equal values clump into the higher bin,
        which matches how ``np.array_split`` assigns boundary cards.
        Returns ``None`` for an empty lookup.
        """
        if not self.means:
            return None
        idx = bisect.bisect_right(self.edges, play_rate)
        return self.means[idx]


@dataclass(frozen=True)
class FormatPriors:
    """Per-(set, event_type) priors used as shrinkage targets.

    For each WR field we hold:

    * ``overall_mean_*`` — the unweighted mean across cards with that WR
      published. Used as the prior when a card has no ``play_rate``
      (and as a fallback when the conditional lookup returns None).
    * ``conditional_mean_*`` — a 10-bin :class:`PlayRateBins` keyed on
      ``play_rate``. The primary prior.

    ``n_cards`` is the number of stats rows that contributed (regardless
    of how many had each particular WR populated).
    """

    expansion: str
    event_type: str
    overall_mean_oh_wr: float | None
    overall_mean_gd_wr: float | None
    overall_mean_gih_wr: float | None
    conditional_mean_oh_wr: PlayRateBins
    conditional_mean_gd_wr: PlayRateBins
    conditional_mean_gih_wr: PlayRateBins
    n_cards: int


@dataclass(frozen=True)
class ShrunkWinRates:
    """One card's sample-shrunk OH / GD / GIH win rates.

    Each ``shrunk_*`` field mirrors the nullability of the underlying
    raw stat: ``None`` when both the raw WR and every available prior
    were ``None`` (e.g. a brand-new format with no published rates).

    ``weight`` is the single ``w = pick_count / (pick_count + k_base)``
    shared across all three WRs (because ``pick_count`` is per-card,
    not per-WR-field). Recorded for diagnostics — telling you how much
    of each shrunk value came from the raw stat vs. the prior.

    ``name`` is the 17Lands display name; it is the join key (via
    :func:`stats_join.fold_card_name`) the feature builder uses.
    ``mtga_id`` is retained as an informational field only — the join
    no longer keys on it (see :mod:`stats_join`).
    """

    mtga_id: int
    name: str
    shrunk_opening_hand_win_rate: float | None
    shrunk_drawn_win_rate: float | None
    shrunk_ever_drawn_win_rate: float | None
    weight: float


def _build_play_rate_bins(pairs: list[tuple[float, float]], n_bins: int) -> PlayRateBins:
    """Build a decile-binned (play_rate, WR) → mean WR lookup.

    ``pairs`` must contain only (play_rate, wr) entries where both
    values are non-None. We sort by play_rate, split into ``n_bins``
    equal-count chunks via ``np.array_split`` (which handles non-divisible
    sizes by spreading the remainder across the first chunks), and
    record each chunk's mean WR plus its starting play_rate as an edge.

    If ``pairs`` is shorter than ``n_bins``, we use one bin per pair
    (so every bin has ≥1 card) instead of producing empty bins.
    """
    if not pairs:
        return PlayRateBins(edges=(), means=())

    pairs_sorted = sorted(pairs, key=lambda x: x[0])
    play_rates = np.array([p for p, _ in pairs_sorted], dtype=float)
    wrs = np.array([w for _, w in pairs_sorted], dtype=float)

    actual_bins = min(n_bins, len(pairs_sorted))
    chunks_pr = np.array_split(play_rates, actual_bins)
    chunks_wr = np.array_split(wrs, actual_bins)

    means: list[float] = []
    edges: list[float] = []
    for i, (cp, cw) in enumerate(zip(chunks_pr, chunks_wr, strict=True)):
        means.append(float(cw.mean()))
        if i > 0:
            # Edge = first play_rate of this bin. bisect_right then puts
            # equal-valued queries into THIS bin (the higher one), which
            # matches how np.array_split assigned the boundary card.
            edges.append(float(cp[0]))

    return PlayRateBins(edges=tuple(edges), means=tuple(means))


def compute_format_priors(
    stats: Iterable[SeventeenLandsStats], *, n_bins: int = DEFAULT_N_BINS
) -> FormatPriors:
    """Build the priors for one (set, event_type) from its stats rows.

    All input rows must share ``expansion`` and ``event_type`` — the
    shrinkage is per-format and mixing is almost certainly a caller bug.

    For each WR field we compute the unweighted mean across non-None
    values (the fallback prior) and a play-rate-conditional decile
    lookup (the primary prior). If every row has ``WR=None`` for some
    field, that field's overall mean is ``None`` and its conditional
    bins are empty — a warning is emitted and downstream shrinkage for
    that field returns whatever raw value the card had.
    """
    stats_list = list(stats)
    if not stats_list:
        raise ValueError("compute_format_priors: no stats rows provided")

    expansions = {s.expansion for s in stats_list}
    event_types = {s.event_type for s in stats_list}
    if len(expansions) != 1 or len(event_types) != 1:
        raise ValueError(
            "compute_format_priors: all stats must share (expansion, event_type); "
            f"got expansions={sorted(expansions)}, event_types={sorted(event_types)}"
        )
    expansion = next(iter(expansions))
    event_type = next(iter(event_types))

    overall: dict[str, float | None] = {}
    bins: dict[str, PlayRateBins] = {}
    for field in _WR_FIELDS:
        wrs = [v for s in stats_list if (v := getattr(s, field)) is not None]
        if wrs:
            overall[field] = float(np.mean(wrs))
        else:
            overall[field] = None
            warnings.warn(
                f"All cards in {expansion}/{event_type} have {field}=None; "
                "no shrinkage prior available for this field.",
                stacklevel=2,
            )
        pairs: list[tuple[float, float]] = [
            (s.play_rate, wr_val)
            for s in stats_list
            if s.play_rate is not None and (wr_val := getattr(s, field)) is not None
        ]
        bins[field] = _build_play_rate_bins(pairs, n_bins)

    return FormatPriors(
        expansion=expansion,
        event_type=event_type,
        overall_mean_oh_wr=overall["opening_hand_win_rate"],
        overall_mean_gd_wr=overall["drawn_win_rate"],
        overall_mean_gih_wr=overall["ever_drawn_win_rate"],
        conditional_mean_oh_wr=bins["opening_hand_win_rate"],
        conditional_mean_gd_wr=bins["drawn_win_rate"],
        conditional_mean_gih_wr=bins["ever_drawn_win_rate"],
        n_cards=len(stats_list),
    )


def _resolve_prior(
    play_rate: float | None,
    conditional: PlayRateBins,
    overall: float | None,
) -> float | None:
    """Pick the prior for one card and one WR field.

    Conditional mean (given play_rate) is preferred. If play_rate is
    None or the conditional lookup returns None (empty bin table), fall
    back to the overall mean. May still return None if neither is
    available — caller handles that.
    """
    if play_rate is not None:
        candidate = conditional.lookup(play_rate)
        if candidate is not None:
            return candidate
    return overall


def _shrink(raw: float | None, prior: float | None, weight: float) -> float | None:
    """Apply ``w * raw + (1 - w) * prior`` with None-handling.

    * Both None → None (no information at all).
    * Raw None, prior present → return prior (the no-stats imputation).
    * Raw present, prior None → return raw unchanged (no prior to pull
      toward; happens only in degenerate "every card in format has
      WR=None for this field" cases).
    * Both present → standard shrinkage.
    """
    if raw is None:
        return prior
    if prior is None:
        return raw
    return weight * raw + (1.0 - weight) * prior


def shrink_stats(
    stats: Iterable[SeventeenLandsStats],
    *,
    k_base: float = DEFAULT_K_BASE,
    priors: FormatPriors | None = None,
) -> dict[str, ShrunkWinRates]:
    """Shrink per-card OH / GD / GIH win rates toward a play-rate-conditional prior.

    Returns a dict keyed by :func:`stats_join.fold_card_name` of the
    card name — the join key the feature builder uses (independent of
    MTGJSON's arena_id lag). One entry per ratings row, no alias
    entries: :func:`zscore_stats` iterates the values and must see each
    card exactly once.

    Pass ``priors=`` to reuse a pre-computed :class:`FormatPriors` (e.g.
    when shrinking a subset of a format, or in tests). Otherwise priors
    are derived from ``stats`` itself.

    Raises ``ValueError`` when input rows mix ``(expansion, event_type)``
    or when ``k_base`` is negative.
    """
    if k_base < 0:
        raise ValueError(f"k_base must be >= 0; got {k_base}")
    stats_list = list(stats)
    if priors is None:
        priors = compute_format_priors(stats_list)

    result: dict[str, ShrunkWinRates] = {}
    for s in stats_list:
        if s.expansion != priors.expansion or s.event_type != priors.event_type:
            raise ValueError(
                f"shrink_stats: card {s.name!r} has (expansion, event_type)="
                f"({s.expansion}, {s.event_type}) which doesn't match priors "
                f"({priors.expansion}, {priors.event_type})"
            )

        prior_oh = _resolve_prior(
            s.play_rate, priors.conditional_mean_oh_wr, priors.overall_mean_oh_wr
        )
        prior_gd = _resolve_prior(
            s.play_rate, priors.conditional_mean_gd_wr, priors.overall_mean_gd_wr
        )
        prior_gih = _resolve_prior(
            s.play_rate, priors.conditional_mean_gih_wr, priors.overall_mean_gih_wr
        )

        # pick_count is always an int per the SeventeenLandsStats schema;
        # 0 just means "never picked" → weight 0 → result is the prior.
        pick = s.pick_count
        weight = pick / (pick + k_base) if (pick + k_base) > 0 else 0.0

        result[fold_card_name(s.name)] = ShrunkWinRates(
            mtga_id=s.mtga_id,
            name=s.name,
            shrunk_opening_hand_win_rate=_shrink(s.opening_hand_win_rate, prior_oh, weight),
            shrunk_drawn_win_rate=_shrink(s.drawn_win_rate, prior_gd, weight),
            shrunk_ever_drawn_win_rate=_shrink(s.ever_drawn_win_rate, prior_gih, weight),
            weight=weight,
        )
    return result
