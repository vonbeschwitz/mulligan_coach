"""Tests for the DuckDB-> :class:`TrainingRow` reader.

Strategy: build an in-memory DuckDB ``games`` view from a small
list of dict rows, hand-feed a name -> :class:`ParsedCard` lookup,
and exercise the iterator's filter logic on hand-built inputs.
This keeps tests fast (no file I/O, no parsed_cards JSON
dependency) and self-contained.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import duckdb
import pytest
from mulligan_coach_cards import (
    Cost,
    Mode,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_model.training_rows import (
    EXPECTED_DECK_SIZE,
    EXPECTED_HAND_SIZE,
    MAX_MULLIGAN_NUMBER,
    UNKNOWN_BUCKET,
    TrainingRow,
    TrainingRowStats,
    bucket_user_n_games,
    bucket_user_wr,
    build_name_lookup,
    iter_training_rows,
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


def _basic_card_lookup(extra_names: Iterable[str] = ()) -> dict[str, ParsedCard]:
    """Build a name -> ParsedCard lookup with synthesised basics plus
    vanilla creatures for any additional names callers want addressable.

    Mirrors :func:`build_name_lookup` shape so the iterator's
    pathways exercise the same key set as production.
    """
    from mulligan_coach_model.training_rows import _BASIC_LANDS, _make_basic_land

    lookup: dict[str, ParsedCard] = {
        name: _make_basic_land(name, color, subtype) for name, color, subtype in _BASIC_LANDS
    }
    for name in extra_names:
        lookup[name] = _vanilla_creature(name)
    return lookup


def _games_view_from_rows(rows: list[dict[str, Any]]) -> duckdb.DuckDBPyConnection:
    """Materialise a games view in an in-memory DuckDB from row dicts.

    Rows may carry different deck_<NAME> / opening_hand_<NAME> column
    sets; the schema is the *union* of all keys, with missing card
    values filled with 0 (so a row that doesn't mention "NotACard"
    sees deck_NotACard=0 instead of NULL, matching the real view's
    behaviour for cards from the same set's universe). Non-card
    columns missing on a row are kept as NULL.

    Uses pyarrow to carry typing through DuckDB's ``register`` —
    that's the standard way to inject Python data without taking
    a pandas dependency.
    """
    import pyarrow as pa

    if not rows:
        raise ValueError("Need at least one row to build the view.")
    # Union of column names across rows, preserving the first
    # appearance order so tests get a stable schema layout.
    all_keys: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    cols: dict[str, list[Any]] = {}
    for k in all_keys:
        if k.startswith(("deck_", "opening_hand_")):
            # 17Lands behaviour: missing card columns for off-set rows
            # come through as NULL; we use 0 here to match the typical
            # _make_row default for card counts and keep schema typing
            # consistent.
            cols[k] = [r.get(k, 0) for r in rows]
        else:
            cols[k] = [r.get(k) for r in rows]

    table = pa.table(cols)
    con = duckdb.connect(":memory:")
    # Registering the arrow table on this connection avoids the
    # cross-connection replacement-scan error you get from
    # ``duckdb.from_arrow`` (which binds to the default connection).
    con.register("games_src", table)
    con.execute("CREATE VIEW games AS SELECT * FROM games_src")
    return con


def _make_row(
    *,
    hand: dict[str, int],
    deck: dict[str, int],
    expansion: str = "TLA",
    event_type: str = "PremierDraft",
    draft_id: str = "draft-1",
    game_number: int = 1,
    on_play: bool = True,
    num_mulligans: int = 0,
    opp_num_mulligans: int = 0,
    won: bool = True,
    user_n_games_bucket: int | None = 100,
    user_game_win_rate_bucket: float | None = 0.55,
    extra_card_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Build one row dict for the in-memory games view.

    ``hand`` / ``deck`` map card name -> count; missing names
    default to 0. ``extra_card_names`` lets the caller list the
    full universe of card columns the row should carry — needed
    so multiple rows over different name sets share a schema.
    """
    all_names: set[str] = set()
    all_names.update(hand.keys())
    all_names.update(deck.keys())
    all_names.update(extra_card_names)

    row: dict[str, Any] = {
        "expansion": expansion,
        "event_type": event_type,
        "draft_id": draft_id,
        "game_number": game_number,
        "on_play": on_play,
        "num_mulligans": num_mulligans,
        "opp_num_mulligans": opp_num_mulligans,
        "won": won,
        "user_n_games_bucket": user_n_games_bucket,
        "user_game_win_rate_bucket": user_game_win_rate_bucket,
    }
    for name in sorted(all_names):
        row[f"deck_{name}"] = deck.get(name, 0)
        row[f"opening_hand_{name}"] = hand.get(name, 0)
    return row


# ---------------------------------------------------------------------------
# bucket_user_wr / bucket_user_n_games
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, UNKNOWN_BUCKET),
        (float("nan"), UNKNOWN_BUCKET),
        (0.0, "<45%"),
        (0.30, "<45%"),
        (0.44, "<45%"),
        (0.45, "45-50%"),
        (0.49, "45-50%"),
        (0.50, "50-55%"),
        (0.54, "50-55%"),
        (0.55, "55-60%"),
        (0.59, "55-60%"),
        (0.60, ">=60%"),
        (0.94, ">=60%"),
    ],
)
def test_bucket_user_wr(value: float | None, expected: str) -> None:
    assert bucket_user_wr(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, UNKNOWN_BUCKET),
        (1, "<10"),
        (5, "<10"),
        (10, "10-49"),
        (50, "50-99"),
        (100, "100-499"),
        (500, "500+"),
        (1000, "500+"),
        (7, UNKNOWN_BUCKET),  # Unknown edge value.
        (99999, UNKNOWN_BUCKET),
    ],
)
def test_bucket_user_n_games(value: int | None, expected: str) -> None:
    assert bucket_user_n_games(value) == expected


# ---------------------------------------------------------------------------
# build_name_lookup — synth basics, DFC fallback
# ---------------------------------------------------------------------------


def test_make_basic_land_via_lookup_synthesises_all_five_wubrg() -> None:
    """build_name_lookup falls back to synthesised basics for the 5 WUBRG
    names even when no parsed-cards file is present (we point at a tmp
    empty data root)."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        lookup = build_name_lookup("ZZZ", data_root=Path(td))
    assert set(lookup) == {"Plains", "Island", "Swamp", "Mountain", "Forest"}
    for name, _color, subtype in (
        ("Plains", "W", "Plains"),
        ("Island", "U", "Island"),
        ("Swamp", "B", "Swamp"),
        ("Mountain", "R", "Mountain"),
        ("Forest", "G", "Forest"),
    ):
        c = lookup[name]
        assert c.role_features.is_land
        assert subtype in c.subtypes
        assert c.mana_abilities and c.mana_abilities[0].produces


# ---------------------------------------------------------------------------
# iter_training_rows — happy path + skips
# ---------------------------------------------------------------------------


def test_iter_training_rows_happy_path() -> None:
    """One well-formed row materialises into one TrainingRow with the
    expected fields and reconstructed hand / deck lists."""
    lookup = _basic_card_lookup(extra_names=["Bear", "Elk"])
    row = _make_row(
        hand={"Forest": 4, "Bear": 2, "Elk": 1},
        deck={"Forest": 17, "Bear": 12, "Elk": 11},
        on_play=False,
        num_mulligans=1,
        opp_num_mulligans=2,
        user_n_games_bucket=100,
        user_game_win_rate_bucket=0.58,
    )
    con = _games_view_from_rows([row])

    out = list(
        iter_training_rows(
            connection=con,
            set_code="TLA",
            name_lookup=lookup,
        )
    )
    assert len(out) == 1
    tr = out[0]
    assert isinstance(tr, TrainingRow)
    assert len(tr.hand) == EXPECTED_HAND_SIZE
    assert len(tr.deck) == EXPECTED_DECK_SIZE
    assert sum(1 for c in tr.hand if c.name == "Forest") == 4
    assert sum(1 for c in tr.deck if c.name == "Bear") == 12
    assert tr.on_the_play is False
    assert tr.mulligan_number == 1
    assert tr.opp_mulligan_number == 2
    assert tr.user_wr_bucket == "55-60%"
    assert tr.user_n_games_bucket == "100-499"
    assert tr.expansion == "TLA"
    assert tr.event_type == "PremierDraft"
    assert tr.draft_id == "draft-1"


def test_iter_training_rows_filters_wrong_set() -> None:
    """SQL filter on expansion drops off-set rows before the wide scan."""
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, expansion="TLA"),
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, expansion="ECL"),
    ]
    con = _games_view_from_rows(rows)
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert out[0].expansion == "TLA"


def test_iter_training_rows_filters_wrong_event_type() -> None:
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, event_type="PremierDraft"),
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, event_type="TradDraft"),
    ]
    con = _games_view_from_rows(rows)
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert out[0].event_type == "PremierDraft"


def test_iter_training_rows_skips_bad_hand_size() -> None:
    lookup = _basic_card_lookup(["Bear"])
    bad_hand = _make_row(hand={"Forest": 6}, deck={"Forest": 25, "Bear": 15})
    good = _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15})
    con = _games_view_from_rows([bad_hand, good])
    stats = TrainingRowStats()
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert stats.skipped_bad_hand_size == 1
    assert stats.emitted == 1


def test_iter_training_rows_skips_bad_deck_size() -> None:
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 14}),  # 39 = bad
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}),  # 40 = good
    ]
    con = _games_view_from_rows(rows)
    stats = TrainingRowStats()
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert stats.skipped_bad_deck_size == 1


def test_iter_training_rows_skips_unknown_card() -> None:
    """A row referencing a card name absent from the lookup is dropped
    AND the unknown name is recorded in stats.unknown_card_names."""
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(
            hand={"Forest": 6, "NotACard": 1},
            deck={"Forest": 25, "Bear": 14, "NotACard": 1},
        ),
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}),
    ]
    con = _games_view_from_rows(rows)
    stats = TrainingRowStats()
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert stats.skipped_unknown_card == 1
    assert stats.unknown_card_names.get("NotACard", 0) >= 1


def test_iter_training_rows_skips_bad_mulligan_number() -> None:
    """Data quirks with num_mulligans=7 must not slip through."""
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(
            hand={"Forest": 7},
            deck={"Forest": 25, "Bear": 15},
            num_mulligans=MAX_MULLIGAN_NUMBER + 1,
        ),
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, num_mulligans=2),
    ]
    con = _games_view_from_rows(rows)
    stats = TrainingRowStats()
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert stats.skipped_bad_mulligan == 1


def test_iter_training_rows_handles_missing_user_buckets() -> None:
    """Missing user_wr / user_n_games values should bucket to "unknown",
    not skip the row."""
    lookup = _basic_card_lookup(["Bear"])
    row = _make_row(
        hand={"Forest": 7},
        deck={"Forest": 25, "Bear": 15},
        user_n_games_bucket=None,
        user_game_win_rate_bucket=None,
    )
    con = _games_view_from_rows([row])
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert out[0].user_wr_bucket == UNKNOWN_BUCKET
    assert out[0].user_n_games_bucket == UNKNOWN_BUCKET


def test_iter_training_rows_limit_respected() -> None:
    """The ``limit`` kwarg caps the SQL fetch — useful for smoke tests."""
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(
            hand={"Forest": 7},
            deck={"Forest": 25, "Bear": 15},
            draft_id=f"draft-{i}",
            game_number=i,
        )
        for i in range(5)
    ]
    con = _games_view_from_rows(rows)
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, limit=2))
    assert len(out) == 2


def test_iter_training_rows_dfc_front_face_fallback() -> None:
    """DFCs are stored under "Front // Back" in ParsedCard.name but
    17Lands columns use the front-face name. build_name_lookup
    indexes both so the iterator finds the card either way."""
    # Synthesise a fake DFC ParsedCard.
    cost = Cost(mana=parse_mana_cost("{1}{G}"))
    dfc = ParsedCard(
        name="Wolfman // Wolf Form",
        set_code="TST",
        collector_number="123",
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="Vanilla.",
        type_line="Creature // Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost("{1}{G}"),
        power="2",
        toughness="2",
        modes=[Mode(kind="cast", cost=cost, effects=[])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )
    lookup = _basic_card_lookup()
    lookup[dfc.name] = dfc
    # The plain build_name_lookup function adds the front-face alias;
    # mirror that here to keep the test honest about the production code path.
    lookup.setdefault("Wolfman", dfc)
    row = _make_row(
        hand={"Forest": 6, "Wolfman": 1},
        deck={"Forest": 24, "Wolfman": 16},
    )
    con = _games_view_from_rows([row])
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup))
    assert len(out) == 1
    assert any(c.name.startswith("Wolfman") for c in out[0].hand)


def test_iter_training_rows_raises_when_required_column_missing() -> None:
    """The view-shape pre-flight check catches schema regressions early."""
    lookup = _basic_card_lookup(["Bear"])
    row = _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15})
    # Strip a required column.
    del row["draft_id"]
    con = _games_view_from_rows([row])
    with pytest.raises(ValueError, match="missing required columns"):
        list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup))


def test_iter_training_rows_skips_when_context_value_is_null() -> None:
    """A row with a NULL ``won`` (or other required) field is dropped."""
    lookup = _basic_card_lookup(["Bear"])
    rows = [
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, won=True),
        _make_row(hand={"Forest": 7}, deck={"Forest": 25, "Bear": 15}, won=True),
    ]
    rows[0]["won"] = None  # force a NULL after construction
    con = _games_view_from_rows(rows)
    stats = TrainingRowStats()
    out = list(iter_training_rows(connection=con, set_code="TLA", name_lookup=lookup, stats=stats))
    assert len(out) == 1
    assert stats.skipped_missing_context == 1
