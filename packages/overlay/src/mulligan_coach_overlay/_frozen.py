"""Frozen-bundle path resolution for the PyInstaller build.

When the overlay runs from source (``uv run mulligan-coach-overlay``)
all data files resolve relative to the repo: ``data/processed/...``
and ``models/choice_prod/`` sit a fixed number of parent dirs above
each package's ``__file__``. That logic is baked into
``mulligan_coach_cards.store._data_root``,
``mulligan_coach_cards.seventeenlands_stats._data_root``, and
``mulligan_coach_recommend.service._DEFAULT_CHOICE_MODEL_DIR`` (all
using ``Path(__file__).resolve().parents[4]``).

When PyInstaller freezes us into a one-folder bundle, those
``__file__`` paths point inside the bundle's temp-extracted directory
(``sys._MEIPASS``) and the ``parents[4]`` walk lands somewhere that
has no ``data/`` or ``models/`` subdir. Rather than patch every
upstream resolver, we let those resolvers read env vars they already
support — :envvar:`MULLIGAN_COACH_DATA_ROOT` and
:envvar:`MULLIGAN_COACH_CHOICE_MODEL_DIR` — and set them here at
the earliest point in the GUI / headless entry points, before any
upstream module's path resolution runs.

Bundle layout the PyInstaller spec produces (under ``dist/MulliganCoach/``):

* ``MulliganCoach.exe`` — the entry point.
* ``_internal/`` — PyInstaller's standard one-folder extracted libs.
* ``_internal/data/processed/parsed_cards/<SET>.json``
* ``_internal/data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet``
* ``_internal/models/choice_prod/{xgboost.json, metadata.json, ...}``

PyInstaller exposes ``sys._MEIPASS`` as the path to ``_internal``
when running frozen, so we point the env vars at subdirectories of
that path. The user's friend doesn't have a repo checkout; they just
have the shipped folder.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def is_frozen() -> bool:
    """``True`` when running from a PyInstaller-frozen bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path | None:
    """Return the bundle's data root (``sys._MEIPASS``) or ``None`` from source.

    PyInstaller sets ``sys._MEIPASS`` to the directory holding the
    extracted libs and any ``--add-data`` payload. From source the
    attribute is absent, so callers should treat ``None`` as "we're
    running from the workspace, the existing path resolvers work
    unchanged".
    """
    if not is_frozen():
        return None
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def configure_bundle_paths() -> None:
    """Point the upstream path resolvers at the user data + model dirs.

    Auto-update prep: data and model files live in a user-writable
    location (see :mod:`user_data`) rather than inside the frozen
    bundle. On first launch (or after an EXE upgrade) the bundled
    defaults are copied into the user dir so the overlay works
    immediately offline.

    Idempotent and safe to call early in any entry point. From source
    this is a no-op — the env vars are left alone so a developer
    override (``set MULLIGAN_COACH_DATA_ROOT=...``) is still honoured.

    Both env vars are only set when not already populated, so a user
    who wants to point the shipped overlay at a different data folder
    can still do so via the environment.
    """
    root = bundle_root()
    if root is None:
        return
    # Local import: ``user_data`` is overlay-internal and depends on
    # nothing else, but importing it at module top would create a
    # cycle if some future caller imports ``_frozen`` from inside
    # ``user_data``.
    from . import user_data

    try:
        user_data.seed_from_bundle(root)
    except OSError as exc:
        # Disk-full / permissions / file lock. Fall back to the
        # bundled paths so the overlay still launches; the user
        # won't have auto-update working, but they'll have a usable
        # tool while they diagnose the disk issue. Logged loudly
        # rather than silently because this state matters for
        # support diagnostics.
        logging.getLogger(__name__).error(
            "seed_from_bundle failed; falling back to bundled data paths: %s", exc
        )
        os.environ.setdefault("MULLIGAN_COACH_DATA_ROOT", str(root / "data"))
        os.environ.setdefault(
            "MULLIGAN_COACH_CHOICE_MODEL_DIR", str(root / "models" / "choice_prod")
        )
        return

    os.environ.setdefault("MULLIGAN_COACH_DATA_ROOT", str(user_data.user_data_root()))
    os.environ.setdefault(
        "MULLIGAN_COACH_CHOICE_MODEL_DIR",
        str(user_data.user_models_root() / "choice_prod"),
    )


def user_log_dir() -> Path:
    """Per-user log directory; created if missing.

    On Windows the shipped EXE has no console window (``console=False``
    in the spec), so anything written to stdout vanishes. We persist
    logs to ``%LOCALAPPDATA%\\MulliganCoach\\logs`` instead so the
    user can attach them to a bug report. Falls back to the
    deck-persistence dir's parent on non-Windows so dev runs work too.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Logs"
    else:
        root = Path.home() / ".local" / "share"
    path = root / "MulliganCoach" / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_frozen_logging() -> None:
    """Add a rotating file handler when running frozen.

    The shipped EXE is GUI-only, so stdout is dropped on the floor.
    This swaps the default basicConfig stream handler for a rotating
    file at ``user_log_dir()/overlay.log`` (5 MB x 3 generations).
    Dev runs from source skip this and keep stdout logging intact.

    Called from the GUI / headless entry points *after*
    ``logging.basicConfig`` has run, so the root logger already has
    its level + format. We just attach an additional handler.
    """
    if not is_frozen():
        return
    log_path = user_log_dir() / "overlay.log"
    handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    # Surface the path so anyone running the EXE from a terminal sees
    # where to look. From a launched .exe (no console) this is invisible
    # but harmless.
    logging.getLogger(__name__).info("overlay log file: %s", log_path)
