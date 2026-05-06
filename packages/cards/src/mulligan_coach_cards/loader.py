"""Scryfall oracle-cards loader.

Reads the JSON snapshots that the ``data-download`` package writes to
``<data_root>/raw/scryfall/oracle_cards.<date>.json``. We always pick the
newest snapshot (highest filename suffix); old ones are kept on disk only
for the user's convenience.

The file is large (~170 MiB / ~37k cards) but loads fast because it's a
single JSON array. Loading once per CLI invocation is fine — we don't try
to stream or memoize at this stage.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Layouts the deterministic parser is willing to *consider*. Anything else
# (token, art_series, double_faced_token, …) gets filtered out before we
# even hand cards to the parser, so the demo report isn't padded with rows
# that aren't real game pieces.
RELEVANT_LAYOUTS: frozenset[str] = frozenset(
    {
        "normal",
        "split",
        "transform",
        "modal_dfc",
        "adventure",
        "saga",
        "leveler",
        "case",
        "class",
        "flip",
        "meld",
        "prototype",
    }
)


def _data_root() -> Path:
    """Resolve the project's ``data/`` directory.

    Mirrors the data-download package's ``paths.data_root`` without taking a
    runtime dependency on it (per the project layout guide, cross-package
    imports are minimised — both packages just agree on the on-disk layout).
    Honors ``MULLIGAN_COACH_DATA_ROOT`` for tests and ad-hoc overrides.
    """
    override = os.environ.get("MULLIGAN_COACH_DATA_ROOT")
    if override:
        return Path(override).resolve()
    # packages/cards/src/mulligan_coach_cards/loader.py
    #   -> mulligan_coach_cards -> src -> cards -> packages -> <repo>
    return Path(__file__).resolve().parents[4] / "data"


def latest_oracle_cards_path(data_root: Path | None = None) -> Path:
    """Return the path to the most recent Scryfall oracle-cards snapshot."""
    raw_dir = (data_root or _data_root()) / "raw" / "scryfall"
    if not raw_dir.exists():
        raise FileNotFoundError(
            f"Scryfall raw dir not found at {raw_dir}. Run 'mulligan-coach-data refresh-scryfall' first."
        )
    candidates = sorted(raw_dir.glob("oracle_cards.*.json"))
    if not candidates:
        raise FileNotFoundError(
            f"No oracle_cards JSON in {raw_dir}. Run 'mulligan-coach-data refresh-scryfall' first."
        )
    # Filenames embed an ISO-formatted date so lexical max == newest.
    return candidates[-1]


def load_all_cards(data_root: Path | None = None) -> list[dict[str, Any]]:
    """Load every card from the latest Scryfall snapshot."""
    path = latest_oracle_cards_path(data_root)
    with path.open(encoding="utf-8") as f:
        data: Any = json.load(f)
    if not isinstance(data, list):
        raise RuntimeError(f"Expected list at top of {path}, got {type(data).__name__}")
    return data


def filter_cards(
    cards: Iterable[dict[str, Any]],
    *,
    set_code: str | None = None,
    lang: str = "en",
) -> list[dict[str, Any]]:
    """Filter a card list by set code and language.

    Set code matching is case-insensitive. Layout filtering keeps only
    "real" gameplay layouts (see ``RELEVANT_LAYOUTS``).
    """
    code = set_code.upper() if set_code else None
    out: list[dict[str, Any]] = []
    for card in cards:
        if card.get("lang", "en") != lang:
            continue
        if str(card.get("layout", "normal")) not in RELEVANT_LAYOUTS:
            continue
        if code is not None and str(card.get("set", "")).upper() != code:
            continue
        out.append(card)
    return out
