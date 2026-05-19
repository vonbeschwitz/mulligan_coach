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
├── arena_window.py      # Win32 watcher: Arena foreground / minimised / absent state
├── deck_persistence.py  # Save / load last submitted deck (cross-restart fallback)
├── events.py            # Pydantic events: DeckSubmitted, MulliganDecisionRequest, MatchEnded
├── log_tailer.py        # Poll-based tail + block / JSON parsing + event extraction
├── card_index.py        # arena_id -> ParsedCard (MTGJSON + Arena DB merged)
├── coordinator.py       # State machine: events -> CoordinatorOutput
├── headless.py          # CLI: tail + recommend + print verdicts
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
     MTGA install (default
     `C:\Program Files\Wizards of the Coast\MTGA\MTGA_Data\Downloads\Raw`).
     Authoritative and always-current. Joined to ParsedCards by
     `(set_code, collector_number)`. Adds ~1000 more arena_ids on the
     current rotation — without this source, the overlay can't resolve
     any deck card on a recent set.

   See `arena_card_db.py` for the SQLite reader. The file is opened
   read-only via `?mode=ro&immutable=1` so we don't compete with the
   game for the file lock.

3. **Coordinator + UI** (`headless.py`, `gui.py`). Wires the tailer
   events to `RecommendationService.recommend_asymmetric`. Holds the
   "current match deck" (from the most recent `DeckSubmitted`), resets
   on `MatchEnded`, and presents the verdict.

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
* While the keep arm runs, the worker emits a synthetic
  ``ComputingOutput`` (before the blocking
  ``recommend_asymmetric`` call) so the panel can flash a
  "Running simulation…" amber state. Lets the user tell the
  difference between Arena-log delivery delay and sim time.
* The window has no `Qt.WindowStaysOnTopHint` flag at construction
  time. Topmost is set dynamically via Win32 `SetWindowPos` whenever
  the Arena watcher emits `foreground` / `background`. Setting it as
  a Qt flag would force-on topmost any time Qt re-realised the
  window.
* Position is top-right of the primary screen by default; the user
  can drag-to-move and the position is preserved across layout
  toggles.

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
