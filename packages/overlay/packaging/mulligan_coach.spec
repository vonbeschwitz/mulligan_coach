# PyInstaller spec for the Mulligan Coach overlay.
#
# Build with:
#
#     .venv/Scripts/pyinstaller.exe packages/overlay/packaging/mulligan_coach.spec
#
# (Run from the repo root; the relative paths below are resolved
# against ``SPECPATH``, which PyInstaller sets to the spec's
# directory, so ``REPO_ROOT`` below derives the correct workspace
# root regardless of where the build is invoked from.)
#
# Output: ``dist/MulliganCoach/MulliganCoach.exe`` plus a sibling
# ``_internal/`` directory holding Python, Qt, XGBoost's libs, the
# choice_v6 model, parsed_cards JSON, and 17Lands ratings parquets.
# The whole ``dist/MulliganCoach/`` folder is what gets zipped and
# shared.
#
# Why one-folder and not one-file:
#   * One-file mode unpacks every launch to a temp dir — slow startup
#     and triggers AV scanners on each unpack.
#   * One-folder mode lets users see a normal "installed" structure
#     and (later) lets an Inno Setup installer wrap it as a proper
#     Start-menu app with uninstall.
#
# Why we bundle the data + model rather than shipping them separately:
#   * The friend running this isn't going to manage a data folder
#     next to the EXE. Everything ships in one tree, the bundle is
#     self-contained, no ``MULLIGAN_COACH_DATA_ROOT`` setup needed.
#   * Bundle size lands around 200-400 MB (PyQt6 + numpy + xgboost
#     + ~30 MB of data/models). Acceptable for a friends-and-family
#     drop; revisit if we ever ship on a CDN.

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# SPECPATH is injected by PyInstaller as a top-level name (not via
# import). Falls back to the spec file's dir if missing (e.g. running
# the spec under a different analyzer).
try:
    _SPEC_DIR = Path(SPECPATH)  # type: ignore[name-defined]
except NameError:  # pragma: no cover - PyInstaller always defines it
    _SPEC_DIR = Path(__file__).resolve().parent

# Repo layout:
#   packages/overlay/packaging/mulligan_coach.spec
#   packages/overlay/packaging/  -> overlay  -> packages -> <repo>
REPO_ROOT = _SPEC_DIR.parents[2]


def _check_required(path: Path, what: str) -> Path:
    """Raise early if a required input is missing.

    PyInstaller silently bundles whatever's at the given path; if the
    path doesn't exist it just adds nothing and the EXE fails at
    runtime with a confusing "model not loaded" error. Catching it
    here gives the actual reason ("you forgot to train choice_v6").
    """
    if not path.exists():
        raise SystemExit(
            f"PyInstaller spec: {what} not found at {path}.\n"
            f"This input is required to produce a working overlay bundle."
        )
    return path


# ----------------------------------------------------------------------
# Inputs the bundle needs at runtime, sourced from the repo tree.
# Each is shipped at the same relative path inside the bundle so the
# overlay's `_frozen.configure_bundle_paths` (which points at
# ``sys._MEIPASS / "data"`` and ``sys._MEIPASS / "models" / "choice_v6"``)
# finds them.
# ----------------------------------------------------------------------
CHOICE_MODEL_DIR = _check_required(REPO_ROOT / "models" / "choice_v6", "choice_v6 model dir")
PARSED_CARDS_DIR = _check_required(
    REPO_ROOT / "data" / "processed" / "parsed_cards", "parsed_cards dir"
)
RATINGS_DIR = _check_required(
    REPO_ROOT / "data" / "processed" / "seventeenlands" / "ratings",
    "17Lands ratings dir",
)

# Workspace package source roots. Adding these to ``pathex`` lets
# PyInstaller resolve imports of our local packages even when the
# spec is invoked outside an activated venv. (Inside an activated
# ``uv sync``ed venv this is redundant — uv installs each workspace
# package as an editable install pointing at the same dirs — but
# it's cheap belt-and-braces.)
PACKAGE_PATHEX = [str(REPO_ROOT / "packages" / p / "src") for p in (
    "cards",
    "features",
    "model",
    "overlay",
    "recommend",
    "simulation",
)]

# Hidden imports: modules PyInstaller's static analysis might miss
# because they're loaded dynamically (workspace packages, optional
# dependencies, etc). We list every first-party top-level module
# the overlay reaches transitively, plus the C-extension shims
# XGBoost / numpy use that benefit from the explicit hint.
HIDDEN_IMPORTS = [
    "mulligan_coach_cards",
    "mulligan_coach_features",
    "mulligan_coach_model",
    "mulligan_coach_recommend",
    "mulligan_coach_simulation",
    # XGBoost ships its native DLL via a small loader module; the
    # PyInstaller hook usually handles this, but the explicit hint
    # protects against versions where the hook lags upstream.
    "xgboost",
]

# Data payload: (source path on disk, destination inside the bundle).
# Destinations are relative to ``sys._MEIPASS`` at runtime.
DATAS = [
    (str(CHOICE_MODEL_DIR), "models/choice_v6"),
    (str(PARSED_CARDS_DIR), "data/processed/parsed_cards"),
    (str(RATINGS_DIR), "data/processed/seventeenlands/ratings"),
]
# XGBoost ships its native ``xgboost.dll`` (and the version manifest
# JSON next to it) inside the package's ``xgboost/lib/`` subdir, which
# PyInstaller's default binary collection skips. Without this the
# frozen EXE crashes on first import with ``XGBoostLibraryNotFound``
# — ``xgboost.libpath.find_lib_path`` scans relative to the installed
# package dir. ``collect_data_files`` preserves the
# ``xgboost/lib/...`` layout; ``collect_dynamic_libs`` adds the DLL
# under ``binaries`` so PyInstaller's DLL fixup runs against it too.
DATAS.extend(collect_data_files("xgboost"))
BINARIES = collect_dynamic_libs("xgboost")

# ----------------------------------------------------------------------
# Modules we deliberately exclude from the bundle.
# Each saves a non-trivial amount of bundle weight and isn't used by
# the overlay path. Add to this list (don't subtract) — if a real
# import dependency creeps in here later, PyInstaller will fail loudly
# with a missing-module error and we can pull it back out.
# ----------------------------------------------------------------------
EXCLUDED_MODULES = [
    # The website's FastAPI / Jinja2 / starlette tree is huge and the
    # overlay doesn't touch any of it.
    "mulligan_coach_website",
    "fastapi",
    "starlette",
    "jinja2",
    "uvicorn",
    # The data-download package isn't needed at recommend-time.
    "mulligan_coach_data_download",
    # NOTE: duckdb cannot be excluded — `mulligan_coach_cards.seventeenlands_stats`
    # and `mulligan_coach_model.feature_matrix` both `import duckdb` at module
    # top-level, and both modules are reached by the recommend service at
    # startup (via `load_premier_draft_stats` and `_library_from_deck`).
    # Lazy-importing duckdb in those modules would let us drop ~30 MB from
    # the bundle, but that's a refactor for another day.
    # Test runners and dev tools.
    "pytest",
    "_pytest",
    "ruff",
    "mypy",
]


block_cipher = None


a = Analysis(  # noqa: F821 - PyInstaller injects ``Analysis`` at exec time
    [str(_SPEC_DIR / "entry.py")],
    pathex=PACKAGE_PATHEX,
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_MODULES,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MulliganCoach",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # ``console=False`` produces a GUI app (no console window flashing
    # on launch). The overlay's logging still goes to a file rotated
    # under ``%LOCALAPPDATA%\MulliganCoach\`` so we don't lose it.
    # See ``gui.main`` for the logging.basicConfig.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MulliganCoach",
)
