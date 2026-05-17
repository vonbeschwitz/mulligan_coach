"""Resolve Arena's ``Player.log`` location across platforms.

Single source of truth so the headless CLI, the GUI, and tests all
agree on where the log lives. Override via the
``MULLIGAN_COACH_OVERLAY_LOG`` env var (mostly for tests + for users
running Arena under Bottles / Lutris on Linux, which lands the file
in a non-standard path).

Windows uses the *LocalLow* folder (note the trailing ``Low``), which
``%LOCALAPPDATA%`` does NOT point at — it points at *Local*. The
canonical path is ``%USERPROFILE%\\AppData\\LocalLow\\...``; we
build it from ``Path.home()`` for the same reason MTGATracker and the
17Lands client do — neither Windows nor Python ship a built-in
"LocalLow" resolver.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_OVERRIDE = "MULLIGAN_COACH_OVERLAY_LOG"


def default_log_path() -> Path:
    """Return Arena's ``Player.log`` location for the current platform.

    Does NOT check whether the file exists — callers that care should
    test that themselves so they can surface a clear "Arena isn't
    running / hasn't run yet" message instead of crashing on a missing
    path. The env-var override always wins.
    """
    override = os.environ.get(_ENV_OVERRIDE, "").strip()
    if override:
        return Path(override)
    if sys.platform == "win32":
        # %USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log
        return Path.home() / "AppData" / "LocalLow" / "Wizards Of The Coast" / "MTGA" / "Player.log"
    if sys.platform == "darwin":
        # ~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
        return Path.home() / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA" / "Player.log"
    # Linux: no canonical location. The user almost certainly runs
    # Arena under Bottles or Lutris, which lands the log inside the
    # prefix at an arbitrary path. We don't try to guess — set the
    # env var.
    raise RuntimeError(
        f"No default Arena log path for platform {sys.platform!r}. "
        f"Set {_ENV_OVERRIDE} to the absolute path of Player.log."
    )
