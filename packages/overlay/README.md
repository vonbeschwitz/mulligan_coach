# mulligan-coach-overlay

Transparent always-on-top overlay for MTG Arena that surfaces a
keep / mulligan recommendation live, by tailing Arena's `Player.log`.

## Status

Shipped — this is the production surface. PyQt6 overlay window with
Arena-follow behaviour, system tray icon, first-run wizard, data
auto-update + EXE update notification, and the choice-model verdict
via the shared `recommend` service. `packaging/` builds the public
Windows installer. See `CLAUDE.md` for the full feature map.

## Running

```
# One-time, in MTG Arena: Options → Account → enable
#   "Detailed Logs (Plugin Support)".
# Then restart Arena so it actually starts writing the JSON payloads.

uv sync
uv run mulligan-coach-overlay             # the GUI overlay
uv run mulligan-coach-overlay-headless    # tail + print verdicts, no GUI
```

Both commands tail `%USERPROFILE%\AppData\LocalLow\Wizards Of The Coast\MTGA\Player.log`
(override with `MULLIGAN_COACH_OVERLAY_LOG=...`) and prints a one-line
recommendation each time Arena asks the player to keep or mulligan.

## Layout

See `pyproject.toml` for the module map. The short version: pure-stdlib
log tailer feeds typed events into a thin coordinator that calls the
shared `mulligan-coach-recommend` service.
