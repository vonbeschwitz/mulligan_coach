"""Tests for the per-row simulation + feature builder + parquet writer.

The simulator path is exercised on a hand-built mono-green deck
(Forests + vanilla creatures + a Cultivate-shaped ramp spell) so
the tests don't depend on a real parsed_cards JSON. The feature
builder runs with empty shrunk / zscore dicts because the TLA
parsed-cards have ``arena_id=None`` in production too — so the
per-card-WR features fall to 0 in both places.
"""

from __future__ import annotations

import logging
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest
from mulligan_coach_cards import (
    Cost,
    EntersBattlefieldEffect,
    FetchLandEffect,
    ManaAbility,
    Mode,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_model import (
    MaterializationStats,
    TrainingRow,
    build_row,
    feature_parquet_paths,
    iter_feature_rows,
    materialize_feature_matrix,
)
from mulligan_coach_model.feature_matrix import _FormatStats, _library_from_deck, _row_seed

# ---------------------------------------------------------------------------
# Card factories — keep these self-contained so tests don't reach into
# packages/features/tests/_factories.py (cross-package test imports
# are a known pain point on Windows pytest collection).
# ---------------------------------------------------------------------------


_NEXT_OID = [0]


def _oid() -> str:
    _NEXT_OID[0] += 1
    return f"00000000-0000-0000-0000-{_NEXT_OID[0]:012d}"


def _forest() -> ParsedCard:
    return ParsedCard(
        name="Forest",
        set_code="BASIC",
        collector_number="forest",
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text="({T}: Add {G}.)",
        type_line="Basic Land — Forest",
        types=["Land"],
        subtypes=["Forest"],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[["G"]])],
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )


def _vanilla(name: str, mana: str = "{1}{G}", pt: tuple[str, str] = ("2", "2")) -> ParsedCard:
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
        power=pt[0],
        toughness=pt[1],
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def _cultivate() -> ParsedCard:
    cost = Cost(mana=parse_mana_cost("{2}{G}"))
    return ParsedCard(
        name="Cultivate",
        set_code="TST",
        collector_number="cult",
        oracle_id=_oid(),
        rarity="uncommon",
        raw_oracle_text=(
            "Search your library for a basic land card, put it onto the battlefield tapped."
        ),
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{2}{G}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    FetchLandEffect(target_filter="basic", destination="battlefield_tapped"),
                ],
            )
        ],
        role_features=RoleFeatures(),
        status=ParseStatus.AUTO,
    )


def _hand_and_deck() -> tuple[tuple[ParsedCard, ...], tuple[ParsedCard, ...]]:
    """Build a mono-G hand + 40-card deck for the simulator.

    Hand: 3 Forests, 3 Bears, 1 Cultivate (7 cards).
    Deck: 17 Forests, 18 Bears, 5 Cultivates (40 cards; includes the hand copies).
    """
    forest = _forest()
    bear = _vanilla("Bear")
    cult = _cultivate()

    hand: list[ParsedCard] = [forest] * 3 + [bear] * 3 + [cult]
    deck: list[ParsedCard] = [forest] * 17 + [bear] * 18 + [cult] * 5
    return tuple(hand), tuple(deck)


def _empty_format_stats() -> _FormatStats:
    return _FormatStats(shrunk={}, zscores={})


def _training_row(
    *,
    on_play: bool = True,
    won: bool = True,
    mulligan: int = 0,
    opp_mulligan: int = 1,
    user_wr_bucket: str = "55-60%",
    user_n_games_bucket: str = "100-499",
    draft_id: str = "draft-1",
    match_number: int = 1,
    game_number: int = 1,
) -> TrainingRow:
    hand, deck = _hand_and_deck()
    return TrainingRow(
        hand=hand,
        deck=deck,
        on_the_play=on_play,
        mulligan_number=mulligan,
        opp_mulligan_number=opp_mulligan,
        user_wr_bucket=user_wr_bucket,
        user_n_games_bucket=user_n_games_bucket,
        expansion="TLA",
        event_type="PremierDraft",
        won=won,
        draft_id=draft_id,
        match_number=match_number,
        game_number=game_number,
    )


# ---------------------------------------------------------------------------
# _library_from_deck
# ---------------------------------------------------------------------------


def test_library_from_deck_subtracts_hand_by_name() -> None:
    forest, bear, cult = _forest(), _vanilla("Bear"), _cultivate()
    deck = (forest, forest, forest, bear, bear, bear, cult, cult)  # 8 cards
    hand = (forest, bear, cult)  # 3 cards
    library = _library_from_deck(hand, deck)
    assert len(library) == 5
    # Verify per-name counts.
    from collections import Counter

    assert Counter(c.name for c in library) == {"Forest": 2, "Bear": 2, "Cultivate": 1}


def test_library_from_deck_handles_full_deck_minus_full_hand() -> None:
    hand, deck = _hand_and_deck()
    library = _library_from_deck(hand, deck)
    assert len(library) == len(deck) - len(hand) == 33


# ---------------------------------------------------------------------------
# _row_seed determinism
# ---------------------------------------------------------------------------


def test_row_seed_is_deterministic_per_row() -> None:
    s1 = _row_seed("draft-1", 1, 1)
    s2 = _row_seed("draft-1", 1, 1)
    s3 = _row_seed("draft-2", 1, 1)
    s4 = _row_seed("draft-1", 1, 2)
    s5 = _row_seed("draft-1", 2, 1)  # different match -> different seed
    assert s1 == s2
    assert s1 != s3
    assert s1 != s4
    assert s1 != s5
    assert 0 <= s1 < 2**32


# ---------------------------------------------------------------------------
# build_row — produces the slim row schema with the expected extras
# ---------------------------------------------------------------------------


def test_build_row_shape_on_the_draw() -> None:
    tr = _training_row(on_play=False, opp_mulligan=2)
    out = build_row(tr, format_stats=_empty_format_stats(), n_sims_per_row=3)

    # Feature row should carry the 200 build_feature_row columns.
    assert "p_land_drop_by_turn_2" in out
    assert "n_lands_in_hand" in out
    assert "on_the_play" in out
    # And the extras for the cache schema.
    assert out["opp_mulligan_count_if_known"] == pytest.approx(2.0)
    assert out["opp_mulligan_number"] == 2
    assert out["user_wr_bucket"] == "55-60%"
    assert out["user_n_games_bucket"] == "100-499"
    assert out["mulligan_number"] == 0
    assert out["expansion"] == "TLA"
    assert out["event_type"] == "PremierDraft"
    assert out["draft_id"] == "draft-1"
    assert out["game_number"] == 1
    assert out["won"] is True


def test_build_row_opp_mull_is_none_on_the_play() -> None:
    """The conditional XGBoost feature is NULL when on_the_play=True; the
    raw opp_mulligan_number is still populated as a baseline context col."""
    tr = _training_row(on_play=True, opp_mulligan=2)
    out = build_row(tr, format_stats=_empty_format_stats(), n_sims_per_row=3)
    assert out["opp_mulligan_count_if_known"] is None
    assert out["opp_mulligan_number"] == 2


def test_build_row_is_reproducible_with_same_seed() -> None:
    """Same (draft_id, game_number) -> identical sim aggregates -> identical
    feature values across builds. Cheap sanity check for the per-row seed."""
    tr = _training_row(draft_id="seed-test", game_number=42)
    a = build_row(tr, format_stats=_empty_format_stats(), n_sims_per_row=5)
    b = build_row(tr, format_stats=_empty_format_stats(), n_sims_per_row=5)
    # Compare sim-derived feature subset (deterministic given seed).
    for key in ("p_land_drop_by_turn_2", "p_land_drop_by_turn_4", "expected_mana_count_turn_4"):
        assert a[key] == b[key], f"{key} drifted: {a[key]} vs {b[key]}"


# ---------------------------------------------------------------------------
# iter_feature_rows error handling
# ---------------------------------------------------------------------------


class _BrokenCard(ParsedCard):
    """A ParsedCard that the simulator will reject (status NEEDS_LLM)."""


def _broken_training_row() -> TrainingRow:
    # NEEDS_LLM card in the deck will fail check_deck_encodings.
    bad = ParsedCard(
        name="NeedsReview",
        set_code="TST",
        collector_number="bad",
        oracle_id=_oid(),
        rarity="rare",
        raw_oracle_text="something complex",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost("{3}"),
        power="3",
        toughness="3",
        modes=[],  # No modes + mana_cost set -> simulator rejects.
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.NEEDS_LLM,
    )
    forest = _forest()
    hand: list[ParsedCard] = [forest] * 4 + [bad] * 3
    deck: list[ParsedCard] = [forest] * 17 + [bad] * 23
    return TrainingRow(
        hand=tuple(hand),
        deck=tuple(deck),
        on_the_play=True,
        mulligan_number=0,
        opp_mulligan_number=0,
        user_wr_bucket="50-55%",
        user_n_games_bucket="100-499",
        expansion="TLA",
        event_type="PremierDraft",
        won=False,
        draft_id="draft-broken",
        match_number=1,
        game_number=1,
    )


def test_iter_feature_rows_skips_simulator_errors(caplog: pytest.LogCaptureFixture) -> None:
    good = _training_row()
    bad = _broken_training_row()
    stats = MaterializationStats()
    with caplog.at_level(logging.WARNING, logger="mulligan_coach_model.feature_matrix"):
        rows = list(
            iter_feature_rows(
                [bad, good],
                format_stats=_empty_format_stats(),
                n_sims_per_row=3,
                stats=stats,
            )
        )
    assert len(rows) == 1
    assert rows[0]["draft_id"] == "draft-1"
    assert stats.rows_failed_simulation == 1
    assert stats.rows_failed_feature_build == 0
    assert any("feature row build failed" in rec.message for rec in caplog.records)


def test_iter_feature_rows_raises_when_on_error_is_raise() -> None:
    bad = _broken_training_row()
    stats = MaterializationStats()
    from mulligan_coach_simulation import DeckEncodingError

    with pytest.raises(DeckEncodingError):
        list(
            iter_feature_rows(
                [bad],
                format_stats=_empty_format_stats(),
                n_sims_per_row=3,
                on_error="raise",
                stats=stats,
            )
        )


def test_iter_feature_rows_rejects_bad_on_error_value() -> None:
    with pytest.raises(ValueError, match="on_error"):
        list(
            iter_feature_rows(
                [_training_row()],
                format_stats=_empty_format_stats(),
                n_sims_per_row=3,
                on_error="nope",
            )
        )


# ---------------------------------------------------------------------------
# materialize_feature_matrix — end-to-end against a real parquet file
# ---------------------------------------------------------------------------


def _make_games_view(con: duckdb.DuckDBPyConnection, n_rows: int) -> None:
    """Hand-build an in-memory games view with `n_rows` mono-G TLA rows.

    Schema is shared across rows (full union of card columns).
    Stub the user-bucket fields with constants.
    """
    import pyarrow as pa

    # The hand and deck are by COUNT in 17Lands columns, not by instance.
    # Mono-G: 17 Forest + 18 Bear + 5 Cultivate = 40 deck;
    #         3 Forest + 3 Bear + 1 Cultivate = 7 hand.
    rows = [
        {
            "expansion": "TLA",
            "event_type": "PremierDraft",
            "draft_id": f"draft-{i}",
            "match_number": 1,
            "game_number": i,
            "on_play": (i % 2 == 0),
            "num_mulligans": 0,
            "opp_num_mulligans": 1,
            "won": (i % 3 == 0),
            "user_n_games_bucket": 100,
            "user_game_win_rate_bucket": 0.55,
            "deck_Forest": 17,
            "opening_hand_Forest": 3,
            "deck_Bear": 18,
            "opening_hand_Bear": 3,
            "deck_Cultivate": 5,
            "opening_hand_Cultivate": 1,
        }
        for i in range(n_rows)
    ]
    keys = list(rows[0].keys())
    cols = {k: [r[k] for r in rows] for k in keys}
    table = pa.table(cols)
    con.register("games_src", table)
    con.execute("CREATE VIEW games AS SELECT * FROM games_src")


def test_materialize_feature_matrix_writes_parquet(tmp_path: Path) -> None:
    # Set up an in-memory DuckDB with a tiny games view; persist it to
    # a file so the function (which opens read-only) can re-open it.
    db_path = tmp_path / "games.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        _make_games_view(con, n_rows=4)
        # Persist the view as a table; read_only mode wouldn't see the
        # registered Python relation.
        con.execute("CREATE TABLE games_table AS SELECT * FROM games")
        con.execute("DROP VIEW games")
        con.execute("CREATE VIEW games AS SELECT * FROM games_table")
    finally:
        con.close()

    # Patch the format-stats helper so we don't need a real ratings parquet
    # on disk. Empty dicts are what TLA looks like today anyway.
    import mulligan_coach_model.feature_matrix as fm

    original = fm._build_format_stats
    fm._build_format_stats = lambda *_, **__: _FormatStats(shrunk={}, zscores={})
    try:
        # Need a name lookup that resolves Forest/Bear/Cultivate. Use the
        # default by pointing data_root at a tmp empty dir (so the
        # iter_training_rows fallback gives basics) plus monkey-patching
        # build_name_lookup output via a custom data path... that's
        # awkward. Instead, monkey-patch iter_training_rows to inject the
        # lookup.
        import mulligan_coach_model.training_rows as tr

        original_build_lookup = tr.build_name_lookup
        # Match the test cards by name.
        lookup = {
            "Forest": _forest(),
            "Bear": _vanilla("Bear"),
            "Cultivate": _cultivate(),
        }
        tr.build_name_lookup = lambda *_args, **_kwargs: lookup

        out_dir = tmp_path / "TLA" / "PremierDraft"
        stats = materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
        )
    finally:
        fm._build_format_stats = original
        tr.build_name_lookup = original_build_lookup

    # Output dir should exist with chunk files.
    chunks = feature_parquet_paths(out_dir)
    assert len(chunks) == 2  # 4 rows / chunk_rows=2 = 2 chunks
    assert stats.rows_written == 4
    assert stats.chunks_written == 2

    # Inspect the written parquet (read all chunks together).
    table = pq.read_table(chunks)  # type: ignore[no-untyped-call]
    assert table.num_rows == 4
    cols = set(table.column_names)
    # Feature columns
    assert "p_land_drop_by_turn_2" in cols
    assert "on_the_play" in cols
    # Context / label
    assert "opp_mulligan_count_if_known" in cols
    assert "user_wr_bucket" in cols
    assert "user_n_games_bucket" in cols
    assert "won" in cols
    assert "draft_id" in cols

    # Half of rows are on_the_play=True (even-numbered draft ids).
    # Verify opp_mulligan_count_if_known is NULL exactly there.
    pdf = table.to_pandas()
    on_play_mask = pdf["on_the_play"].astype(bool) == True  # noqa: E712
    assert pdf.loc[on_play_mask, "opp_mulligan_count_if_known"].isna().all()
    assert (pdf.loc[~on_play_mask, "opp_mulligan_count_if_known"] == 1.0).all()


def test_materialize_feature_matrix_refuses_to_overwrite(tmp_path: Path) -> None:
    """When chunks exist and both overwrite=False and resume=False,
    the function refuses rather than risk losing or duplicating work."""
    out_dir = tmp_path / "shard_dir"
    out_dir.mkdir()
    (out_dir / "chunk_00000000.parquet").write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=tmp_path / "missing.duckdb",
            output_dir=out_dir,
            resume=False,
        )


def test_materialize_feature_matrix_rejects_bad_n_workers(tmp_path: Path) -> None:
    """n_workers=0 (or negative) is a config error; the function refuses
    before opening any files."""
    with pytest.raises(ValueError, match="n_workers"):
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=tmp_path / "missing.duckdb",
            output_dir=tmp_path / "out_dir",
            n_workers=0,
        )


def test_materialize_feature_matrix_rejects_bad_chunk_rows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="chunk_rows"):
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=tmp_path / "missing.duckdb",
            output_dir=tmp_path / "out_dir",
            chunk_rows=0,
        )


# ---------------------------------------------------------------------------
# Multiprocessing path: n_workers > 1
# ---------------------------------------------------------------------------
#
# Pool startup on Windows uses the spawn method, which re-imports the
# module fresh in each worker. We avoid the spawn cost in the test
# suite by limiting these to one or two compact cases — the heavy
# wall-clock evidence for the speedup comes from manual benchmarking,
# not the unit suite.


def _build_test_games_db(tmp_path: Path, n_rows: int) -> Path:
    """Create a small games.duckdb on disk with `n_rows` mono-G rows.

    Persisted to disk because :func:`materialize_feature_matrix`
    re-opens the file in read-only mode; an in-memory registered
    relation wouldn't survive the reopen.
    """
    db_path = tmp_path / "games.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        _make_games_view(con, n_rows=n_rows)
        con.execute("CREATE TABLE games_table AS SELECT * FROM games")
        con.execute("DROP VIEW games")
        con.execute("CREATE VIEW games AS SELECT * FROM games_table")
    finally:
        con.close()
    return db_path


def _patch_for_in_process_materialize() -> tuple[object, object]:
    """Monkey-patch the format-stats helper + name lookup for tests.

    These patches are applied in the MAIN process; workers inherit
    the patched values via the Pool initializer's ``initargs``
    (format_stats) or because :func:`iter_training_rows` runs only
    on the main process (name_lookup). So multiprocessing's
    spawn-re-import doesn't undo them.

    Returns the originals so the caller can restore them.
    """
    import mulligan_coach_model.feature_matrix as fm
    import mulligan_coach_model.training_rows as tr

    fm_original = fm._build_format_stats
    tr_original = tr.build_name_lookup
    fm._build_format_stats = lambda *_, **__: _FormatStats(shrunk={}, zscores={})
    tr.build_name_lookup = lambda *_a, **_kw: {
        "Forest": _forest(),
        "Bear": _vanilla("Bear"),
        "Cultivate": _cultivate(),
    }
    return fm_original, tr_original


def _restore_patches(originals: tuple[object, object]) -> None:
    import mulligan_coach_model.feature_matrix as fm
    import mulligan_coach_model.training_rows as tr

    fm._build_format_stats, tr.build_name_lookup = originals  # type: ignore[assignment]


def test_materialize_feature_matrix_n_workers_2_matches_single_process(
    tmp_path: Path,
) -> None:
    """Materializing the same shard with n_workers=1 and n_workers=2
    should yield identical feature values per (draft_id, game_number).

    Row ORDER may differ (imap_unordered) so we sort both sides by the
    grouping key before comparing.
    """
    db_path = _build_test_games_db(tmp_path, n_rows=4)
    originals = _patch_for_in_process_materialize()
    try:
        out_serial = tmp_path / "serial"
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_serial,
            n_sims_per_row=3,
            n_workers=1,
            chunk_rows=10,
        )
        out_parallel = tmp_path / "parallel"
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_parallel,
            n_sims_per_row=3,
            n_workers=2,
            chunksize=2,
            chunk_rows=10,
        )
    finally:
        _restore_patches(originals)

    chunks_serial = feature_parquet_paths(out_serial)
    chunks_parallel = feature_parquet_paths(out_parallel)
    assert len(chunks_serial) >= 1
    assert len(chunks_parallel) >= 1

    table_serial = pq.read_table(chunks_serial)  # type: ignore[no-untyped-call]
    table_parallel = pq.read_table(chunks_parallel)  # type: ignore[no-untyped-call]
    assert table_serial.num_rows == table_parallel.num_rows == 4

    # Sort both sides by (draft_id, game_number); compare every column.
    df_serial = (
        table_serial.to_pandas().sort_values(["draft_id", "game_number"]).reset_index(drop=True)
    )
    df_parallel = (
        table_parallel.to_pandas().sort_values(["draft_id", "game_number"]).reset_index(drop=True)
    )
    # The deterministic seed (sha256(draft_id || game_number)) means the
    # simulator's aggregate is bit-identical per row regardless of worker.
    import pandas as pd

    pd.testing.assert_frame_equal(df_serial, df_parallel, check_like=True)


def test_materialize_feature_matrix_n_workers_2_records_errors(tmp_path: Path) -> None:
    """A row with a NEEDS_LLM card in the deck must still bump
    ``rows_failed_simulation`` when processed through a worker pool."""
    # Build a games view where one row references a "BrokenCard" that
    # the name lookup resolves to a NEEDS_LLM ParsedCard — that'll
    # trip the simulator's deck-encoding check and raise inside the
    # worker. The other rows resolve to vanilla cards and succeed.
    db_path = tmp_path / "games.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        import pyarrow as pa

        rows = []
        for i in range(3):
            rows.append(
                {
                    "expansion": "TLA",
                    "event_type": "PremierDraft",
                    "draft_id": f"draft-{i}",
                    "match_number": 1,
                    "game_number": i,
                    "on_play": True,
                    "num_mulligans": 0,
                    "opp_num_mulligans": 0,
                    "won": False,
                    "user_n_games_bucket": 100,
                    "user_game_win_rate_bucket": 0.55,
                    "deck_Forest": 17,
                    "opening_hand_Forest": 3,
                    "deck_Bear": 18,
                    "opening_hand_Bear": 3,
                    "deck_Cultivate": 5,
                    "opening_hand_Cultivate": 1,
                    "deck_BrokenCard": 0,
                    "opening_hand_BrokenCard": 0,
                }
            )
        # One additional row uses BrokenCard so the worker hits the
        # deck-encoding error. Hand/deck sums must still match the
        # 7/40 invariants iter_training_rows enforces (otherwise
        # the bad row is skipped upstream).
        rows.append(
            {
                "expansion": "TLA",
                "event_type": "PremierDraft",
                "draft_id": "draft-bad",
                "match_number": 1,
                "game_number": 99,
                "on_play": True,
                "num_mulligans": 0,
                "opp_num_mulligans": 0,
                "won": False,
                "user_n_games_bucket": 100,
                "user_game_win_rate_bucket": 0.55,
                "deck_Forest": 17,
                "opening_hand_Forest": 2,
                "deck_Bear": 17,
                "opening_hand_Bear": 3,
                "deck_Cultivate": 5,
                "opening_hand_Cultivate": 1,
                "deck_BrokenCard": 1,
                "opening_hand_BrokenCard": 1,
            }
        )
        keys = list(rows[0].keys())
        cols = {k: [r[k] for r in rows] for k in keys}
        con.register("games_src", pa.table(cols))
        con.execute("CREATE TABLE games_table AS SELECT * FROM games_src")
        con.execute("CREATE VIEW games AS SELECT * FROM games_table")
    finally:
        con.close()

    # Build a name lookup that includes a BrokenCard with status=NEEDS_LLM
    # + mana_cost set + empty modes — the simulator's check_deck_encodings
    # will reject any deck containing it.
    broken = ParsedCard(
        name="BrokenCard",
        set_code="TST",
        collector_number="bad",
        oracle_id="broken",
        rarity="rare",
        raw_oracle_text="something complex",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost("{3}"),
        power="3",
        toughness="3",
        modes=[],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.NEEDS_LLM,
    )

    import mulligan_coach_model.feature_matrix as fm
    import mulligan_coach_model.training_rows as tr

    fm_original = fm._build_format_stats
    tr_original = tr.build_name_lookup
    fm._build_format_stats = lambda *_, **__: _FormatStats(shrunk={}, zscores={})
    tr.build_name_lookup = lambda *_a, **_kw: {
        "Forest": _forest(),
        "Bear": _vanilla("Bear"),
        "Cultivate": _cultivate(),
        "BrokenCard": broken,
    }
    try:
        out_dir = tmp_path / "shard_dir"
        stats = materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            n_workers=2,
            chunksize=1,
        )
    finally:
        fm._build_format_stats = fm_original
        tr.build_name_lookup = tr_original

    # 3 good rows + 1 row that fails simulation.
    assert stats.rows_written == 3
    assert stats.rows_failed_simulation == 1


# ---------------------------------------------------------------------------
# Resume / chunked-output tests
# ---------------------------------------------------------------------------


def test_materialize_feature_matrix_resumes_from_existing_chunks(tmp_path: Path) -> None:
    """Simulate a crash: run materialize to produce chunks, delete one
    chunk to leave a gap, run again with resume=True (default) and
    confirm only the missing rows are re-materialised."""
    db_path = _build_test_games_db(tmp_path, n_rows=6)
    originals = _patch_for_in_process_materialize()
    try:
        out_dir = tmp_path / "shard"
        stats_initial = materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
        )
        # 6 rows / chunk_rows=2 = 3 chunks expected.
        chunks = feature_parquet_paths(out_dir)
        assert len(chunks) == 3
        assert stats_initial.rows_written == 6
        assert stats_initial.chunks_written == 3
        assert stats_initial.rows_skipped_resume == 0

        # Delete one of the chunks to simulate a partial-run state.
        chunks[1].unlink()
        assert len(feature_parquet_paths(out_dir)) == 2

        # Re-run; resume=True is the default. Should skip the rows from
        # the two surviving chunks and re-materialise the 2 missing rows.
        stats_resume = materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
        )
    finally:
        _restore_patches(originals)

    # 4 surviving rows skipped + 2 new rows materialised.
    assert stats_resume.rows_skipped_resume == 4
    assert stats_resume.rows_written == 2
    assert stats_resume.chunks_written == 1

    # Total rows on disk should now equal the original 6, with no duplicates.
    final_chunks = feature_parquet_paths(out_dir)
    table = pq.read_table(final_chunks)  # type: ignore[no-untyped-call]
    df = table.to_pandas()
    assert len(df) == 6
    # No (draft_id, match_number, game_number) duplicates.
    assert df.duplicated(subset=["draft_id", "match_number", "game_number"]).sum() == 0


def test_materialize_feature_matrix_overwrite_clears_existing(tmp_path: Path) -> None:
    """overwrite=True deletes existing chunks and starts fresh at chunk 0."""
    db_path = _build_test_games_db(tmp_path, n_rows=4)
    originals = _patch_for_in_process_materialize()
    try:
        out_dir = tmp_path / "shard"
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
        )
        initial_chunks = feature_parquet_paths(out_dir)
        assert len(initial_chunks) == 2

        # Capture initial mtimes so we can detect that the chunks were rewritten.
        initial_mtimes = {p.name: p.stat().st_mtime_ns for p in initial_chunks}

        # Sleep briefly to ensure mtimes differ.
        import time

        time.sleep(0.05)

        stats_overwrite = materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
            overwrite=True,
        )
    finally:
        _restore_patches(originals)

    # Fresh start: all 4 rows re-materialised, no resume-skip.
    assert stats_overwrite.rows_skipped_resume == 0
    assert stats_overwrite.rows_written == 4
    final_chunks = feature_parquet_paths(out_dir)
    assert len(final_chunks) == 2
    # mtimes should be strictly newer than the originals.
    for p in final_chunks:
        assert p.stat().st_mtime_ns > initial_mtimes[p.name]


def test_materialize_feature_matrix_sweeps_stale_tmp_files(tmp_path: Path) -> None:
    """Orphan ``.chunk_*.tmp-*`` files (from a crashed prior run) are
    deleted at the start of a new run so they don't accumulate."""
    out_dir = tmp_path / "shard"
    out_dir.mkdir()
    stale_tmp = out_dir / ".chunk_00000000.parquet.tmp-99999"
    stale_tmp.write_bytes(b"orphan")
    assert stale_tmp.exists()

    db_path = _build_test_games_db(tmp_path, n_rows=2)
    originals = _patch_for_in_process_materialize()
    try:
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=out_dir,
            n_sims_per_row=3,
            chunk_rows=2,
        )
    finally:
        _restore_patches(originals)

    # The stale tmp file is gone; only the real chunk remains.
    assert not stale_tmp.exists()
    assert len(feature_parquet_paths(out_dir)) == 1


def test_feature_parquet_paths_returns_sorted_chunks(tmp_path: Path) -> None:
    out_dir = tmp_path / "shard"
    out_dir.mkdir()
    # Write chunks in non-alphabetical order; glob+sort should still
    # return them ordered.
    (out_dir / "chunk_00000002.parquet").write_bytes(b"")
    (out_dir / "chunk_00000000.parquet").write_bytes(b"")
    (out_dir / "chunk_00000001.parquet").write_bytes(b"")
    paths = feature_parquet_paths(out_dir)
    assert [p.name for p in paths] == [
        "chunk_00000000.parquet",
        "chunk_00000001.parquet",
        "chunk_00000002.parquet",
    ]


def test_feature_parquet_paths_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    assert feature_parquet_paths(tmp_path / "does_not_exist") == []
