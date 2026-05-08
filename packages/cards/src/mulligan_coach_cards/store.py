"""Persistent store for ``ParsedCard`` records.

Each set's parsed cards live in a single JSON file at
``<data_root>/processed/parsed_cards/<SET>.json``. The file is a JSON
list of ``ParsedCard.model_dump(mode="json")`` entries, ordered by
collector number.

The store is the source of truth for cards whose review-status is
``LLM_ENCODED`` or ``NEEDS_HUMAN`` — re-running the deterministic parser
preserves those entries verbatim. Cards whose status is ``AUTO`` or
``NEEDS_LLM`` (or that aren't in the file at all) get rewritten on each
detector run.

We deliberately keep the on-disk format human-readable (one big JSON
file per set, indented). It's easy to diff in version control if the
file ever ends up tracked, easy to inspect by eye, and small enough to
re-load on every command (~hundreds of cards per set, not millions).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ParsedCard, ParseStatus


def _data_root() -> Path:
    """Resolve the project's ``data/`` directory.

    Mirrors ``loader._data_root()`` to keep the two readers in lock-step
    without taking a runtime dependency.
    """
    override = os.environ.get("MULLIGAN_COACH_DATA_ROOT")
    if override:
        return Path(override).resolve()
    # packages/cards/src/mulligan_coach_cards/store.py
    #   -> mulligan_coach_cards -> src -> cards -> packages -> <repo>
    return Path(__file__).resolve().parents[4] / "data"


def parsed_cards_dir(data_root: Path | None = None) -> Path:
    """Directory holding per-set parsed card JSON files."""
    return (data_root or _data_root()) / "processed" / "parsed_cards"


def parsed_cards_path(set_code: str, data_root: Path | None = None) -> Path:
    """Per-set JSON path. Set code is upper-cased for filename consistency."""
    return parsed_cards_dir(data_root) / f"{set_code.upper()}.json"


def _collector_sort_key(card: ParsedCard) -> tuple[int, str]:
    """Sort key that handles "12a" / "★" suffixes by pushing them to the end."""
    digits = ""
    for ch in card.collector_number:
        if ch.isdigit():
            digits += ch
        else:
            break
    return (int(digits) if digits else 10**9, card.collector_number)


def load_parsed_cards(set_code: str, data_root: Path | None = None) -> list[ParsedCard]:
    """Load the per-set parsed card list. Returns ``[]`` if the file is missing."""
    path = parsed_cards_path(set_code, data_root)
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise RuntimeError(f"Expected list at top of {path}, got {type(raw).__name__}")
    return [ParsedCard.model_validate(entry) for entry in raw]


def save_parsed_cards(
    set_code: str,
    cards: list[ParsedCard],
    data_root: Path | None = None,
) -> Path:
    """Write the per-set parsed card list to disk. Creates the directory if missing.

    Cards are sorted by collector number for stable output. Returns the
    path written.
    """
    path = parsed_cards_path(set_code, data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    sorted_cards = sorted(cards, key=_collector_sort_key)
    payload = [c.model_dump(mode="json") for c in sorted_cards]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def update_parsed_card(
    set_code: str,
    card: ParsedCard,
    data_root: Path | None = None,
) -> Path:
    """Replace one entry by oracle_id (insert if absent), then save."""
    existing = load_parsed_cards(set_code, data_root)
    existing = [c for c in existing if c.oracle_id != card.oracle_id]
    existing.append(card)
    return save_parsed_cards(set_code, existing, data_root)


def cards_by_status(
    set_code: str,
    status: ParseStatus,
    data_root: Path | None = None,
) -> list[ParsedCard]:
    """Return all cards in the per-set file with the given status."""
    return [c for c in load_parsed_cards(set_code, data_root) if c.status == status]


def status_histogram(set_code: str, data_root: Path | None = None) -> dict[ParseStatus, int]:
    """Count cards by status in the per-set file."""
    counts: dict[ParseStatus, int] = {s: 0 for s in ParseStatus}
    for card in load_parsed_cards(set_code, data_root):
        counts[card.status] = counts.get(card.status, 0) + 1
    return counts


def merge_detector_run(
    set_code: str,
    freshly_parsed: list[ParsedCard],
    data_root: Path | None = None,
    force: bool = False,
) -> tuple[list[ParsedCard], int, int]:
    """Merge a fresh deterministic-parser pass with existing stored cards.

    Cards in the existing file with status ``LLM_ENCODED`` or
    ``NEEDS_HUMAN`` are preserved verbatim (their hand-encoded fields
    survive). All other cards are replaced with the freshly-parsed
    version. Cards not in the existing file are added.

    With ``force=True``, even ``LLM_ENCODED`` / ``NEEDS_HUMAN`` entries
    are overwritten — useful if the LLM encoding is known to be stale.

    Returns ``(merged_list, n_preserved, n_rewritten)``.
    """
    existing = load_parsed_cards(set_code, data_root)
    by_oracle: dict[str, ParsedCard] = {c.oracle_id: c for c in existing}

    preserved_statuses = {ParseStatus.LLM_ENCODED, ParseStatus.NEEDS_HUMAN}
    n_preserved = 0
    n_rewritten = 0
    merged: list[ParsedCard] = []
    seen_oracle_ids: set[str] = set()
    for fresh in freshly_parsed:
        seen_oracle_ids.add(fresh.oracle_id)
        prior = by_oracle.get(fresh.oracle_id)
        if prior is not None and prior.status in preserved_statuses and not force:
            merged.append(prior)
            n_preserved += 1
        else:
            merged.append(fresh)
            n_rewritten += 1
    # Anything in `by_oracle` not in `freshly_parsed` is dropped (no longer
    # in Scryfall data — likely a snapshot delta). We deliberately don't
    # carry orphans forward; if the card no longer exists in our raw data,
    # it shouldn't sit in our processed store either.
    return merged, n_preserved, n_rewritten
