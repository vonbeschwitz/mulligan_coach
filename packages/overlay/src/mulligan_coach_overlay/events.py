"""Typed events emitted by :class:`LogTailer`.

The tailer is a pure event producer — it doesn't talk to the
recommendation service, doesn't touch Qt, and doesn't know about
``ParsedCard``. It hands out Arena card IDs (a.k.a. ``grpId`` /
``mtga_id``); the coordinator above the tailer (the headless wire-up
or the GUI) is responsible for resolving those IDs to typed cards
and calling the recommender.

Three event kinds:

* :class:`DeckSubmitted` — Arena sent the deck for the next match
  (``Event_SetDeck`` or the equivalent GRE ``SubmitDeckReq``).
* :class:`MulliganDecisionRequest` — the client is currently being
  asked "keep or mulligan?" (a ``GameStateMessage`` whose ``players[]``
  contains a player with
  ``pendingMessageType == "ClientMessageType_MulliganResp"``).
* :class:`MatchEnded` — the match concluded; consumers should reset
  any per-match state.

The discriminator field (:attr:`LogEvent.kind`) lets consumers ``match``
on the type cleanly without needing ``isinstance`` chains.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeckSubmitted(BaseModel):
    """Player submitted a deck list to start (or restart) a match.

    ``arena_ids`` is the fully-expanded deck — one integer per copy in
    the maindeck — in the order Arena reported them. The order
    doesn't matter for the recommender (it works on multisets), but
    preserving it is cheaper than sorting and lets the headless / GUI
    show "what was submitted" in the same order the user sees it in
    Arena's deck editor.

    We deliberately don't carry the sideboard. Limited Premier Draft
    has no sideboard; Sealed sideboards exist but aren't used during
    Bo1 games, and the mulligan recommender only sees the 40-card
    maindeck.
    """

    kind: Literal["deck_submitted"] = "deck_submitted"
    arena_ids: list[int] = Field(..., min_length=1)


class MulliganDecisionRequest(BaseModel):
    """Arena is asking the player to keep or mulligan the current hand.

    Fields mirror what the website's
    :meth:`RecommendationService.recommend_asymmetric` needs:

    * ``hand_arena_ids`` — the current opening-hand contents (always
      seven cards under London mulligan rules; the player will bottom
      ``mulligan_count`` cards if they keep).
    * ``mulligan_count`` — how many times the player has already
      mulliganed in this match. 0 on the first opening hand.
    * ``on_the_play`` — true if the local player is on the play.
    * ``opp_mulligan_count`` — opponent's mulligan count. Only
      meaningful when the local player is on the draw (the model masks
      this feature on the play; we surface ``None`` in that case to
      make the intent explicit).
    * ``seat_id`` — the local player's seat number (1 or 2). Useful
      for logging / debug; the recommender doesn't read it.
    """

    kind: Literal["mulligan_decision_request"] = "mulligan_decision_request"
    hand_arena_ids: list[int] = Field(..., min_length=1)
    mulligan_count: int = Field(..., ge=0, le=6)
    on_the_play: bool
    opp_mulligan_count: int | None = None
    seat_id: int


class MatchEnded(BaseModel):
    """The current match concluded.

    Fires on the ``MatchGameRoomStateType_MatchCompleted`` transition
    in the gameRoom state. The coordinator should clear any per-match
    state it cached (decklist, accumulated game-object map, …) so a
    subsequent match starts clean even if the user never restarts the
    overlay.
    """

    kind: Literal["match_ended"] = "match_ended"


# Discriminated union for consumers that want ``match`` exhaustiveness.
# Pydantic recognises ``Literal`` discriminators via field ``kind``.
LogEvent = DeckSubmitted | MulliganDecisionRequest | MatchEnded
