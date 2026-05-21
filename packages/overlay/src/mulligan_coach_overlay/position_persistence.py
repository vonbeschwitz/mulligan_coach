"""Persist the overlay window's compact + expanded positions to disk.

Motivation
----------

The overlay has two layouts on the same widget — a full panel and a
compact pill — and the user typically wants them in different places.
Common pattern: park the compact pill at the bottom of the screen
(out of the way during play), but have the expanded panel pop back
to a comfortable read position above the mulligan UI. Without
separate memory, every collapse/expand cycle drags one layout to
wherever the other was last.

We store both positions in a small JSON file under the platform's
local-app-data directory, alongside ``last_deck.json``. Either
position can be absent (``None``) — first launch in a layout the
user has never moved, or a corrupt file recovered partially.

The on-disk schema is intentionally tiny: one ``x``/``y`` pair per
layout. We don't try to reason about multi-monitor topology or
DPI changes — if the user moves the overlay to a screen that's no
longer attached on next launch, Qt's ``move()`` will clamp the
window into the visible area on the next paint, which is the
right behaviour.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# Bump if the on-disk schema ever changes incompatibly; ``load_positions``
# will then ignore older files rather than mis-parse them.
_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Positions:
    """Loaded positions for the two overlay layouts.

    Both fields are optional ``(x, y)`` tuples. ``None`` means the
    user has never parked that layout (or the stored value was
    malformed) — the caller should fall back to its default layout
    placement.
    """

    compact: tuple[int, int] | None = None
    expanded: tuple[int, int] | None = None


def default_positions_path() -> Path:
    """Return the platform-appropriate location for ``positions.json``.

    Same directory convention as :func:`deck_persistence.default_persistence_path`
    so all overlay-state files cluster under a single per-user folder.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "MulliganCoach" / "positions.json"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "MulliganCoach" / "positions.json"
    xdg = os.environ.get("XDG_DATA_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base_path / "mulligan-coach" / "positions.json"


def save_positions(
    path: Path,
    *,
    compact: tuple[int, int] | None,
    expanded: tuple[int, int] | None,
) -> None:
    """Atomically persist *compact* and *expanded* to *path*.

    No-op if both arguments are ``None`` — saving "nothing for either
    layout" would just delete useful state for a future caller that
    happens to come through with one arm set.

    Atomic write via sibling ``.tmp`` + rename so a crash mid-write
    leaves any previously valid file intact.
    """
    if compact is None and expanded is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "saved_at": time.time(),
        "compact": list(compact) if compact is not None else None,
        "expanded": list(expanded) if expanded is not None else None,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def load_positions(path: Path) -> Positions:
    """Return the stored positions, or an empty :class:`Positions` if absent.

    Treats missing file, unreadable file, corrupt JSON, wrong schema
    version, and malformed entries all as "no stored position." Each
    layout decodes independently — a malformed ``compact`` entry
    doesn't suppress a valid ``expanded`` one.
    """
    if not path.is_file():
        return Positions()
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read positions from %s: %s", path, exc)
        return Positions()
    if not isinstance(payload, dict):
        log.warning("positions file %s has wrong top-level shape", path)
        return Positions()
    version = payload.get("version")
    if version != _SCHEMA_VERSION:
        log.info(
            "ignoring positions file %s with schema version %r (expected %r)",
            path,
            version,
            _SCHEMA_VERSION,
        )
        return Positions()
    return Positions(
        compact=_decode_xy(payload.get("compact")),
        expanded=_decode_xy(payload.get("expanded")),
    )


def _decode_xy(value: object) -> tuple[int, int] | None:
    """Return ``(x, y)`` from a JSON ``[x, y]`` list, else ``None``.

    Bool guards: ``isinstance(True, int)`` is True in Python, and we
    don't want a stray ``[true, false]`` to round-trip as ``(1, 0)``.
    """
    if not isinstance(value, list) or len(value) != 2:
        return None
    x, y = value
    if isinstance(x, bool) or isinstance(y, bool):
        return None
    if not isinstance(x, int) or not isinstance(y, int):
        return None
    return (x, y)
