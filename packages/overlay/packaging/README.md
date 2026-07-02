# Overlay packaging

Builds the Windows distribution of Mulligan Coach: a single folder
containing `MulliganCoach.exe` plus the bundled Python runtime,
PyQt6, XGBoost, the choice_prod model, parsed-cards JSON, and 17Lands
ratings parquets. The folder is what gets zipped and sent to a
friend.

## Build

The end-to-end wrapper handles swapping the venv to `xgboost-cpu` for
the build and restoring `xgboost` (with CUDA) afterwards — see the
size-reduction notes below for why this matters:

```
.venv/Scripts/python.exe packages/overlay/packaging/build_distribution.py
```

Output: `dist/MulliganCoach/` (~325 MB). `MulliganCoach.exe` is the
launcher; everything else lives next to it in `_internal/`.

The wrapper is idempotent — re-running it is safe even after an
interrupted build (it cleans `build/` and the dist subfolder before
re-running PyInstaller). If you'd rather drive PyInstaller directly,
the spec also works standalone:

```
.venv/Scripts/pyinstaller.exe packages/overlay/packaging/mulligan_coach.spec --noconfirm
```

…but in that case you'd ship the larger CUDA-flavoured xgboost.dll
(~143 MB on top of the standard bundle) unless you've already swapped
to `xgboost-cpu` manually.

Re-run after any change to overlay code, the choice model, the
parsed cards, or the 17Lands ratings. The spec is deterministic
given those inputs.

## Layout produced

```
dist/MulliganCoach/
├── MulliganCoach.exe                                       # entry point
└── _internal/
    ├── <Python runtime, Qt, numpy, xgboost, ...>
    ├── data/processed/parsed_cards/{TLA,TMT,ECL,SOS}.json
    ├── data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet
    └── models/choice_prod/{xgboost.json, metadata.json, sweep_results.json}
```

The frozen-mode shim in
`mulligan_coach_overlay._frozen.configure_bundle_paths` sets
`MULLIGAN_COACH_DATA_ROOT` and `MULLIGAN_COACH_CHOICE_MODEL_DIR`
to subdirectories of `sys._MEIPASS` (= `_internal/`) at startup,
so the upstream path resolvers in `cards/` and `recommend/` find
the bundled copies without any source-code changes.

## Sharing the result

```
cd dist
powershell Compress-Archive -Path MulliganCoach -DestinationPath MulliganCoach.zip
```

Send the ZIP. The recipient extracts it and runs `MulliganCoach.exe`.
No installer, no Python install, no virtualenv on their machine.

On first launch Windows SmartScreen will warn that the binary is
unrecognised — "More info" → "Run anyway". A real code-signing
certificate would silence this; not worth it for a friends-and-
family drop.

## Runtime logs

The shipped EXE is GUI-only (no console window). Logs go to
`%LOCALAPPDATA%\MulliganCoach\logs\overlay.log` (rotating, 5 MB ×
3 generations). Ask the recipient to attach this file when
reporting issues.

## Size reduction (vs. unrestricted PyInstaller default)

A naive bundle weighs ~520 MB. The current pipeline trims it to
~325 MB with no behavioural changes:

* **`xgboost-cpu` swap (~138 MB).** The PyPI `xgboost` wheel ships
  a 143 MB `xgboost.dll` carrying full CUDA build artifacts. We
  never invoke the GPU code path (training uses `tree_method=hist`;
  inference uses Booster.predict). `build_distribution.py`
  temporarily swaps the venv to `xgboost-cpu` (5 MB DLL, same Python
  API) for the PyInstaller pass, then reinstates `xgboost` after.
  Doing this transparently around the build keeps day-to-day
  `uv sync` semantics unchanged.
* **DuckDB excluded (~36 MB).** DuckDB is only used by the training
  / data-download paths. `seventeenlands_stats` now reads ratings
  parquets via `pyarrow.parquet` directly, and `training_rows` /
  `feature_matrix` lazy-import duckdb inside their training entry
  points. The spec then lists `duckdb` under `EXCLUDED_MODULES`.
* **pyarrow optional natives stripped (~22 MB).** Arrow Flight,
  Substrait, Acero, and Dataset DLLs are shipped inside the
  `pyarrow/` wheel directory and copied by PyInstaller even though
  the overlay only calls `pq.read_table`. The spec has a
  post-Analysis filter (`_BINARY_PATTERNS_TO_DROP`) that removes
  them from `a.binaries` / `a.datas`.
* **Qt6 non-English translations stripped (~7 MB).** We don't
  localise the UI, so `Qt6/translations/qt_*.qm` files for every
  locale Qt supports are deadweight. The spec keeps `qt_en.qm` /
  `qtbase_en.qm` as a fallback and drops the rest.

If you want to claw back more, the next-cheapest target is pyarrow
itself (~58 MB left after the optional-native pass): the bundle still
ships the full Arrow C++ runtime via `arrow.dll` plus the Parquet
DLL, which we genuinely need. A custom path using only the parquet
read functions doesn't exist in pure-Python form, so a meaningful
trim there means writing a one-shot loader using `fastparquet` or
similar. Not worth it for friends-and-family.

## Known limitations

* **Single-platform (Windows).** PyInstaller builds for the host
  OS. Cross-compilation is not supported by PyInstaller itself;
  a macOS / Linux build would need to run on that platform.
* **No code signing.** The EXE trips Windows SmartScreen on a
  fresh download. Adding a signing certificate would silence the
  warning but isn't a priority for casual distribution.

## Updating

When any of the bundled inputs change — overlay code, the choice
model, parsed cards, ratings — rebuild and re-share the ZIP.
There's no automatic update mechanism. Future work: an Inno Setup
installer wrapping the directory would give the user a real
Programs-and-Features entry plus a Start-menu shortcut, but the
plain ZIP is fine for one or two recipients.
