"""Tests for the persistent ParsedCard store and the run-detector merge logic."""

from __future__ import annotations

import json
from pathlib import Path

from mulligan_coach_cards.mana import parse_mana_cost
from mulligan_coach_cards.models import (
    Cost,
    Mode,
    NoopEffect,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
)
from mulligan_coach_cards.store import (
    DEFAULT_CAST_ROLE_TAG,
    cards_by_status,
    load_parsed_cards,
    merge_detector_run,
    parsed_cards_path,
    save_parsed_cards,
    status_histogram,
    update_parsed_card,
)


def _make_card(
    *,
    name: str,
    collector_number: str,
    oracle_id: str,
    status: ParseStatus = ParseStatus.AUTO,
    role_features: RoleFeatures | None = None,
) -> ParsedCard:
    """Build a minimal ParsedCard for store tests."""
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=collector_number,
        oracle_id=oracle_id,
        rarity="common",
        raw_oracle_text="",
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
        role_features=role_features or RoleFeatures(is_creature=True),
        status=status,
        reasons=[],
    )


def test_round_trip_preserves_records(tmp_path: Path) -> None:
    cards = [
        _make_card(name="Alpha", collector_number="1", oracle_id="oracle-a"),
        _make_card(name="Beta", collector_number="2", oracle_id="oracle-b"),
        _make_card(name="Gamma", collector_number="3", oracle_id="oracle-c"),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    reloaded = load_parsed_cards("TST", data_root=tmp_path)
    assert [c.name for c in reloaded] == ["Alpha", "Beta", "Gamma"]
    assert reloaded[0].oracle_id == "oracle-a"
    assert reloaded[0].status is ParseStatus.AUTO


def test_save_writes_to_expected_path(tmp_path: Path) -> None:
    cards = [_make_card(name="Solo", collector_number="1", oracle_id="oracle-z")]
    out = save_parsed_cards("TLA", cards, data_root=tmp_path)
    expected = parsed_cards_path("TLA", data_root=tmp_path)
    assert out == expected
    assert expected.exists()
    raw = json.loads(expected.read_text(encoding="utf-8"))
    assert isinstance(raw, list)
    assert raw[0]["name"] == "Solo"
    assert raw[0]["status"] == "auto"


def test_save_sorts_by_collector_number(tmp_path: Path) -> None:
    cards = [
        _make_card(name="Three", collector_number="3", oracle_id="o3"),
        _make_card(name="One", collector_number="1", oracle_id="o1"),
        _make_card(name="Two", collector_number="2", oracle_id="o2"),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    reloaded = load_parsed_cards("TST", data_root=tmp_path)
    assert [c.collector_number for c in reloaded] == ["1", "2", "3"]


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_parsed_cards("DOES_NOT_EXIST", data_root=tmp_path) == []


def test_update_replaces_existing_entry_by_oracle_id(tmp_path: Path) -> None:
    cards = [
        _make_card(name="Original", collector_number="1", oracle_id="oracle-x"),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    updated = _make_card(
        name="Updated",
        collector_number="1",
        oracle_id="oracle-x",
        status=ParseStatus.LLM_ENCODED,
    )
    update_parsed_card("TST", updated, data_root=tmp_path)
    after = load_parsed_cards("TST", data_root=tmp_path)
    assert len(after) == 1
    assert after[0].name == "Updated"
    assert after[0].status is ParseStatus.LLM_ENCODED


def test_update_inserts_when_missing(tmp_path: Path) -> None:
    cards = [_make_card(name="Existing", collector_number="1", oracle_id="o1")]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    new = _make_card(name="Brand New", collector_number="2", oracle_id="o2")
    update_parsed_card("TST", new, data_root=tmp_path)
    after = load_parsed_cards("TST", data_root=tmp_path)
    assert len(after) == 2
    assert {c.name for c in after} == {"Existing", "Brand New"}


def test_cards_by_status_filters(tmp_path: Path) -> None:
    cards = [
        _make_card(name="A", collector_number="1", oracle_id="o1", status=ParseStatus.AUTO),
        _make_card(name="B", collector_number="2", oracle_id="o2", status=ParseStatus.NEEDS_LLM),
        _make_card(name="C", collector_number="3", oracle_id="o3", status=ParseStatus.LLM_ENCODED),
        _make_card(name="D", collector_number="4", oracle_id="o4", status=ParseStatus.NEEDS_HUMAN),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    auto = cards_by_status("TST", ParseStatus.AUTO, data_root=tmp_path)
    needs_llm = cards_by_status("TST", ParseStatus.NEEDS_LLM, data_root=tmp_path)
    llm_enc = cards_by_status("TST", ParseStatus.LLM_ENCODED, data_root=tmp_path)
    needs_human = cards_by_status("TST", ParseStatus.NEEDS_HUMAN, data_root=tmp_path)
    assert [c.name for c in auto] == ["A"]
    assert [c.name for c in needs_llm] == ["B"]
    assert [c.name for c in llm_enc] == ["C"]
    assert [c.name for c in needs_human] == ["D"]


def test_status_histogram_counts(tmp_path: Path) -> None:
    cards = [
        _make_card(name="A", collector_number="1", oracle_id="o1", status=ParseStatus.AUTO),
        _make_card(name="B", collector_number="2", oracle_id="o2", status=ParseStatus.AUTO),
        _make_card(name="C", collector_number="3", oracle_id="o3", status=ParseStatus.NEEDS_LLM),
        _make_card(name="D", collector_number="4", oracle_id="o4", status=ParseStatus.LLM_ENCODED),
    ]
    save_parsed_cards("TST", cards, data_root=tmp_path)
    hist = status_histogram("TST", data_root=tmp_path)
    assert hist[ParseStatus.AUTO] == 2
    assert hist[ParseStatus.NEEDS_LLM] == 1
    assert hist[ParseStatus.LLM_ENCODED] == 1
    assert hist[ParseStatus.NEEDS_HUMAN] == 0


def test_merge_preserves_llm_encoded_and_needs_human(tmp_path: Path) -> None:
    # Existing file has hand-encoded entries.
    existing = [
        _make_card(
            name="Hand-encoded",
            collector_number="1",
            oracle_id="o1",
            status=ParseStatus.LLM_ENCODED,
        ),
        _make_card(
            name="Needs human", collector_number="2", oracle_id="o2", status=ParseStatus.NEEDS_HUMAN
        ),
        _make_card(name="Auto-old", collector_number="3", oracle_id="o3", status=ParseStatus.AUTO),
        _make_card(
            name="Stale", collector_number="4", oracle_id="o4", status=ParseStatus.NEEDS_LLM
        ),
    ]
    save_parsed_cards("TST", existing, data_root=tmp_path)

    # Detector reruns on all four cards. The "fresh" parses should NOT
    # clobber the LLM-encoded / needs-human ones.
    fresh = [
        _make_card(name="Fresh-1", collector_number="1", oracle_id="o1", status=ParseStatus.AUTO),
        _make_card(name="Fresh-2", collector_number="2", oracle_id="o2", status=ParseStatus.AUTO),
        _make_card(name="Fresh-3", collector_number="3", oracle_id="o3", status=ParseStatus.AUTO),
        _make_card(name="Fresh-4", collector_number="4", oracle_id="o4", status=ParseStatus.AUTO),
    ]
    merged, n_preserved, n_rewritten = merge_detector_run("TST", fresh, data_root=tmp_path)
    assert n_preserved == 2
    assert n_rewritten == 2
    by_oid = {c.oracle_id: c for c in merged}
    assert by_oid["o1"].name == "Hand-encoded"
    assert by_oid["o1"].status is ParseStatus.LLM_ENCODED
    assert by_oid["o2"].name == "Needs human"
    assert by_oid["o2"].status is ParseStatus.NEEDS_HUMAN
    assert by_oid["o3"].name == "Fresh-3"  # AUTO is overwritable
    assert by_oid["o4"].name == "Fresh-4"  # NEEDS_LLM is overwritable


def test_merge_force_overwrites_everything(tmp_path: Path) -> None:
    existing = [
        _make_card(
            name="Hand-encoded",
            collector_number="1",
            oracle_id="o1",
            status=ParseStatus.LLM_ENCODED,
        ),
    ]
    save_parsed_cards("TST", existing, data_root=tmp_path)
    fresh = [
        _make_card(
            name="Fresh",
            collector_number="1",
            oracle_id="o1",
            status=ParseStatus.AUTO,
        ),
    ]
    merged, n_preserved, n_rewritten = merge_detector_run(
        "TST", fresh, data_root=tmp_path, force=True
    )
    assert n_preserved == 0
    assert n_rewritten == 1
    assert merged[0].name == "Fresh"


def _make_castable(
    *,
    name: str,
    collector_number: str,
    oracle_id: str,
    cost: str,
    modes: list[Mode] | None = None,
    status: ParseStatus = ParseStatus.LLM_ENCODED,
) -> ParsedCard:
    """A non-creature castable card with the given cost.

    Used by the default-cast-mode invariant tests below.
    """
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=collector_number,
        oracle_id=oracle_id,
        rarity="rare",
        raw_oracle_text="Test card.",
        type_line="Sorcery",
        types=["Sorcery"],
        subtypes=[],
        supertypes=[],
        colors=[],
        mana_cost=parse_mana_cost(cost),
        modes=modes or [],
        mana_abilities=[],
        enter_condition=None,
        role_features=RoleFeatures(),
        status=status,
        reasons=[],
    )


def test_save_adds_default_cast_mode_for_castable_card_with_no_modes(tmp_path: Path) -> None:
    """A castable card with empty modes gets a stub cast mode on save.

    Mirrors the LLM-encoded entries that initially shipped with empty
    modes because their alt-cost mechanics (kicker / flashback) were
    too complex to fit the Mode vocabulary. The simulator's
    check_deck_encodings rejects such entries; the stub fixes that.
    """
    card = _make_castable(name="Empty Modes", collector_number="1", oracle_id="o1", cost="{2}{R}")
    save_parsed_cards("TST", [card], data_root=tmp_path)
    [reloaded] = load_parsed_cards("TST", data_root=tmp_path)

    assert len(reloaded.modes) == 1
    only = reloaded.modes[0]
    assert only.kind == "cast"
    assert only.cost.mana.raw == "{2}{R}"
    # The stub effect carries the role_tag marker so this synthesised
    # mode is greppable in dumps.
    assert len(only.effects) == 1
    effect = only.effects[0]
    assert isinstance(effect, NoopEffect)
    assert effect.role_tag == DEFAULT_CAST_ROLE_TAG


def test_save_leaves_existing_modes_alone(tmp_path: Path) -> None:
    """If the card already has modes, save doesn't touch them."""
    real_mode = Mode(
        kind="cast",
        cost=Cost(mana=parse_mana_cost("{1}{G}")),
        effects=[NoopEffect(role_tag="real_effect")],
    )
    card = _make_castable(
        name="Already Encoded",
        collector_number="1",
        oracle_id="o1",
        cost="{1}{G}",
        modes=[real_mode],
    )
    save_parsed_cards("TST", [card], data_root=tmp_path)
    [reloaded] = load_parsed_cards("TST", data_root=tmp_path)

    assert len(reloaded.modes) == 1
    [effect] = reloaded.modes[0].effects
    assert isinstance(effect, NoopEffect)
    assert effect.role_tag == "real_effect"


def test_save_leaves_noncastable_card_alone(tmp_path: Path) -> None:
    """Lands (mana_cost=None) don't trigger the helper."""
    land = _make_card(name="Plains", collector_number="1", oracle_id="o1")
    # _make_card builds a creature; lands have mana_cost=None and types=Land.
    # Use model_copy to flip it into a land for this test.
    land = land.model_copy(
        update={
            "type_line": "Basic Land — Plains",
            "types": ["Land"],
            "subtypes": ["Plains"],
            "supertypes": ["Basic"],
            "mana_cost": None,
            "modes": [],
        }
    )
    save_parsed_cards("TST", [land], data_root=tmp_path)
    [reloaded] = load_parsed_cards("TST", data_root=tmp_path)
    assert reloaded.modes == []
    assert reloaded.mana_cost is None


def test_default_cast_mode_round_trip_is_idempotent(tmp_path: Path) -> None:
    """Saving twice doesn't duplicate the stub mode."""
    card = _make_castable(name="Twice-saved", collector_number="1", oracle_id="o1", cost="{R}")
    save_parsed_cards("TST", [card], data_root=tmp_path)
    once = load_parsed_cards("TST", data_root=tmp_path)
    save_parsed_cards("TST", once, data_root=tmp_path)
    twice = load_parsed_cards("TST", data_root=tmp_path)

    assert len(twice[0].modes) == 1


def test_merge_drops_orphans_no_longer_in_fresh(tmp_path: Path) -> None:
    # The Scryfall snapshot may shed cards (set rotated / errata); orphans
    # in the parsed-cards file are dropped on the next merge.
    existing = [
        _make_card(
            name="Old", collector_number="1", oracle_id="oracle-old", status=ParseStatus.AUTO
        ),
    ]
    save_parsed_cards("TST", existing, data_root=tmp_path)
    fresh = [
        _make_card(
            name="New", collector_number="2", oracle_id="oracle-new", status=ParseStatus.AUTO
        ),
    ]
    merged, _, _ = merge_detector_run("TST", fresh, data_root=tmp_path)
    assert [c.oracle_id for c in merged] == ["oracle-new"]
