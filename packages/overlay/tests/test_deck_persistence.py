"""Tests for the deck persistence layer.

These cover the surface the GUI relies on: a round-trip preserves
the arena_ids, missing / corrupt / wrong-schema files yield ``None``
rather than crashing, and the saved file lands at the expected
location with parent directories created on demand.

We deliberately don't exercise :func:`default_persistence_path`
against the real OS env — that would couple tests to the host
machine. Instead we always pass an explicit path under
``tmp_path``.
"""

from __future__ import annotations

import json
from pathlib import Path

from mulligan_coach_overlay.deck_persistence import (
    default_persistence_path,
    load_last_deck,
    save_last_deck,
)


def test_save_then_load_round_trip(tmp_path: Path) -> None:
    """Save followed by load returns the original arena_ids list."""
    path = tmp_path / "last_deck.json"
    arena_ids = [12345, 12345, 67890, 13579, 24680]
    save_last_deck(path, arena_ids)
    loaded = load_last_deck(path)
    assert loaded == arena_ids


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    """Save populates missing parent directories rather than failing."""
    path = tmp_path / "nested" / "dirs" / "last_deck.json"
    arena_ids = [111, 222]
    save_last_deck(path, arena_ids)
    assert path.is_file()
    loaded = load_last_deck(path)
    assert loaded == arena_ids


def test_save_empty_list_is_noop(tmp_path: Path) -> None:
    """Saving with an empty list does not overwrite (or create) a file."""
    path = tmp_path / "last_deck.json"
    save_last_deck(path, [1, 2, 3])
    save_last_deck(path, [])
    loaded = load_last_deck(path)
    # The first save still wins — the empty save was a no-op.
    assert loaded == [1, 2, 3]


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    """A path that doesn't exist resolves to ``None``, not a crash."""
    assert load_last_deck(tmp_path / "does_not_exist.json") is None


def test_load_corrupt_json_returns_none(tmp_path: Path) -> None:
    """Malformed JSON is logged-and-ignored, not raised."""
    path = tmp_path / "broken.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert load_last_deck(path) is None


def test_load_wrong_top_level_shape_returns_none(tmp_path: Path) -> None:
    """A JSON list at the top (instead of a dict) is treated as unusable."""
    path = tmp_path / "wrong_shape.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_last_deck(path) is None


def test_load_wrong_schema_version_returns_none(tmp_path: Path) -> None:
    """Future schema versions are skipped rather than mis-parsed."""
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps({"version": 999, "arena_ids": [1, 2, 3], "saved_at": 0.0}),
        encoding="utf-8",
    )
    assert load_last_deck(path) is None


def test_load_missing_arena_ids_returns_none(tmp_path: Path) -> None:
    """A payload without ``arena_ids`` (or with an empty list) is rejected."""
    path = tmp_path / "noids.json"
    path.write_text(json.dumps({"version": 1, "saved_at": 0.0}), encoding="utf-8")
    assert load_last_deck(path) is None
    path.write_text(
        json.dumps({"version": 1, "saved_at": 0.0, "arena_ids": []}),
        encoding="utf-8",
    )
    assert load_last_deck(path) is None


def test_load_non_int_arena_ids_returns_none(tmp_path: Path) -> None:
    """Mixed types in the arena_ids list disqualify the file."""
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps({"version": 1, "saved_at": 0.0, "arena_ids": [1, "two", 3]}),
        encoding="utf-8",
    )
    assert load_last_deck(path) is None


def test_save_overwrites_existing(tmp_path: Path) -> None:
    """Each save replaces the previous payload atomically."""
    path = tmp_path / "last_deck.json"
    save_last_deck(path, [1, 2, 3])
    save_last_deck(path, [9, 8, 7])
    assert load_last_deck(path) == [9, 8, 7]


def test_save_leaves_schema_version_in_payload(tmp_path: Path) -> None:
    """Saved files include a schema version so we can migrate later."""
    path = tmp_path / "last_deck.json"
    save_last_deck(path, [42])
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["arena_ids"] == [42]
    assert "saved_at" in payload


def test_default_persistence_path_lives_under_user_home() -> None:
    """The default path resolves to *somewhere*; the exact subtree
    differs by platform but the result should be absolute and end
    with ``last_deck.json``."""
    path = default_persistence_path()
    assert path.is_absolute()
    assert path.name == "last_deck.json"
    assert "MulliganCoach" in path.parts or "mulligan-coach" in path.parts
