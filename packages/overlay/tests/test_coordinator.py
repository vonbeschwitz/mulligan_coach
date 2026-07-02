"""Unit tests for :class:`OverlayCoordinator`.

These tests fake out the recommendation service (a real one needs a
trained model on disk and a heavy upstream stack). The coordinator
is the layer of pure state-machine logic between the tailer's events
and the recommender — tests confirm:

* DeckSubmitted resolves arena_ids and reports unresolved counts.
* MulliganDecisionRequest produces a RecommendationOutput when
  everything's in place.
* All four "can't recommend" failure modes (no deck, missing hand
  cards, model not loaded, partially-resolved deck) produce a
  MissingDataOutput with the right ``what`` tag.
* MatchEnded clears state — a second match starts clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from mulligan_coach_cards import (
    Cost,
    ManaAbility,
    ParsedCard,
    ParseStatus,
)
from mulligan_coach_cards.models import ManaOption
from mulligan_coach_overlay.card_index import ArenaCardIndex
from mulligan_coach_overlay.coordinator import (
    DeckLoadedOutput,
    MatchResetOutput,
    MissingDataOutput,
    OverlayCoordinator,
    RecommendationOutput,
)
from mulligan_coach_overlay.events import (
    DeckSubmitted,
    MatchEnded,
    MulliganDecisionRequest,
)
from mulligan_coach_recommend import ChoiceRecommendation, ServiceStatus
from mulligan_coach_recommend.service import RecommendationExplanation

# ---------------------------------------------------------------------------
# Test fixtures: minimal ParsedCards + fake service
# ---------------------------------------------------------------------------


def _basic_card(arena_id: int, color: ManaOption = "G") -> ParsedCard:
    """Mint a tiny ParsedCard fixture. arena_id and set_code/collector vary.

    The recommender (mocked here) doesn't read any of these fields,
    so a minimal shape suffices: we only need the index to be able
    to find it and the coordinator's de-dupe / primary-set logic to
    operate on real data.
    """
    return ParsedCard(
        name=f"Card-{arena_id}",
        set_code="TLA",
        collector_number=str(arena_id),
        oracle_id=f"00000000-0000-0000-0000-{arena_id:012d}",
        arena_id=arena_id,
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line="Basic Land — Forest",
        types=["Land"],
        subtypes=["Forest"],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[[color]])],
        enter_condition=None,
        status=ParseStatus.AUTO,
    )


def _build_index(arena_ids: list[int]) -> ArenaCardIndex:
    """In-memory card index covering the given arena_ids."""
    return ArenaCardIndex(
        by_arena_id={aid: _basic_card(aid) for aid in arena_ids},
        loaded_sets=["TLA"],
    )


def _fake_choice_recommendation(
    *,
    p_keep: float = 0.85,
    verdict: str = "clear_keep",
) -> ChoiceRecommendation:
    """Hand-built ChoiceRecommendation with sane field values.

    Default ``p_keep`` lands above the 0.75 clear-keep threshold, so
    the verdict matches by default; tests that need a different
    bucket pass both fields.
    """
    return ChoiceRecommendation(
        verdict=verdict,  # type: ignore[arg-type]
        p_keep=p_keep,
        mulligan_number_from=0,
        mulligan_number_to=1,
        n_sims=200,
        explanation=_fake_explanation(),
    )


def _fake_explanation() -> RecommendationExplanation:
    """All-zeros RecommendationExplanation for tests that don't read the panel."""
    return RecommendationExplanation(
        p_make_2nd_land_by_t2=0.0,
        p_make_3rd_land_by_t3=0.0,
        p_make_4th_land_by_t4=0.0,
        expected_mana_at_t4=0.0,
        p_cast_any_spell_t1=0.0,
        p_cast_any_creature_t2=0.0,
        p_cast_any_removal_t2=0.0,
        p_cast_small_creature_by_t3=0.0,
        p_cast_3drop_by_t4=0.0,
        color_fix_by_t4=0.0,
        hand_cards=(),
    )


@dataclass
class _FakeService:
    """Minimal stand-in for RecommendationService.

    Records calls and returns a pre-baked ChoiceRecommendation (or
    raises) so the coordinator's branches can be exercised without
    booting the real choice model.
    """

    status: ServiceStatus
    rec: ChoiceRecommendation | None = None
    raise_on_recommend: Exception | None = None
    last_call_kwargs: dict[str, Any] | None = None

    def recommend_choice(self, **kwargs: Any) -> ChoiceRecommendation:
        self.last_call_kwargs = kwargs
        if self.raise_on_recommend is not None:
            raise self.raise_on_recommend
        if self.rec is None:
            raise RuntimeError("test forgot to seed self.rec")
        return self.rec


def _ready_status() -> ServiceStatus:
    return ServiceStatus(
        model_loaded=True,
        model_dir=Path("/fake"),
        formats_with_stats=["TLA"],
        formats_missing_stats=[],
    )


def _not_ready_status(msg: str = "test: no model") -> ServiceStatus:
    return ServiceStatus(
        model_loaded=False,
        model_dir=Path("/fake"),
        formats_with_stats=[],
        formats_missing_stats=[],
        error=msg,
    )


# ---------------------------------------------------------------------------
# Coordinator behaviour
# ---------------------------------------------------------------------------


class TestDeckHandling:
    def test_deck_fully_resolved_emits_deck_loaded(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(status=_ready_status())
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        out = coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        assert isinstance(out, DeckLoadedOutput)
        assert out.n_cards == 40
        assert out.n_resolved == 40
        assert out.n_unresolved == 0
        assert out.primary_set == "TLA"

    def test_deck_with_unresolved_cards_flags_them(self) -> None:
        # Index only covers half the deck.
        index = _build_index(list(range(1, 21)))
        service = _FakeService(status=_ready_status())
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        out = coord.handle_event(DeckSubmitted(arena_ids=list(range(1, 41))))
        assert isinstance(out, DeckLoadedOutput)
        assert out.n_resolved == 20
        assert out.n_unresolved == 20

    def test_repeated_identical_deck_suppressed(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(status=_ready_status())
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        first = coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        assert isinstance(first, DeckLoadedOutput)
        second = coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        assert second is None  # dedupe


class TestMulliganRecommendation:
    def test_recommend_when_everything_in_place(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, RecommendationOutput)
        assert out.recommendation is not None
        assert out.recommendation.verdict == "clear_keep"
        assert len(out.hand) == 7
        # The fake service captured the kwargs; verify the coordinator
        # passes through the right ones.
        assert service.last_call_kwargs is not None
        assert service.last_call_kwargs["mulligan_number"] == 0
        assert service.last_call_kwargs["on_the_play"] is True
        assert len(service.last_call_kwargs["deck"]) == 40

    def test_accepts_41_card_deck(self) -> None:
        # A 41-card Limited deck is legal and the choice service accepts
        # 40-42; the coordinator must reach the service, not reject it.
        # (Regression: `_REQUIRED_DECK_SIZE` used to hard-code 40.)
        deck_ids = list(range(1, 42))  # 41 cards
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, RecommendationOutput)
        assert service.last_call_kwargs is not None
        assert len(service.last_call_kwargs["deck"]) == 41

    def test_rejects_oversized_deck(self) -> None:
        # 43 cards is above the service's 40-42 window -> deck_unresolved,
        # and the service is never called.
        deck_ids = list(range(1, 44))  # 43 cards
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "deck_unresolved"
        assert service.last_call_kwargs is None

    def test_missing_hand_card_returns_missing_data(self) -> None:
        # Deck cards 1-40 are indexed; hand references 999 which isn't.
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=[1, 2, 3, 4, 5, 6, 999],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "hand_unresolved"

    def test_no_deck_yet_returns_missing_data(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        # Skip the DeckSubmitted; jump straight to a mulligan request.
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "no_deck"

    def test_partially_resolved_deck_tagged_deck_unresolved(self) -> None:
        # The deck submitted couldn't fully resolve; primary_set is
        # set but the deck itself isn't stashed → next mulligan
        # surfaces a deck_unresolved code, not no_deck.
        index = _build_index(list(range(1, 21)))  # half the deck
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=list(range(1, 41))))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=list(range(1, 8)),
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "deck_unresolved"

    def test_model_not_loaded_returns_missing_data(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(status=_not_ready_status("custom error msg"))
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "model_not_loaded"
        assert "custom error msg" in out.reason

    def test_service_exception_returns_service_error(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            raise_on_recommend=RuntimeError("boom"),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "service_error"
        assert "boom" in out.reason

    @pytest.mark.parametrize(
        "on_play, opp_mull, expected_opp_kw",
        [
            (True, None, None),
            (False, 1, 1),
        ],
    )
    def test_on_play_and_opp_mull_passthrough(
        self,
        on_play: bool,
        opp_mull: int | None,
        expected_opp_kw: int | None,
    ) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=on_play,
                opp_mulligan_count=opp_mull,
                seat_id=1,
            )
        )
        assert service.last_call_kwargs is not None
        assert service.last_call_kwargs["on_the_play"] is on_play
        assert service.last_call_kwargs["opp_mulligan_number"] == expected_opp_kw


class TestMatchReset:
    def test_match_end_clears_deck(self) -> None:
        deck_ids = list(range(1, 41))
        index = _build_index(deck_ids)
        service = _FakeService(
            status=_ready_status(),
            rec=_fake_choice_recommendation(),
        )
        coord = OverlayCoordinator(index, service)  # type: ignore[arg-type]

        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        reset = coord.handle_event(MatchEnded())
        assert isinstance(reset, MatchResetOutput)

        # Subsequent mulligan with no deck loaded → no_deck.
        out = coord.handle_event(
            MulliganDecisionRequest(
                hand_arena_ids=deck_ids[:7],
                mulligan_count=0,
                on_the_play=True,
                opp_mulligan_count=None,
                seat_id=1,
            )
        )
        assert isinstance(out, MissingDataOutput)
        assert out.what == "no_deck"


class TestCardIndexReload:
    """``replace_card_index`` is the seam the auto-updater will call.

    After parsed_cards JSONs land in the user dir, the manifest
    fetcher rebuilds an :class:`ArenaCardIndex` from the new files
    and pushes it through here. The current match's resolved deck is
    intentionally preserved so the in-flight game isn't disturbed.
    """

    def test_swap_index_picks_up_new_cards(self) -> None:
        original_ids = list(range(1, 41))
        coord = OverlayCoordinator(
            _build_index(original_ids),
            _FakeService(status=_ready_status()),  # type: ignore[arg-type]
        )
        # Original index doesn't know arena_id 999.
        resolved_before, missing_before = coord.card_index.resolve_all([999])
        assert resolved_before == [] and missing_before == [999]

        new_index = _build_index([*original_ids, 999])
        coord.replace_card_index(new_index)

        # New index resolves the previously-missing id.
        resolved_after, missing_after = coord.card_index.resolve_all([999])
        assert len(resolved_after) == 1 and missing_after == []

    def test_swap_preserves_current_deck_snapshot(self) -> None:
        deck_ids = list(range(1, 41))
        coord = OverlayCoordinator(
            _build_index(deck_ids),
            _FakeService(
                status=_ready_status(),
                rec=_fake_choice_recommendation(),
            ),  # type: ignore[arg-type]
        )
        coord.handle_event(DeckSubmitted(arena_ids=deck_ids))
        deck_before = coord._current_deck

        # Swap to a brand-new index. The in-flight match's resolved
        # deck shouldn't be silently re-resolved against it.
        coord.replace_card_index(_build_index(deck_ids))

        assert coord._current_deck is deck_before
