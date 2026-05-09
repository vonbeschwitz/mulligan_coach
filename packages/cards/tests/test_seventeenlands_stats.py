"""Tests for the 17Lands per-card stats loader and StatsLookup matcher.

Hand-written parquet fixtures only — no real download, no network.
Tests pass a ``data_root=tmp_path`` to the loader (same convention used
by ``test_store.py`` for ``load_parsed_cards``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mulligan_coach_cards.models import ParsedCard, ParseStatus, RoleFeatures
from mulligan_coach_cards.seventeenlands_stats import (
    SeventeenLandsStats,
    StatsLookup,
    load_premier_draft_stats,
    ratings_parquet_path,
)


def _row(**overrides: Any) -> dict[str, Any]:
    """Build a complete 17Lands ratings row with sensible defaults.

    Mirrors the JSON shape that data-download writes after injecting
    ``expansion`` / ``event_type``. Tests override only the fields they
    care about and rely on the defaults for the rest.
    """
    base: dict[str, Any] = {
        "name": "Test Card",
        "mtga_id": 12345,
        "expansion": "TST",
        "event_type": "PremierDraft",
        "color": "G",
        "rarity": "common",
        "types": ["Creature - Beast"],
        "layout": "standard",
        "seen_count": 1000,
        "avg_seen": 5.0,
        "pick_count": 200,
        "avg_pick": 7.0,
        "game_count": 800,
        "pool_count": 1500,
        "play_rate": 0.6,
        "win_rate": 0.55,
        "opening_hand_game_count": 100,
        "opening_hand_win_rate": 0.54,
        "drawn_game_count": 200,
        "drawn_win_rate": 0.56,
        "ever_drawn_game_count": 300,
        "ever_drawn_win_rate": 0.555,
        "never_drawn_game_count": 500,
        "never_drawn_win_rate": 0.50,
        "drawn_improvement_win_rate": 0.055,
    }
    base.update(overrides)
    return base


def _write_ratings_parquet(
    data_root: Path, set_code: str, rows: list[dict[str, Any]]
) -> Path:
    """Write a hand-rolled parquet at the canonical location for tests."""
    path = ratings_parquet_path(set_code, "PremierDraft", data_root=data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    # pyarrow's parquet stubs aren't strict-mode clean; the call is fine.
    pq.write_table(table, path)  # type: ignore[no-untyped-call]
    return path


def _make_card(
    *,
    name: str,
    arena_id: int | None = None,
    oracle_id: str = "oracle-x",
    set_code: str = "TST",
) -> ParsedCard:
    """Minimal ParsedCard for match() tests."""
    return ParsedCard(
        name=name,
        set_code=set_code,
        collector_number="1",
        oracle_id=oracle_id,
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text="",
        type_line="Creature — Test",
        types=["Creature"],
        subtypes=["Test"],
        supertypes=[],
        colors=[],
        power="1",
        toughness="1",
        evergreen_keywords=[],
        modes=[],
        mana_abilities=[],
        enter_condition=None,
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
        reasons=[],
    )


# ---------------------------------------------------------------------------
# Loader: parquet → StatsLookup
# ---------------------------------------------------------------------------


def test_load_premier_draft_stats_returns_stats_lookup(tmp_path: Path) -> None:
    rows = [
        _row(name="Aang's Journey", mtga_id=97274, ever_drawn_win_rate=0.5658),
        _row(name="Some Other Card", mtga_id=99999, ever_drawn_win_rate=0.48),
    ]
    _write_ratings_parquet(tmp_path, "TST", rows)

    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    assert isinstance(stats, StatsLookup)
    assert set(stats.by_name.keys()) == {"Aang's Journey", "Some Other Card"}
    assert set(stats.by_arena_id.keys()) == {97274, 99999}
    aang = stats.by_name["Aang's Journey"]
    assert isinstance(aang, SeventeenLandsStats)
    assert aang.mtga_id == 97274
    assert aang.expansion == "TST"
    assert aang.event_type == "PremierDraft"
    assert aang.ever_drawn_win_rate == pytest.approx(0.5658)
    # Both indices point at the same instance.
    assert stats.by_arena_id[97274] is aang


def test_load_handles_null_win_rates(tmp_path: Path) -> None:
    """17Lands returns null for win-rate fields when the sample is too small.

    The loader must accept None for every Optional[float] win-rate column
    while keeping the always-populated game-count columns intact.
    """
    rows = [
        # First row keeps the float columns float-typed so pyarrow infers
        # the column types correctly; the second row exercises the null path.
        _row(name="Has Data", ever_drawn_win_rate=0.55),
        _row(
            name="Low Sample",
            mtga_id=99999,
            opening_hand_win_rate=None,
            drawn_win_rate=None,
            ever_drawn_win_rate=None,
            never_drawn_win_rate=None,
            drawn_improvement_win_rate=None,
        ),
    ]
    _write_ratings_parquet(tmp_path, "TST", rows)

    stats = load_premier_draft_stats("TST", data_root=tmp_path)
    has_data = stats.by_name["Has Data"]
    low_sample = stats.by_name["Low Sample"]

    assert has_data.ever_drawn_win_rate == pytest.approx(0.55)
    assert low_sample.opening_hand_win_rate is None
    assert low_sample.drawn_win_rate is None
    assert low_sample.ever_drawn_win_rate is None
    assert low_sample.never_drawn_win_rate is None
    assert low_sample.drawn_improvement_win_rate is None
    # Game-count columns are always populated even when win rates are null.
    assert low_sample.game_count == 800


def test_load_raises_clear_error_when_parquet_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        load_premier_draft_stats("MISSING", data_root=tmp_path)
    msg = str(excinfo.value)
    # Surface the path so the user can see exactly where we looked, and
    # point at the refresh CLI so they know how to fix it.
    assert "MISSING" in msg
    assert "mulligan-coach-data refresh-17lands" in msg


def test_set_code_is_case_normalised(tmp_path: Path) -> None:
    """Callers may pass lowercase set codes — the path resolver upper-cases."""
    rows = [_row(name="A", expansion="TLA")]
    # Write under upper-case directory; load with lower-case argument.
    _write_ratings_parquet(tmp_path, "TLA", rows)
    stats = load_premier_draft_stats("tla", data_root=tmp_path)
    assert "A" in stats.by_name


# ---------------------------------------------------------------------------
# StatsLookup.match — three-tier fallback
# ---------------------------------------------------------------------------


def test_match_uses_arena_id_when_available(tmp_path: Path) -> None:
    """Tier 1: arena_id match wins, even when name would also match."""
    # Two stat rows with different names but one matching the card's arena_id.
    rows = [
        _row(name="By Name", mtga_id=11111, ever_drawn_win_rate=0.40),
        _row(name="By Arena ID", mtga_id=22222, ever_drawn_win_rate=0.60),
    ]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    # Card has arena_id=22222 but name="By Name". arena_id should win.
    card = _make_card(name="By Name", arena_id=22222)
    matched = stats.match(card)
    assert matched is not None
    assert matched.name == "By Arena ID"
    assert matched.ever_drawn_win_rate == pytest.approx(0.60)


def test_match_falls_back_to_exact_name_when_no_arena_id(tmp_path: Path) -> None:
    """Tier 2: when arena_id is None, exact name still works."""
    rows = [_row(name="Aang's Journey", mtga_id=97274, ever_drawn_win_rate=0.55)]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    card = _make_card(name="Aang's Journey", arena_id=None)
    matched = stats.match(card)
    assert matched is not None
    assert matched.mtga_id == 97274


def test_match_falls_back_to_front_face_for_dfc(tmp_path: Path) -> None:
    """Tier 3: DFC joint name → front-face name match.

    This is the primary motivator for the three-tier scheme — solves
    the 8 unmatched TLA DFCs without needing MTGJSON to populate
    arena_id for the new set.
    """
    # 17Lands stores under the front face only.
    rows = [_row(name="The Legend of Yangchen", mtga_id=97300)]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    # ParsedCard preserves the joint name.
    card = _make_card(
        name="The Legend of Yangchen // Avatar Yangchen",
        arena_id=None,
    )
    matched = stats.match(card)
    assert matched is not None
    assert matched.name == "The Legend of Yangchen"


def test_match_falls_back_when_arena_id_is_unknown(tmp_path: Path) -> None:
    """If card has arena_id but stats don't, name fallback still kicks in."""
    rows = [_row(name="Aang's Journey", mtga_id=97274, ever_drawn_win_rate=0.55)]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    # Card has a stale/wrong arena_id, but the name matches.
    card = _make_card(name="Aang's Journey", arena_id=88888)
    matched = stats.match(card)
    assert matched is not None
    assert matched.name == "Aang's Journey"


def test_match_returns_none_when_nothing_matches(tmp_path: Path) -> None:
    rows = [_row(name="Existing Card", mtga_id=11111)]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    card = _make_card(name="Unknown Card", arena_id=99999)
    assert stats.match(card) is None


def test_match_does_not_split_names_without_separator(tmp_path: Path) -> None:
    """Cards with no `// ` in the name shouldn't trigger the front-face path."""
    rows = [_row(name="Half", mtga_id=11111)]
    _write_ratings_parquet(tmp_path, "TST", rows)
    stats = load_premier_draft_stats("TST", data_root=tmp_path)

    # "Half-Elf" is not a DFC — must not match "Half" via tier-3.
    card = _make_card(name="Half-Elf", arena_id=None)
    assert stats.match(card) is None
