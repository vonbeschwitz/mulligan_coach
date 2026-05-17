"""Poll-based tailer for Arena's ``Player.log``.

Reads new bytes from the file, slices the stream into blocks
delimited by Arena's ``[UnityCrossThreadLogger]`` headers, finds any
JSON payloads inside each block, and recognises the JSON shapes that
carry the events we care about. Yields typed :class:`LogEvent`
instances; doesn't talk to Qt or to the recommender.

Why polling and not :mod:`watchdog`
-----------------------------------

The official 17Lands client (``mtga_follower.py``) polls with the
same 0.5 s interval — Arena's log gets so many writes that
inotify-style per-event callbacks are actually more overhead than a
periodic ``read()``. Polling also makes log-rotation handling
trivial: every poll cycle ``stat()``s the file, and if it shrank we
just re-open from the start.

Why anchor on ``[UnityCrossThreadLogger]``
------------------------------------------

Arena's logger emits a timestamped header for every event before the
JSON body. That gives us a natural framing: everything between two
consecutive headers is one logical entry. The trailing partial
"block" (between the last header and the current EOF) is held in the
buffer until the next header arrives — which guarantees the JSON
inside is fully written before we try to parse it. The block-split
approach is what the 17Lands client uses; we follow it for the same
robustness reasons.

Event shapes recognised
-----------------------

The tailer walks the parsed JSON tree looking for known keys and
ignores everything else (so the log can contain new message types
we don't model yet without breaking the parser):

* ``greToClientEvent.greToClientMessages[].type ==
  "GREMessageType_GameStateMessage"`` → updates internal instance→grpId
  map, sets seat / starting-team, and (when a player's
  ``pendingMessageType`` is ``"ClientMessageType_MulliganResp"``) emits
  :class:`MulliganDecisionRequest`.
* ``matchGameRoomStateChangedEvent.gameRoomInfo.stateType ==
  "MatchGameRoomStateType_MatchCompleted"`` → emits :class:`MatchEnded`
  and resets per-match parse state.
* Any nested ``MainDeck`` key with a list value (handles
  ``Event_SetDeck`` payloads and the GRE ``SubmitDeckReq`` shape
  uniformly) → emits :class:`DeckSubmitted`. The search recurses into
  string-encoded JSON values to handle the
  ``params.deck = "{\\"MainDeck\\":...}"`` escape pattern Arena uses
  for outbound client events.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Event
from typing import Any

from .events import DeckSubmitted, LogEvent, MatchEnded, MulliganDecisionRequest

log = logging.getLogger(__name__)

# Lines starting with this marker delimit logical entries in Player.log.
# The Arena Unity logger emits a timestamped header before each event's
# JSON body, e.g. ``[UnityCrossThreadLogger]3/15/2026 7:23:00 PM``.
_HEADER_MARKER = "[UnityCrossThreadLogger]"

# Default poll interval for the tailing loop. Matches the 17Lands
# client's value; proven safe and ~negligible CPU.
_DEFAULT_POLL_INTERVAL = 0.5

# Cap on the rolling buffer between drains. Arena's largest game-state
# JSON is on the order of a few hundred KB; 2 MB comfortably absorbs
# the worst case and prevents a runaway log from eating memory.
_MAX_BUFFER = 2_000_000


class LogTailer:
    """Tail Arena's Player.log and yield typed mulligan/deck events.

    Parse state (``instance_to_grp`` map, ``seat_id``,
    ``starting_team_id``) accumulates across the GameStateMessages in a
    single match and is reset on :class:`MatchEnded`. That mirrors how
    Arena itself communicates: per-match the GRE sends incremental
    state, and there's no expectation that any one message contains a
    full snapshot.

    Not thread-safe. Use one instance per consumer thread; the GUI's
    QThread wraps :meth:`tail` and re-emits via a pyqtSignal.
    """

    def __init__(
        self,
        path: Path,
        *,
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
        start_at_end: bool = True,
    ) -> None:
        """Initialise the tailer.

        ``start_at_end=True`` (the default for live usage) skips past
        historical content on first open so we only react to the
        current / next match. Tests and fixture replay pass
        ``start_at_end=False`` to read the whole file from the start.
        """
        self.path = path
        self.poll_interval = poll_interval
        self.start_at_end = start_at_end
        # Per-match parse state; reset by _reset_match_state().
        self._instance_to_grp: dict[int, int] = {}
        self._seat_id: int | None = None
        self._starting_team_id: int | None = None
        # Dedupe key for mulligan decisions. The same decision can
        # show up in multiple GameStateMessages as Arena resends state;
        # we emit at most once per (seat, mulligan_count, hand_tuple).
        self._last_mulligan_key: tuple[int, int, tuple[int, ...]] | None = None

    # ---------------------------------------------------------------
    # Public entry: streaming tail
    # ---------------------------------------------------------------

    def tail(
        self,
        *,
        follow: bool = True,
        stop_event: Event | None = None,
    ) -> Iterator[LogEvent]:
        """Yield :class:`LogEvent` instances as they appear in the log.

        ``follow=False`` reads to EOF and stops — used for replay of
        captured logs and by the test suite.

        ``stop_event`` is checked between polls; consumers running the
        tailer on a background thread set it to terminate cleanly.

        The generator opens the file lazily so the headless CLI can
        wait for Arena to start writing it (the user may have launched
        the overlay before Arena). It blocks (polling) until the file
        appears.
        """
        buffer = ""
        f = None
        last_size = 0
        try:
            while True:
                # Open / reopen if needed.
                if f is None:
                    if not self.path.exists():
                        if not follow:
                            return
                        if self._should_stop(stop_event):
                            return
                        time.sleep(self.poll_interval)
                        continue
                    f = self.path.open("r", encoding="utf-8", errors="replace")
                    if self.start_at_end and follow:
                        f.seek(0, 2)  # 2 = end-of-file
                    last_size = self.path.stat().st_size
                    buffer = ""

                chunk = f.read()
                if chunk:
                    buffer += chunk
                    events, buffer = self._drain(buffer, final=False)
                    yield from events
                    # Cap the LEFTOVER (post-drain) buffer. Capping
                    # pre-drain would discard valid complete blocks
                    # whenever a single ``f.read()`` returns more than
                    # ``_MAX_BUFFER`` bytes (e.g. start_at_end=False
                    # against a multi-MB historical log).
                    if len(buffer) > _MAX_BUFFER:
                        log.warning(
                            "log buffer leftover exceeded %d bytes; truncating",
                            _MAX_BUFFER,
                        )
                        buffer = buffer[-_MAX_BUFFER:]
                    # File vanished mid-read? It'll be reopened next loop;
                    # we just skip the size refresh this cycle.
                    with contextlib.suppress(FileNotFoundError):
                        last_size = self.path.stat().st_size
                    continue

                # EOF reached.
                if not follow:
                    events, _ = self._drain(buffer, final=True)
                    yield from events
                    return
                if self._should_stop(stop_event):
                    events, _ = self._drain(buffer, final=True)
                    yield from events
                    return

                # Live tailing: poll for new data, watch for rotation.
                try:
                    current_size = self.path.stat().st_size
                except FileNotFoundError:
                    log.info("log file disappeared; will reopen when it returns")
                    f.close()
                    f = None
                    time.sleep(self.poll_interval)
                    continue
                if current_size < last_size:
                    # Truncation / rotation. Re-open from the new start.
                    log.info(
                        "log truncated (%d -> %d bytes); reopening",
                        last_size,
                        current_size,
                    )
                    f.close()
                    f = None
                    # Force start-at-end off for the reopen so we don't
                    # skip the new match's setup.
                    self.start_at_end = False
                    continue
                last_size = current_size
                time.sleep(self.poll_interval)
        finally:
            if f is not None:
                f.close()

    # ---------------------------------------------------------------
    # Buffer draining: split blocks, extract JSON, classify
    # ---------------------------------------------------------------

    def _drain(self, buffer: str, *, final: bool) -> tuple[list[LogEvent], str]:
        """Pull complete blocks out of ``buffer``; return (events, tail).

        ``final=True`` flushes the trailing block too — used at EOF
        when ``follow=False``. Otherwise the trailing block is kept in
        the buffer because more bytes may still arrive for it.
        """
        parts = buffer.split(_HEADER_MARKER)
        # parts[0] is junk before the first header (initial header
        # markers or leftover from a previous drain). We discard it
        # because anything before a header isn't part of a logical
        # entry we want to parse.
        if len(parts) <= 1:
            # No headers in the buffer yet. Keep everything.
            return [], buffer

        if final:
            complete_blocks = parts[1:]
            leftover = ""
        else:
            complete_blocks = parts[1:-1]
            leftover = _HEADER_MARKER + parts[-1]

        events: list[LogEvent] = []
        for body in complete_blocks:
            # Re-prefix the header so the block contains its own
            # timestamp — useful for debugging if we ever log the
            # offending block on a parse error.
            events.extend(self._process_block(_HEADER_MARKER + body))
        return events, leftover

    def _process_block(self, block: str) -> Iterator[LogEvent]:
        """Find every JSON object in ``block`` and dispatch on each."""
        decoder = json.JSONDecoder()
        i = 0
        while i < len(block):
            start = block.find("{", i)
            if start < 0:
                return
            try:
                obj, end = decoder.raw_decode(block, start)
            except json.JSONDecodeError:
                # Malformed (or string-mode "{" that isn't actually
                # JSON). Skip this char and keep scanning.
                i = start + 1
                continue
            try:
                yield from self._extract_events(obj)
            except Exception:
                log.exception("failed to extract events from JSON object")
            i = end

    # ---------------------------------------------------------------
    # Event extraction
    # ---------------------------------------------------------------

    def _extract_events(self, obj: Any) -> Iterator[LogEvent]:
        """Walk one parsed JSON object for known event shapes."""
        if not isinstance(obj, dict):
            return

        # 1. GRE messages (server → client).
        gre = obj.get("greToClientEvent")
        if isinstance(gre, dict):
            messages = gre.get("greToClientMessages")
            if isinstance(messages, list):
                for msg in messages:
                    if isinstance(msg, dict):
                        yield from self._handle_gre_message(msg)

        # 2. Match-room state change (covers match end).
        room = obj.get("matchGameRoomStateChangedEvent")
        if isinstance(room, dict):
            yield from self._handle_match_room_state(room)

        # 3. Deck submission. The MainDeck list can show up at many
        # nesting levels and inside string-encoded JSON; walk the
        # whole tree to find it.
        for arena_ids in _find_maindecks(obj):
            yield DeckSubmitted(arena_ids=arena_ids)

    def _handle_gre_message(self, msg: dict[str, Any]) -> Iterator[LogEvent]:
        """Dispatch one GRE message; only GameStateMessage is wired today."""
        msg_type = msg.get("type")
        # systemSeatIds[0] tells us which seat this message was sent
        # TO. Arena always sends the local player their own seat first,
        # so this is the local player's seat — the same value used by
        # the 17Lands client.
        seat_ids = msg.get("systemSeatIds")
        if isinstance(seat_ids, list) and seat_ids:
            first = seat_ids[0]
            if isinstance(first, int):
                self._seat_id = first

        if msg_type == "GREMessageType_GameStateMessage":
            gsm = msg.get("gameStateMessage")
            if isinstance(gsm, dict):
                yield from self._handle_game_state_message(gsm)

    def _handle_game_state_message(self, gsm: dict[str, Any]) -> Iterator[LogEvent]:
        """Update parse state from a GameStateMessage; emit if mulligan-time."""
        # Accumulate game-object → grpId mappings. Arena sends
        # incremental updates: a single object can appear with grpId in
        # one message, then later messages reference only its
        # instanceId. We keep the running map keyed by instanceId.
        game_objects = gsm.get("gameObjects")
        if isinstance(game_objects, list):
            for go in game_objects:
                if not isinstance(go, dict):
                    continue
                iid = go.get("instanceId")
                grp = go.get("grpId")
                if isinstance(iid, int) and isinstance(grp, int):
                    self._instance_to_grp[iid] = grp

        # Capture starting team once per match (set by the first
        # turnInfo to land with activePlayer populated).
        turn_info = gsm.get("turnInfo")
        if isinstance(turn_info, dict) and self._starting_team_id is None:
            active = turn_info.get("activePlayer")
            if isinstance(active, int):
                self._starting_team_id = active

        # Check whether the local player is currently being asked to
        # decide on a mulligan.
        players = gsm.get("players")
        if not isinstance(players, list):
            return
        deciding_player: dict[str, Any] | None = None
        opp_mulligan: int | None = None
        for p in players:
            if not isinstance(p, dict):
                continue
            seat = p.get("systemSeatNumber")
            if (
                p.get("pendingMessageType") == "ClientMessageType_MulliganResp"
                and seat == self._seat_id
            ):
                deciding_player = p
            elif seat != self._seat_id:
                mc = p.get("mulliganCount")
                if isinstance(mc, int):
                    opp_mulligan = mc
        if deciding_player is None:
            return

        mulligan_count = deciding_player.get("mulliganCount", 0)
        if not isinstance(mulligan_count, int):
            mulligan_count = 0

        # Pull the local player's hand zone.
        zones = gsm.get("zones")
        if not isinstance(zones, list):
            return
        hand_object_ids: list[int] = []
        for z in zones:
            if not isinstance(z, dict):
                continue
            if (
                z.get("type") == "ZoneType_Hand"
                and z.get("ownerSeatId") == self._seat_id
                and isinstance(z.get("objectInstanceIds"), list)
            ):
                hand_object_ids = [iid for iid in z["objectInstanceIds"] if isinstance(iid, int)]
                break
        if not hand_object_ids:
            return

        # Map instance IDs → grpIds. Skip any we don't yet have a
        # mapping for (shouldn't happen in a well-formed log, but
        # defensive: drop unknowns rather than crash).
        hand_grps: list[int] = []
        for iid in hand_object_ids:
            grp = self._instance_to_grp.get(iid)
            if grp is not None:
                hand_grps.append(grp)
        if len(hand_grps) != len(hand_object_ids):
            log.debug(
                "hand has %d instance ids but only %d resolved to grpIds; skipping",
                len(hand_object_ids),
                len(hand_grps),
            )
            return

        # Dedupe: skip if we already emitted this exact decision.
        key = (self._seat_id or 0, mulligan_count, tuple(hand_grps))
        if self._last_mulligan_key == key:
            return
        self._last_mulligan_key = key

        on_the_play = (
            self._starting_team_id is not None
            and self._seat_id is not None
            and self._seat_id == self._starting_team_id
        )
        yield MulliganDecisionRequest(
            hand_arena_ids=hand_grps,
            mulligan_count=mulligan_count,
            on_the_play=on_the_play,
            opp_mulligan_count=None if on_the_play else opp_mulligan,
            seat_id=self._seat_id or 0,
        )

    def _handle_match_room_state(self, room: dict[str, Any]) -> Iterator[LogEvent]:
        """Emit :class:`MatchEnded` (and reset state) on match completion."""
        info = room.get("gameRoomInfo")
        if not isinstance(info, dict):
            return
        state = info.get("stateType")
        if state == "MatchGameRoomStateType_MatchCompleted":
            self._reset_match_state()
            yield MatchEnded()

    # ---------------------------------------------------------------
    # State management
    # ---------------------------------------------------------------

    def _reset_match_state(self) -> None:
        """Clear per-match parse state. Called on :class:`MatchEnded`."""
        self._instance_to_grp.clear()
        self._seat_id = None
        self._starting_team_id = None
        self._last_mulligan_key = None

    @staticmethod
    def _should_stop(stop_event: Event | None) -> bool:
        return stop_event is not None and stop_event.is_set()


# ---------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------


def _find_maindecks(obj: Any) -> Iterator[list[int]]:
    """Recursively yield every ``MainDeck`` list found anywhere in ``obj``.

    Handles:

    * Nested dicts / lists — the deck can sit at almost any depth
      depending on which envelope wraps it.
    * String-encoded JSON values. Arena's outbound ``Event_SetDeck``
      payload puts the deck inside a JSON-encoded string field
      (``params.deck = "{\\"MainDeck\\":...}"``); we ``json.loads`` any
      string value that looks like it might be JSON and recurse.

    Yields the deck as a fully-expanded list of arena_ids — every copy
    listed individually, in MainDeck order — for direct consumption
    as a :class:`DeckSubmitted` payload.
    """
    if isinstance(obj, dict):
        md = obj.get("MainDeck")
        expanded = _expand_maindeck(md) if md is not None else None
        if expanded is not None:
            yield expanded
        for v in obj.values():
            yield from _find_maindecks(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _find_maindecks(item)
    elif isinstance(obj, str):
        # Cheap heuristic: only attempt to decode if it could plausibly
        # be a JSON object/list. Avoids JSON-parsing every string field
        # in the log (which would be a lot of wasted work).
        stripped = obj.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                inner = json.loads(obj)
            except (json.JSONDecodeError, ValueError):
                return
            yield from _find_maindecks(inner)


def _expand_maindeck(md: Any) -> list[int] | None:
    """Turn one ``MainDeck`` value into a flat list of arena_ids.

    Accepts the two shapes Arena emits in the wild:

    * ``[{"cardId": 12345, "quantity": 4}, ...]`` — the ``Event_SetDeck``
      form. Keys are sometimes strings (``{"cardId": "12345",
      "quantity": "4"}``); we coerce.
    * ``[12345, 12345, 12345, ...]`` — the ``GREMessageType_SubmitDeckReq``
      ``deckCards`` form. One int per copy, already expanded.

    Returns ``None`` for a shape we don't recognise — the caller
    silently ignores those (we'd rather miss a deck-submit than emit
    a junk one).
    """
    if not isinstance(md, list) or not md:
        return None

    # Shape 2: flat list of ints.
    if all(isinstance(x, (int, str)) and _coerce_int(x) is not None for x in md):
        out: list[int] = []
        for x in md:
            val = _coerce_int(x)
            if val is None:
                return None
            out.append(val)
        return out

    # Shape 1: list of {cardId / id, quantity} dicts.
    if all(isinstance(x, dict) for x in md):
        out = []
        for entry in md:
            cid = entry.get("cardId", entry.get("id"))
            qty = entry.get("quantity", entry.get("Quantity", 1))
            cid_int = _coerce_int(cid)
            qty_int = _coerce_int(qty)
            if cid_int is None or qty_int is None or qty_int < 1:
                return None
            out.extend([cid_int] * qty_int)
        return out

    return None


def _coerce_int(x: Any) -> int | None:
    """Best-effort int coercion. Handles JSON ints, strs, and ``None``."""
    if isinstance(x, bool):
        # bool is a subclass of int but never a sensible card id.
        return None
    if isinstance(x, int):
        return x
    if isinstance(x, str):
        try:
            return int(x.strip())
        except ValueError:
            return None
    return None
