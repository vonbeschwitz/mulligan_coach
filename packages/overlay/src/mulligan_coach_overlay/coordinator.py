"""Stateful glue between the log tailer and the recommendation service.

Three things to do per match:

1. Listen for :class:`DeckSubmitted`; build a 40-card list of
   :class:`ParsedCard` and stash it as the current deck.
2. Listen for :class:`MulliganDecisionRequest`; look up the hand
   cards via the :class:`ArenaCardIndex`; call
   :meth:`RecommendationService.recommend_choice` (the production
   choice model; the legacy ``recommend_asymmetric`` win-model path
   is not used here); produce a typed result for display.
3. Listen for :class:`MatchEnded`; clear the per-match deck so the
   next match starts clean.

Why a coordinator class rather than inline logic in the headless CLI:
both the headless CLI and the (future) PyQt6 GUI need exactly this
state machine. The coordinator is pure (no I/O, no Qt), so it's
testable in isolation against synthetic event sequences and reusable
across both surfaces.

The :class:`CoordinatorOutput` discriminator is the surface to the
display layer. It's not the same shape as
:class:`mulligan_coach_recommend.ChoiceRecommendation` because:

* The display needs to know whether a recommendation could even be
  computed — was a deck loaded? Were all hand cards resolved? —
  before it gets a chance to render numbers.
* Wrapping the recommend result with the resolved
  :class:`ParsedCard` instances saves the display layer from having
  to re-resolve via the card index.

For the unhappy paths (model not loaded, missing card encoding, no
deck yet, deck-card resolution incomplete) the coordinator returns a
:class:`MissingDataOutput` with a human-readable ``reason`` field
rather than raising. The display layer then renders the message
verbatim instead of crashing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from mulligan_coach_cards import ParsedCard
from mulligan_coach_recommend import ChoiceRecommendation, RecommendationService

from .card_index import ArenaCardIndex
from .events import DeckSubmitted, LogEvent, MatchEnded, MulliganDecisionRequest

log = logging.getLogger(__name__)

# Limited deck size the recommender expects. Mirrors the website's
# `_REQUIRED_DECK_SIZE` — kept here as a separate constant so the
# overlay doesn't have to import a private website name.
_REQUIRED_DECK_SIZE = 40

# Sim count for the overlay's choice-model call. The shared service
# defaults to 1000 (what the website uses). Dropping to 200 here
# matches the previous overlay budget — the variance benchmark
# (``packages/model/scripts/keep_arm_variance.py``) measured ~0.006
# run-to-run std on the win-model P(win) at 200 sims, well below
# any verdict-band width; the choice model reads the same simulator
# features so the same trade-off applies.
_OVERLAY_N_SIMS = 200


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeckLoadedOutput:
    """A deck submission was recognised and (mostly) resolved.

    ``n_unresolved`` > 0 means some cards lacked a ``ParsedCard``
    encoding (typically: data-download out of date for a new set, or
    MTGJSON hasn't reported arena_ids for those printings yet). The
    overlay's next-step recommendation will fail with
    :class:`MissingDataOutput` if the user reaches a mulligan
    decision before fixing this — surfacing the count up front lets
    the user know to refresh.
    """

    kind: Literal["deck_loaded"] = "deck_loaded"
    n_cards: int = 0
    n_resolved: int = 0
    n_unresolved: int = 0
    primary_set: str | None = None


@dataclass(frozen=True)
class RecommendationOutput:
    """Successful recommendation, ready to display."""

    kind: Literal["recommendation"] = "recommendation"
    recommendation: ChoiceRecommendation | None = None
    hand: tuple[ParsedCard, ...] = ()
    primary_set: str | None = None
    mulligan_count: int = 0
    on_the_play: bool = True


@dataclass(frozen=True)
class MissingDataOutput:
    """We can't recommend yet. ``reason`` explains what's missing.

    ``what`` is a short machine-readable tag the display layer can
    group on (e.g. show "no deck" with a hint vs. "missing cards"
    with the card list).
    """

    kind: Literal["missing_data"] = "missing_data"
    reason: str = ""
    what: Literal[
        "no_deck",
        "deck_unresolved",
        "hand_unresolved",
        "model_not_loaded",
        "service_error",
    ] = "no_deck"


@dataclass(frozen=True)
class MatchResetOutput:
    """Match ended. Display can clear any sticky verdict from screen."""

    kind: Literal["match_reset"] = "match_reset"


@dataclass(frozen=True)
class ComputingOutput:
    """Sentinel: a mulligan-decision request is being processed.

    Emitted by the tailer worker the moment a
    :class:`MulliganDecisionRequest` arrives, *before* the blocking
    ``recommend_choice`` call. Lets the UI render a "computing"
    state so the user can tell when latency comes from sim time
    vs. when the Arena log delivery itself was slow.

    Not produced by :meth:`OverlayCoordinator.handle_event` — the
    coordinator's call into the service is what we're waiting on,
    so it can't emit this itself. The worker is the right place.
    """

    kind: Literal["computing"] = "computing"
    mulligan_count: int = 0
    on_the_play: bool = True


CoordinatorOutput = (
    DeckLoadedOutput | RecommendationOutput | MissingDataOutput | MatchResetOutput | ComputingOutput
)


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class OverlayCoordinator:
    """Bridge between log events and recommendations.

    Owns the per-match deck. Stateless across matches; the only state
    that lives longer than a match is the ``card_index`` and the
    ``service``, both of which are built once at process start.
    """

    def __init__(
        self,
        card_index: ArenaCardIndex,
        service: RecommendationService,
    ) -> None:
        self.card_index = card_index
        self.service = service
        # Current match's deck as a list of ParsedCard (one per copy).
        # Set on DeckSubmitted, cleared on MatchEnded. ``None`` means
        # "no deck for this match yet" — we'll refuse to recommend.
        self._current_deck: list[ParsedCard] | None = None
        # Sticky primary_set inferred from the current deck. Cached so
        # the deck-loaded output and subsequent recommendations agree.
        self._current_primary_set: str | None = None

    def replace_card_index(self, new_index: ArenaCardIndex) -> None:
        """Swap in a freshly-built :class:`ArenaCardIndex`.

        Called by the auto-updater after new parsed_cards JSONs have
        been written to the user dir. Python attribute assignment is
        atomic under the GIL, so a concurrent ``handle_event`` either
        sees the old index or the new one — never half — and we don't
        need a lock here.

        The currently-loaded deck (held as resolved ``ParsedCard``
        instances on ``self._current_deck``) is intentionally NOT
        re-resolved. Re-resolving could quietly change what
        ``recommend`` sees for the match in progress, which is more
        surprising than the alternative (the match keeps running
        against the snapshot taken at deck-submit time; the next
        match picks up the new encodings). If the user wants the
        refreshed encodings active immediately they can re-submit
        the deck or wait for ``MatchEnded`` to clear the slot.
        """
        self.card_index = new_index

    def handle_event(self, event: LogEvent) -> CoordinatorOutput | None:
        """Process one tailer event.

        Returns ``None`` for events that don't produce user-facing
        output (e.g. a deck submission that's identical to the
        previously-loaded one, which would just produce a duplicate
        notification).
        """
        if isinstance(event, DeckSubmitted):
            return self._handle_deck(event)
        if isinstance(event, MulliganDecisionRequest):
            return self._handle_mulligan(event)
        if isinstance(event, MatchEnded):
            self._current_deck = None
            self._current_primary_set = None
            return MatchResetOutput()
        # Unknown event type slipped through — log and move on rather
        # than crash. The tailer's union type should keep us out of
        # this branch in practice.
        log.warning("unhandled event type: %s", type(event).__name__)
        return None

    # -----------------------------------------------------------------
    # Per-event handlers
    # -----------------------------------------------------------------

    def _handle_deck(self, event: DeckSubmitted) -> CoordinatorOutput | None:
        resolved, missing = self.card_index.resolve_all(event.arena_ids)
        primary = _pick_primary_set(resolved)
        if missing:
            log.warning(
                "deck has %d arena_ids without a ParsedCard encoding: %s",
                len(missing),
                missing[:5],
            )
            # Treat a partially-resolved deck as unusable for
            # recommendations — feeding the recommender 38 of 40
            # cards would silently produce a misleading verdict. Save
            # nothing as the "current deck", so the next mulligan
            # request will surface a clear MissingDataOutput.
            self._current_deck = None
            self._current_primary_set = primary
            return DeckLoadedOutput(
                n_cards=len(event.arena_ids),
                n_resolved=len(resolved),
                n_unresolved=len(missing),
                primary_set=primary,
            )
        # Suppress dupes: same multiset → same deck, no need to
        # re-notify the display.
        new_signature = _multiset_signature(resolved)
        old_signature = _multiset_signature(self._current_deck) if self._current_deck else None
        self._current_deck = resolved
        self._current_primary_set = primary
        if new_signature == old_signature:
            return None
        return DeckLoadedOutput(
            n_cards=len(resolved),
            n_resolved=len(resolved),
            n_unresolved=0,
            primary_set=primary,
        )

    def _handle_mulligan(self, event: MulliganDecisionRequest) -> CoordinatorOutput:
        # Preflight: hand cards must resolve, deck must be loaded,
        # and the model must be loaded. Each failure mode has its own
        # MissingDataOutput so the display can render the right hint.
        hand, missing = self.card_index.resolve_all(event.hand_arena_ids)
        if missing:
            return MissingDataOutput(
                reason=(
                    f"Hand has {len(missing)} card(s) without a local encoding "
                    f"(arena_ids: {missing}). Refresh card data and try again."
                ),
                what="hand_unresolved",
            )
        if self._current_deck is None:
            # Two sub-cases: we never saw a DeckSubmitted at all, or
            # we saw one that failed to fully resolve.
            return MissingDataOutput(
                reason=(
                    "No deck for this match yet. Wait for the deck "
                    "submission to appear in the log (or check that "
                    "all cards in your deck have a local encoding)."
                ),
                what="no_deck" if self._current_primary_set is None else "deck_unresolved",
            )
        if not self.service.status.ready:
            return MissingDataOutput(
                reason=(
                    self.service.status.error
                    or "Model is not loaded; cannot make a recommendation."
                ),
                what="model_not_loaded",
            )
        if len(self._current_deck) != _REQUIRED_DECK_SIZE:
            return MissingDataOutput(
                reason=(
                    f"Deck has {len(self._current_deck)} cards; the trained "
                    f"model expects exactly {_REQUIRED_DECK_SIZE}."
                ),
                what="deck_unresolved",
            )

        try:
            recommendation = self.service.recommend_choice(
                hand=hand,
                deck=self._current_deck,
                on_the_play=event.on_the_play,
                mulligan_number=event.mulligan_count,
                opp_mulligan_number=event.opp_mulligan_count,
                n_sims=_OVERLAY_N_SIMS,
            )
        except Exception as exc:
            # Surface unexpected failures inline rather than letting
            # the tail loop crash. The tailer keeps running and the
            # next decision gets a fresh attempt.
            log.exception("recommend_choice raised")
            return MissingDataOutput(
                reason=f"Recommendation failed: {type(exc).__name__}: {exc}",
                what="service_error",
            )
        return RecommendationOutput(
            recommendation=recommendation,
            hand=tuple(hand),
            primary_set=self._current_primary_set,
            mulligan_count=event.mulligan_count,
            on_the_play=event.on_the_play,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_primary_set(cards: list[ParsedCard]) -> str | None:
    """Most common non-BASIC set code in *cards*.

    Mirrors :meth:`RecommendationService.primary_set_of` so the
    overlay reports the same "format set" the service will infer
    for the recommendation. Duplicated rather than imported because
    the service method takes a deck and we sometimes call this on a
    partial / non-deck list (e.g. a partially-resolved deck).
    """
    counts: dict[str, int] = {}
    for c in cards:
        if c.set_code.upper() == "BASIC":
            continue
        counts[c.set_code.upper()] = counts.get(c.set_code.upper(), 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def _multiset_signature(cards: list[ParsedCard]) -> tuple[str, ...]:
    """Set-aware multiset signature: ``(SET:NUMBER, ...)`` sorted."""
    return tuple(sorted(f"{c.set_code.upper()}:{c.collector_number}" for c in cards))
