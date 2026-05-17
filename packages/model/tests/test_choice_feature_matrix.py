"""Tests for the choice-model feature-matrix materialiser.

Covers the two paths through :func:`materialize_choice_feature_matrix`:

* **Cache-hit**: a ``was_kept=True`` decision row whose
  ``(draft_id, match_number, game_number)`` exists in the win-model
  feature cache with a matching ``mulligan_number``. The materialiser
  should reuse the cached features bit-identically and only swap the
  label / context.
* **Fresh compute**: a ``was_kept=False`` row, or a cache miss. The
  materialiser runs the simulator + feature builder from scratch and
  produces a row with the same schema as the cache-hit path.

Tests build everything from synthetic fixtures (a hand-written
mulligan-decisions parquet + a hand-written win-model cache parquet)
so they stay self-contained and fast.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mulligan_coach_cards import (
    Cost,
    Mode,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_model.choice_feature_matrix import (
    ChoiceMaterializationStats,
    KeptHandCache,
    build_choice_row_fresh,
    build_choice_row_from_cache,
    choice_parquet_paths,
    load_kept_hand_cache,
    materialize_choice_feature_matrix,
)
from mulligan_coach_model.choice_rows import ChoiceRow
from mulligan_coach_model.feature_matrix import (
    _build_format_stats,
)
from mulligan_coach_model.feature_matrix import (
    _chunk_path as _wm_chunk_path,
)
from mulligan_coach_model.training_rows import _BASIC_LANDS, _make_basic_land


def _has_real_tla_stats() -> bool:
    """True when the TLA per-set ratings + parsed cards are available
    locally so the fresh-sim path can build a real format_stats bundle."""
    try:
        from mulligan_coach_cards import load_parsed_cards, load_premier_draft_stats

        load_parsed_cards("TLA")
        load_premier_draft_stats("TLA")
    except Exception:
        return False
    return True


_HAS_REAL_TLA_STATS = _has_real_tla_stats()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


_NEXT_OID = [0]


def _oid() -> str:
    _NEXT_OID[0] += 1
    return f"00000000-0000-0000-0000-{_NEXT_OID[0]:012d}"


def _vanilla_creature(name: str, mana: str = "{2}") -> ParsedCard:
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"Vanilla {name}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost(mana),
        power="2",
        toughness="2",
        modes=[Mode(kind="cast", cost=cost, effects=[])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def _basic_lookup(extra_names: Iterable[str] = ()) -> dict[str, ParsedCard]:
    lookup: dict[str, ParsedCard] = {
        name: _make_basic_land(name, color, subtype) for name, color, subtype in _BASIC_LANDS
    }
    for name in extra_names:
        lookup[name] = _vanilla_creature(name)
    return lookup


def _encode_hand(names: list[str]) -> str:
    return "|".join(names)


def _encode_deck(counts: dict[str, int]) -> str:
    parts = [f"{name} x{count}" for name, count in sorted(counts.items()) if count > 0]
    return " | ".join(parts)


def _make_decision_record(
    *,
    hand_names: list[str],
    deck_counts: dict[str, int],
    draft_id: str = "draft-1",
    build_index: int = 0,
    match_number: int = 1,
    game_number: int = 1,
    on_play: bool = True,
    mulligan_number: int = 0,
    num_mulligans_in_game: int = 0,
    was_kept: bool = True,
    opp_num_mulligans: int = 0,
    user_n_games_bucket: int | None = 100,
    user_game_win_rate_bucket: float | None = 0.56,
    won: bool = True,
) -> dict[str, Any]:
    return {
        "expansion": "TLA",
        "event_type": "PremierDraft",
        "draft_id": draft_id,
        "build_index": build_index,
        "match_number": match_number,
        "game_number": game_number,
        "on_play": on_play,
        "rank": "diamond",
        "opp_rank": "diamond",
        "main_colors": "WG",
        "splash_colors": None,
        "opp_colors": None,
        "user_n_games_bucket": user_n_games_bucket,
        "user_game_win_rate_bucket": user_game_win_rate_bucket,
        "num_mulligans_in_game": num_mulligans_in_game,
        "opp_num_mulligans": opp_num_mulligans,
        "num_turns": 10,
        "won": won,
        "mulligan_number": mulligan_number,
        "was_kept": was_kept,
        "hand_size": 7,
        "hand": _encode_hand(hand_names),
        "hand_arena_ids": "|".join("0" for _ in hand_names),
        "deck": _encode_deck(deck_counts),
    }


def _write_decisions_parquet(records: list[dict[str, Any]], path: Path) -> Path:
    if not records:
        raise ValueError("Need at least one record")
    all_keys: list[str] = []
    seen: set[str] = set()
    for r in records:
        for k in r:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)
    cols: dict[str, list[Any]] = {k: [r.get(k) for r in records] for k in all_keys}
    table = pa.table(cols)
    pq.write_table(table, path)  # type: ignore[no-untyped-call]
    return path


def _write_cache_chunk(rows: list[dict[str, Any]], cache_dir: Path, chunk_idx: int = 0) -> Path:
    """Write a synthetic win-model feature chunk to disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _wm_chunk_path(cache_dir, chunk_idx)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, path)  # type: ignore[no-untyped-call]
    return path


def _make_cached_feature_row(
    *,
    draft_id: str = "draft-1",
    match_number: int = 1,
    game_number: int = 1,
    mulligan_number: int = 0,
    won: bool = True,
    feature_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a fake cached feature row carrying a unique marker.

    The marker column ``__cache_marker`` lets tests assert that a row
    came from the cache (its presence + value) rather than from a
    fresh compute (where the column wouldn't appear).
    """
    row: dict[str, Any] = {
        # Sentinel feature plus enough plausible features that pandas
        # round-trip stays clean. Real win-model cache has ~200 floats.
        "__cache_marker": 1.0,
        "on_the_play": 1.0,
        "p_land_drop_by_turn_2": 0.95,
        "mulligan_number": mulligan_number,
        "draft_id": draft_id,
        "match_number": match_number,
        "game_number": game_number,
        "expansion": "TLA",
        "event_type": "PremierDraft",
        "opp_mulligan_number": 0,
        "opp_mulligan_count_if_known": None,
        "user_wr_bucket": "55-60%",
        "user_n_games_bucket": "100-499",
        "won": won,
    }
    if feature_overrides:
        row.update(feature_overrides)
    return row


# ---------------------------------------------------------------------------
# load_kept_hand_cache
# ---------------------------------------------------------------------------


def test_load_kept_hand_cache_strips_label_and_buckets(tmp_path: Path) -> None:
    """The cache loader should drop won / user_*_bucket so the choice
    materialiser doesn't accidentally inherit the win-model's columns."""
    cache_dir = tmp_path / "cached"
    _write_cache_chunk(
        [_make_cached_feature_row(draft_id="d1", match_number=1, game_number=1)],
        cache_dir,
    )
    cache = load_kept_hand_cache(cache_dir)
    assert isinstance(cache, KeptHandCache)
    assert len(cache) == 1
    # Dropped columns shouldn't appear in the loaded frame.
    assert "won" not in cache.frame.columns
    assert "user_wr_bucket" not in cache.frame.columns
    assert "user_n_games_bucket" not in cache.frame.columns
    # Sentinel + retained context columns survive.
    assert "__cache_marker" in cache.frame.columns
    assert "mulligan_number" in cache.frame.columns


def test_load_kept_hand_cache_raises_on_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No win-model feature chunks"):
        load_kept_hand_cache(tmp_path / "missing")


def test_kept_hand_cache_lookup_hit_and_miss(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cached"
    _write_cache_chunk(
        [
            _make_cached_feature_row(draft_id="d1", match_number=1, game_number=1),
            _make_cached_feature_row(draft_id="d1", match_number=1, game_number=2),
        ],
        cache_dir,
    )
    cache = load_kept_hand_cache(cache_dir)
    hit = cache.lookup("d1", 1, 1)
    assert hit is not None
    assert hit["__cache_marker"] == 1.0
    miss = cache.lookup("d-missing", 1, 1)
    assert miss is None


# ---------------------------------------------------------------------------
# build_choice_row_from_cache
# ---------------------------------------------------------------------------


def _make_choice_row(
    *,
    draft_id: str = "draft-1",
    build_index: int = 0,
    match_number: int = 1,
    game_number: int = 1,
    on_the_play: bool = True,
    mulligan_number: int = 0,
    was_kept: bool = True,
    num_mulligans_in_game: int = 0,
    opp_mulligan_number: int = 0,
    hand_cards: tuple[ParsedCard, ...] | None = None,
    deck_cards: tuple[ParsedCard, ...] | None = None,
    user_n_games_raw: int | None = 100,
    user_wr_raw: float | None = 0.56,
) -> ChoiceRow:
    """Compose a ChoiceRow without going through parquet I/O."""
    lookup = _basic_lookup(extra_names=["Bear"])
    if hand_cards is None:
        hand_cards = (
            lookup["Forest"],
            lookup["Forest"],
            lookup["Forest"],
            lookup["Forest"],
            lookup["Bear"],
            lookup["Bear"],
            lookup["Bear"],
        )
    if deck_cards is None:
        deck_cards = tuple([lookup["Forest"]] * 24 + [lookup["Bear"]] * 16)
    return ChoiceRow(
        hand=hand_cards,
        deck=deck_cards,
        on_the_play=on_the_play,
        mulligan_number=mulligan_number,
        was_kept=was_kept,
        num_mulligans_in_game=num_mulligans_in_game,
        opp_mulligan_number=opp_mulligan_number,
        user_n_games_raw=user_n_games_raw,
        user_wr_raw=user_wr_raw,
        expansion="TLA",
        event_type="PremierDraft",
        won=was_kept,
        draft_id=draft_id,
        build_index=build_index,
        match_number=match_number,
        game_number=game_number,
    )


def test_build_choice_row_from_cache_overrides_context() -> None:
    """Cache-hit path: features preserved, context replaced from ChoiceRow."""
    cached = _make_cached_feature_row(
        draft_id="draft-1",
        match_number=1,
        game_number=1,
        mulligan_number=2,
        won=False,
    )
    # The loader would have already dropped won + user buckets; mimic that.
    for col in ("won", "user_wr_bucket", "user_n_games_bucket"):
        cached.pop(col, None)
    cr = _make_choice_row(
        draft_id="draft-1",
        match_number=1,
        game_number=1,
        on_the_play=False,
        mulligan_number=2,
        was_kept=True,
        num_mulligans_in_game=2,
        opp_mulligan_number=1,
    )
    row = build_choice_row_from_cache(cr, cached)

    # Feature sentinel survives unchanged.
    assert row["__cache_marker"] == 1.0
    assert row["p_land_drop_by_turn_2"] == 0.95
    # Context comes from the ChoiceRow.
    assert row["was_kept"] is True
    assert row["mulligan_number"] == 2
    assert row["num_mulligans_in_game"] == 2
    assert row["opp_mulligan_number"] == 1
    # opp_mulligan_count_if_known is set conditionally on on_the_play.
    assert row["opp_mulligan_count_if_known"] == pytest.approx(1.0)
    assert row["user_n_games_raw"] == 100
    assert row["user_wr_raw"] == pytest.approx(0.56)
    assert row["build_index"] == 0
    # Win-model's `won` label must not leak through.
    assert "won" not in row


# ---------------------------------------------------------------------------
# Full materialiser — synthetic end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_REAL_TLA_STATS,
    reason="Needs real TLA cards.json to build format_stats for the fresh-sim path.",
)
def test_materialize_uses_cache_for_kept_skips_resim(tmp_path: Path) -> None:
    """End-to-end on synthetic inputs: a kept-hand decision row should
    be served from the cache (rows_via_cache == 1, rows_via_sim == 0)."""
    decisions = tmp_path / "decisions.parquet"
    _write_decisions_parquet(
        [
            _make_decision_record(
                hand_names=["Forest"] * 4 + ["Plains"] * 3,
                deck_counts={"Forest": 17, "Plains": 17, "Island": 6},
                was_kept=True,
                mulligan_number=0,
                num_mulligans_in_game=0,
            ),
        ],
        decisions,
    )
    cache_dir = tmp_path / "cached"
    _write_cache_chunk(
        [
            _make_cached_feature_row(
                draft_id="draft-1",
                match_number=1,
                game_number=1,
                mulligan_number=0,
            )
        ],
        cache_dir,
    )
    out_dir = tmp_path / "choice_out"
    stats = materialize_choice_feature_matrix(
        decisions_parquet=decisions,
        cached_features_dir=cache_dir,
        output_dir=out_dir,
        set_code="TLA",
        chunk_rows=1,
    )
    assert stats.rows_written == 1
    assert stats.rows_via_cache == 1
    assert stats.rows_via_simulation == 0

    chunks = choice_parquet_paths(out_dir)
    assert len(chunks) == 1
    df = pd.read_parquet(chunks[0])
    assert len(df) == 1
    assert bool(df.iloc[0]["was_kept"]) is True
    assert df.iloc[0]["__cache_marker"] == 1.0


@pytest.mark.skipif(
    not _HAS_REAL_TLA_STATS,
    reason="Needs real TLA cards.json to build format_stats for the fresh-sim path.",
)
def test_materialize_resims_mulled_hands(tmp_path: Path) -> None:
    """A was_kept=False row never hits the cache and must be re-simulated."""
    decisions = tmp_path / "decisions.parquet"
    _write_decisions_parquet(
        [
            _make_decision_record(
                hand_names=["Forest"] * 7,
                deck_counts={"Forest": 17, "Plains": 17, "Island": 6},
                was_kept=False,
                mulligan_number=0,
                num_mulligans_in_game=1,
            ),
        ],
        decisions,
    )
    cache_dir = tmp_path / "cached"
    _write_cache_chunk(
        [
            _make_cached_feature_row(
                draft_id="draft-1", match_number=1, game_number=1, mulligan_number=1
            )
        ],
        cache_dir,
    )
    out_dir = tmp_path / "choice_out"
    stats = materialize_choice_feature_matrix(
        decisions_parquet=decisions,
        cached_features_dir=cache_dir,
        output_dir=out_dir,
        set_code="TLA",
        chunk_rows=10,
        n_sims_per_row=5,
    )
    assert stats.rows_via_cache == 0
    assert stats.rows_via_simulation == 1
    assert stats.rows_written == 1

    df = pd.read_parquet(choice_parquet_paths(out_dir)[0])
    assert bool(df.iloc[0]["was_kept"]) is False
    # Fresh-compute path produces real feature columns from build_feature_row;
    # the cache marker column should NOT be present.
    assert "__cache_marker" not in df.columns


@pytest.mark.skipif(
    not _HAS_REAL_TLA_STATS,
    reason="Needs real TLA cards.json to build format_stats for the fresh-sim path.",
)
def test_materialize_resume_skips_already_written_rows(tmp_path: Path) -> None:
    """A second pass over the same decisions should be a no-op when
    chunks already exist on disk."""
    decisions = tmp_path / "decisions.parquet"
    _write_decisions_parquet(
        [
            _make_decision_record(
                hand_names=["Forest"] * 4 + ["Plains"] * 3,
                deck_counts={"Forest": 17, "Plains": 17, "Island": 6},
                was_kept=True,
                mulligan_number=0,
                num_mulligans_in_game=0,
            ),
        ],
        decisions,
    )
    cache_dir = tmp_path / "cached"
    _write_cache_chunk(
        [_make_cached_feature_row(draft_id="draft-1", match_number=1, game_number=1)],
        cache_dir,
    )
    out_dir = tmp_path / "choice_out"
    first = materialize_choice_feature_matrix(
        decisions_parquet=decisions,
        cached_features_dir=cache_dir,
        output_dir=out_dir,
        set_code="TLA",
        chunk_rows=1,
    )
    assert first.rows_written == 1

    second = materialize_choice_feature_matrix(
        decisions_parquet=decisions,
        cached_features_dir=cache_dir,
        output_dir=out_dir,
        set_code="TLA",
        chunk_rows=1,
    )
    assert second.rows_written == 0
    assert second.rows_skipped_resume == 1


# ---------------------------------------------------------------------------
# Sanity: stats type defaults are consistent
# ---------------------------------------------------------------------------


def test_stats_starts_at_zero() -> None:
    s = ChoiceMaterializationStats()
    assert s.rows_written == 0
    assert s.rows_via_cache == 0
    assert s.rows_via_simulation == 0
    assert s.choice_row_stats.emitted == 0


# build_choice_row_fresh is exercised by the end-to-end test_materialize_resims_mulled_hands
# test; calling it in isolation requires real format_stats, which we
# build there. Keep the import live so a refactor surfaces here too.
assert build_choice_row_fresh is not None
assert _build_format_stats is not None
