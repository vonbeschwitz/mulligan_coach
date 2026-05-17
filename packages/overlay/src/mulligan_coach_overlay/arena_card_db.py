"""Read Arena's local CardDatabase SQLite for canonical ``grpId`` mappings.

The MTGA client ships a SQLite database — ``Raw_CardDatabase_<hash>.mtga``
— inside its install directory. It contains every printing currently
available in Arena, including each row's ``GrpId`` (Arena's primary key
for cards in log messages) and the ``ExpansionCode`` + ``CollectorNumber``
that link each row back to Scryfall / ParsedCard records.

This is the authoritative source for "what GrpId does Arena use for this
card?". MTGJSON's ``AllIdentifiers`` and Scryfall's bulk dumps both lag
by weeks for newly-released sets, so for a freshly-rotated format
(TLA / TMT / ECL / SOS today) those upstream sources cover only a sliver
of the printings. Arena's own DB updates as soon as the client patches —
so as long as the user has MTGA installed and reasonably up-to-date,
this lookup covers every card they could possibly draw in a real game.

The file is opened **read-only** so we don't compete with Arena for the
file lock while the game is running.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Env var override — both for tests and for users whose MTGA install
# lives somewhere non-standard (custom drive, Steam, etc.).
_ENV_OVERRIDE = "MULLIGAN_COACH_MTGA_CARDDB"

# Standard Windows install. Arena is a Wizards-published Windows client;
# Mac users typically run it through Wine / Parallels with the file laid
# out in a Wine prefix at an arbitrary path — we don't try to guess those.
_DEFAULT_WINDOWS_DIR = Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data/Downloads/Raw")


def default_card_database_path() -> Path | None:
    """Locate Arena's most recent ``Raw_CardDatabase_*.mtga``.

    Returns ``None`` if no database is found — the overlay then falls
    back to the MTGJSON/Scryfall-derived ``arena_id`` already on
    :class:`ParsedCard`, which is reliable for older sets but mostly
    empty for the current Premier Draft rotation.
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        p = Path(override)
        return p if p.exists() else None
    if sys.platform != "win32":
        # Non-Windows: skip the default-path probe. The env var override
        # above still works for users with a custom layout (Bottles,
        # Crossover, …).
        return None
    if not _DEFAULT_WINDOWS_DIR.exists():
        return None
    candidates = list(_DEFAULT_WINDOWS_DIR.glob("Raw_CardDatabase_*.mtga"))
    if not candidates:
        return None
    # Newest mtime wins — Arena keeps the previous file briefly after
    # a content patch and we want the freshly-downloaded one.
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def load_arena_id_pairs(db_path: Path) -> list[tuple[str, str, int, str]]:
    """Return ``(ExpansionCode, CollectorNumber, GrpId, Name)`` for every card row.

    Tokens / non-playable rows are excluded; rows with a missing or
    blank ``ExpansionCode`` or ``CollectorNumber`` are excluded too
    (they aren't matchable against our per-set ``ParsedCard`` index).
    One row per Arena printing — alt-art / showcase / promo treatments
    each get their own GrpId in Arena, so each is returned separately.

    The ``Name`` column is joined from ``Localizations_enUS`` (key
    ``Cards.TitleId``). It's needed for the basic-land synthesis
    fallback in :class:`ArenaCardIndex` — basics in Arena come from a
    dozen different sets and we don't keep parsed_cards for most of
    them, so matching by name is the only way to resolve them.

    Opens read-only via SQLite's ``file:...?mode=ro&immutable=1`` URI
    form. ``immutable=1`` skips locking entirely — necessary because
    Arena holds an exclusive lock on the file while the game runs.
    """
    uri = f"file:{db_path.as_posix()}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.execute(
            "SELECT c.ExpansionCode, c.CollectorNumber, c.GrpId, l.Loc "
            "FROM Cards c "
            "LEFT JOIN Localizations_enUS l ON l.LocId = c.TitleId "
            "WHERE c.IsToken = 0 "
            "AND c.ExpansionCode IS NOT NULL AND c.ExpansionCode != '' "
            "AND c.CollectorNumber IS NOT NULL AND c.CollectorNumber != ''"
        )
        out: list[tuple[str, str, int, str]] = []
        for set_code, collector_number, grp_id, name in cur:
            if not isinstance(grp_id, int):
                continue
            out.append(
                (
                    str(set_code).upper(),
                    str(collector_number),
                    grp_id,
                    str(name) if name else "",
                )
            )
        return out
    finally:
        conn.close()
