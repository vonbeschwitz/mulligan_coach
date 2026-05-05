"""Shared pytest fixtures for the data-download package.

These fixtures keep tests **hermetic**: every test gets its own temp data
directory, and HTTP traffic is mocked via ``pytest-httpx`` so we never hit the
real 17Lands / Scryfall / MTGJSON endpoints.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def data_root(tmp_path: Path) -> Iterator[Path]:
    """A throwaway data root with the standard layout pre-created.

    Mirrors the layout described in the package README so each test starts
    from a known-good empty state.
    """
    (tmp_path / "raw" / "seventeenlands").mkdir(parents=True)
    (tmp_path / "raw" / "scryfall").mkdir(parents=True)
    (tmp_path / "raw" / "mtgjson").mkdir(parents=True)
    (tmp_path / "processed" / "seventeenlands" / "games").mkdir(parents=True)
    (tmp_path / "processed" / "seventeenlands" / "ratings").mkdir(parents=True)
    yield tmp_path
