# overlay — Claude instructions

## Purpose

PyQt6 always-on-top transparent overlay over MTG Arena. Tails
Arena's `Player.log`, detects mulligan decisions (and the deck
submitted for the match), feeds the data into the shared
`mulligan-coach-recommend` service, and shows a small Keep / Mulligan
verdict pane near the mulligan UI in the Arena window.

Pure read-only: only reads `Player.log`, never touches the Arena
client, never reads Arena's memory. This is the line WotC tolerates
(see project root `CLAUDE.md`).

## Layout

```
src/mulligan_coach_overlay/
├── __init__.py          # Re-exports nothing — top-level import is Qt-free
├── arena_paths.py       # Resolve Player.log on Windows / macOS
├── arena_card_db.py     # Read Arena's local Raw_CardDatabase SQLite for grpId mapping
├── events.py            # Pydantic events: DeckSubmitted, MulliganDecisionRequest, MatchEnded
├── log_tailer.py        # Poll-based tail + block / JSON parsing + event extraction
├── card_index.py        # arena_id -> ParsedCard (MTGJSON + Arena DB merged)
├── coordinator.py       # State machine: events -> CoordinatorOutput
├── headless.py          # CLI: tail + recommend + print verdicts
└── gui.py               # PyQt6 overlay widget
tests/
├── fixtures/            # Captured / synthesized Player.log snippets
└── test_log_tailer.py   # Parser tests; no live Arena needed
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

## Out of scope (for now)

* **Suggesting which card to bottom on a mulligan.** Same reason as
  the website: the model is trained on the 17Lands pre-bottom
  convention.
* **Modifying or reading from the Arena client.** Read-only log
  tailing only. Anything that requires hooking the game process is a
  hard no.
* **Cross-platform GUI parity.** Windows is the primary target. macOS
  PyQt6 transparent-overlay behaviour is messier (window-server quirks
  with `Qt.WindowStaysOnTopHint`); we'll address it if a real macOS
  user shows up.
