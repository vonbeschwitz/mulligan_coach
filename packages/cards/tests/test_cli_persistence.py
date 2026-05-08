"""Tests for the CLI commands that touch the persistent store: ``mark`` and
``list-needs-llm``. We test these via the typer CliRunner against a tmp
data root so no real on-disk data is touched.

The ``run-detector`` command is tested indirectly via the underlying
``merge_detector_run`` in ``test_store.py``; the CLI thin-wrapper here
just exercises the input plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path

from mulligan_coach_cards.cli import app
from mulligan_coach_cards.models import (
    ParsedCard,
    ParseStatus,
    RoleFeatures,
)
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
