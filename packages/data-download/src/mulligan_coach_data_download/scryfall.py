"""Scryfall bulk-data download.

Scryfall publishes large card dumps at ``https://api.scryfall.com/bulk-data``.
The endpoint returns metadata — including the actual download URL for each
bulk type — and the URL of the data file itself rotates daily. So we always
hit the metadata endpoint first to discover the current URL for the type we
want.

We only download ``oracle_cards`` (one entry per unique English card with
oracle text and the canonical printing's stats). It's enough for the cards
package and is much smaller than ``default_cards`` or ``all_cards``.

**Format note (upstream change observed 2026-08-09).** Scryfall used to
expose a ``download_uri`` serving one big *JSON array*. That field is gone;
the index now offers only ``jsonl_download_uri``, a **gzipped JSONL** file
(one JSON object per line, gzip-compressed — ~24 MiB compressed vs ~180 MiB
uncompressed). Every downstream consumer in this repo (notably
``packages/cards``' ``loader.py``) reads a JSON *array* from
``data/raw/scryfall/oracle_cards.<date>.json``, so rather than churn all of
them we keep that on-disk contract and convert here: download the ``.jsonl.gz``
as the raw artifact, then expand it into the canonical ``.json`` array. This
mirrors how ``seventeenlands/ratings.py`` downloads raw JSON and converts to
its canonical parquet.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from pathlib import Path

import httpx

from . import paths
from .config import SCRYFALL_BULK_DATA_URL
from .http import download_to
from .manifest import Manifest, SourceEntry

log = logging.getLogger(__name__)

ORACLE_CARDS_TYPE = "oracle_cards"

# Scryfall's current field name for the gzipped-JSONL bulk download.
JSONL_URI_FIELD = "jsonl_download_uri"


def _resolve_oracle_cards_url(client: httpx.Client) -> tuple[str, str]:
    """Hit the bulk-data index, return (download_url, updated_at) for the
    oracle-cards entry.

    Raises ``RuntimeError`` if the index doesn't include the type we want, or
    includes it without the JSONL download field — either would be a Scryfall
    API change worth surfacing loudly rather than silently downloading
    something in an unexpected shape.
    """
    response = client.get(SCRYFALL_BULK_DATA_URL)
    response.raise_for_status()
    payload = response.json()
    entries = payload.get("data", [])
    for entry in entries:
        if entry.get("type") == ORACLE_CARDS_TYPE:
            uri = entry.get(JSONL_URI_FIELD)
            updated = entry.get("updated_at", "")
            if not uri:
                raise RuntimeError(
                    f"Scryfall oracle_cards entry has no {JSONL_URI_FIELD!r} "
                    f"(saw fields: {sorted(entry)!r}). The bulk-data API shape "
                    f"has changed again — update scryfall.py."
                )
            return str(uri), str(updated)
    raise RuntimeError(
        f"Scryfall bulk-data index did not include type {ORACLE_CARDS_TYPE!r}; "
        f"saw types: {[e.get('type') for e in entries]!r}"
    )


def convert_jsonl_gz_to_json_array(gz_path: Path, dest: Path) -> int:
    """Expand a gzipped-JSONL bulk file into a JSON array at ``dest``.

    Streams line-by-line so we never hold the whole ~180 MiB payload (let
    alone its parsed object graph) in memory. Each line is parsed once to
    validate it, then the *original* text is written through unchanged — that
    keeps the bytes byte-for-byte as Scryfall published them while still
    failing loudly on a malformed file.

    Written to a temp file and ``replace``-d into position, so a partial file
    never appears at the canonical path.

    Returns the number of cards written.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")

    count = 0
    try:
        with (
            gzip.open(gz_path, "rt", encoding="utf-8") as src,
            tmp.open("w", encoding="utf-8") as out,
        ):
            out.write("[")
            for lineno, raw_line in enumerate(src, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"{gz_path.name}: line {lineno} is not valid JSON: {exc}"
                    ) from exc
                if not isinstance(parsed, dict):
                    raise RuntimeError(
                        f"{gz_path.name}: line {lineno} is a {type(parsed).__name__}, "
                        f"expected a JSON object (one card per line)"
                    )
                if count:
                    out.write(",")
                out.write(line)
                count += 1
            out.write("]")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    if count == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"{gz_path.name}: contained no cards")

    tmp.replace(dest)
    log.info("Expanded %s → %s (%d cards)", gz_path.name, dest.name, count)
    return count


def refresh_oracle_cards(
    *,
    client: httpx.Client,
    manifest: Manifest,
    root: Path | None = None,
    show_progress: bool = True,
) -> SourceEntry:
    """Fetch the latest Scryfall oracle-cards data to data/raw/scryfall/.

    Downloads the gzipped-JSONL artifact, then expands it into the canonical
    ``oracle_cards.<date>.json`` array that the cards package reads.

    We name the files with the Scryfall ``updated_at`` date so successive
    snapshots can be retained side-by-side if the user keeps the old ones.
    """
    download_uri, updated_at = _resolve_oracle_cards_url(client)
    # Take just the date component for the filename — keeps things tidy.
    date_part = (updated_at or "unknown").split("T", 1)[0] or "unknown"
    raw_dir = paths.scryfall_raw_dir(root)
    gz_dest = raw_dir / f"oracle_cards.{date_part}.jsonl.gz"
    json_dest = raw_dir / f"oracle_cards.{date_part}.json"
    raw_dir.mkdir(parents=True, exist_ok=True)

    previous = manifest.get(download_uri)
    result = download_to(
        download_uri,
        gz_dest,
        client=client,
        previous=previous,
        show_progress=show_progress,
    )

    # Re-expand when the download actually changed, or when the canonical
    # array is missing (e.g. deleted by hand, or a previous run died between
    # the download and the conversion).
    if result.not_modified and json_dest.exists():
        row_count = previous.row_count if previous and previous.row_count else 0
    else:
        row_count = convert_jsonl_gz_to_json_array(gz_dest, json_dest)

    entry = result.entry.model_copy(update={"row_count": row_count})
    return manifest.upsert(entry)
