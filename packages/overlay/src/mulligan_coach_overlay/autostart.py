"""Windows "Start with Windows" registry helper.

The overlay's `ArenaWindowWatcher` already hides the panel when MTG
Arena isn't running, so the path of least surprise for the user is
"run all the time, show up automatically when Arena launches." That
needs the EXE on the Windows autostart list.

We use the per-user Run key
(``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``) rather
than the machine-wide one (``HKLM``) so:

* No UAC prompt or admin rights needed.
* Each user account on a shared machine controls its own state.
* Uninstall is a single registry delete, no side files to chase.

The entry value is the absolute, quoted path to the frozen
``MulliganCoach.exe`` (taken from :data:`sys.executable` — PyInstaller
sets that to the actual launcher when running frozen). Running from
source falls outside the supported set entirely; we don't want a dev
to accidentally wire ``python.exe`` into autostart.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# Per-user Run key. The OS scans every value in this key at login
# and launches the referenced executable for the current user.
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# Name of the value we own under that key. Picked to match the EXE
# / Start-menu name so a user inspecting ``regedit`` or Task Manager's
# Startup tab sees a recognisable entry.
_ENTRY_NAME = "MulliganCoach"

# Marker file name. Lives in the user state dir alongside the slice-1
# ``_seeded_version.txt``. Presence means "we've already enabled
# autostart at least once on this account" — so if the user later
# unchecks the Start-with-Windows toggle, the next launch won't
# re-enable it behind their back.
_SEEDED_MARKER = "_autostart_seeded.txt"


def supported() -> bool:
    """``True`` when the helper can read and write the autostart entry.

    Two conditions:

    * Windows (other OSes have no equivalent registry; the friend the
      bundle is shipped to is on Windows).
    * Frozen by PyInstaller (so ``sys.executable`` resolves to the
      shipped ``MulliganCoach.exe``, not the dev ``python.exe``).

    Returning ``False`` here lets the UI hide the menu item cleanly
    rather than offer a toggle that does nothing.
    """
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def _executable_path() -> Path:
    """Absolute path to the running launcher EXE."""
    return Path(sys.executable).resolve()


def is_enabled() -> bool:
    """``True`` iff the Run key already points at this EXE.

    "Points at this EXE" means the stored value (after stripping any
    surrounding quotes) resolves to the same path as the running
    process's :data:`sys.executable`. A value present but pointing at
    a different location — e.g. the user kept an old copy of the
    overlay elsewhere — counts as *not enabled* for this binary; the
    UI then correctly shows the toggle as off.
    """
    # mypy on Linux CI doesn't narrow through ``supported()`` so we
    # inline the platform check before the ``winreg`` import. The
    # frozen check still uses ``supported()`` — it doesn't affect
    # type narrowing.
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    import winreg  # local import: stdlib module, Windows-only

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, _ENTRY_NAME)
    except FileNotFoundError:
        return False
    except OSError:
        log.exception("could not read autostart entry; assuming not enabled")
        return False
    try:
        stored = Path(str(value).strip().strip('"')).resolve()
    except OSError:
        return False
    return stored == _executable_path()


def enable() -> None:
    """Add (or overwrite) the autostart entry to point at this EXE.

    Overwrite-on-enable is intentional: if the user moved the
    install folder between launches, the new ``sys.executable`` path
    gets written, and the stale registry entry is replaced rather
    than left orphaned.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import winreg

    exe = _executable_path()
    # The Run key expects a command line. Quote the path so spaces in
    # the install folder (``C:\Program Files\...``) work; no
    # arguments needed — the overlay takes none.
    value = f'"{exe}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, _ENTRY_NAME, 0, winreg.REG_SZ, value)
        log.info("autostart enabled: %s = %s", _ENTRY_NAME, value)
    except OSError:
        log.exception("could not write autostart entry")


def disable() -> None:
    """Remove the autostart entry. No-op if it isn't there."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _ENTRY_NAME)
        log.info("autostart disabled: removed %s", _ENTRY_NAME)
    except FileNotFoundError:
        # Already absent — that's what the caller asked for.
        return
    except OSError:
        log.exception("could not delete autostart entry")


def enable_default_if_first_run(state_dir: Path) -> bool:
    """Enable autostart automatically on the *first* launch only.

    Default-on behaviour the project owner asked for: shipping a
    fresh EXE to a friend should land with "Start with Windows"
    already ticked, so they don't have to discover the gear-menu
    toggle to get the convenient behaviour. The very next time we
    'd otherwise re-enable it, we check a marker file in the user
    state dir — if it's present, we leave the registry alone so a
    deliberate un-tick from the gear menu sticks across launches.

    Returns ``True`` iff we just wrote the registry entry. ``False``
    when:

    * we're on a non-Windows / source build (``supported()`` =
      ``False``);
    * the marker is already present (we've done this before, so the
      current state is whatever the user last asked for);
    * the marker write itself failed (we'd otherwise re-enable on
      every launch, which is the failure mode we're guarding against).

    The marker carries the EXE path that we enabled, purely for
    diagnostics — comparing it to the running EXE path tells you
    whether the user moved the install dir since the seed.
    """
    if not supported():
        return False
    marker = state_dir / _SEEDED_MARKER
    if marker.exists():
        return False
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        enable()
        # Write the marker AFTER enable() so a registry-write failure
        # doesn't leave us in "marker says we did, registry says we
        # didn't" — the next launch can retry cleanly.
        marker.write_text(str(_executable_path()), encoding="utf-8")
    except OSError as exc:
        # Disk full / permissions / antivirus blocking the write —
        # log and bail; the user can still flip the toggle manually
        # from the gear menu, and the next launch will retry.
        log.warning("could not write autostart marker %s: %s", marker, exc)
        return False
    log.info("autostart: default-enabled on first launch (marker at %s)", marker)
    return True
