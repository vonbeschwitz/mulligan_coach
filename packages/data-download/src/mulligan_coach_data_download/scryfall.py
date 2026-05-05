"""Scryfall bulk-data download.

Scryfall publishes large JSON dumps at
``https://api.scryfall.com/bulk-data``. The endpoint returns metadata —
including the actual ``download_uri`` for each bulk type — and the URL of
the data file itself rotates daily. So we always hit the metadata endpoint
first to discover the current ``download_uri`` for the type we want.

We only download ``oracle_cards`` (one entry per unique English card with
oracle text and the canonical printing's stats). It's enough for the cards
package and is much smaller than ``default_cards`` or ``all_cards``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from . import paths
from .config import SCRYFALL_BULK_DATA_URL
from .http import download_to
from .manifest import Manifest, SourceEntry

log = logging.getLogger(__name__)

ORACLE_CARDS_TYPE = "oracle_cards"


def _resolve_oracle_cards_url(client: httpx.Client) -> tuple[str, str]:
    """Hit the bulk-data index, return (download_uri, updated_at) for the
    oracle-cards entry.

    Raises ``RuntimeError`` if the index doesn't include the type we want
    (which would be a Scryfall API change worth surfacing loudly).
    """
    response = client.get(SCRYFALL_BULK_DATA_URL)
    response.raise_for_status()
    payload = response.json()
    entries = payload.get("data", [])
    for entry in entries:
        if entry.get("type") == ORACLE_CARDS_TYPE:
            uri = entry.get("download_uri")
            updated = entry.get("updated_at", "")
            if not uri:
                raise RuntimeError("Scryfall oracle_cards entry has no download_uri")
            return uri, updated
    raise RuntimeError(
        f"Scryfall bulk-data index did not include type {ORACLE_CARDS_TYPE!r}; "
        f"saw types: {[e.get('type') for e in entries]!r}"
    )


def refresh_oracle_cards(
    *,
    client: httpx.Client,
    manifest: Manifest,
    root: Path | None = None,
    show_progress: bool = True,
) -> SourceEntry:
    """Fetch the latest Scryfall oracle-cards JSON to data/raw/scryfall/.

    We name the file with the Scryfall ``updated_at`` date so successive
    snapshots can be retained side-by-side if the user keeps the old ones.
    """
    download_uri, updated_at = _resolve_oracle_cards_url(client)
    # Take just the date component for the filename — keeps things tidy.
    date_part = (updated_at or "unknown").split("T", 1)[0] or "unknown"
    dest = paths.scryfall_raw_dir(root) / f"oracle_cards.{date_part}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    previous = manifest.get(download_uri)
    result = download_to(
        download_uri,
        dest,
        client=client,
        previous=previous,
        show_progress=show_progress,
    )

    # Sanity-check the JSON is parseable and is a list — Scryfall could
    # change formats and we'd rather know now than at cards-package read time.
    if not result.not_modified:
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Scryfall download at {dest} is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise RuntimeError(
                f"Scryfall oracle_cards JSON is not an array (got {type(data).__name__})"
            )
        row_count = len(data)
    else:
        row_count = previous.row_count if previous and previous.row_count else 0

    entry = result.entry.model_copy(update={"row_count": row_count})
    return manifest.upsert(entry)
