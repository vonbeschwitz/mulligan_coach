"""Per-card z-scores of shrunk 17Lands win rates.

The XGBoost feature stage (per ``packages/features/features_list.md``)
includes performance bucket counts like
"number of hand spells with OH WR z-score > 1.3". This module computes
those z-scores, taking the shrunk OH / GD / GIH win rates as input
(from :func:`seventeenlands_shrinkage.shrink_stats`) and normalising
each card against its format's distribution.

Z-scoring against the **shrunk** values (rather than the raw 17Lands
output) is deliberate. Raw WRs include heavy noise for low-N cards;
shrinking first means the distribution we standardise against is
already pulled to a sensible per-format / per-play_rate-decile
reference. The downstream model can then trust that "z-score > 1.3"
corresponds to a card that's genuinely above-average, not one that's
seen 30 games and rolled a few wins.

Formula, per WR field, per ``(set, event_type)``:

::

    mean = mean(shrunk_WR across cards with shrunk_WR is not None)
    std  = std (shrunk_WR across cards with shrunk_WR is not None)   # population std (ddof=0)
    z    = (shrunk_WR - mean) / std                                  if both inputs are non-None and std > 0
         = None                                                      otherwise

``std`` uses ``ddof=0`` (population) rather than ``ddof=1`` (sample)
because we treat the format's cards as the whole population, not a
sample drawn from a wider distribution. With ~250 cards per Premier
Draft set the numerical difference is tiny (~0.2%), but ddof=0 is the
correct semantic.

API mirrors :mod:`seventeenlands_shrinkage` so callers can use the
two side-by-side:

* :class:`FormatWRDistribution` — per-(set, event_type) reference
  means and stds. Analog of :class:`FormatPriors`.
* :class:`CardZScores` — per-card z-scores. Analog of
  :class:`ShrunkWinRates`.
* :func:`compute_format_wr_distribution` — build the reference from a
  format's shrunk rows. Analog of :func:`compute_format_priors`.
* :func:`zscore_stats` — apply the reference to compute per-card
  z-scores. Analog of :func:`shrink_stats`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from .seventeenlands_shrinkage import ShrunkWinRates

# Field-name pairs: (ShrunkWinRates attribute, FormatWRDistribution mean/std stem).
# Kept here so adding a new WR field is a single edit.
_WR_FIELDS: tuple[tuple[str, str], ...] = (
    ("shrunk_opening_hand_win_rate", "oh_wr"),
    ("shrunk_drawn_win_rate", "gd_wr"),
    ("shrunk_ever_drawn_win_rate", "gih_wr"),
)


@dataclass(frozen=True)
class FormatWRDistribution:
    """Per-(set, event_type) reference distribution of shrunk WRs.

    For each WR field we hold the mean and std across all cards whose
    shrunk value is non-None. ``n_cards`` counts the shrunk-WR rows
    that contributed (regardless of how many had any particular field
    populated).

    Either ``mean`` or ``std`` can be ``None`` for a field where every
    card came back ``shrunk_X is None`` — in that case z-scoring that
    field returns ``None`` per card. ``std`` can also be ``0.0`` in
    degenerate test cases (every card has the exact same shrunk value);
    callers must treat ``std == 0.0`` as "no signal" and emit ``None``.
    """

    mean_oh_wr: float | None
    std_oh_wr: float | None
    mean_gd_wr: float | None
    std_gd_wr: float | None
    mean_gih_wr: float | None
    std_gih_wr: float | None
    n_cards: int


@dataclass(frozen=True)
class CardZScores:
    """One card's z-scores against its format's shrunk-WR distribution.

    Each ``z_*`` field mirrors the nullability of the underlying
    ``shrunk_*`` value plus the format's mean/std — returns ``None``
    when the card has no shrunk WR for that field OR when the
    reference distribution has no signal for the field (every card's
    shrunk value was ``None``, or every card landed on the same value
    so ``std == 0.0``).
    """

    mtga_id: int
    name: str
    z_opening_hand_win_rate: float | None
    z_drawn_win_rate: float | None
    z_ever_drawn_win_rate: float | None


def compute_format_wr_distribution(
    shrunk: Iterable[ShrunkWinRates],
) -> FormatWRDistribution:
    """Compute the mean and std of each shrunk WR field across the format.

    The input should be one format's worth of :class:`ShrunkWinRates`
    rows — typically the values of the dict returned by
    :func:`seventeenlands_shrinkage.shrink_stats`. Unlike
    :func:`compute_format_priors`, this function doesn't need to know
    ``expansion`` / ``event_type``: the shrunken values it consumes
    have already been computed per-format upstream, so mixing formats
    is the caller's bug to avoid (and would just produce a less useful
    distribution, not an outright error).

    Raises ``ValueError`` when the input is empty.
    """
    shrunk_list = list(shrunk)
    if not shrunk_list:
        raise ValueError("compute_format_wr_distribution: no shrunk WR rows provided")

    stats: dict[str, tuple[float | None, float | None]] = {}
    for attr, stem in _WR_FIELDS:
        values = [v for s in shrunk_list if (v := getattr(s, attr)) is not None]
        if values:
            arr = np.array(values, dtype=float)
            # Population std (ddof=0): we're describing the format itself,
            # not estimating a wider population from a sample.
            stats[stem] = (float(arr.mean()), float(arr.std(ddof=0)))
        else:
            stats[stem] = (None, None)

    return FormatWRDistribution(
        mean_oh_wr=stats["oh_wr"][0],
        std_oh_wr=stats["oh_wr"][1],
        mean_gd_wr=stats["gd_wr"][0],
        std_gd_wr=stats["gd_wr"][1],
        mean_gih_wr=stats["gih_wr"][0],
        std_gih_wr=stats["gih_wr"][1],
        n_cards=len(shrunk_list),
    )


def _zscore(value: float | None, mean: float | None, std: float | None) -> float | None:
    """Standard score, with None / zero-std handled as 'no signal' → None.

    ``std == 0.0`` happens in degenerate test fixtures (every card has
    the same shrunk WR) and would otherwise blow up to NaN. Returning
    None there is correct — there's no spread to normalise against.
    """
    if value is None or mean is None or std is None or std == 0.0:
        return None
    return (value - mean) / std


def zscore_stats(
    shrunk: Iterable[ShrunkWinRates],
    *,
    distribution: FormatWRDistribution | None = None,
) -> dict[int, CardZScores]:
    """Compute per-card z-scores of shrunk OH / GD / GIH win rates.

    Returns a dict keyed by ``mtga_id`` so callers can join against
    ``StatsLookup.by_arena_id`` or any other arena-id-indexed structure
    — the same key the upstream :func:`shrink_stats` uses.

    Pass ``distribution=`` to reuse a pre-computed
    :class:`FormatWRDistribution` (e.g. when z-scoring a subset of the
    format, or in tests). Otherwise the distribution is computed from
    ``shrunk`` itself.
    """
    shrunk_list = list(shrunk)
    if distribution is None:
        distribution = compute_format_wr_distribution(shrunk_list)

    result: dict[int, CardZScores] = {}
    for s in shrunk_list:
        result[s.mtga_id] = CardZScores(
            mtga_id=s.mtga_id,
            name=s.name,
            z_opening_hand_win_rate=_zscore(
                s.shrunk_opening_hand_win_rate,
                distribution.mean_oh_wr,
                distribution.std_oh_wr,
            ),
            z_drawn_win_rate=_zscore(
                s.shrunk_drawn_win_rate,
                distribution.mean_gd_wr,
                distribution.std_gd_wr,
            ),
            z_ever_drawn_win_rate=_zscore(
                s.shrunk_ever_drawn_win_rate,
                distribution.mean_gih_wr,
                distribution.std_gih_wr,
            ),
        )
    return result
