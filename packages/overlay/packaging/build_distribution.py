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
import shutil
import subprocess
import sys
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


def _swap_to_cpu_xgboost() -> None:
    """Replace the installed xgboost wheel with xgboost-cpu.

    ``uv pip install`` happily installs the alternative wheel over
    the top of the existing one — they share the ``xgboost`` import
    name but ship different ``xgboost.dll`` payloads.
    """
    _run([_uv(), "pip", "uninstall", "xgboost"], check=False)
    _run([_uv(), "pip", "install", f"xgboost-cpu=={XGBOOST_VERSION}"])


def _restore_cuda_xgboost() -> None:
    """Reinstate the standard xgboost wheel after the build.

    We uninstall xgboost-cpu first (best-effort — on Windows, leftover
    dist-info from the original install sometimes blocks the uninstall;
    that's recoverable on the next ``uv sync``). Then explicitly
    install the pinned xgboost version so the venv is in its
    pre-build state.
    """
    _run([_uv(), "pip", "uninstall", "xgboost-cpu"], check=False)
    _run([_uv(), "pip", "install", f"xgboost=={XGBOOST_VERSION}"])


def _clean_previous_build() -> None:
    """Remove stale PyInstaller outputs so the next pass starts fresh.

    PyInstaller's own ``--clean`` flag has historically failed on
    Windows when leftover ``localpycs/`` is locked by an antivirus
    scanner; deleting upfront sidesteps that.
    """
    for path in (REPO_ROOT / "dist" / "MulliganCoach", REPO_ROOT / "build" / "mulligan_coach"):
        if path.exists():
            print(f"removing {path}", flush=True)
            shutil.rmtree(path, ignore_errors=True)


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
        print(f"\nBuild complete. Bundle at: {dist_dir}", flush=True)
        return 0
    print("\n!!! Build finished but dist/MulliganCoach is missing.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
