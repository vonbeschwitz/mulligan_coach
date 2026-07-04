# overlay — Claude instructions

## Purpose

PyQt6 transparent overlay over MTG Arena. Tails Arena's `Player.log`,
detects mulligan decisions (and the deck submitted for the match),
feeds the data into the shared `mulligan-coach-recommend` service,
and shows a small Keep / Mulligan verdict pane near the mulligan UI
in the Arena window.

Behaves like untapped.gg's overlay: topmost only when Arena (or the
overlay itself) is the foreground window, follows Arena when it
minimises, hides when Arena exits, and remembers the last submitted
deck across restarts so a launch mid-match doesn't end up with "no
deck loaded". Can be collapsed to a compact pill (verdict + keep% /
mull% only) via a global hotkey or the title bar.

Pure read-only: only reads `Player.log`, never touches the Arena
client, never reads Arena's memory. This is the line WotC tolerates
(see project root `CLAUDE.md`).

## Layout

```
src/mulligan_coach_overlay/
├── __init__.py          # Re-exports nothing — top-level import is Qt-free
├── arena_paths.py       # Resolve Player.log on Windows / macOS
├── arena_card_db.py     # Read Arena's local Raw_CardDatabase SQLite for grpId mapping
│                        #   (auto-locates standalone / Epic / log-derived installs)
├── arena_window.py      # Win32 watcher: Arena foreground / minimised / absent state
├── deck_persistence.py  # Save / load last submitted deck (cross-restart fallback)
├── detailed_logs.py     # Detect Arena's "Detailed Logs (Plugin Support)" setting from the log
├── events.py            # Pydantic events: DeckSubmitted, MulliganDecisionRequest, MatchEnded
├── feedback.py          # Build the "Send feedback" URL (Google Form pre-fill or Issues fallback)
├── first_run.py         # Setup assessment (Arena / Detailed Logs / card DB) + onboarded-state
├── first_run_dialog.py  # Thin Qt wizard dialog over first_run
├── log_tailer.py        # Poll-based tail + block / JSON parsing + event extraction
├── card_index.py        # arena_id -> ParsedCard (MTGJSON + Arena DB merged)
├── coordinator.py       # State machine: events -> CoordinatorOutput
├── headless.py          # CLI: tail + recommend + print verdicts
├── screen_geometry.py   # Clamp a restored window position onto an attached screen
├── tray.py              # System tray icon + menu + manual-launch balloon
└── gui.py               # PyQt6 overlay widget + Arena-follow + collapse hotkey
tests/
├── fixtures/                  # Captured / synthesized Player.log snippets
├── test_arena_card_db.py
├── test_coordinator.py
├── test_deck_persistence.py
└── test_log_tailer.py
```

## Architecture

Three layers, each replaceable without touching the others:

1. **Log tailer** (`log_tailer.py`). Pure-stdlib, no GUI deps. Reads
   bytes from `Player.log`, groups them into blocks delimited by
   `[UnityCrossThreadLogger]` headers, parses any JSON in each
   completed block, and yields typed `LogEvent`s. Same polling cadence
   the 17Lands client uses (0.5 s) — Arena writes the log
   frequently, so polling beats inotify on overhead.

2. **Card index** (`card_index.py`). One-shot load of every persisted
   `ParsedCard`, indexed by `arena_id`. Built once at process start.
   The tailer emits raw arena_ids; the consumer above (headless or
   GUI) uses this to resolve to typed cards before calling the
   recommender.

   Two arena_id sources are merged:

   * `ParsedCard.arena_id` populated from MTGJSON at parse time
     (`packages/cards`). Reliable for older sets but mostly empty for
     a freshly-rotated format (TLA/TMT/ECL/SOS at the time of writing —
     177 of ~1180 cards covered).
   * Arena's local SQLite — `Raw_CardDatabase_<hash>.mtga` inside the
     MTGA install. Authoritative and always-current. Joined to
     ParsedCards by `(set_code, collector_number)`. Adds ~1000 more
     arena_ids on the current rotation — without this source, the
     overlay can't resolve any deck card on a recent set.

     `arena_card_db.default_card_database_path()` locates it
     install-source-agnostically: first the install dir Arena records
     in its own `Player.log` (`Mono path[0] = '.../MTGA_Data/Managed'`
     — covers Epic Games Store, Steam, and custom-drive installs
     automatically), then the well-known standalone
     (`C:\Program Files\Wizards of the Coast\...`) and Epic
     (`C:\Program Files\Epic Games\MagicTheGathering\...`) fallbacks.
     `MULLIGAN_COACH_MTGA_CARDDB` (a file path) still overrides
     everything, and the first-run wizard's folder picker persists a
     `Downloads/Raw` dir (`first_run.json`'s `card_db_dir`) as a last
     resort — stored as the *directory* so it survives a content patch
     renaming the file.

   See `arena_card_db.py` for the SQLite reader. The file is opened
   read-only via `?mode=ro&immutable=1` so we don't compete with the
   game for the file lock.

3. **Coordinator + UI** (`headless.py`, `gui.py`). Wires the tailer
   events to `RecommendationService.recommend_choice` (the production
   choice model; the older `recommend_asymmetric` win-model path is
   legacy and no longer displayed). Holds the "current match deck"
   (from the most recent `DeckSubmitted`), resets on `MatchEnded`, and
   presents the verdict.

4. **Arena window watcher** (`arena_window.py`). Polls Win32 every
   250 ms for Arena's main HWND and emits one of four states —
   `foreground`, `background`, `minimized`, `absent` — whenever it
   changes. The GUI uses this to set its own z-order (Win32
   `SetWindowPos` with `HWND_TOPMOST` / `HWND_NOTOPMOST`),
   minimise / restore in lock-step with Arena, and hide when Arena
   exits. Non-Windows platforms get a constant-`foreground` stub so
   the call sites don't need platform checks.

5. **Deck persistence** (`deck_persistence.py`). Serialises every
   fully-resolved `DeckSubmitted` to
   `%LOCALAPPDATA%\MulliganCoach\last_deck.json`
   (`~/Library/Application Support/MulliganCoach/last_deck.json` on
   macOS, `~/.local/share/mulligan-coach/last_deck.json` on Linux).
   On startup the GUI loads it and seeds the coordinator before the
   tailer starts — so launching the overlay after Arena has already
   submitted the deck falls back to the previous-session deck rather
   than refusing to recommend. Atomic-write via `.tmp` + `replace`
   so a crash mid-write leaves the previous valid file untouched.

## Event flow

```
Player.log -- LogTailer -->  DeckSubmitted              -> store deck
                             MulliganDecisionRequest    -> recommend & display
                             MatchEnded                 -> reset
```

The tailer is a pure generator. Consumers iterate. The GUI runs it
on a `QThread` and forwards events through a `pyqtSignal`.

## Known event shapes

* **Mulligan decision** — a `GREMessageType_GameStateMessage` where
  one player's `pendingMessageType == "ClientMessageType_MulliganResp"`.
  Hand contents read from the player's `ZoneType_Hand`; instance
  ids resolved against the accumulated `gameObjects` map.
* **Deck submitted** — any payload containing a `MainDeck` list,
  either as `[{cardId, quantity}, ...]` (the `Event_SetDeck` form) or
  as a flat int list (the `SubmitDeckReq` form). We recursively walk
  the JSON tree and decode any string-encoded inner JSON to find it.
* **Match ended** — `matchGameRoomStateChangedEvent.gameRoomInfo.stateType
  == "MatchGameRoomStateType_MatchCompleted"`. Resets per-match parse
  state.

When a new event shape appears in the wild (a Bo3 setup, a new
draft-time message, an unfamiliar GRE message kind), add a tiny
fixture under `tests/fixtures/` and extend `_extract_events` to
recognise it. Don't reach for `if "any of these substrings" in
raw_line` — always go through `json.JSONDecoder` so we don't get
fooled by string contents that happen to mention an event name.

## Running

```
# Prereq (one-time): MTG Arena → Options → Account → enable
#   "Detailed Logs (Plugin Support)". Restart Arena.

uv sync
uv run mulligan-coach-overlay-headless          # tail the live log and print events
uv run mulligan-coach-overlay-headless --no-follow --from-start --log path/to/captured.log
```

## Tests

```
uv run pytest packages/overlay
```

All current tests run against fixture files under
`tests/fixtures/` — no live Arena, no Qt context. The fixtures are
hand-constructed minimal JSON that matches the shapes the real log
emits; when we start capturing real-log samples we'll add them here
too (anonymised — strip `clientMetadata` block, screen names, etc.).

## GUI surface

* Two layouts on the same widget:
  * **Expanded panel** — verdict, keep% / mull% (bias-adjusted),
    resolved hand, a "Why this hand plays out the way it does"
    block mirroring the website's playability panel
    (mana base, curve hits, per-card playability table), and a
    debug footer.
  * **Compact pill** — single line: ``verdict · keep% vs mull%``
    at 12 px font (e.g. "Clear keep · 62.5% vs 47.3%"). No labels
    on the percentages — context conveys which is which once the
    user has seen the expanded panel.
  Toggle via the title-bar collapse button, a left double-click
  anywhere on the panel, or the global hotkey **Alt+E** (Win32
  `RegisterHotKey`; registered under the overlay's HWND and routed
  through a `QAbstractNativeEventFilter`). Avoid Ctrl-based
  combos here — Arena's full-control binds Ctrl, and a global
  hotkey on Ctrl+anything ate combos the player needed in-game.
* While the simulation runs, the worker emits a synthetic
  ``ComputingOutput`` (before the blocking ``recommend_choice``
  call) so the panel can flash a "Running simulation…" amber
  state. Lets the user tell the difference between Arena-log
  delivery delay and sim time.
* **Degradation footer.** The choice recommendation's ``degradations``
  (no ratings loaded / partial coverage / set unknown to the model /
  pipeline-version mismatch — see ``packages/recommend/CLAUDE.md``) are
  shown in the expanded panel as a small amber word-wrapped label under
  the context footer (strings joined with ``"  ·  "``; hidden when
  empty). The compact pill has no room for prose, so it appends a
  single ``" ⚠"`` to the verdict text when any degradation is present.
  The stats join is keyed by folded card name, so the overlay's
  ``card_index.py`` arena_id backfill (still needed to resolve grpIds
  from the log) no longer influences feature values — the overlay and
  website now feed the model an identical stats distribution.
* **System tray icon** (`tray.py`). The overlay window hides whenever
  Arena isn't running, and Start-with-Windows is on by default — so
  without a tray icon the app would frequently be running with zero
  visible presence and no way to quit it. The tray icon is permanent
  for the app's lifetime; its right-click menu holds the update-check
  entries, "Setup & troubleshooting…" (first-run wizard), "Send
  feedback…" (see below), Start-with-Windows (mirrors the gear menu),
  and Quit. On a *manual* launch with
  Arena closed it shows a one-shot balloon ("Mulligan Coach is
  running — the overlay will appear when you open MTG Arena") so the
  user knows the launch worked. Autostart launches are identified by
  the `--autostart` flag baked into the registry Run entry
  (`autostart.AUTOSTART_LAUNCH_FLAG`) and stay silent — no balloon at
  every login. `autostart.ensure_entry_current()` migrates pre-flag
  Run entries on launch. The icon itself is drawn programmatically
  (no binary assets in the wheel).
* **EXE update notification — notify-only** (`auto_update/exe_update.py`
  + tray). Separate from the data auto-updater: it never downloads or
  replaces the running executable (owner decision 2026-07-03 — unsigned
  self-update is an AV magnet; full self-update is deferred to Phase 2,
  gated on signing). `ExeUpdateChecker.check` fetches the
  `exe_version.json` sidecar `publish_exe_release.py` uploads to the
  `exe-latest` release and compares its `bundle_version` to the running
  EXE's stamp (`_frozen.running_bundle_version()` reads
  `_internal/_bundle_version.txt`). Comparison is by the timestamp
  prefix (`is_newer_bundle_version`), with a notify-safe "any
  difference ⇒ update" fallback when a stamp can't be parsed. When a
  newer build is published the tray shows a balloon + a "Download
  update…" menu entry that open the **release page** (not a raw ZIP
  download). The tray also gains a manual **"Check for updates"** entry.
  Checks run on a daemon thread (`gui._ExeUpdateController`), ~8 s after
  launch and every 6 h; any failure folds into an `unknown` result and
  never disturbs the overlay (silent on auto-checks, gentle "couldn't
  check" only on a manual one). Disable via
  `MULLIGAN_COACH_EXE_VERSION_URL=""`.
* **First-run wizard** (`first_run.py` + `first_run_dialog.py` + tray).
  The overlay is silent unless three prerequisites hold: Arena has run
  (Player.log exists), **Detailed Logs (Plugin Support)** is enabled
  (detected via the `DETAILED LOGS: ENABLED`/`DISABLED` marker Arena
  writes at session start — see `detailed_logs.py`; falls back to
  "any GRE message ⇒ enabled"), and a `Raw_CardDatabase` is reachable.
  `first_run.assess_setup` produces a `SetupStatus`; the thin
  `FirstRunDialog` renders it with a Detailed-Logs enable guide, a
  Re-check button, and a "Locate Arena install…" folder picker (last
  resort for the card DB). It auto-pops at launch **only** when
  something's wrong *and* the user hasn't been onboarded before —
  `first_run.json`'s `detailed_logs_verified` flips on the first
  all-clear and suppresses auto-popping thereafter (no-nag guarantee).
  The tray's **"Setup & troubleshooting…"** entry re-opens it any time.
  All decision logic is Qt-free + unit-tested; the dialog is thin glue.
* **Feedback channel** (`feedback.py` + tray). The tray's always-visible
  **"Send feedback…"** entry opens a feedback destination in the default
  browser. When the owner has configured a Google Form (the
  `OWNER CONFIGURATION` block in `feedback.py` — form URL + three
  `entry.<id>` field ids), it opens that form pre-filled with the app
  (EXE build) version, the seeded data version, and the OS as
  `entry.<id>=` query params. Until then it falls back to the **public
  data repo's Issues page** (`.../mulligan_coach_data/issues`), so the
  entry is functional before the form exists. URL construction is pure +
  unit-tested (`build_feedback_url`); the `QDesktopServices.openUrl` call
  is the only Qt part, in `tray._open_feedback`. GitHub issue templates
  for the public data repo are staged (not pushed) under
  `packaging/data_repo_files/.github/ISSUE_TEMPLATE/` with copy
  instructions in that dir's README — a one-time owner action.
* The window has no `Qt.WindowStaysOnTopHint` flag at construction
  time. Topmost is set dynamically via Win32 `SetWindowPos` whenever
  the Arena watcher emits `foreground` / `background`. Setting it as
  a Qt flag would force-on topmost any time Qt re-realised the
  window.
* Position is top-right of the primary screen by default; the user
  can drag-to-move and the position is preserved across layout
  toggles. A restored position is clamped onto a currently-attached
  screen (`screen_geometry.clamp_to_screens`) so a spot saved on a
  since-unplugged monitor doesn't strand the (frameless, taskbar-
  hidden) window off every display. A deliberately edge-parked
  position is left untouched — only a stranded one is rescued.

## Out of scope (for now)

* **Suggesting which card to bottom on a mulligan.** Same reason as
  the website: the model is trained on the 17Lands pre-bottom
  convention.
* **Modifying or reading from the Arena client.** Read-only log
  tailing only. Anything that requires hooking the game process is a
  hard no.
* **Cross-platform GUI parity.** Windows is the primary target. The
  Arena window watcher is a no-op stub on macOS / Linux (the
  overlay collapses back to its plain "always topmost" behaviour
  there); the global hotkey is Win32-only. macOS PyQt6
  transparent-overlay behaviour also has window-server quirks we
  haven't addressed because Arena doesn't ship for macOS in
  the form the overlay would target. We'll revisit if a real
  cross-platform user shows up.
