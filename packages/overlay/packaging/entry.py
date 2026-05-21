"""PyInstaller entry-point shim.

PyInstaller's ``Analysis`` step takes a Python *script* path (not a
module name), so we provide a tiny script that imports and calls the
overlay GUI ``main``. This decouples the shipped binary's name
(``MulliganCoach.exe``) from the source-tree script entry-point name.

Nothing else lives here on purpose — the entry shim must stay
trivial because PyInstaller's static analysis treats whatever this
module imports as the dependency root of the bundle.
"""

from __future__ import annotations

import sys

from mulligan_coach_overlay.gui import main

if __name__ == "__main__":
    sys.exit(main())
