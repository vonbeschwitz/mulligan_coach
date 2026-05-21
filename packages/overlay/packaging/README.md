# Overlay packaging

Builds the Windows distribution of Mulligan Coach: a single folder
containing `MulliganCoach.exe` plus the bundled Python runtime,
PyQt6, XGBoost, the choice_v6 model, parsed-cards JSON, and 17Lands
ratings parquets. The folder is what gets zipped and sent to a
friend.

## Build

```
.venv/Scripts/pyinstaller.exe packages/overlay/packaging/mulligan_coach.spec --noconfirm
```

Output: `dist/MulliganCoach/` (~520 MB). `MulliganCoach.exe` is the
launcher; everything else lives next to it in `_internal/`.

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
    └── models/choice_v6/{xgboost.json, metadata.json, sweep_results.json}
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

## Known limitations

* **Bundle size (~520 MB).** XGBoost's `xgboost.dll` is 137 MB on
  its own — Windows wheels ship with full CUDA build artifacts
  even when only the CPU path is used. Switching to the
  `xgboost-cpu` wheel would trim the bundle by ~120 MB; not done
  yet because the project's training scripts may want GPU support
  later and we don't want two divergent install closures.
* **DuckDB is bundled (~50 MB) but unused at runtime.** The
  `seventeenlands_stats` and `feature_matrix` modules have
  top-level `import duckdb` even on the read-only ratings-parquet
  code path. Lazy-importing it would shave the bundle further;
  filed under "later".
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
