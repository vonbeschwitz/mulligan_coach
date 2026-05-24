"""End-to-end distribution build for the Mulligan Coach overlay.

What this does
--------------

1. Cleans previous PyInstaller output (``dist/MulliganCoach/`` and
   ``build/mulligan_coach/``).
2. Temporarily swaps the workspace venv from ``xgboost`` (CUDA
   wheel, ~143 MB DLL) to ``xgboost-cpu`` (~5 MB DLL). Same Python
   API; the overlay only ever calls Booster.predict, so the CUDA
   build is pure deadweight in the shipped bundle.
3. Runs PyInstaller against ``mulligan_coach.spec``.
4. Restores ``xgboost`` so the venv keeps working for normal
   development (training scripts, ``uv sync``, etc.).

The wrapper is best-effort transactional: if step 3 fails, step 4
still runs in a ``finally`` so the venv doesn't end up stuck on the
CPU-only wheel.

Why a Python wrapper and not a .ps1 / .bat?
-------------------------------------------

The project owner isn't a Windows-shell person, and the existing dev
loop is "open the cloned repo, type one command into the integrated
terminal." Keeping the wrapper in Python means it works the same way
from any terminal and reuses the workspace's existing ``.venv``
binaries without needing PowerShell-specific tooling. It also keeps
the swap+restore semantics in one readable file rather than scattered
across shell snippets.

Usage
-----

::

    .venv/Scripts/python.exe packages/overlay/packaging/build_distribution.py

Run from the repo root. Add ``--keep-cuda-xgboost`` if you genuinely
want the bigger CUDA-flavoured xgboost.dll in the bundle (you
probably don't).
"""

from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SPEC_PATH = Path(__file__).resolve().parent / "mulligan_coach.spec"

XGBOOST_VERSION = "3.2.0"
"""Pinned to match the version listed transitively via the model package.

If the workspace ever bumps xgboost in its pyproject.toml, update this
too so the swap installs the matching xgboost-cpu wheel.
"""


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command, streaming its output. Returns the completed process."""
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=check, cwd=REPO_ROOT)


def _uv() -> str:
    """Locate the ``uv`` executable.

    Falls back to a bare ``uv`` so PATH-resolution still works on
    systems where the venv's Scripts dir isn't first on PATH.
    """
    venv_uv = REPO_ROOT / ".venv" / "Scripts" / "uv.exe"
    return str(venv_uv) if venv_uv.exists() else "uv"


def _pyinstaller() -> str:
    """Path to the workspace's pyinstaller entry point."""
    return str(REPO_ROOT / ".venv" / "Scripts" / "pyinstaller.exe")


def _site_packages_dir() -> Path:
    """Resolve the workspace venv's site-packages directory."""
    return REPO_ROOT / ".venv" / "Lib" / "site-packages"


def _purge_stale_dist_info(*pkg_names: str, retries: int = 10, delay_seconds: float = 1.0) -> None:
    """Delete any leftover ``<pkg>-*.dist-info`` directories.

    ``uv pip uninstall`` on Windows sometimes leaves a partially-
    emptied dist-info dir behind: ``METADATA`` is deleted but the
    directory itself can't be removed because antivirus / Explorer /
    Windows Defender still holds a handle on a sibling ``.dll`` or
    on the dir itself. The next ``uv pip install`` then fails
    because it sees a dist-info dir without a METADATA file.

    Retry a handful of times with short sleeps — handles typically
    release within 1–3 seconds once the launching process exits.
    """
    site = _site_packages_dir()
    if not site.exists():
        return

    def _matches(name: str) -> bool:
        normalised = {p.replace("-", "_") for p in pkg_names} | set(pkg_names)
        return any(name.startswith(f"{p}-") and name.endswith(".dist-info") for p in normalised)

    for attempt in range(retries):
        stuck = []
        for child in site.iterdir():
            if not child.is_dir() or not _matches(child.name):
                continue
            try:
                shutil.rmtree(child)
            except OSError:
                stuck.append(child)
        if not stuck:
            return
        if attempt == retries - 1:
            print(
                f"!!! could not remove dist-info dirs after {retries} attempts: "
                + ", ".join(str(p) for p in stuck),
                file=sys.stderr,
            )
            return
        time.sleep(delay_seconds)


def _swap_to_cpu_xgboost() -> None:
    """Replace the installed xgboost wheel with xgboost-cpu.

    ``uv pip install`` happily installs the alternative wheel over
    the top of the existing one — they share the ``xgboost`` import
    name but ship different ``xgboost.dll`` payloads. We sweep any
    stale dist-info dirs first so a previously failed uninstall
    doesn't keep the install from going through.
    """
    _run([_uv(), "pip", "uninstall", "xgboost"], check=False)
    _purge_stale_dist_info("xgboost", "xgboost-cpu")
    _run([_uv(), "pip", "install", f"xgboost-cpu=={XGBOOST_VERSION}"])


def _restore_cuda_xgboost() -> None:
    """Reinstate the standard xgboost wheel after the build.

    Same stub-cleanup as ``_swap_to_cpu_xgboost`` — uninstalling
    xgboost-cpu can leave its dist-info as a stub if the freshly-
    built EXE's DLL handle hasn't been released by the OS yet.
    """
    _run([_uv(), "pip", "uninstall", "xgboost-cpu"], check=False)
    _purge_stale_dist_info("xgboost", "xgboost-cpu")
    _run([_uv(), "pip", "install", f"xgboost=={XGBOOST_VERSION}"])


def _clean_previous_build() -> None:
    """Remove stale PyInstaller outputs so the next pass starts fresh.

    PyInstaller's own ``--clean`` flag has historically failed on
    Windows when leftover ``localpycs/`` (or `_internal/data/...`)
    is locked by an antivirus scanner / Explorer / Defender;
    deleting upfront sidesteps that, with a short retry loop for
    the locks that just need a moment to release.
    """
    for path in (REPO_ROOT / "dist" / "MulliganCoach", REPO_ROOT / "build" / "mulligan_coach"):
        if not path.exists():
            continue
        print(f"removing {path}", flush=True)
        for attempt in range(10):
            try:
                shutil.rmtree(path)
                break
            except OSError as exc:
                if attempt == 9:
                    print(
                        f"!!! could not remove {path} after retries: {exc}\n"
                        "    PyInstaller will likely fail with a stale-output error;\n"
                        "    close Explorer windows pointing into dist/ and retry.",
                        file=sys.stderr,
                    )
                else:
                    time.sleep(1.0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-cuda-xgboost",
        action="store_true",
        help="Ship the larger CUDA-flavoured xgboost wheel (~+138 MB).",
    )
    args = parser.parse_args()

    _clean_previous_build()

    swapped = False
    try:
        if not args.keep_cuda_xgboost:
            _swap_to_cpu_xgboost()
            swapped = True

        result = subprocess.run(
            [_pyinstaller(), str(SPEC_PATH), "--noconfirm"],
            cwd=REPO_ROOT,
        )
        if result.returncode != 0:
            print(
                f"\n!!! PyInstaller exited with code {result.returncode} — "
                "see build/mulligan_coach/warn-mulligan_coach.txt for details.",
                file=sys.stderr,
            )
            return result.returncode
    finally:
        if swapped:
            _restore_cuda_xgboost()

    dist_dir = REPO_ROOT / "dist" / "MulliganCoach"
    if dist_dir.exists():
        _stamp_bundle_version(dist_dir / "_internal")
        print(f"\nBuild complete. Bundle at: {dist_dir}", flush=True)
        return 0
    print("\n!!! Build finished but dist/MulliganCoach is missing.", file=sys.stderr)
    return 1


def _stamp_bundle_version(internal_dir: Path) -> None:
    """Write ``_bundle_version.txt`` into the bundle's ``_internal`` dir.

    Read by :mod:`mulligan_coach_overlay.user_data` on launch to
    decide whether to re-seed the user dir from the bundle. The
    stamp combines a UTC timestamp (so two builds of the same commit
    still differ) with the short git hash (so the source revision
    is recoverable from a friend's logs).

    Best-effort: if git isn't available we still write a
    timestamp-only stamp, which is enough for the seed comparison.
    """
    if not internal_dir.exists():
        # The COLLECT step should always produce this dir; complain
        # loudly if it didn't so the stamp doesn't silently go missing.
        print(
            f"!!! expected {internal_dir} to exist after the build; "
            "no _bundle_version.txt will be written.",
            file=sys.stderr,
        )
        return

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "nogit"
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    stamp = f"{timestamp}+{commit}"
    (internal_dir / "_bundle_version.txt").write_text(stamp + "\n", encoding="utf-8")
    print(f"stamped _bundle_version.txt = {stamp}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
