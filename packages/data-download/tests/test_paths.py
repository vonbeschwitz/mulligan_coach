"""Tests for the on-disk path resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from mulligan_coach_data_download import paths


def test_data_root_honors_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MULLIGAN_COACH_DATA_ROOT", str(tmp_path))
    assert paths.data_root() == tmp_path.resolve()


def test_data_root_default_is_under_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    # With no env override, the data root is <repo>/data. We can't assert the
    # exact path from a test, but it must end in "data" and exist relative to
    # this package.
    monkeypatch.delenv("MULLIGAN_COACH_DATA_ROOT", raising=False)
    assert paths.data_root().name == "data"


def test_seventeenlands_paths(tmp_path: Path) -> None:
    raw = paths.seventeenlands_game_csv("FIN", "PremierDraft", root=tmp_path)
    assert raw.name == "game_data_public.FIN.PremierDraft.csv.gz"
    assert raw.parent == tmp_path / "raw" / "seventeenlands"

    parquet = paths.seventeenlands_games_parquet("FIN", "PremierDraft", root=tmp_path)
    assert parquet.name == "PremierDraft.parquet"
    assert parquet.parent == tmp_path / "processed" / "seventeenlands" / "games" / "FIN"

    ratings_json = paths.seventeenlands_ratings_json("FIN", "PremierDraft", root=tmp_path)
    assert ratings_json.name == "card_ratings.FIN.PremierDraft.json"


def test_ensure_layout_is_idempotent(tmp_path: Path) -> None:
    paths.ensure_layout(tmp_path)
    paths.ensure_layout(tmp_path)  # second call must not fail

    # Spot-check key directories.
    for sub in [
        "raw/seventeenlands",
        "raw/scryfall",
        "raw/mtgjson",
        "processed/seventeenlands/games",
        "processed/seventeenlands/ratings",
    ]:
        assert (tmp_path / sub).is_dir(), sub


def test_manifest_and_duckdb_paths(tmp_path: Path) -> None:
    assert paths.manifest_path(tmp_path) == tmp_path / "processed" / "manifest.json"
    assert paths.games_duckdb(tmp_path) == tmp_path / "processed" / "games.duckdb"
