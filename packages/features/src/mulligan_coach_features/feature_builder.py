"""Assemble the 196-feature XGBoost row for one (hand, deck) pair.

Three sub-builders, one top-level composer:

* :func:`build_deck_features` — 16 deck-shape features (curve, removal %,
  color counts, avg WR of spells).
* :func:`build_hand_features` — 72 hand-shape features (basic counts,
  role-by-MV grid, Z-bucket counts, hand quality, color availability).
* :func:`build_simulation_features` — 105 features sourced from the
  Monte Carlo aggregate (mana-availability, per-turn castability across
  many overlapping subsets).
* :func:`build_feature_row` — 3 context features (on_the_play,
  event_type one-hot, set_code one-hot) plus the three sub-builders'
  outputs, returned as one flat ``dict[str, float]``.

The feature catalogue lives in ``features_list.md``. Naming follows
the spec's snake_case names where given, and uses a regular schema for
the castability section (which the spec describes only by category +
turn):

* ``p_any_<category>_t<N>`` — P(at least one card in category castable
  on turn N).
* ``avg_count_<category>_t<N>`` — expected number of castable cards
  in category on turn N.
* ``p_any_<category>_high_oh_t<N>`` / ``avg_count_<category>_high_oh_t<N>``
  — same, restricted to cards with shrunken OH WR z-score > 0.5.
  Emitted only for the three broad buckets (any spell / creature /
  removal) where typical hands have enough matching cards to make the
  high-WR split signal-bearing.

Per-card castability comes from ``aggregate_stats.by_card_name`` —
the simulator aggregates by name, so duplicate hand copies of the
same card share the same per-card P(castable | in hand by turn T).
For the "any castable" feature we treat the duplicates as
independent given that probability, i.e.
``P(any) = 1 - product(1 - p_i)``. This is the natural approximation
given per-name aggregate output and is the same shape the model
will see at training time.

The ``high_oh`` filter uses the **shrunk** OH WR z-score, per spec —
matching the Z-bucket feature definition in the hand-level section.

Inputs the builder needs:

* ``hand`` and ``deck`` as lists of ``ParsedCard`` — the deck includes
  the hand (so deck-level features are over the full deck).
* ``aggregate_stats`` — output of ``simulate(hand, library, ...)``.
* ``shrunk`` and ``zscores`` — dicts keyed by folded card name
  (``stats_join.fold_card_name``), the shape ``shrink_stats`` /
  ``zscore_stats`` return. Looked up per card via
  ``stats_join.stats_for_card`` (folded-name match + DFC front-face
  fallback), so a card without an ``arena_id`` still joins.
* ``on_the_play``, ``mulligan_number``, ``event_type``, ``set_code``
  — context. ``known_event_types`` / ``known_sets`` parameterise the
  one-hot columns so new sets at inference time get all-zero columns
  rather than blowing up the feature row's shape.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from math import prod

from mulligan_coach_cards import ParsedCard
from mulligan_coach_simulation import AggregateStats

from . import categories as cat
from .seventeenlands_shrinkage import ShrunkWinRates
from .seventeenlands_zscores import CardZScores
from .stats_join import stats_for_card

# Z-bucket boundaries from features_list.md. Each card lands in exactly
# one bucket per WR field (non-overlapping); cards with z=None drop out
# of all buckets.
_Z_GT_1_3 = 1.3
_Z_GT_0_4 = 0.4
_Z_GT_NEG_0_7 = -0.7

# High-OH-WR threshold for the castability split (different from the
# bucket boundaries: the spec calls for z > 0.5 here).
_HIGH_OH_THRESHOLD = 0.5

# Default one-hot vocabularies for context features. The spec says new
# sets / event types at inference time should fall through to all-zero
# columns; we keep these as defaults but let callers override.
DEFAULT_KNOWN_EVENT_TYPES: tuple[str, ...] = ("PremierDraft", "Sealed", "TradDraft")
# APPEND new sets — never reorder existing entries. The one-hot column
# name for a set is stable (``set_code_<S>``), but the *order* here is
# what a human sees when diffing a materialised feature row, so keeping
# TMT/ECL/TLA first keeps old rows visually stable. SOS + MSH were added
# in the FEATURES_SEMANTICS_VERSION 1 -> 2 bump (roadmap Step 2): before
# that, SOS trained as the all-zero reference category and MSH (live on
# Arena since 2026-06-26) was indistinguishable from it at inference.
DEFAULT_KNOWN_SETS: tuple[str, ...] = ("TMT", "ECL", "TLA", "SOS", "MSH")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _safe_div(num: float, denom: float) -> float:
    """``num / denom``, returning 0.0 when ``denom == 0``.

    Used wherever the feature row's denominator might be zero — e.g. a
    deck-level percentage when there happens to be no nonland in the
    deck (degenerate), or an average over an empty hand subset. 0.0 is
    the conventional default for absent features in XGBoost rows.
    """
    return num / denom if denom else 0.0


def _shrunk_for(card: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> ShrunkWinRates | None:
    """Lookup helper: folded-name join with DFC front-face fallback.

    Returns ``None`` when the card has no ratings row. Independent of
    ``arena_id`` — a freshly-rotated set whose printings MTGJSON hasn't
    ingested still joins by name."""
    return stats_for_card(card, shrunk)


def _zscores_for(card: ParsedCard, zscores: dict[str, CardZScores]) -> CardZScores | None:
    """Same shape as :func:`_shrunk_for`, for z-scores."""
    return stats_for_card(card, zscores)


def _z_oh(card: ParsedCard, zscores: dict[str, CardZScores]) -> float | None:
    """OH WR z-score for the card, or None if unknown."""
    cz = _zscores_for(card, zscores)
    if cz is None:
        return None
    return cz.z_opening_hand_win_rate


def _z_gd(card: ParsedCard, zscores: dict[str, CardZScores]) -> float | None:
    """GD WR z-score for the card, or None if unknown."""
    cz = _zscores_for(card, zscores)
    if cz is None:
        return None
    return cz.z_drawn_win_rate


def _is_high_oh(card: ParsedCard, zscores: dict[str, CardZScores]) -> bool:
    """``z_OH > 0.5`` for the per-turn castability high-WR split.

    Cards without a published z-score (no ratings row, or the format's
    z-distribution had no signal) fall to False, conservatively — they
    aren't counted as high-WR.
    """
    z = _z_oh(card, zscores)
    return z is not None and z > _HIGH_OH_THRESHOLD


# ---------------------------------------------------------------------------
# Deck-level features (16)
# ---------------------------------------------------------------------------


def build_deck_features(
    deck: Sequence[ParsedCard],
    *,
    shrunk: dict[str, ShrunkWinRates],
) -> dict[str, float]:
    """Compute the 16 deck-level features per features_list.md.

    All "% of nonland" features use ``count / nonland_total`` so a
    +1-land deck doesn't inflate creature percentages. The single
    "% lands" feature uses the whole deck size as denominator (it's
    the lone exception, called out in the spec).
    """
    out: dict[str, float] = {}
    deck_list = list(deck)
    total = len(deck_list)
    lands = [c for c in deck_list if cat.is_land(c)]
    spells = [c for c in deck_list if cat.is_spell(c)]
    n_spells = len(spells)

    # 1. % lands of the FULL deck — handles 41+ card decks.
    out["pct_lands_in_deck"] = _safe_div(len(lands), total)

    # 2-3. % creatures / removal of nonland.
    creatures = [c for c in spells if cat.is_creature_strict(c)]
    removal = [c for c in spells if cat.is_removal(c)]
    out["pct_creatures_in_deck"] = _safe_div(len(creatures), n_spells)
    out["pct_removal_in_deck"] = _safe_div(len(removal), n_spells)

    # 4-11. Curve by MV bucket (% of nonland).
    for bucket_name, bucket_pred in (
        ("mv_le_2", cat.mv_le_2),
        ("mv_eq_3", cat.mv_eq_3),
        ("mv_4_5", cat.mv_4_5),
        ("mv_ge_6", cat.mv_ge_6),
    ):
        n_spells_in_bucket = sum(1 for c in spells if bucket_pred(c))
        n_creatures_in_bucket = sum(1 for c in creatures if bucket_pred(c))
        out[f"pct_spells_{bucket_name}_in_deck"] = _safe_div(n_spells_in_bucket, n_spells)
        out[f"pct_creatures_{bucket_name}_in_deck"] = _safe_div(n_creatures_in_bucket, n_spells)

    # 12-13. Color shape. We count, per color, how many spells need
    # that color (i.e. have at least one mandatory pip in color_pips).
    # Hybrid pips don't go into color_pips, so they don't force a
    # color count — matching the spec's "main color" intuition.
    per_color_count: dict[str, int] = {}
    for c in spells:
        if c.mana_cost is None:
            continue
        for color in c.mana_cost.color_pips:
            per_color_count[color] = per_color_count.get(color, 0) + 1
    out["n_main_colors_in_deck"] = float(sum(1 for n in per_color_count.values() if n > 3))
    out["n_total_colors_in_deck"] = float(sum(1 for n in per_color_count.values() if n > 0))

    # 14-16. Average shrunk WRs over deck spells. Lands are excluded —
    # they have no 17Lands win rate worth averaging in (most are basics
    # with WR null in the raw data).
    out["avg_oh_wr_of_spells"] = _avg_over(spells, lambda c: _shrunk_oh(c, shrunk))
    out["avg_gd_wr_of_spells"] = _avg_over(spells, lambda c: _shrunk_gd(c, shrunk))
    out["avg_gih_wr_of_spells"] = _avg_over(spells, lambda c: _shrunk_gih(c, shrunk))

    return out


def _shrunk_oh(c: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> float | None:
    s = _shrunk_for(c, shrunk)
    return None if s is None else s.shrunk_opening_hand_win_rate


def _shrunk_gd(c: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> float | None:
    s = _shrunk_for(c, shrunk)
    return None if s is None else s.shrunk_drawn_win_rate


def _shrunk_gih(c: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> float | None:
    s = _shrunk_for(c, shrunk)
    return None if s is None else s.shrunk_ever_drawn_win_rate


def _avg_over(cards: Iterable[ParsedCard], extract: Callable[[ParsedCard], float | None]) -> float:
    """Mean of a per-card extractor, skipping None.

    Returns 0.0 when every card returned None (or the input is empty).
    Used by the avg WR features and the hand-quality additions.
    """
    values = [v for c in cards if (v := extract(c)) is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


# ---------------------------------------------------------------------------
# Hand-level features (72)
# ---------------------------------------------------------------------------


def build_hand_features(
    hand: Sequence[ParsedCard],
    deck: Sequence[ParsedCard],
    *,
    shrunk: dict[str, ShrunkWinRates],
    zscores: dict[str, CardZScores],
    mulligan_number: int,
) -> dict[str, float]:
    """Compute the 72 hand-level features (excluding mana-availability,
    which lives in :func:`build_simulation_features`).

    ``deck`` is accepted for consistency with the other builders but is
    not read here — kept in the signature so the API is symmetric and
    future hand-level features that need deck context (e.g. "fraction
    of deck's bombs in this hand") have a place to land.
    """
    del deck  # currently unused; see docstring.
    out: dict[str, float] = {}
    hand_list = list(hand)
    spells_in_hand = [c for c in hand_list if cat.is_spell(c)]

    # --- Basic hand composition (13) -----------------------------------
    out["mulligan_number"] = float(mulligan_number)
    out["n_lands_in_hand"] = float(sum(1 for c in hand_list if cat.is_land(c)))
    out["n_nonbasic_lands_in_hand"] = float(sum(1 for c in hand_list if cat.is_nonbasic_land(c)))
    out["n_spells_in_hand"] = float(len(spells_in_hand))
    out["n_ramp_spells_in_hand"] = float(sum(1 for c in spells_in_hand if cat.is_ramp(c)))
    out["n_multi_modal_spells_in_hand"] = float(
        sum(1 for c in spells_in_hand if cat.has_alt_mode(c))
    )

    # MV ≥ 6 spells, split by whether they have an alt mode (cycling /
    # evoke / etc. give the hand an escape valve for the heavy card).
    mv6_plus = [c for c in spells_in_hand if cat.mv_ge_6(c)]
    out["n_spells_mv_ge_6_no_alt_mode_in_hand"] = float(
        sum(1 for c in mv6_plus if not cat.has_alt_mode(c))
    )
    out["n_spells_mv_ge_6_with_alt_mode_in_hand"] = float(
        sum(1 for c in mv6_plus if cat.has_alt_mode(c))
    )

    # Creatures by MV bucket. Uses STRICT creature definition (not
    # token-makers) — the basic hand counts mean "actual creature card".
    creatures_in_hand = [c for c in spells_in_hand if cat.is_creature_strict(c)]
    out["n_creatures_mv_le_2_in_hand"] = float(sum(1 for c in creatures_in_hand if cat.mv_le_2(c)))
    out["n_creatures_mv_3_in_hand"] = float(sum(1 for c in creatures_in_hand if cat.mv_eq_3(c)))
    out["n_creatures_mv_4_in_hand"] = float(sum(1 for c in creatures_in_hand if cat.cmc(c) == 4))
    out["n_creatures_mv_5_in_hand"] = float(sum(1 for c in creatures_in_hand if cat.cmc(c) == 5))
    out["n_creatures_mv_ge_6_in_hand"] = float(sum(1 for c in creatures_in_hand if cat.mv_ge_6(c)))

    # --- Role-by-MV grid (42) ------------------------------------------
    # 14 roles x 3 MV buckets (mv_0_2, mv_3, mv_4_5). The spec
    # deliberately stops at mv_4_5 here (no mv_ge_6 bucket).
    role_grid: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        ("removal_destroy_or_exile", lambda c: c.role_features.removal_destroy_or_exile),
        ("burn", lambda c: c.role_features.removal_burn_damage is not None),
        ("bounce", lambda c: c.role_features.is_bounce),
        ("top_library", lambda c: c.role_features.is_top_library),
        ("removal_aura", lambda c: c.role_features.is_removal_aura),
        ("punch_fight", lambda c: c.role_features.is_punch_fight),
        ("combat_trick", cat.is_combat_trick),
        ("pump_aura", lambda c: c.role_features.is_pump_aura),
        ("equipment", lambda c: c.role_features.is_equipment),
        ("vehicle", lambda c: c.role_features.is_vehicle),
        ("saga", lambda c: c.role_features.is_saga),
        ("class_card", lambda c: c.role_features.is_class),
        ("planeswalker", lambda c: c.role_features.is_planeswalker),
        ("card_draw_or_manipulation", cat.is_card_manipulation),
    ]
    mv_buckets: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        ("mv_0_2", cat.mv_0_2),
        ("mv_3", cat.mv_eq_3),
        ("mv_4_5", cat.mv_4_5),
    ]
    for role_name, role_pred in role_grid:
        for bucket_name, bucket_pred in mv_buckets:
            key = f"n_{role_name}_{bucket_name}_in_hand"
            out[key] = float(sum(1 for c in spells_in_hand if role_pred(c) and bucket_pred(c)))

    # --- Performance-based: Z-buckets + max/avg (11) -------------------
    # Each card lands in exactly one bucket per WR field. Cards with
    # z=None drop out of all buckets.
    oh_buckets = _z_bucket_counts(spells_in_hand, lambda c: _z_oh(c, zscores))
    gd_buckets = _z_bucket_counts(spells_in_hand, lambda c: _z_gd(c, zscores))
    out["n_spells_oh_z_gt_1_3"] = float(oh_buckets["gt_1_3"])
    out["n_spells_oh_z_0_4_to_1_3"] = float(oh_buckets["mid_high"])
    out["n_spells_oh_z_neg_0_7_to_0_4"] = float(oh_buckets["mid_low"])
    out["n_spells_oh_z_lt_neg_0_7"] = float(oh_buckets["lt_neg_0_7"])
    out["n_spells_gd_z_gt_1_3"] = float(gd_buckets["gt_1_3"])
    out["n_spells_gd_z_0_4_to_1_3"] = float(gd_buckets["mid_high"])
    out["n_spells_gd_z_neg_0_7_to_0_4"] = float(gd_buckets["mid_low"])
    out["n_spells_gd_z_lt_neg_0_7"] = float(gd_buckets["lt_neg_0_7"])

    # Hand-level summaries that round out the performance section.
    out["avg_gih_wr_of_hand_spells"] = _avg_over(spells_in_hand, lambda c: _shrunk_gih(c, shrunk))
    out["max_oh_wr_of_hand_spells"] = _max_over(spells_in_hand, lambda c: _shrunk_oh(c, shrunk))
    out["max_gd_wr_of_hand_spells"] = _max_over(spells_in_hand, lambda c: _shrunk_gd(c, shrunk))

    # --- Hand-quality additions (4) -------------------------------------
    out["sum_gih_wr_of_hand_spells"] = _sum_over(spells_in_hand, lambda c: _shrunk_gih(c, shrunk))
    # Earliness = OH - GD (front-loadedness). Per spell, average across
    # hand spells with both values populated.
    out["avg_earliness_score_of_hand"] = _avg_over(
        spells_in_hand, lambda c: _earliness_for(c, shrunk)
    )
    out["max_gih_wr_of_hand_spells"] = _max_over(spells_in_hand, lambda c: _shrunk_gih(c, shrunk))
    # Shrinkage weight is per-card (single ``w`` shared across WRs in
    # ShrunkWinRates). Use 1.0 as a sentinel "no data" — that's the
    # value a card-with-tons-of-data would have anyway and avoids
    # making this feature artificially low for unknown cards.
    out["avg_shrinkage_weight_of_hand_spells"] = _avg_over(
        spells_in_hand, lambda c: _shrinkage_weight_for(c, shrunk)
    )

    # --- Color-availability additions (2) -------------------------------
    out["n_double_or_triple_pip_cards_in_hand"] = float(
        sum(1 for c in spells_in_hand if _has_double_pip(c))
    )
    out["n_distinct_colors_required_by_hand"] = float(_distinct_colors_in_hand(spells_in_hand))

    return out


def _z_bucket_counts(
    cards: Iterable[ParsedCard], extract: Callable[[ParsedCard], float | None]
) -> dict[str, int]:
    """Bucket cards into the four non-overlapping Z-score bins.

    Spec:
        z > 1.3
        0.4 ≤ z ≤ 1.3
        -0.7 ≤ z < 0.4
        z < -0.7

    Boundary semantics taken from features_list.md — the top bin is
    strict (``> 1.3``), the middle two are half-open on the lower side,
    and the bottom is strict (``< -0.7``). Cards with z=None drop out.
    """
    counts = {"gt_1_3": 0, "mid_high": 0, "mid_low": 0, "lt_neg_0_7": 0}
    for c in cards:
        z = extract(c)
        if z is None:
            continue
        if z > _Z_GT_1_3:
            counts["gt_1_3"] += 1
        elif z >= _Z_GT_0_4:
            counts["mid_high"] += 1
        elif z >= _Z_GT_NEG_0_7:
            counts["mid_low"] += 1
        else:
            counts["lt_neg_0_7"] += 1
    return counts


def _max_over(cards: Iterable[ParsedCard], extract: Callable[[ParsedCard], float | None]) -> float:
    """Max of a per-card extractor, skipping None. 0.0 when all None."""
    values = [v for c in cards if (v := extract(c)) is not None]
    if not values:
        return 0.0
    return max(values)


def _sum_over(cards: Iterable[ParsedCard], extract: Callable[[ParsedCard], float | None]) -> float:
    """Sum of a per-card extractor, skipping None. 0.0 when all None.

    ``sum(...)`` on an empty / all-None iterable returns ``int(0)``;
    the explicit float start keeps the dtype consistent.
    """
    return sum((v for c in cards if (v := extract(c)) is not None), 0.0)


def _earliness_for(c: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> float | None:
    """Per-card earliness score = shrunk OH WR - shrunk GD WR.

    Front-loaded cards (early, situational, fade in the late game)
    score higher; late-game / mana-hungry cards score lower. Returns
    None when either WR is missing — the avg helper skips Nones.
    """
    s = _shrunk_for(c, shrunk)
    if s is None:
        return None
    if s.shrunk_opening_hand_win_rate is None or s.shrunk_drawn_win_rate is None:
        return None
    return s.shrunk_opening_hand_win_rate - s.shrunk_drawn_win_rate


def _shrinkage_weight_for(c: ParsedCard, shrunk: dict[str, ShrunkWinRates]) -> float | None:
    """The single ``w`` from ShrunkWinRates — tells the model how much
    raw data is behind this card's WRs. None if the card has no entry."""
    s = _shrunk_for(c, shrunk)
    return None if s is None else s.weight


def _has_double_pip(c: ParsedCard) -> bool:
    """At least one color in the card's cost has ≥ 2 mandatory pips."""
    if c.mana_cost is None:
        return False
    return any(n >= 2 for n in c.mana_cost.color_pips.values())


def _distinct_colors_in_hand(spells: Iterable[ParsedCard]) -> int:
    """Number of distinct WUBRG colors named by hand spells' costs.

    Only counts mandatory pips (``color_pips``); hybrid / phyrexian
    pips don't pin a specific color and are excluded from this count.
    """
    seen: set[str] = set()
    for c in spells:
        if c.mana_cost is None:
            continue
        seen.update(c.mana_cost.color_pips.keys())
    return len(seen)


# ---------------------------------------------------------------------------
# Simulation features (105)
# ---------------------------------------------------------------------------


def build_simulation_features(
    hand: Sequence[ParsedCard],
    deck: Sequence[ParsedCard],
    *,
    aggregate_stats: AggregateStats,
    zscores: dict[str, CardZScores],
) -> dict[str, float]:
    """Compute the 105 simulation-sourced features.

    Three families:

    * Game-level mana availability (7) — from
      ``aggregate_stats.game_level``: P(land drop turn N) for N=2..5
      and expected mana count for N=2..4.
    * Per-turn castability (96) — for each turn 1..4, for each of
      several overlapping subsets of the hand, compute
      ``p_any_castable`` and (for T2+) ``avg_count_castable``. Broad
      buckets (any spell / creature / removal) also emit the
      ``high_oh`` restricted version.
    * Additional castability (2) — average fraction of deck / hand
      spells that are castable by turn 4.

    The per-card castability comes from ``aggregate_stats.by_card_name``;
    aggregation is per name, so duplicate hand copies share the same
    P(castable). The ``p_any`` aggregator treats duplicates as
    independent given the per-card probability — a natural
    approximation given the name-level rollup.
    """
    out: dict[str, float] = {}

    # --- Game-level mana availability (7) ------------------------------
    gl = aggregate_stats.game_level
    # p_land_drop_by_turn[0] = T2, [1] = T3, [2] = T4, [3] = T5.
    out["p_land_drop_by_turn_2"] = float(gl.p_land_drop_by_turn[0])
    out["p_land_drop_by_turn_3"] = float(gl.p_land_drop_by_turn[1])
    out["p_land_drop_by_turn_4"] = float(gl.p_land_drop_by_turn[2])
    out["p_land_drop_by_turn_5"] = float(gl.p_land_drop_by_turn[3])
    # expected_mana_count_turn[0] = T2, [1] = T3, [2] = T4.
    out["expected_mana_count_turn_2"] = float(gl.expected_mana_count_turn[0])
    out["expected_mana_count_turn_3"] = float(gl.expected_mana_count_turn[1])
    out["expected_mana_count_turn_4"] = float(gl.expected_mana_count_turn[2])

    # --- Per-turn castability (96) -------------------------------------
    # Operates over the whole deck via the simulator's per-snapshot
    # aggregate, so drawn cards count toward "P(any creature castable
    # on T2)" etc. — matching the simulator's semantics rather than
    # showing 0 just because the opening hand happens to lack the
    # category. See _build_castability_features for details.
    hand_list = list(hand)
    out.update(_build_castability_features(hand_list, list(deck), aggregate_stats, zscores))

    # --- Additional castability (2) ------------------------------------
    # Average over deck spells / hand spells of P(castable by turn 4).
    # Lands are excluded — they don't have a meaningful castability.
    deck_spells = [c for c in deck if cat.is_spell(c)]
    hand_spells = [c for c in hand_list if cat.is_spell(c)]
    out["avg_pct_deck_spells_with_colored_mana_by_turn_4"] = _avg_p_castable_turn_4(
        deck_spells, aggregate_stats
    )
    out["avg_pct_hand_spells_with_colored_mana_by_turn_4"] = _avg_p_castable_turn_4(
        hand_spells, aggregate_stats
    )

    return out


def _avg_p_castable_turn_4(cards: Sequence[ParsedCard], stats: AggregateStats) -> float:
    """Mean of ``p_castable_by_turn[3]`` across cards.

    Cards not present in the aggregate (shouldn't happen — anything
    passed through ``simulate`` is in the aggregate by name — but be
    defensive) contribute 0. Lands have ``p_castable_by_turn`` of 0
    by simulator convention; the caller is responsible for filtering
    them out before calling.
    """
    if not cards:
        return 0.0
    total = 0.0
    for c in cards:
        cs = stats.by_card_name.get(c.name)
        if cs is None:
            continue
        total += cs.p_castable_by_turn[3]
    return total / len(cards)


def _p_castable_for(card: ParsedCard, stats: AggregateStats, turn: int) -> float:
    """Look up P(castable by turn T | in hand by turn T) for one card.

    Returns 0.0 when the card isn't in the aggregate (defensive — the
    simulator's aggregate covers every card name in the simulated
    deck, so this only fires on bugs). ``turn`` is 1-indexed (1..4).
    """
    cs = stats.by_card_name.get(card.name)
    if cs is None:
        return 0.0
    return float(cs.p_castable_by_turn[turn - 1])


def _p_snapshot_castable_for(name: str, stats: AggregateStats, turn: int) -> float:
    """Per-game marginal: fraction of sim runs where ≥1 instance of
    ``name`` was castable in the turn-``turn`` snapshot.

    Defensively returns 0.0 for unknown names. ``turn`` is 1-indexed.
    """
    cs = stats.by_card_name.get(name)
    if cs is None:
        return 0.0
    return float(cs.p_castable_in_snapshot_by_turn[turn - 1])


def _deck_p_any_and_avg_count(
    deck_cards: Sequence[ParsedCard], stats: AggregateStats, turn: int
) -> tuple[float, float]:
    """Compute (P(any castable), expected count of distinct names
    castable) for a deck-wide subset, using the simulator's per-game
    snapshot marginal.

    Iterates over **distinct names** in the input (not instances): two
    copies of the same card share one marginal because the underlying
    aggregate already collapses copies (it counts "did any copy of
    name X show up castable this turn?" per game). Independence across
    *different* names is the same approximation used by the prior
    hand-only path.

    ``avg_count`` is the sum of per-name marginals — the expected
    number of *distinct* matching names castable in the snapshot,
    averaged across games. This loses the multi-copy contribution to
    "count of castable cards" but stays consistent with the per-name
    aggregation that drives ``p_any``.

    Both return 0.0 for an empty subset — XGBoost convention.
    """
    if not deck_cards:
        return 0.0, 0.0
    unique_names = {c.name for c in deck_cards}
    ps = [_p_snapshot_castable_for(name, stats, turn) for name in unique_names]
    p_any = 1.0 - prod(1.0 - p for p in ps)
    avg_count = sum(ps)
    return p_any, avg_count


# Categories used in the per-turn castability grid. Each turn picks a
# subset of these; see ``_build_castability_features`` below.

# Broad category definitions for the high-WR split. "Broad" = the
# spec's three broadest buckets that get both an ``all`` and a
# ``high_oh`` variant per turn.
_BROAD_ANY_SPELL = "any_spell"
_BROAD_CREATURE = "creature"
_BROAD_REMOVAL = "removal"


def _build_castability_features(
    hand: Sequence[ParsedCard],
    deck: Sequence[ParsedCard],
    stats: AggregateStats,
    zscores: dict[str, CardZScores],
) -> dict[str, float]:
    """Build all 96 per-turn castability features.

    Iterates over deck *spells* — the simulator's per-snapshot
    aggregate (``p_castable_in_snapshot_by_turn``) is a per-game
    marginal that already captures both opening-hand and drawn
    instances, so the right "did I have a creature castable on T2?"
    feature averages across the whole deck, not just the opening
    seven. ``hand`` is no longer read here (kept in the signature
    for callsite stability; reserved for hand-only summaries we
    haven't yet folded in).

    Turn 1 emits ``p_any`` only (``avg_count`` is uninformative for
    T1 — only one card can be cast, so it equals ``p_any``). Turns
    2-4 emit both ``p_any`` and ``avg_count``. Each turn also
    introduces narrower MV-stratified buckets per the spec's growing
    castability surface.
    """
    del hand  # currently unused — see docstring.
    out: dict[str, float] = {}
    spells = [c for c in deck if cat.is_spell(c)]

    # Pre-compute the high-OH-WR subset so the per-bucket filter is a
    # cheap id-set membership check. ``id()`` keys are stable for the
    # lifetime of these Python objects, which is the scope of one
    # ``build_feature_row`` call.
    high_oh_ids = {id(c) for c in spells if _is_high_oh(c, zscores)}

    def restrict(cards: Sequence[ParsedCard], high_oh: bool) -> Sequence[ParsedCard]:
        return [c for c in cards if id(c) in high_oh_ids] if high_oh else cards

    # --- Turn 1 (6 features): broad categories, p_any only ----------
    t1_broad: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        (_BROAD_ANY_SPELL, lambda c: True),
        (_BROAD_CREATURE, cat.is_creature_for_castability),
        (_BROAD_REMOVAL, cat.is_removal),
    ]
    _emit_turn_features(out, spells, restrict, t1_broad, [], stats, turn=1, include_avg_count=False)

    # --- Turn 2 (22 features): broad (3) + narrow (5) ---------------
    t2_broad = t1_broad  # same three categories as T1
    t2_narrow: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        ("pump", cat.is_pump_broad),
        ("equipment_or_vehicle", cat.is_equipment_or_vehicle),
        ("card_manipulation", cat.is_card_manipulation),
        ("other", cat.is_other),
        ("alt_mode", cat.has_alt_mode),
    ]
    _emit_turn_features(
        out, spells, restrict, t2_broad, t2_narrow, stats, turn=2, include_avg_count=True
    )

    # --- Turn 3 (30 features): broad (5) + narrow (5) ---------------
    # The broad set picks up MV-stratified any_spell and creature
    # variants. The "removal" broad row carries over from T2.
    t3_broad: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        (
            f"{_BROAD_ANY_SPELL}_mv_0_2",
            lambda c: cat.mv_0_2(c),
        ),
        (
            f"{_BROAD_ANY_SPELL}_mv_3p",
            lambda c: cat.mv_3_plus(c),
        ),
        (
            f"{_BROAD_CREATURE}_mv_0_2",
            lambda c: cat.is_creature_for_castability(c) and cat.mv_0_2(c),
        ),
        (
            f"{_BROAD_CREATURE}_mv_3p",
            lambda c: cat.is_creature_for_castability(c) and cat.mv_3_plus(c),
        ),
        (_BROAD_REMOVAL, cat.is_removal),
    ]
    _emit_turn_features(
        out, spells, restrict, t3_broad, t2_narrow, stats, turn=3, include_avg_count=True
    )

    # --- Turn 4 (38 features): broad (7) + narrow (5) ---------------
    # Adds the mv_eq_3 and mv_4_plus splits on top of T3's structure.
    t4_broad: list[tuple[str, Callable[[ParsedCard], bool]]] = [
        (
            f"{_BROAD_ANY_SPELL}_mv_0_2",
            lambda c: cat.mv_0_2(c),
        ),
        (
            f"{_BROAD_ANY_SPELL}_mv_3",
            lambda c: cat.mv_eq_3(c),
        ),
        (
            f"{_BROAD_ANY_SPELL}_mv_4p",
            lambda c: cat.mv_4_plus(c),
        ),
        (
            f"{_BROAD_CREATURE}_mv_0_2",
            lambda c: cat.is_creature_for_castability(c) and cat.mv_0_2(c),
        ),
        (
            f"{_BROAD_CREATURE}_mv_3",
            lambda c: cat.is_creature_for_castability(c) and cat.mv_eq_3(c),
        ),
        (
            f"{_BROAD_CREATURE}_mv_4p",
            lambda c: cat.is_creature_for_castability(c) and cat.mv_4_plus(c),
        ),
        (_BROAD_REMOVAL, cat.is_removal),
    ]
    _emit_turn_features(
        out, spells, restrict, t4_broad, t2_narrow, stats, turn=4, include_avg_count=True
    )

    return out


def _emit_turn_features(
    out: dict[str, float],
    spells: Sequence[ParsedCard],
    restrict: Callable[[Sequence[ParsedCard], bool], Sequence[ParsedCard]],
    broad: list[tuple[str, Callable[[ParsedCard], bool]]],
    narrow: list[tuple[str, Callable[[ParsedCard], bool]]],
    stats: AggregateStats,
    *,
    turn: int,
    include_avg_count: bool,
) -> None:
    """Emit per-turn castability features for one turn.

    Mutates ``out`` in place. ``broad`` categories get both an ``all``
    and a ``high_oh`` variant; ``narrow`` categories get only the
    ``all`` variant. ``include_avg_count`` is False on turn 1 (the
    spec drops it there because P(any) == avg_count when at most one
    card can be cast).

    Uses :func:`_deck_p_any_and_avg_count`, which keys the simulator
    aggregate by *card name* rather than per-instance — multiple
    copies of one card name in the deck collapse to a single per-game
    marginal, which is the correct interpretation of "did ≥1 copy
    show up castable in this turn's snapshot?"
    """
    # Broad: all + high_oh
    for cat_name, pred in broad:
        matching = [c for c in spells if pred(c)]
        for is_high_oh, suffix in ((False, ""), (True, "_high_oh")):
            subset = restrict(matching, is_high_oh)
            p_any, avg_count = _deck_p_any_and_avg_count(subset, stats, turn=turn)
            out[f"p_any_{cat_name}{suffix}_t{turn}"] = p_any
            if include_avg_count:
                out[f"avg_count_{cat_name}{suffix}_t{turn}"] = avg_count

    # Narrow: all only
    for cat_name, pred in narrow:
        matching = [c for c in spells if pred(c)]
        p_any, avg_count = _deck_p_any_and_avg_count(matching, stats, turn=turn)
        out[f"p_any_{cat_name}_t{turn}"] = p_any
        if include_avg_count:
            out[f"avg_count_{cat_name}_t{turn}"] = avg_count


# ---------------------------------------------------------------------------
# Context features (3) + top-level row builder
# ---------------------------------------------------------------------------


def build_context_features(
    *,
    on_the_play: bool,
    event_type: str,
    set_code: str,
    known_event_types: Sequence[str] = DEFAULT_KNOWN_EVENT_TYPES,
    known_sets: Sequence[str] = DEFAULT_KNOWN_SETS,
) -> dict[str, float]:
    """Three context features.

    * ``on_the_play`` — bool as 0/1 float.
    * ``event_type`` — one-hot across ``known_event_types``. A new
      event type not in the list produces all-zero columns; XGBoost
      can handle that, and the model can be retrained when the
      vocabulary expands.
    * ``set_code`` — one-hot across ``known_sets``. Same all-zero
      fallback for new sets at inference time.
    """
    out: dict[str, float] = {"on_the_play": 1.0 if on_the_play else 0.0}
    for et in known_event_types:
        out[f"event_type_{et}"] = 1.0 if event_type == et else 0.0
    for sc in known_sets:
        out[f"set_code_{sc}"] = 1.0 if set_code == sc else 0.0
    return out


def build_feature_row(
    *,
    hand: Sequence[ParsedCard],
    deck: Sequence[ParsedCard],
    aggregate_stats: AggregateStats,
    shrunk: dict[str, ShrunkWinRates],
    zscores: dict[str, CardZScores],
    on_the_play: bool,
    mulligan_number: int,
    event_type: str,
    set_code: str,
    known_event_types: Sequence[str] = DEFAULT_KNOWN_EVENT_TYPES,
    known_sets: Sequence[str] = DEFAULT_KNOWN_SETS,
) -> dict[str, float]:
    """Assemble one full feature row for (hand, deck) at training /
    inference time.

    Composes the four section builders into a single flat dict. Keys
    don't collide by construction (each builder uses its own prefix
    set); we still assert no overlap in tests as a regression catch.

    See module docstring for the role of each input.
    """
    row: dict[str, float] = {}
    row.update(
        build_context_features(
            on_the_play=on_the_play,
            event_type=event_type,
            set_code=set_code,
            known_event_types=known_event_types,
            known_sets=known_sets,
        )
    )
    row.update(build_deck_features(deck, shrunk=shrunk))
    row.update(
        build_hand_features(
            hand,
            deck,
            shrunk=shrunk,
            zscores=zscores,
            mulligan_number=mulligan_number,
        )
    )
    row.update(
        build_simulation_features(
            hand,
            deck,
            aggregate_stats=aggregate_stats,
            zscores=zscores,
        )
    )
    return row
