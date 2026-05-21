"""Tests for :mod:`mulligan_coach_overlay.position_persistence`.

The persistence file has two independent slots (compact + expanded);
each test exercises one shape of input/output and confirms the
fall-back to ``None`` on missing / corrupt data.
"""

from __future__ import annotations

import json
from pathlib import Path

from mulligan_coach_overlay.position_persistence import (
    Positions,
    default_positions_path,
    load_positions,
    save_positions,
)


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    """Both slots round-trip through save → load."""
    path = tmp_path / "positions.json"
    save_positions(path, compact=(1900, 1050), expanded=(120, 80))
    loaded = load_positions(path)
    assert loaded.compact == (1900, 1050)
    assert loaded.expanded == (120, 80)


def test_partial_round_trip(tmp_path: Path) -> None:
    """Only one slot set — the other arrives back as ``None``."""
    path = tmp_path / "positions.json"
    save_positions(path, compact=None, expanded=(200, 300))
    loaded = load_positions(path)
    assert loaded.compact is None
    assert loaded.expanded == (200, 300)


def test_save_both_none_is_noop(tmp_path: Path) -> None:
    """Saving ``compact=None, expanded=None`` does not create a file."""
    path = tmp_path / "positions.json"
    save_positions(path, compact=None, expanded=None)
    assert not path.exists()


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    """Save creates missing parent dirs rather than raising."""
    path = tmp_path / "deep" / "nested" / "positions.json"
    save_positions(path, compact=(0, 0), expanded=(10, 10))
    assert path.is_file()
    assert load_positions(path) == Positions(compact=(0, 0), expanded=(10, 10))


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    """Absent file decodes to an empty Positions, not an error."""
    loaded = load_positions(tmp_path / "does_not_exist.json")
    assert loaded == Positions()


def test_load_corrupt_json_returns_empty(tmp_path: Path) -> None:
    """Malformed JSON is logged-and-ignored."""
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_positions(path) == Positions()


def test_load_wrong_top_level_shape_returns_empty(tmp_path: Path) -> None:
    """Top-level list (instead of dict) is treated as unusable."""
    path = tmp_path / "wrong_shape.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_positions(path) == Positions()


def test_load_wrong_schema_version_returns_empty(tmp_path: Path) -> None:
    """Future schema versions are skipped, not mis-parsed."""
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps(
            {
                "version": 999,
                "saved_at": 0.0,
                "compact": [1, 2],
                "expanded": [3, 4],
            }
        ),
        encoding="utf-8",
    )
    assert load_positions(path) == Positions()


def test_load_malformed_pair_for_one_slot(tmp_path: Path) -> None:
    """A bad ``compact`` entry doesn't suppress a valid ``expanded`` entry."""
    path = tmp_path / "halfgood.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": 0.0,
                "compact": "garbage",
                "expanded": [50, 60],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_positions(path)
    assert loaded.compact is None
    assert loaded.expanded == (50, 60)


def test_load_rejects_bool_pair(tmp_path: Path) -> None:
    """``[true, false]`` must not round-trip as ``(1, 0)``."""
    path = tmp_path / "bool.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": 0.0,
                "compact": [True, False],
                "expanded": None,
            }
        ),
        encoding="utf-8",
    )
    assert load_positions(path) == Positions()


def test_load_rejects_wrong_arity(tmp_path: Path) -> None:
    """A three-element list isn't a valid ``(x, y)`` pair."""
    path = tmp_path / "arity.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": 0.0,
                "compact": [1, 2, 3],
                "expanded": [4, 5],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_positions(path)
    assert loaded.compact is None
    assert loaded.expanded == (4, 5)


def test_save_overwrites_existing(tmp_path: Path) -> None:
    """Each save replaces the previous payload atomically."""
    path = tmp_path / "positions.json"
    save_positions(path, compact=(1, 2), expanded=(3, 4))
    save_positions(path, compact=(9, 8), expanded=(7, 6))
    assert load_positions(path) == Positions(compact=(9, 8), expanded=(7, 6))


def test_save_leaves_schema_version_in_payload(tmp_path: Path) -> None:
    """Saved files include a schema version so we can migrate later."""
    path = tmp_path / "positions.json"
    save_positions(path, compact=(0, 0), expanded=(0, 0))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["compact"] == [0, 0]
    assert payload["expanded"] == [0, 0]
    assert "saved_at" in payload


def test_default_positions_path_lives_under_user_home() -> None:
    """The default path is absolute and ends with ``positions.json``."""
    path = default_positions_path()
    assert path.is_absolute()
    assert path.name == "positions.json"
    assert "MulliganCoach" in path.parts or "mulligan-coach" in path.parts
