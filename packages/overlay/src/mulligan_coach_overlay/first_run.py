"""First-run setup assessment + persisted "already onboarded" state.

The overlay only works once the user has:

1. MTG Arena installed and run at least once (so ``Player.log`` exists);
2. Arena's **Detailed Logs (Plugin Support)** setting enabled (so the
   log carries the GRE events the tailer needs);
3. a reachable ``Raw_CardDatabase`` (so grpIds from the log resolve to
   cards) — auto-detected for standalone / Epic / Steam installs, with
   a wizard folder-picker as the last resort.

This module assesses those three conditions (pure, Qt-free, so it
unit-tests) and remembers when a setup has been confirmed working, so
the first-run wizard doesn't nag on every launch once the user is up and
running. The Qt dialog in :mod:`first_run_dialog` renders the
:class:`SetupStatus` this module produces and calls back into
:func:`assess_setup` on "Re-check".

Persistence mirrors :mod:`position_persistence` / :mod:`deck_persistence`:
a tiny JSON file under the per-user state dir, atomic-written, tolerant
of a missing / corrupt file.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

from . import user_data
from .arena_card_db import default_card_database_path, find_card_database_in
from .arena_paths import default_log_path
from .detailed_logs import DetailedLogStatus, detect_detailed_logs_from_path

log = logging.getLogger(__name__)

# Bump if the on-disk schema changes incompatibly; ``load_first_run_state``
# then ignores older files rather than mis-reading them.
_SCHEMA_VERSION = 1

_STATE_FILENAME = "first_run.json"


@dataclass(frozen=True)
class SetupStatus:
    """Snapshot of whether the overlay's prerequisites are met.

    ``log_path`` / ``card_db_path`` are surfaced so the wizard can show
    the user exactly which files were (or weren't) found.
    """

    arena_log_found: bool
    detailed_logs: DetailedLogStatus
    card_db_found: bool
    log_path: Path | None = None
    card_db_path: Path | None = None

    @property
    def all_clear(self) -> bool:
        """True when every prerequisite is satisfied.

        ``detailed_logs`` must be *explicitly* ``"enabled"`` — an
        ``"unknown"`` (log unreadable, or Arena not launched since the
        setting last changed) is not good enough to call the setup done.
        """
        return self.arena_log_found and self.detailed_logs == "enabled" and self.card_db_found


@dataclass(frozen=True)
class FirstRunState:
    """Persisted onboarding state.

    * ``detailed_logs_verified`` — set once we've observed an all-clear
      setup. Its purpose is the no-nag guarantee: after the user has the
      overlay working once, we never auto-pop the wizard again (it stays
      reachable from the tray menu).
    * ``card_db_dir`` — a ``Downloads/Raw`` directory the user pointed us
      at via the wizard's folder picker, when auto-detection failed. We
      store the *directory*, not the file, so it survives Arena content
      patches (which rename the ``Raw_CardDatabase_<hash>.mtga`` file).
    """

    detailed_logs_verified: bool = False
    card_db_dir: str | None = None


def default_first_run_path() -> Path:
    """Location of ``first_run.json`` under the per-user state dir."""
    return user_data.user_state_root() / _STATE_FILENAME


def assess_setup(
    *,
    log_path: Path | None = None,
    card_db_dir: str | None = None,
) -> SetupStatus:
    """Evaluate the three prerequisites.

    ``log_path`` defaults to Arena's canonical ``Player.log`` location
    (``None`` when the platform has no default, e.g. Linux without the
    override — treated as "Arena not found"). ``card_db_dir``, when
    given (from :class:`FirstRunState`), is searched for a CardDatabase
    before falling back to auto-detection.
    """
    if log_path is None:
        try:
            log_path = default_log_path()
        except RuntimeError:
            log_path = None

    arena_log_found = log_path is not None and log_path.is_file()
    detailed = (
        detect_detailed_logs_from_path(log_path) if arena_log_found and log_path else "unknown"
    )

    card_db_path: Path | None = None
    if card_db_dir:
        card_db_path = find_card_database_in(Path(card_db_dir))
    if card_db_path is None:
        card_db_path = default_card_database_path(log_path)

    return SetupStatus(
        arena_log_found=arena_log_found,
        detailed_logs=detailed,
        card_db_found=card_db_path is not None,
        log_path=log_path,
        card_db_path=card_db_path,
    )


def should_auto_show(status: SetupStatus, state: FirstRunState) -> bool:
    """Whether the wizard should pop automatically at launch.

    Rules (the no-nag contract):

    * setup all-clear ⇒ never (nothing to fix);
    * otherwise, if we've confirmed a working setup before
      (``detailed_logs_verified``) ⇒ never auto-pop — the user reaches
      the wizard from the tray if something later breaks;
    * otherwise (not yet onboarded, something's wrong) ⇒ show it.
    """
    if status.all_clear:
        return False
    # Not all-clear. Auto-pop only if we haven't confirmed a working
    # setup before — once onboarded, the user reaches the wizard from
    # the tray rather than being nagged at every launch.
    return not state.detailed_logs_verified


def reconcile_state(status: SetupStatus, state: FirstRunState) -> FirstRunState:
    """Return the state to persist given a fresh assessment.

    Flips ``detailed_logs_verified`` on the first all-clear so the
    wizard stops auto-popping thereafter. Returns *state* unchanged when
    nothing needs updating (the caller can skip the disk write).
    """
    if status.all_clear and not state.detailed_logs_verified:
        return FirstRunState(detailed_logs_verified=True, card_db_dir=state.card_db_dir)
    return state


def load_first_run_state(path: Path) -> FirstRunState:
    """Read persisted onboarding state, or defaults if absent/corrupt.

    Missing file, unreadable file, bad JSON, wrong schema version, and
    malformed fields all collapse to a fresh :class:`FirstRunState`.
    """
    if not path.is_file():
        return FirstRunState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read first-run state from %s: %s", path, exc)
        return FirstRunState()
    if not isinstance(payload, dict) or payload.get("version") != _SCHEMA_VERSION:
        return FirstRunState()
    verified = payload.get("detailed_logs_verified")
    card_db_dir = payload.get("card_db_dir")
    return FirstRunState(
        detailed_logs_verified=verified is True,
        card_db_dir=card_db_dir if isinstance(card_db_dir, str) and card_db_dir else None,
    )


def save_first_run_state(path: Path, state: FirstRunState) -> None:
    """Atomically persist *state* to *path* (sibling ``.tmp`` + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": _SCHEMA_VERSION,
        "saved_at": time.time(),
        "detailed_logs_verified": state.detailed_logs_verified,
        "card_db_dir": state.card_db_dir,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)
