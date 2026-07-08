"""Shared HTTP client + streamed-download helper.

Why a small helper instead of just calling ``httpx.get`` directly?

* Every download we do is large enough to want **streaming** (don't load
  multi-GB CSVs into RAM) plus a **progress bar** (the user can see things
  are alive).
* Every download benefits from a **conditional GET** (``If-None-Match`` /
  ``If-Modified-Since``), so re-running ``refresh-all`` against unchanged
  upstream data is a fast no-op.
* Writes must be **atomic** — partial files at the canonical path break
  later ingestion. We always download to ``<dest>.tmp-<pid>`` in the same
  directory and ``os.replace`` into place.
* Concentrating retries / timeouts / user-agent here means the rest of the
  codebase stays free of HTTP boilerplate.
"""

from __future__ import annotations

import hashlib
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import httpx
from tqdm import tqdm

from . import __version__
from .manifest import SourceEntry

log = logging.getLogger(__name__)

# A polite user-agent — both 17Lands and Scryfall ask integrators to identify
# themselves so they can reach out if we cause problems.
USER_AGENT = (
    f"mulligan-coach-data-download/{__version__} "
    "(+https://github.com/vonbeschwitz/mulligan_coach_data; "
    "bastian.vonbeschwitz@gmail.com)"
)

# Generous overall timeout — a multi-GB download over a slow link can take a
# while. Connect/read are kept tight so a stuck server fails fast.
DEFAULT_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)


def make_client() -> httpx.Client:
    """Build a configured httpx.Client.

    ``HTTPTransport(retries=3)`` retries connection failures (DNS, refused
    connections) — it does NOT retry on HTTP status codes. That's appropriate
    here: the public datasets endpoints rarely return 5xx, and a 4xx is
    something we want to surface immediately rather than retry blindly.
    """
    transport = httpx.HTTPTransport(retries=3)
    return httpx.Client(
        transport=transport,
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a single ``download_to`` call.

    ``not_modified`` is True when the server returned 304 and we left the
    on-disk file untouched. ``entry`` is the fully-populated manifest row
    that the caller should ``manifest.upsert(...)``.
    """

    not_modified: bool
    entry: SourceEntry


@contextmanager
def _atomic_writer(dest: Path) -> Iterator[tuple[Path, IO[bytes]]]:
    """Open a temp file alongside ``dest``; on success the caller renames it
    in. On failure, the temp file is removed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp-{os.getpid()}")
    handle = tmp.open("wb")
    try:
        yield tmp, handle
    except BaseException:
        handle.close()
        if tmp.exists():
            tmp.unlink()
        raise
    else:
        handle.close()


def download_to(
    url: str,
    dest: Path,
    *,
    client: httpx.Client,
    previous: SourceEntry | None = None,
    show_progress: bool = True,
    chunk_size: int = 1 << 20,  # 1 MiB
) -> DownloadResult:
    """Download ``url`` to ``dest`` with conditional-GET + atomic rename.

    Parameters
    ----------
    url:
        URL to fetch.
    dest:
        Final on-disk path. Parent is created if missing.
    client:
        Shared httpx.Client (build via :func:`make_client`).
    previous:
        Manifest entry from the last successful download of this URL, if any.
        Used to populate ``If-None-Match`` and ``If-Modified-Since`` headers
        for cheap "is it actually different?" checks.
    show_progress:
        Toggle the tqdm progress bar (off in tests).
    chunk_size:
        Bytes per streamed chunk — 1 MiB is a good default for big files.

    Returns
    -------
    DownloadResult
        ``not_modified=True`` if the server returned 304 (file on disk is
        already current). Otherwise the new manifest entry, including
        ETag / Last-Modified / sha256 / size.
    """
    headers: dict[str, str] = {}
    # Only send conditional headers if the previously-recorded local file is
    # still on disk. Otherwise we MUST re-download regardless of ETag.
    if previous is not None and dest.exists():
        if previous.etag:
            headers["If-None-Match"] = previous.etag
        if previous.last_modified:
            headers["If-Modified-Since"] = previous.last_modified

    log.info("GET %s", url)
    with client.stream("GET", url, headers=headers) as response:
        if response.status_code == 304:
            log.info("304 Not Modified — keeping %s", dest.name)
            assert previous is not None
            return DownloadResult(not_modified=True, entry=previous)
        response.raise_for_status()

        total = int(response.headers.get("Content-Length", 0)) or None
        sha = hashlib.sha256()
        size = 0
        progress = (
            tqdm(
                total=total,
                unit="B",
                unit_scale=True,
                desc=dest.name,
                leave=False,
            )
            if show_progress
            else None
        )

        with _atomic_writer(dest) as (tmp, fh):
            for chunk in response.iter_bytes(chunk_size):
                fh.write(chunk)
                sha.update(chunk)
                size += len(chunk)
                if progress is not None:
                    progress.update(len(chunk))
        if progress is not None:
            progress.close()
        tmp.replace(dest)

        new_entry = SourceEntry(
            url=url,
            local_path=str(dest),
            etag=response.headers.get("ETag"),
            last_modified=response.headers.get("Last-Modified"),
            sha256=sha.hexdigest(),
            size=size,
        )
        log.info("Wrote %s (%d bytes)", dest, size)
        return DownloadResult(not_modified=False, entry=new_entry)
