"""Tests for the CLI commands that touch the persistent store: ``mark`` and
``list-needs-llm``. We test these via the typer CliRunner against a tmp
data root so no real on-disk data is touched.

The ``run-detector`` command is tested indirectly via the underlying
``merge_detector_run`` in ``test_store.py``; the CLI thin-wrapper here
just exercises the input plumbing. The bonus-sheet helper used by
``run-detector`` is unit-tested below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from mulligan_coach_cards.cli import (
    _backfill_arena_ids_from_ratings,
    _bonus_sheet_scryfall_entries,
    app,
)
from mulligan_coach_cards.models import (
    ParsedCard,
    ParseStatus,
    RoleFeatures,
)
from mulligan_coach_cards.seventeenlands_stats import ratings_parquet_path
from mulligan_coach_cards.store import load_parsed_cards, save_parsed_cards
from typer.testing import CliRunner

runner = CliRunner()


def _make_card(
    *,
    name: str,
    collector_number: str,
    oracle_id: str,
    status: ParseStatus = ParseStatus.NEEDS_LLM,
    raw_oracle_text: str = "Some oracle text.",
) -> ParsedCard:
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=collector_number,
        oracle_id=oracle_id,
        rarity="common",
        raw_oracle_text=raw_oracle_text,
        type_line="Creature — Human",
        types=["Creature"],
        subtypes=["Human"],
        supertypes=[],
        colors=[],
        power="1",
        toughness="1",
        evergreen_keywords=[],
        modes=[],
        mana_abilities=[],
        enter_condition=None,
        role_features=RoleFeatures(is_creature=True),
        status=status,
        reasons=["unrecognised line: 'foo bar'"],
    )


def test_mark_updates_status(tmp_path: Path) -> None:
    cards = [_make_card(name="Subject", collector_number="42", oracle_id="o42")]
    save_parsed_cards("TST", cards, data_root=tmp_path)

    result = runner.invoke(
        app,
        [
            "mark",
            "--set",
            "TST",
            "--collector",
            "42",
            "--status",
            "llm_encoded",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "llm_encoded" in result.stdout

    after = load_parsed_cards("TST", data_root=tmp_path)
    assert after[0].status is ParseStatus.LLM_ENCODED


def test_mark_applies_patch_and_status(tmp_path: Path) -> None:
    cards = [_make_card(name="Subject", collector_number="7", oracle_id="o7")]
    save_parsed_cards("TST", cards, data_root=tmp_path)

    patch_path = tmp_path / "patch.json"
    patch_path.write_text(
        json.dumps(
            {
                "role_features": {
                    "is_creature": True,
                    "is_bounce": True,
                    "cards_drawn": 2,
                },
                "reasons": ["llm: bounce + draw two"],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "mark",
            "--set",
            "TST",
            "--collector",
            "7",
            "--status",
            "llm_encoded",
            "--patch",
            str(patch_path),
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout

    after = load_parsed_cards("TST", data_root=tmp_path)
    assert after[0].status is ParseStatus.LLM_ENCODED
    assert after[0].role_features.is_bounce is True
    assert after[0].role_features.cards_drawn == 2
    assert "llm: bounce + draw two" in after[0].reasons


def test_mark_rejects_unknown_collector(tmp_path: Path) -> None:
    save_parsed_cards("TST", [], data_root=tmp_path)
    result = runner.invoke(
        app,
        [
            "mark",
            "--set",
            "TST",
            "--collector",
            "99",
            "--status",
            "llm_encoded",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "No card with collector number" in result.stdout


def test_mark_rejects_invalid_status(tmp_path: Path) -> None:
    cards = [_make_card(name="Subject", collector_number="1", oracle_id="o1")]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    result = runner.invoke(
        app,
        [
            "mark",
            "--set",
            "TST",
            "--collector",
            "1",
            "--status",
            "garbage",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "Invalid status" in result.stdout


def test_list_needs_llm_json_output(tmp_path: Path) -> None:
    cards = [
        _make_card(name="A", collector_number="1", oracle_id="oa", status=ParseStatus.AUTO),
        _make_card(name="B", collector_number="2", oracle_id="ob", status=ParseStatus.NEEDS_LLM),
        _make_card(name="C", collector_number="3", oracle_id="oc", status=ParseStatus.NEEDS_LLM),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)

    result = runner.invoke(
        app,
        [
            "list-needs-llm",
            "--set",
            "TST",
            "--limit",
            "10",
            "--json",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert {c["name"] for c in payload} == {"B", "C"}
    # Status must be needs_llm in the dump.
    assert all(c["status"] == "needs_llm" for c in payload)


def test_list_needs_llm_respects_limit(tmp_path: Path) -> None:
    cards = [
        _make_card(
            name=f"Card-{i}",
            collector_number=str(i),
            oracle_id=f"o{i}",
            status=ParseStatus.NEEDS_LLM,
        )
        for i in range(1, 6)
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)

    result = runner.invoke(
        app,
        [
            "list-needs-llm",
            "--set",
            "TST",
            "--limit",
            "3",
            "--json",
            "--data-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert len(payload) == 3
    # Sorted by collector number numeric order.
    assert [c["collector_number"] for c in payload] == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# _bonus_sheet_scryfall_entries — picking up reprints from 17Lands ratings
# ---------------------------------------------------------------------------


def _ratings_row(
    *,
    name: str,
    mtga_id: int,
    expansion: str = "TST",
) -> dict[str, Any]:
    """Minimal 17Lands ratings row; only the fields the loader requires."""
    return {
        "name": name,
        "mtga_id": mtga_id,
        "expansion": expansion,
        "event_type": "PremierDraft",
        "color": "G",
        "rarity": "common",
        "types": ["Creature - Beast"],
        "layout": "standard",
        "seen_count": 100,
        "avg_seen": 5.0,
        "pick_count": 50,
        "avg_pick": 7.0,
        "game_count": 80,
        "pool_count": 150,
        "play_rate": 0.6,
        "win_rate": 0.55,
        "opening_hand_game_count": 10,
        "opening_hand_win_rate": 0.54,
        "drawn_game_count": 20,
        "drawn_win_rate": 0.56,
        "ever_drawn_game_count": 30,
        "ever_drawn_win_rate": 0.555,
        "never_drawn_game_count": 50,
        "never_drawn_win_rate": 0.50,
        "drawn_improvement_win_rate": 0.055,
    }


def _scryfall_card(
    *,
    name: str,
    set_code: str,
    oracle_id: str,
    layout: str = "normal",
) -> dict[str, Any]:
    """Minimal Scryfall-shaped card dict for testing the bonus-sheet helper."""
    return {
        "name": name,
        "set": set_code,
        "oracle_id": oracle_id,
        "collector_number": "1",
        "rarity": "common",
        "type_line": "Land",
        "layout": layout,
        "lang": "en",
        "oracle_text": "",
        "colors": [],
        "keywords": [],
    }


def _write_ratings(data_root: Path, set_code: str, rows: list[dict[str, Any]]) -> None:
    path = ratings_parquet_path(set_code, "PremierDraft", data_root=data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path)  # type: ignore[no-untyped-call]


def test_bonus_sheet_finds_reprints_outside_primary_pool(tmp_path: Path) -> None:
    """A card in the 17Lands ratings parquet but not in the primary set
    pool gets pulled in from another set's Scryfall entry, with its
    ``set`` and ``collector_number`` rewritten to the target code."""
    primary = _scryfall_card(name="Local Card", set_code="tst", oracle_id="oid-local")
    bonus_source = _scryfall_card(name="Evolving Wilds", set_code="tmc", oracle_id="oid-wilds")
    scryfall_by_name = {"Local Card": primary, "Evolving Wilds": bonus_source}

    _write_ratings(
        tmp_path,
        "TST",
        [
            _ratings_row(name="Local Card", mtga_id=1001),
            _ratings_row(name="Evolving Wilds", mtga_id=98586),
        ],
    )

    extras = _bonus_sheet_scryfall_entries("TST", [primary], scryfall_by_name, data_root=tmp_path)
    assert len(extras) == 1
    assert extras[0]["name"] == "Evolving Wilds"
    # Set was rewritten to the target so the parser will report set_code="TST".
    assert extras[0]["set"] == "tst"
    # Collector number rewritten so bonus-sheet entries don't collide
    # with primary set collector numbers in the resulting JSON.
    assert extras[0]["collector_number"] == "bonus-tmc-1"


def test_bonus_sheet_returns_empty_when_no_ratings_parquet(tmp_path: Path) -> None:
    """If the ratings parquet doesn't exist for the format (e.g., a brand-
    new set we haven't downloaded 17Lands data for yet), the helper
    returns empty rather than raising."""
    primary = _scryfall_card(name="Only Card", set_code="tst", oracle_id="oid-1")
    extras = _bonus_sheet_scryfall_entries(
        "TST", [primary], {"Only Card": primary}, data_root=tmp_path
    )
    assert extras == []


def test_bonus_sheet_skips_names_already_in_primary_pool(tmp_path: Path) -> None:
    """A name that appears both in the primary set pool AND the ratings
    parquet is NOT duplicated — the primary's entry wins."""
    primary = _scryfall_card(name="Shared Card", set_code="tst", oracle_id="oid-1")
    scryfall_by_name = {"Shared Card": primary}

    _write_ratings(tmp_path, "TST", [_ratings_row(name="Shared Card", mtga_id=1001)])

    extras = _bonus_sheet_scryfall_entries("TST", [primary], scryfall_by_name, data_root=tmp_path)
    assert extras == []


def test_bonus_sheet_warns_on_names_not_in_scryfall(tmp_path: Path, caplog: Any) -> None:
    """If a 17Lands ratings name has no Scryfall match (rare — Scryfall
    is essentially exhaustive), the helper logs a warning and continues
    rather than raising. The valid bonus cards still get returned."""
    primary = _scryfall_card(name="Local", set_code="tst", oracle_id="oid-1")
    bonus_known = _scryfall_card(name="Bitterblossom", set_code="2x2", oracle_id="oid-bb")
    scryfall_by_name = {"Local": primary, "Bitterblossom": bonus_known}
    _write_ratings(
        tmp_path,
        "TST",
        [
            _ratings_row(name="Local", mtga_id=1),
            _ratings_row(name="Bitterblossom", mtga_id=2),
            _ratings_row(name="Mystery Future Card", mtga_id=3),
        ],
    )

    extras = _bonus_sheet_scryfall_entries("TST", [primary], scryfall_by_name, data_root=tmp_path)
    assert [e["name"] for e in extras] == ["Bitterblossom"]


def test_bonus_sheet_handles_dfc_front_face_in_primary_pool(tmp_path: Path) -> None:
    """The primary pool exposes DFCs by their joint ``Front // Back`` name;
    17Lands uses the front-face name only. The helper de-duplicates by
    treating the front face of any DFC in the primary as already-covered."""
    primary_dfc = _scryfall_card(
        name="Bursting Sunrise // Day's Glow",
        set_code="tst",
        oracle_id="oid-dfc",
    )
    # 17Lands lists the card under its front-face name.
    _write_ratings(tmp_path, "TST", [_ratings_row(name="Bursting Sunrise", mtga_id=42)])

    extras = _bonus_sheet_scryfall_entries(
        "TST",
        [primary_dfc],
        {"Bursting Sunrise // Day's Glow": primary_dfc},
        data_root=tmp_path,
    )
    assert extras == []


# ---------------------------------------------------------------------------
# _backfill_arena_ids_from_ratings — arena_id from 17Lands' mtga_id
# ---------------------------------------------------------------------------


def test_backfill_arena_ids_stamps_cards_missing_an_id(tmp_path: Path) -> None:
    """Cards whose arena_id MTGJSON hasn't supplied get it from 17Lands'
    ``mtga_id``; cards that already have one are left untouched; DFCs
    resolve on their front-face name; cards absent from the ratings stay
    None."""
    cards = [
        _make_card(name="No Arena Id", collector_number="1", oracle_id="o1"),
        _make_card(name="Already Has Id", collector_number="2", oracle_id="o2"),
        _make_card(name="Front Face // Back Face", collector_number="3", oracle_id="o3"),
        _make_card(name="Not In Ratings", collector_number="4", oracle_id="o4"),
    ]
    cards[1].arena_id = 999  # MTGJSON already populated this — must be preserved.

    _write_ratings(
        tmp_path,
        "TST",
        [
            _ratings_row(name="No Arena Id", mtga_id=111),
            _ratings_row(name="Already Has Id", mtga_id=222),
            _ratings_row(name="Front Face", mtga_id=333),
        ],
    )

    n = _backfill_arena_ids_from_ratings(cards, "TST", data_root=tmp_path)

    assert n == 2  # the missing-id card and the DFC front-face match.
    assert cards[0].arena_id == 111
    assert cards[1].arena_id == 999  # untouched — MTGJSON stays authoritative.
    assert cards[2].arena_id == 333  # matched on the front face.
    assert cards[3].arena_id is None  # not in the ratings → left None.


def test_backfill_arena_ids_noop_without_ratings_parquet(tmp_path: Path) -> None:
    """A brand-new set with no ratings parquet yet → no-op, returns 0,
    leaves arena_ids untouched rather than raising."""
    cards = [_make_card(name="Lonely", collector_number="1", oracle_id="o1")]
    n = _backfill_arena_ids_from_ratings(cards, "TST", data_root=tmp_path)
    assert n == 0
    assert cards[0].arena_id is None
