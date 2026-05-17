"""Persist the most recent fully-resolved deck to disk.

Motivation
----------

The log tailer starts at end-of-file by default — if the user starts
the overlay *after* Arena has already submitted the deck for the
current match, that submission is in the part of the log we never
read, and the next mulligan decision lands with no deck loaded.
Result: ``Can't recommend / no_deck``.

Mitigation: each time the coordinator sees a fully-resolved
``DeckSubmitted``, we serialise it to a small JSON file under the
platform's local-app-data directory. On the next overlay launch we
load that file and seed the coordinator before the tailer starts, so
a missed submission falls back to "the deck Arena submitted last
time" instead of "no deck at all".

The persisted file is intentionally minimal — just the arena_id
list. The recommendation pipeline doesn't need anything else (the
:class:`ArenaCardIndex` re-resolves arena_ids to ``ParsedCard`` on
every load anyway).

If the user switches decks between matches, the next deck submission
will overwrite the stored file before the next mulligan decision.
The only failure mode is "user starts the overlay between two
matches with different decks and Arena never re-submits before the
mulligan" — they'd then get a recommendation against the previous
deck. The footer line in the GUI shows the inferred ``primary_set``
so the user can spot the mismatch.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)


# Bump if the on-disk schema ever changes in a non-backwards-compatible
# way; ``load_last_deck`` will then refuse files with an older version.
_SCHEMA_VERSION = 1


def default_persistence_path() -> Path:
    """Return the platform-appropriate location for the last-deck JSON.

    * Windows: ``%LOCALAPPDATA%\\MulliganCoach\\last_deck.json`` —
      same convention as untapped.gg and the MTGA log itself.
    * macOS:   ``~/Library/Application Support/MulliganCoach/last_deck.json``
    * Linux:   ``~/.local/share/mulligan-coach/last_deck.json`` (XDG
      data home).

    Existence is not checked here; callers should be ready for either
    "file exists" or "file does not exist yet". Parent directories
    are created on save.
    """
    if sys.platform == "win32":
        # %LOCALAPPDATA% is the canonical per-user / per-machine cache
        # location on Windows. We fall back to the user profile root
        # if the env var is unset (very unusual; happens in some CI).
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "MulliganCoach" / "last_deck.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MulliganCoach" / "last_deck.json"
    # Linux + other POSIX: XDG_DATA_HOME if set, else ~/.local/share.
    xdg = os.environ.get("XDG_DATA_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base_path / "mulligan-coach" / "last_deck.json"


def save_last_deck(path: Path, arena_ids: list[int]) -> None:
    """Atomically persist *arena_ids* to *path*.

    No-op if *arena_ids* is empty — we never want to overwrite a
    valid stored deck with nothing.

    The write goes via a sibling ``.tmp`` file plus a rename, which is
    atomic on Windows (>=Vista) and POSIX. A crash mid-write leaves
    the previous valid file untouched rather than truncating it.
    """
    if not arena_ids:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "saved_at": time.time(),
        "arena_ids": list(arena_ids),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_last_deck(path: Path) -> list[int] | None:
    """Return arena_ids from *path*, or ``None`` if absent / unusable.

    Treats missing file, unreadable file, corrupt JSON, wrong schema
    version, and missing / malformed ``arena_ids`` field all as
    "nothing to seed" — none of them should crash the overlay.
    """
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read last-deck from %s: %s", path, exc)
        return None
    if not isinstance(payload, dict):
        log.warning("last-deck file %s has wrong top-level shape", path)
        return None
    version = payload.get("version")
    if version != _SCHEMA_VERSION:
        log.info(
            "ignoring last-deck file %s with schema version %r (expected %r)",
            path,
            version,
            _SCHEMA_VERSION,
        )
        return None
    arena_ids = payload.get("arena_ids")
    if not isinstance(arena_ids, list) or not arena_ids:
        return None
    if not all(isinstance(x, int) for x in arena_ids):
        log.warning("last-deck file %s has non-int arena_ids", path)
        return None
    return arena_ids
