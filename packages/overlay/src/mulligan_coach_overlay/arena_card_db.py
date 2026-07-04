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
import re
import sqlite3
import sys
from pathlib import Path

from .arena_paths import default_log_path

log = logging.getLogger(__name__)

# Env var override — both for tests and for users whose MTGA install
# lives somewhere non-standard (custom drive, Steam, etc.). Points at
# the ``Raw_CardDatabase_*.mtga`` *file* directly.
_ENV_OVERRIDE = "MULLIGAN_COACH_MTGA_CARDDB"

# Well-known ``MTGA_Data`` install locations, by distribution channel.
# We append ``Downloads/Raw`` to each to reach the CardDatabase dir.
# These are *fallbacks*: the primary, install-source-agnostic source is
# the path Arena records in its own log (see
# :func:`arena_data_dir_from_log`), which covers Epic / Steam / custom
# drives without us having to enumerate them here.
_DEFAULT_WINDOWS_DATA_DIRS: tuple[Path, ...] = (
    # Standalone Wizards installer (the historical default).
    Path("C:/Program Files/Wizards of the Coast/MTGA/MTGA_Data"),
    Path("C:/Program Files (x86)/Wizards of the Coast/MTGA/MTGA_Data"),
    # Epic Games Store. Arena's Epic app installs under the app folder
    # ``MagicTheGathering`` (confirmed via the Epic/macOS layout
    # ``Epic Games/MagicTheGathering/MTGA``); on Windows Epic defaults
    # to ``C:/Program Files/Epic Games/<AppFolder>``.
    Path("C:/Program Files/Epic Games/MagicTheGathering/MTGA/MTGA_Data"),
    Path("C:/Program Files (x86)/Epic Games/MagicTheGathering/MTGA/MTGA_Data"),
)

# ``Downloads/Raw`` under an ``MTGA_Data`` dir is where the
# ``Raw_CardDatabase_<hash>.mtga`` files live.
_RAW_SUBDIR = ("Downloads", "Raw")

# Glob for the CardDatabase file (the ``<hash>`` changes each content patch).
_CARD_DB_GLOB = "Raw_CardDatabase_*.mtga"

# Arena's ``Player.log`` opens with Unity's ``Mono path[0]`` line, which
# embeds the install's managed-assemblies dir, e.g.::
#
#     Mono path[0] = 'C:/Program Files/Epic Games/MagicTheGathering/MTGA/MTGA_Data/Managed'
#
# The part before ``/Managed`` is the ``MTGA_Data`` dir — an
# authoritative, install-source-agnostic pointer at where *this* user's
# Arena lives (the same trick MTGA_Draft_17Lands uses). We match both
# separators so a backslash variant is covered.
_MANAGED_RE = re.compile(r"['\"]([^'\"]+?)[/\\]Managed['\"]")

# How many bytes of the log head to read when hunting for the Mono-path
# line. It's the first line, so this is generous.
_LOG_HEAD_BYTES = 65_536


def default_card_database_path(log_path: Path | None = None) -> Path | None:
    """Locate Arena's most recent ``Raw_CardDatabase_*.mtga``.

    Resolution order:

    1. The ``MULLIGAN_COACH_MTGA_CARDDB`` env override (a file path).
    2. The install dir Arena records in its own ``Player.log`` (covers
       Epic Games Store / Steam / custom-drive installs automatically).
    3. The well-known standalone + Epic install locations.

    Returns ``None`` if no database is found — the overlay then falls
    back to the MTGJSON/Scryfall-derived ``arena_id`` already on
    :class:`ParsedCard`, which is reliable for older sets but mostly
    empty for the current Premier Draft rotation. ``log_path`` defaults
    to Arena's canonical log location; pass an explicit path for tests.
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
    if log_path is None:
        try:
            log_path = default_log_path()
        except RuntimeError:
            log_path = None
    for raw_dir in candidate_raw_dirs(log_path):
        db = find_card_database_in(raw_dir)
        if db is not None:
            return db
    return None


def arena_data_dir_from_log(log_path: Path) -> Path | None:
    """Return the ``MTGA_Data`` dir Arena records in ``Player.log``, if any.

    Reads the log head, extracts the ``Mono path[0] = '.../Managed'``
    line, and returns the ``MTGA_Data`` dir that precedes ``/Managed``
    when it exists on disk. This is the most reliable install locator —
    it's whatever path Arena itself was launched from — so it resolves
    Epic Games Store, Steam, and custom-drive installs without a
    hard-coded candidate list. Returns ``None`` on any read/parse miss.
    """
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(_LOG_HEAD_BYTES)
    except OSError as exc:
        log.info("card-db: could not read log head for install dir: %s", exc)
        return None
    match = _MANAGED_RE.search(head)
    if match is None:
        return None
    data_dir = Path(match.group(1))
    return data_dir if data_dir.is_dir() else None


def candidate_raw_dirs(log_path: Path | None) -> list[Path]:
    """Ordered, de-duplicated list of ``Downloads/Raw`` dirs to probe.

    The log-derived install (if resolvable) comes first — it's the one
    the running Arena actually uses — followed by the well-known
    standalone + Epic locations as fallbacks.
    """
    dirs: list[Path] = []
    if log_path is not None:
        data_dir = arena_data_dir_from_log(log_path)
        if data_dir is not None:
            dirs.append(data_dir.joinpath(*_RAW_SUBDIR))
    for data_dir in _DEFAULT_WINDOWS_DATA_DIRS:
        dirs.append(data_dir.joinpath(*_RAW_SUBDIR))
    # De-dupe while preserving order (the log-derived dir often equals a
    # default one on a standalone install).
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def find_card_database_in(raw_dir: Path) -> Path | None:
    """Newest ``Raw_CardDatabase_*.mtga`` directly inside ``raw_dir``.

    Newest mtime wins — Arena keeps the previous file briefly after a
    content patch and we want the freshly-downloaded one. Returns
    ``None`` when the dir is absent or holds no matching file.
    """
    if not raw_dir.is_dir():
        return None
    candidates = [p for p in raw_dir.glob(_CARD_DB_GLOB) if p.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def find_card_database_under(root: Path) -> Path | None:
    """Search a user-picked folder for a CardDatabase (wizard last resort).

    The first-run wizard lets a user point at their Arena install when
    auto-detection fails (an exotic drive, a portable copy). They might
    pick the install root, the ``MTGA_Data`` dir, or the ``Raw`` dir
    itself — so we check the likely spots directly, then fall back to a
    bounded recursive search. Returns the newest matching file, or
    ``None``.
    """
    if not root.is_dir():
        return None
    # Likely exact locations first (cheap, no walk).
    direct = [
        root,
        root.joinpath(*_RAW_SUBDIR),
        root.joinpath("MTGA_Data", *_RAW_SUBDIR),
        root.joinpath("MTGA", "MTGA_Data", *_RAW_SUBDIR),
    ]
    for raw_dir in direct:
        db = find_card_database_in(raw_dir)
        if db is not None:
            return db
    # Fallback: recursive hunt. Arena's data tree is large but finite;
    # collect every match and take the newest so we're robust to the
    # user picking a level above the Raw dir.
    matches = [p for p in root.rglob(_CARD_DB_GLOB) if p.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]


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
