"""Tests for the mulligan-decisions parquet -> :class:`ChoiceRow` reader.

Strategy mirrors :mod:`test_training_rows`: build a small parquet
file with hand-written rows, hand-feed a name -> :class:`ParsedCard`
lookup, and exercise the iterator's filter logic. The parquet path
is real (uses pyarrow) but tiny and tmp_path-scoped, so tests stay
fast and self-contained.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

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
from mulligan_coach_model.choice_rows import (
    DEFAULT_MIN_N_GAMES_TO_JUDGE,
    DEFAULT_MIN_WIN_RATE,
    ChoiceRow,
    ChoiceRowStats,
    iter_choice_rows,
    parse_deck_string,
    parse_hand_names,
    should_keep_player,
)

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
    from mulligan_coach_model.training_rows import _BASIC_LANDS, _make_basic_land

    lookup: dict[str, ParsedCard] = {
        name: _make_basic_land(name, color, subtype) for name, color, subtype in _BASIC_LANDS
    }
    for name in extra_names:
        lookup[name] = _vanilla_creature(name)
    return lookup


def _encode_hand(names: list[str]) -> str:
    """Pipe-delimited hand string, matching the builder's format."""
    return "|".join(names)


def _encode_deck(counts: dict[str, int]) -> str:
    """``"Name xN | Name xM | ..."`` sorted alphabetically by name."""
    parts = [f"{name} x{count}" for name, count in sorted(counts.items()) if count > 0]
    return " | ".join(parts)


def _make_record(
    *,
    hand_names: list[str],
    deck_counts: dict[str, int],
    expansion: str = "TLA",
    event_type: str = "PremierDraft",
    draft_id: str = "draft-1",
    build_index: int = 0,
    match_number: int = 1,
    game_number: int = 1,
    on_play: bool = True,
    user_n_games_bucket: int | None = 100,
    user_game_win_rate_bucket: float | None = 0.56,
    num_mulligans_in_game: int = 0,
    opp_num_mulligans: int = 0,
    num_turns: int | None = 10,
    won: bool = True,
    mulligan_number: int = 0,
    was_kept: bool = True,
    hand_size: int = 7,
    rank: str = "diamond",
    opp_rank: str = "diamond",
    main_colors: str = "WR",
    splash_colors: str | None = None,
    opp_colors: str | None = None,
) -> dict[str, Any]:
    """Build one parquet-record dict matching build_dataset.py's output."""
    return {
        "expansion": expansion,
        "event_type": event_type,
        "draft_id": draft_id,
        "build_index": build_index,
        "match_number": match_number,
        "game_number": game_number,
        "on_play": on_play,
        "rank": rank,
        "opp_rank": opp_rank,
        "main_colors": main_colors,
        "splash_colors": splash_colors,
        "opp_colors": opp_colors,
        "user_n_games_bucket": user_n_games_bucket,
        "user_game_win_rate_bucket": user_game_win_rate_bucket,
        "num_mulligans_in_game": num_mulligans_in_game,
        "opp_num_mulligans": opp_num_mulligans,
        "num_turns": num_turns,
        "won": won,
        "mulligan_number": mulligan_number,
        "was_kept": was_kept,
        "hand_size": hand_size,
        "hand": _encode_hand(hand_names),
        "hand_arena_ids": "0|" * (len(hand_names) - 1) + "0",
        "deck": _encode_deck(deck_counts),
    }


def _write_parquet(records: list[dict[str, Any]], path: Path) -> Path:
    """Write records to a parquet file matching the builder's schema."""
    if not records:
        raise ValueError("Need at least one record to write a parquet.")
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


# ---------------------------------------------------------------------------
# should_keep_player
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("n_games", "wr", "expected"),
    [
        # Both unknown -> keep (can't judge).
        (None, None, True),
        (None, 0.30, True),
        (100, None, True),
        (100, float("nan"), True),
        # Below judging sample -> keep regardless of WR.
        (10, 0.20, True),
        (49, 0.30, True),
        # Above judging sample, WR at or above threshold -> keep.
        (50, 0.50, True),
        (100, 0.55, True),
        (500, 0.60, True),
        # Above judging sample, WR below threshold -> drop.
        (50, 0.48, False),
        (100, 0.30, False),
        (1000, 0.49, False),
    ],
)
def test_should_keep_player(n_games: int | None, wr: float | None, expected: bool) -> None:
    assert should_keep_player(n_games, wr) is expected


def test_should_keep_player_thresholds_are_configurable() -> None:
    """Tightening thresholds should drop borderline rows."""
    # At defaults: 100 games & 0.50 WR keeps.
    assert should_keep_player(100, 0.50) is True
    # Tighten WR floor to 0.55: 100 games & 0.50 WR now drops.
    assert should_keep_player(100, 0.50, min_n_games_to_judge=50, min_win_rate=0.55) is False
    # Raise the sample threshold to 500: 100 games skirts the judgment entirely.
    assert should_keep_player(100, 0.30, min_n_games_to_judge=500, min_win_rate=0.50) is True


# ---------------------------------------------------------------------------
# parse_hand_names / parse_deck_string
# ---------------------------------------------------------------------------


def test_parse_hand_names_splits_on_pipes() -> None:
    names = parse_hand_names("Forest|Plains|Bear")
    assert names == ["Forest", "Plains", "Bear"]


def test_parse_hand_names_empty_string_returns_empty_list() -> None:
    assert parse_hand_names("") == []


def test_parse_deck_string_round_trips_with_encoder() -> None:
    """parse_deck_string should be the inverse of _encode_deck."""
    counts = {"Forest": 17, "Bear": 12, "Plains x Beyond": 1, "Aang, the Last Airbender": 1}
    encoded = _encode_deck(counts)
    parsed = parse_deck_string(encoded)
    assert dict(parsed) == counts


def test_parse_deck_string_rejects_malformed_entry() -> None:
    with pytest.raises(ValueError, match="Malformed deck entry"):
        parse_deck_string("Forest xFOUR")


def test_parse_deck_string_empty_returns_empty_list() -> None:
    assert parse_deck_string("") == []


# ---------------------------------------------------------------------------
# iter_choice_rows — happy path
# ---------------------------------------------------------------------------


def test_iter_choice_rows_happy_path(tmp_path: Path) -> None:
    lookup = _basic_lookup(extra_names=["Bear", "Elk"])
    record = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 2 + ["Elk"],
        deck_counts={"Forest": 17, "Bear": 12, "Elk": 11},
        on_play=False,
        mulligan_number=0,
        num_mulligans_in_game=0,
        was_kept=True,
        opp_num_mulligans=1,
        user_n_games_bucket=100,
        user_game_win_rate_bucket=0.58,
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")

    stats = ChoiceRowStats()
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, stats=stats))

    assert stats.emitted == 1
    assert len(out) == 1
    row = out[0]
    assert isinstance(row, ChoiceRow)
    assert len(row.hand) == 7
    assert sum(1 for c in row.hand if c.name == "Forest") == 4
    assert len(row.deck) == 40
    assert sum(1 for c in row.deck if c.name == "Bear") == 12
    assert row.on_the_play is False
    assert row.was_kept is True
    assert row.mulligan_number == 0
    assert row.num_mulligans_in_game == 0
    assert row.opp_mulligan_number == 1
    assert row.user_n_games_raw == 100
    assert row.user_wr_raw == pytest.approx(0.58)
    assert row.draft_id == "draft-1"
    assert row.build_index == 0
    assert row.match_number == 1
    assert row.game_number == 1


def test_iter_choice_rows_yields_kept_and_mulled_for_one_game(tmp_path: Path) -> None:
    """A game with one mulligan emits two ChoiceRows: the mulled-away
    candidate (was_kept=False) and the kept one (was_kept=True)."""
    lookup = _basic_lookup(extra_names=["Bear"])
    deck_counts = {"Forest": 24, "Bear": 16}
    mulled = _make_record(
        hand_names=["Forest"] * 7,  # all-lands hand the player mulled away
        deck_counts=deck_counts,
        mulligan_number=0,
        num_mulligans_in_game=1,
        was_kept=False,
    )
    kept = _make_record(
        hand_names=["Forest"] * 3 + ["Bear"] * 4,
        deck_counts=deck_counts,
        mulligan_number=1,
        num_mulligans_in_game=1,
        was_kept=True,
    )
    path = _write_parquet([mulled, kept], tmp_path / "tla.parquet")

    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup))
    assert len(out) == 2
    by_kept = {row.was_kept: row for row in out}
    assert by_kept[False].mulligan_number == 0
    assert by_kept[True].mulligan_number == 1
    # Decks are identical across the two rows; the iterator's deck cache
    # should have returned the same tuple for both.
    assert by_kept[False].deck is by_kept[True].deck


# ---------------------------------------------------------------------------
# iter_choice_rows — filters
# ---------------------------------------------------------------------------


def test_iter_choice_rows_filters_out_bad_player(tmp_path: Path) -> None:
    """100-game player with 0.30 WR fails the default filter."""
    lookup = _basic_lookup(extra_names=["Bear"])
    bad = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        user_n_games_bucket=100,
        user_game_win_rate_bucket=0.30,
    )
    good = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        draft_id="draft-2",
        user_n_games_bucket=100,
        user_game_win_rate_bucket=0.60,
    )
    path = _write_parquet([bad, good], tmp_path / "tla.parquet")

    stats = ChoiceRowStats()
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert out[0].draft_id == "draft-2"
    assert stats.skipped_player_filter == 1


def test_iter_choice_rows_keeps_low_sample_below_average_player(tmp_path: Path) -> None:
    """20-game player with 0.30 WR is kept — not enough sample to judge."""
    lookup = _basic_lookup(extra_names=["Bear"])
    record = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        user_n_games_bucket=10,
        user_game_win_rate_bucket=0.30,
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")

    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1


def test_iter_choice_rows_filters_out_wrong_set(tmp_path: Path) -> None:
    """Rows from another set are silently dropped — they don't bump any
    skip counter (the format filter is just a "this row isn't for us")."""
    lookup = _basic_lookup(extra_names=["Bear"])
    tla = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        expansion="TLA",
    )
    tmt = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        expansion="TMT",
        draft_id="draft-tmt",
    )
    path = _write_parquet([tla, tmt], tmp_path / "combined.parquet")

    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert out[0].expansion == "TLA"


def test_iter_choice_rows_skips_bad_hand_size(tmp_path: Path) -> None:
    lookup = _basic_lookup(extra_names=["Bear"])
    record = _make_record(
        hand_names=["Forest", "Forest", "Bear"],  # only 3 cards
        deck_counts={"Forest": 24, "Bear": 16},
        hand_size=3,
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")
    stats = ChoiceRowStats()
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 0
    assert stats.skipped_bad_hand_size == 1


def test_iter_choice_rows_skips_bad_deck_size(tmp_path: Path) -> None:
    """A deck string parsing to < 40 cards is dropped."""
    lookup = _basic_lookup(extra_names=["Bear"])
    record = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 20, "Bear": 10},  # only 30 total
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")
    stats = ChoiceRowStats()
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 0
    assert stats.skipped_bad_deck_size == 1


def test_iter_choice_rows_skips_unknown_card_name(tmp_path: Path) -> None:
    """A hand containing a card the lookup doesn't know -> skip + log."""
    lookup = _basic_lookup(extra_names=["Bear"])
    record = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 2 + ["UnknownCard"],
        deck_counts={"Forest": 24, "Bear": 16},
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")
    stats = ChoiceRowStats()
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 0
    assert stats.skipped_unknown_card == 1
    assert stats.unknown_card_names == {"UnknownCard": 1}


def test_iter_choice_rows_handles_nan_win_rate_bucket(tmp_path: Path) -> None:
    """A NaN WR (rare but real — 110/626K rows in TLA) keeps the row;
    we can't judge a player whose WR is unknown."""
    lookup = _basic_lookup(extra_names=["Bear"])
    record = _make_record(
        hand_names=["Forest"] * 4 + ["Bear"] * 3,
        deck_counts={"Forest": 24, "Bear": 16},
        user_n_games_bucket=100,
        user_game_win_rate_bucket=float("nan"),
    )
    path = _write_parquet([record], tmp_path / "tla.parquet")
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert out[0].user_wr_raw is None


def test_iter_choice_rows_limit_caps_emitted(tmp_path: Path) -> None:
    lookup = _basic_lookup(extra_names=["Bear"])
    records = [
        _make_record(
            hand_names=["Forest"] * 4 + ["Bear"] * 3,
            deck_counts={"Forest": 24, "Bear": 16},
            draft_id=f"draft-{i}",
            game_number=i,
        )
        for i in range(5)
    ]
    path = _write_parquet(records, tmp_path / "tla.parquet")
    out = list(iter_choice_rows(path, set_code="TLA", name_lookup=lookup, limit=2))
    assert len(out) == 2


# ---------------------------------------------------------------------------
# Default thresholds are sensible
# ---------------------------------------------------------------------------


def test_default_thresholds_match_owner_intent() -> None:
    """Sanity-check the documented defaults so a future tweak surfaces
    in the test suite."""
    assert DEFAULT_MIN_N_GAMES_TO_JUDGE == 50
    assert DEFAULT_MIN_WIN_RATE == 0.50


# Avoids the unused-import warning for `math` in slim test envs.
assert math.isnan(float("nan"))
