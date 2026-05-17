# mulligan-coach-overlay

Transparent always-on-top overlay for MTG Arena that surfaces a
keep / mulligan recommendation live, by tailing Arena's `Player.log`.

## Status

Foundation only at the moment: the log tailer + headless integration
work today. The PyQt6 GUI pane comes in a follow-up commit.

## Running

```
# One-time, in MTG Arena: Options → Account → enable
#   "Detailed Logs (Plugin Support)".
# Then restart Arena so it actually starts writing the JSON payloads.

uv sync
uv run mulligan-coach-overlay-headless
```

The headless command tails `%LOCALAPPDATA%Low\Wizards Of The Coast\MTGA\Player.log`
(override with `MULLIGAN_COACH_OVERLAY_LOG=...`) and prints a one-line
recommendation each time Arena asks the player to keep or mulligan.

## Layout

See `pyproject.toml` for the module map. The short version: pure-stdlib
log tailer feeds typed events into a thin coordinator that calls the
shared `mulligan-coach-recommend` service.
