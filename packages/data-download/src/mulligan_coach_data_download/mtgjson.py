"""MTGJSON download.

We download ``AllIdentifiers.json`` rather than ``AllPrintings.json`` because
all we need is the cross-source ID mapping (Arena ID ↔ Scryfall ID ↔
MTGJSON UUID). AllIdentifiers is significantly smaller than AllPrintings.

Scryfall actually exposes ``arena_id`` directly on most cards too, so this
serves as a fallback / sanity check rather than a primary lookup table.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from . import paths
from .config import MTGJSON_ALL_IDENTIFIERS_URL
from .http import download_to
from .manifest import Manifest, SourceEntry

log = logging.getLogger(__name__)


def refresh_all_identifiers(
    *,
    client: httpx.Client,
    manifest: Manifest,
    root: Path | None = None,
    show_progress: bool = True,
) -> SourceEntry:
    """Fetch the latest AllIdentifiers.json to data/raw/mtgjson/."""
    dest = paths.mtgjson_raw_dir(root) / "AllIdentifiers.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    url = MTGJSON_ALL_IDENTIFIERS_URL
    previous = manifest.get(url)
    result = download_to(
        url,
        dest,
        client=client,
        previous=previous,
        show_progress=show_progress,
    )

    # row_count for this file isn't meaningful in the same way as the 17Lands
    # ones (it's a single nested document, not an array). We leave it None.
    return manifest.upsert(result.entry)
