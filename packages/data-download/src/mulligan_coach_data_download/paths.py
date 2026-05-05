"""Centralized resolver for the project's on-disk data layout.

All file-path logic for ``data/raw/`` and ``data/processed/`` lives here so
that the rest of the package never builds paths by hand. If we ever decide
to move the data root (e.g. to a configurable location), this is the only
file that needs to change.

Layout (mirrors the package README):

    <root>/
    ├── raw/
    │   ├── seventeenlands/
    │   ├── scryfall/
    │   └── mtgjson/
    └── processed/
        ├── seventeenlands/
        │   ├── games/<SET>/<EVENT_TYPE>.parquet
        │   └── ratings/<SET>/<EVENT_TYPE>.parquet
        ├── games.duckdb
        └── manifest.json
"""

from __future__ import annotations

import os
from pathlib import Path

# Environment variable that, when set, overrides the default data root. Tests
# set this to a tmp directory; production runs leave it unset and use the
# repo-relative ``data/`` directory.
_DATA_ROOT_ENV = "MULLIGAN_COACH_DATA_ROOT"


def _repo_root() -> Path:
    """Walk up from this file to find the repo root (directory containing the
    workspace ``pyproject.toml`` with ``[tool.uv.workspace]``).

    Falling back to four parents up keeps things working even if we are
    installed in a weird location — the directory layout is fixed by the
    workspace.
    """
    here = Path(__file__).resolve()
    # src/mulligan_coach_data_download/paths.py
    #  -> src/
    #   -> data-download/
    #    -> packages/
    #     -> <repo root>/
    return here.parents[4]


def data_root() -> Path:
    """Return the project's data root directory.

    Honors the ``MULLIGAN_COACH_DATA_ROOT`` environment variable for tests
    and ad-hoc overrides; otherwise defaults to ``<repo>/data``.
    """
    override = os.environ.get(_DATA_ROOT_ENV)
    if override:
        return Path(override).resolve()
    return _repo_root() / "data"


# ---------------------------------------------------------------------------
# Raw data paths.
# ---------------------------------------------------------------------------


def raw_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "raw"


def seventeenlands_raw_dir(root: Path | None = None) -> Path:
    return raw_dir(root) / "seventeenlands"


def scryfall_raw_dir(root: Path | None = None) -> Path:
    return raw_dir(root) / "scryfall"


def mtgjson_raw_dir(root: Path | None = None) -> Path:
    return raw_dir(root) / "mtgjson"


def seventeenlands_game_csv(set_code: str, event_type: str, root: Path | None = None) -> Path:
    """Path to the raw gz CSV for a (set, event_type)."""
    return seventeenlands_raw_dir(root) / f"game_data_public.{set_code}.{event_type}.csv.gz"


def seventeenlands_ratings_json(set_code: str, event_type: str, root: Path | None = None) -> Path:
    return seventeenlands_raw_dir(root) / f"card_ratings.{set_code}.{event_type}.json"


# ---------------------------------------------------------------------------
# Processed data paths.
# ---------------------------------------------------------------------------


def processed_dir(root: Path | None = None) -> Path:
    return (root or data_root()) / "processed"


def seventeenlands_processed_dir(root: Path | None = None) -> Path:
    return processed_dir(root) / "seventeenlands"


def seventeenlands_games_parquet(set_code: str, event_type: str, root: Path | None = None) -> Path:
    return seventeenlands_processed_dir(root) / "games" / set_code / f"{event_type}.parquet"


def seventeenlands_ratings_parquet(
    set_code: str, event_type: str, root: Path | None = None
) -> Path:
    return seventeenlands_processed_dir(root) / "ratings" / set_code / f"{event_type}.parquet"


def games_duckdb(root: Path | None = None) -> Path:
    return processed_dir(root) / "games.duckdb"


def manifest_path(root: Path | None = None) -> Path:
    return processed_dir(root) / "manifest.json"


def ensure_layout(root: Path | None = None) -> Path:
    """Create the standard directory tree under ``root`` if it doesn't exist.

    Returns the resolved root path.
    """
    base = root or data_root()
    for d in (
        seventeenlands_raw_dir(base),
        scryfall_raw_dir(base),
        mtgjson_raw_dir(base),
        seventeenlands_processed_dir(base) / "games",
        seventeenlands_processed_dir(base) / "ratings",
    ):
        d.mkdir(parents=True, exist_ok=True)
    return base
