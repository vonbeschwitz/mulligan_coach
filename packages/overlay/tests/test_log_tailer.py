"""Unit tests for :mod:`mulligan_coach_overlay.log_tailer`.

All tests run against in-memory log strings written to ``tmp_path``;
no live Arena required. Each test constructs the JSON payloads it
needs as Python dicts (so the schema is visible in the test) and
serialises via :func:`_make_block`.

When real captured logs surface, drop them into ``tests/fixtures/``
and add a regression test that loads them — anonymise screen names
and clientMetadata first.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mulligan_coach_overlay.events import (
    DeckSubmitted,
    MatchEnded,
    MulliganDecisionRequest,
)
from mulligan_coach_overlay.log_tailer import LogTailer, _find_maindecks

# ---------------------------------------------------------------------------
# Helpers — build Arena-shaped log payloads as Python dicts
# ---------------------------------------------------------------------------


def _make_block(payload: Any, *, timestamp: str = "3/15/2026 7:23:00 PM") -> str:
    """One ``[UnityCrossThreadLogger]`` block carrying ``payload``.

    ``payload`` is serialised to JSON. The block is the header line
    followed by the JSON on the next line — the actual Arena format
    sometimes inlines them on one line; the tailer handles either.
    """
    return f"[UnityCrossThreadLogger]{timestamp}\n{json.dumps(payload)}\n"


def _gsm_envelope(gsm: dict[str, Any], *, seat: int = 1) -> dict[str, Any]:
    """Wrap a GameStateMessage body in the GRE envelope Arena uses."""
    return {
        "greToClientEvent": {
            "greToClientMessages": [
                {
                    "type": "GREMessageType_GameStateMessage",
                    "systemSeatIds": [seat],
                    "gameStateMessage": gsm,
                }
            ]
        }
    }


def _hand_gsm(
    *,
    hand_grpids: list[int],
    seat: int = 1,
    mulligan_count: int = 0,
    opp_mulligan_count: int = 0,
    active_player: int = 1,
    pending: bool = True,
    include_game_objects: bool = True,
) -> dict[str, Any]:
    """Construct a GameStateMessage simulating a mulligan decision.

    Hand cards are assigned sequential instanceIds 101.. .
    ``pending=False`` produces an identical state with no
    ``pendingMessageType``, useful for "no decision yet" baselines.
    ``include_game_objects=False`` simulates Arena's incremental-state
    behaviour where a follow-up GSM omits gameObjects already sent.
    """
    instance_ids = [100 + i for i in range(len(hand_grpids))]
    game_objects = [
        {
            "instanceId": iid,
            "grpId": grp,
            "ownerSeatId": seat,
            "type": "GameObjectType_Card",
        }
        for iid, grp in zip(instance_ids, hand_grpids, strict=True)
    ]
    zones = [
        {
            "zoneId": 28,
            "type": "ZoneType_Hand",
            "ownerSeatId": seat,
            "objectInstanceIds": instance_ids,
        },
        {
            "zoneId": 29,
            "type": "ZoneType_Library",
            "ownerSeatId": seat,
            "objectInstanceIds": [],
        },
    ]
    players = [
        {
            "systemSeatNumber": seat,
            "lifeTotal": 20,
            "mulliganCount": mulligan_count,
            **({"pendingMessageType": "ClientMessageType_MulliganResp"} if pending else {}),
        },
        {
            "systemSeatNumber": 2 if seat == 1 else 1,
            "lifeTotal": 20,
            "mulliganCount": opp_mulligan_count,
        },
    ]
    gsm: dict[str, Any] = {
        "type": "GameStateType_Full",
        "zones": zones,
        "players": players,
        "turnInfo": {
            "activePlayer": active_player,
            "phase": "Phase_Beginning",
            "step": "Step_Upkeep",
            "turnNumber": 1,
        },
    }
    if include_game_objects:
        gsm["gameObjects"] = game_objects
    return gsm


def _write_log(tmp_path: Path, body: str) -> Path:
    log_path = tmp_path / "Player.log"
    log_path.write_text(body, encoding="utf-8")
    return log_path


def _tail_all(log_path: Path) -> list[Any]:
    """Drain every event from a static log."""
    tailer = LogTailer(log_path, start_at_end=False)
    return list(tailer.tail(follow=False))


# ---------------------------------------------------------------------------
# Mulligan-decision detection
# ---------------------------------------------------------------------------


class TestMulliganDecision:
    def test_emits_event_with_hand_and_context(self, tmp_path: Path) -> None:
        body = _make_block(_gsm_envelope(_hand_gsm(hand_grpids=list(range(101, 108)))))
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, MulliganDecisionRequest)
        assert ev.hand_arena_ids == list(range(101, 108))
        assert ev.mulligan_count == 0
        assert ev.on_the_play is True  # seat 1 == active_player 1
        assert ev.opp_mulligan_count is None  # masked on the play
        assert ev.seat_id == 1

    def test_on_the_draw_carries_opp_mulligan_count(self, tmp_path: Path) -> None:
        body = _make_block(
            _gsm_envelope(
                _hand_gsm(
                    hand_grpids=[1, 2, 3, 4, 5, 6, 7],
                    seat=2,
                    active_player=1,  # opponent on the play
                    opp_mulligan_count=1,  # opponent mulled once
                ),
                seat=2,
            )
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1
        ev = events[0]
        assert isinstance(ev, MulliganDecisionRequest)
        assert ev.on_the_play is False
        assert ev.opp_mulligan_count == 1
        assert ev.seat_id == 2

    def test_no_event_when_no_pending_mulligan(self, tmp_path: Path) -> None:
        body = _make_block(
            _gsm_envelope(_hand_gsm(hand_grpids=list(range(101, 108)), pending=False))
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert events == []

    def test_mulligan_count_propagates(self, tmp_path: Path) -> None:
        body = _make_block(
            _gsm_envelope(_hand_gsm(hand_grpids=list(range(101, 108)), mulligan_count=2))
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1
        assert events[0].mulligan_count == 2

    def test_accumulated_instance_map_across_messages(self, tmp_path: Path) -> None:
        """Later GSMs may omit gameObjects but still reference the same ids."""
        full = _make_block(
            _gsm_envelope(_hand_gsm(hand_grpids=[201, 202, 203, 204, 205, 206, 207], pending=False))
        )
        partial = _make_block(
            _gsm_envelope(
                _hand_gsm(
                    hand_grpids=[201, 202, 203, 204, 205, 206, 207],
                    pending=True,
                    include_game_objects=False,
                )
            )
        )
        events = _tail_all(_write_log(tmp_path, full + partial))
        assert len(events) == 1
        assert isinstance(events[0], MulliganDecisionRequest)
        assert events[0].hand_arena_ids == [201, 202, 203, 204, 205, 206, 207]

    def test_dedupes_repeated_decision(self, tmp_path: Path) -> None:
        """The same decision can show up in successive GSMs; emit once."""
        body = _make_block(
            _gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7]))
        ) + _make_block(_gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7])))
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Deck submission
# ---------------------------------------------------------------------------


class TestDeckSubmission:
    def test_dict_form_with_quantity(self, tmp_path: Path) -> None:
        """Event_SetDeck shape: list of {cardId, quantity}."""
        payload = {
            "params": {
                "deck": {
                    "DeckId": "abc",
                    "MainDeck": [
                        {"cardId": 12345, "quantity": 4},
                        {"cardId": 12346, "quantity": 3},
                    ],
                }
            }
        }
        events = _tail_all(_write_log(tmp_path, _make_block(payload)))
        decks = [e for e in events if isinstance(e, DeckSubmitted)]
        assert len(decks) == 1
        assert decks[0].arena_ids == [12345, 12345, 12345, 12345, 12346, 12346, 12346]

    def test_dict_form_with_string_keys(self, tmp_path: Path) -> None:
        """Same as above but with quantities as strings (also seen)."""
        payload = {
            "MainDeck": [
                {"cardId": "100", "quantity": "2"},
                {"cardId": "200", "quantity": "1"},
            ]
        }
        events = _tail_all(_write_log(tmp_path, _make_block(payload)))
        decks = [e for e in events if isinstance(e, DeckSubmitted)]
        assert len(decks) == 1
        assert decks[0].arena_ids == [100, 100, 200]

    def test_flat_int_list_form(self, tmp_path: Path) -> None:
        """SubmitDeckReq shape: list of arena_ids already expanded."""
        payload = {
            "submitDeckReq": {
                "deck": {
                    "MainDeck": [1, 1, 1, 2, 3, 3],
                }
            }
        }
        events = _tail_all(_write_log(tmp_path, _make_block(payload)))
        decks = [e for e in events if isinstance(e, DeckSubmitted)]
        assert len(decks) == 1
        assert decks[0].arena_ids == [1, 1, 1, 2, 3, 3]

    def test_string_encoded_inner_json(self, tmp_path: Path) -> None:
        """Outbound events nest the deck as a JSON-encoded string."""
        inner = json.dumps(
            {
                "DeckId": "abc",
                "MainDeck": [{"cardId": 99, "quantity": 1}],
            }
        )
        payload = {"params": {"deck": inner}}
        events = _tail_all(_write_log(tmp_path, _make_block(payload)))
        decks = [e for e in events if isinstance(e, DeckSubmitted)]
        assert len(decks) == 1
        assert decks[0].arena_ids == [99]


class TestFindMaindecksUnit:
    """Cover the recursion in :func:`_find_maindecks` directly."""

    def test_returns_empty_for_no_maindeck(self) -> None:
        assert list(_find_maindecks({"foo": "bar"})) == []

    def test_finds_at_root(self) -> None:
        result = list(_find_maindecks({"MainDeck": [{"cardId": 1, "quantity": 2}]}))
        assert result == [[1, 1]]

    def test_recurses_into_lists(self) -> None:
        result = list(_find_maindecks([{"x": {"MainDeck": [{"cardId": 5, "quantity": 1}]}}]))
        assert result == [[5]]

    def test_ignores_non_jsonish_strings(self) -> None:
        # Plain strings without leading { or [ shouldn't trip json.loads.
        assert list(_find_maindecks({"note": "some prose, no JSON here"})) == []

    def test_ignores_unrecognised_shape(self) -> None:
        # MainDeck is present but its value isn't a usable shape.
        assert list(_find_maindecks({"MainDeck": "not a list"})) == []
        assert list(_find_maindecks({"MainDeck": [{"weird": "shape"}]})) == []


# ---------------------------------------------------------------------------
# Match end + state reset
# ---------------------------------------------------------------------------


class TestMatchEnd:
    def test_emits_match_ended_on_completed_state(self, tmp_path: Path) -> None:
        body = _make_block(
            {
                "matchGameRoomStateChangedEvent": {
                    "gameRoomInfo": {"stateType": "MatchGameRoomStateType_MatchCompleted"}
                }
            }
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert any(isinstance(e, MatchEnded) for e in events)

    def test_does_not_emit_on_other_state(self, tmp_path: Path) -> None:
        body = _make_block(
            {
                "matchGameRoomStateChangedEvent": {
                    "gameRoomInfo": {"stateType": "MatchGameRoomStateType_Playing"}
                }
            }
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert not any(isinstance(e, MatchEnded) for e in events)

    def test_match_end_resets_parse_state(self, tmp_path: Path) -> None:
        """After MatchEnded, a second match's IDs should not collide."""
        match1 = _make_block(_gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7])))
        match_end = _make_block(
            {
                "matchGameRoomStateChangedEvent": {
                    "gameRoomInfo": {"stateType": "MatchGameRoomStateType_MatchCompleted"}
                }
            }
        )
        # Reuse the same instance ids in match 2 but a different hand
        # — without state reset, dedupe could mask the second emission.
        match2 = _make_block(_gsm_envelope(_hand_gsm(hand_grpids=[10, 20, 30, 40, 50, 60, 70])))
        events = _tail_all(_write_log(tmp_path, match1 + match_end + match2))
        mulligans = [e for e in events if isinstance(e, MulliganDecisionRequest)]
        assert len(mulligans) == 2
        assert mulligans[0].hand_arena_ids == [1, 2, 3, 4, 5, 6, 7]
        assert mulligans[1].hand_arena_ids == [10, 20, 30, 40, 50, 60, 70]


# ---------------------------------------------------------------------------
# Buffer / framing edge cases
# ---------------------------------------------------------------------------


class TestBufferFraming:
    def test_no_headers_emits_nothing(self, tmp_path: Path) -> None:
        body = "this is plain noise without a header marker\n"
        events = _tail_all(_write_log(tmp_path, body))
        assert events == []

    def test_junk_before_first_header_ignored(self, tmp_path: Path) -> None:
        body = "noise\nmore noise\n" + _make_block(
            _gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7]))
        )
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1

    def test_multiple_json_in_one_block(self, tmp_path: Path) -> None:
        """Two payloads in the same block both get parsed."""
        gsm_json = json.dumps(_gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7])))
        deck_json = json.dumps({"MainDeck": [{"cardId": 9, "quantity": 1}]})
        body = f"[UnityCrossThreadLogger]ts\n{gsm_json}\nintervening text {deck_json}\n"
        events = _tail_all(_write_log(tmp_path, body))
        kinds = sorted(type(e).__name__ for e in events)
        assert kinds == ["DeckSubmitted", "MulliganDecisionRequest"]

    def test_multiline_json_is_recovered(self, tmp_path: Path) -> None:
        """JSON pretty-printed across many lines still parses."""
        payload = _gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7]))
        body = "[UnityCrossThreadLogger]ts\n" + json.dumps(payload, indent=2) + "\n"
        events = _tail_all(_write_log(tmp_path, body))
        assert len(events) == 1


# ---------------------------------------------------------------------------
# Skip behaviour
# ---------------------------------------------------------------------------


class TestSkipping:
    def test_drops_hand_when_grpid_unknown(self, tmp_path: Path) -> None:
        """Hand references an instanceId we never saw a grpId for; skip."""
        # Build a GSM that has a hand zone but no game_objects mapping
        # for any of its instance ids.
        gsm = _hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7])
        gsm["gameObjects"] = []  # nuke the mapping
        body = _make_block(_gsm_envelope(gsm))
        events = _tail_all(_write_log(tmp_path, body))
        # No event should fire — better to surface nothing than to
        # recommend on a partial hand.
        assert not any(isinstance(e, MulliganDecisionRequest) for e in events)


# ---------------------------------------------------------------------------
# Live(-ish) tailing: file grows between reads
# ---------------------------------------------------------------------------


class TestLiveTail:
    def test_follow_stops_on_stop_event(self, tmp_path: Path) -> None:
        """The follow loop terminates promptly when stop_event is set."""
        import threading

        log_path = tmp_path / "Player.log"
        log_path.write_text("", encoding="utf-8")
        tailer = LogTailer(log_path, start_at_end=False, poll_interval=0.05)
        stop = threading.Event()
        results: list[Any] = []

        def consume() -> None:
            for ev in tailer.tail(follow=True, stop_event=stop):
                results.append(ev)

        t = threading.Thread(target=consume)
        t.start()
        # Append a complete block + a trailing header so the first one
        # is "complete" for non-final draining.
        log_path.write_text(
            _make_block(_gsm_envelope(_hand_gsm(hand_grpids=[1, 2, 3, 4, 5, 6, 7])))
            + "[UnityCrossThreadLogger]end\n",
            encoding="utf-8",
        )
        # Give the poll loop a couple of cycles to pick it up.
        deadline = 2.0
        step = 0.05
        elapsed = 0.0
        while not results and elapsed < deadline:
            import time

            time.sleep(step)
            elapsed += step
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        assert len(results) == 1
        assert isinstance(results[0], MulliganDecisionRequest)


# ---------------------------------------------------------------------------
# arena_paths env override
# ---------------------------------------------------------------------------


def test_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``MULLIGAN_COACH_OVERLAY_LOG`` always wins over the platform default."""
    from mulligan_coach_overlay.arena_paths import default_log_path

    custom = tmp_path / "custom_player.log"
    monkeypatch.setenv("MULLIGAN_COACH_OVERLAY_LOG", str(custom))
    assert default_log_path() == custom
