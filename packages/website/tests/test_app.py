"""Smoke tests for the FastAPI routes via TestClient.

The model bundle is not loaded in tests — it'd add 1-2 seconds and
require a trained model on disk. We monkeypatch the recommendation
service to a "no-model" shape so the route layer's "model not loaded"
branch is exercised instead. Deeper end-to-end coverage that
actually runs the model lives in ``packages/model/tests``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

# Absolute import (not `from .`) so the test module doesn't depend on
# packages/website/tests/ being a Python package. Two packages
# (simulation, website) used to declare a `tests/__init__.py` and
# pytest's prepend importer was collapsing both into a single `tests`
# namespace — only one could win, the other's relative imports broke.
# Renaming the factories file to `_website_factories.py` keeps it
# unambiguous on sys.path.
from _website_factories import goblin_decklist, make_store  # type: ignore[import-not-found]
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mulligan_coach_recommend import (
    ChoiceRecommendation,
    RecommendationExplanation,
    RecommendationService,
    ServiceStatus,
)
from mulligan_coach_website.app import app
from mulligan_coach_website.scryfall import ScryfallImages


@pytest.fixture
def client() -> Iterator[TestClient]:
    """TestClient backed by a tiny in-memory store + stub service.

    We swap the app's ``lifespan_context`` so it doesn't try to load
    anything from disk. The Scryfall client is built (it's just an
    httpx ``AsyncClient`` — no network until a card-image route is
    hit) so its shutdown path is exercised on teardown.
    """

    @asynccontextmanager
    async def stub_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app.state.store = make_store()
        app.state.service = RecommendationService(
            bundle=None,
            stats_by_set={},
            status=ServiceStatus(
                model_loaded=False,
                model_dir=Path("/nonexistent"),
                formats_with_stats=[],
                formats_missing_stats=[],
                error="stubbed: model intentionally not loaded in tests",
            ),
        )
        app.state.scryfall = ScryfallImages.build()
        try:
            yield
        finally:
            await app.state.scryfall.aclose()

    # Swap the router's lifespan_context for the duration of the test.
    # Restoring it afterwards is important because the ``app`` module is
    # cached across pytest's process and the next test (or session) might
    # otherwise see the stub.
    original = app.router.lifespan_context
    app.router.lifespan_context = stub_lifespan
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


def test_index_renders(client: TestClient) -> None:
    """``GET /`` returns HTML with the recognisable page header."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Mulligan Coach" in resp.text
    # The "model not loaded" banner should be present because we
    # stubbed the service that way.
    assert "Model not loaded" in resp.text


def test_index_has_hidden_on_the_play_companion(client: TestClient) -> None:
    """The On-the-play checkbox needs a hidden ``=false`` companion.

    HTML forms drop unchecked checkboxes from the POST body, so
    without the companion the FastAPI route's ``= True`` default
    sticks and the user can never actually pick "on the draw" from
    the website. Render the page and confirm both inputs are wired.
    """
    resp = client.get("/")
    assert resp.status_code == 200
    # Hidden field always sends "false"; checkbox overrides with
    # "true" only when checked. Order matters — Starlette keeps the
    # last value for duplicate keys.
    hidden_pos = resp.text.find('<input type="hidden" name="on_the_play" value="false">')
    checkbox_pos = resp.text.find('<input type="checkbox" name="on_the_play" value="true"')
    assert hidden_pos != -1, "hidden on_the_play=false companion missing"
    assert checkbox_pos != -1, "on_the_play checkbox missing"
    assert hidden_pos < checkbox_pos, "hidden field must come BEFORE checkbox"


def test_validate_returns_validation_partial(client: TestClient) -> None:
    """``POST /validate`` parses the deck and returns a 200 HTML fragment."""
    resp = client.post("/validate", data={"decklist": goblin_decklist()})
    assert resp.status_code == 200
    assert "Deck parsed" in resp.text
    # Datalist for the autocomplete should be present with deck names.
    assert "deck-cards" in resp.text
    assert "Test Goblin" in resp.text
    # A legal 40-card deck sits inside the 40-42 window → no size warning.
    assert "Heads up" not in resp.text


def test_validate_41_card_deck_has_no_size_warning(client: TestClient) -> None:
    """A 41-card deck is legal (service accepts 40-42) → no size warning.

    Regression: the website used to warn (and the recommend route used
    to reject) any deck that wasn't exactly 40.
    """
    deck = "Deck\n17 Mountain (TST) 270\n24 Test Goblin (TST) 001\n"  # 41 cards
    resp = client.post("/validate", data={"decklist": deck})
    assert resp.status_code == 200
    assert "Deck parsed" in resp.text
    assert "Heads up" not in resp.text


def test_validate_43_card_deck_warns_out_of_range(client: TestClient) -> None:
    """43 cards is above the 40-42 window → the size warning shows."""
    deck = "Deck\n17 Mountain (TST) 270\n26 Test Goblin (TST) 001\n"  # 43 cards
    resp = client.post("/validate", data={"decklist": deck})
    assert resp.status_code == 200
    assert "Heads up" in resp.text
    assert "40-42" in resp.text


def test_hand_random_action_returns_hand_partial(client: TestClient) -> None:
    """``POST /hand`` with action=random returns a populated 7-slot grid."""
    resp = client.post(
        "/hand",
        data={"action": "random", "decklist": goblin_decklist()},
    )
    assert resp.status_code == 200
    # 7 hand_ids hidden inputs should be in the rendered grid.
    assert resp.text.count('name="hand_ids"') == 7


def test_hand_clear_returns_empty_grid(client: TestClient) -> None:
    """``POST /hand`` with action=clear empties the hand."""
    resp = client.post(
        "/hand",
        data={
            "action": "clear",
            "decklist": goblin_decklist(),
            "hand_ids": ["TST:001"],
        },
    )
    assert resp.status_code == 200
    assert 'name="hand_ids"' not in resp.text


def test_recommend_without_model_returns_error_message(client: TestClient) -> None:
    """``POST /recommend`` surfaces the "model not loaded" error inline."""
    resp = client.post(
        "/recommend",
        data={
            "decklist": goblin_decklist(),
            "hand_ids": ["TST:001"] * 7,
            "on_the_play": "true",
            "mulligan_number": "0",
        },
    )
    assert resp.status_code == 200
    assert "Can't compute" in resp.text


def _stub_explanation() -> RecommendationExplanation:
    """A minimal playability explanation for a crafted recommendation."""
    return RecommendationExplanation(
        p_make_2nd_land_by_t2=0.9,
        p_make_3rd_land_by_t3=0.8,
        p_make_4th_land_by_t4=0.7,
        expected_mana_at_t4=3.5,
        p_cast_any_spell_t1=0.5,
        p_cast_any_creature_t2=0.6,
        p_cast_any_removal_t2=0.2,
        p_cast_small_creature_by_t3=0.7,
        p_cast_3drop_by_t4=0.4,
        color_fix_by_t4=0.9,
        hand_cards=(),
    )


def _choice_rec(
    *,
    degradations: tuple[str, ...],
    stats_coverage: tuple[int, int],
    verdict: str = "marginal_keep",
    p_keep: float = 0.7,
) -> ChoiceRecommendation:
    return ChoiceRecommendation(
        verdict=verdict,  # type: ignore[arg-type]
        p_keep=p_keep,
        mulligan_number_from=0,
        mulligan_number_to=1,
        n_sims=100,
        explanation=_stub_explanation(),
        degradations=degradations,
        stats_coverage=stats_coverage,
    )


class _FakeService:
    """Minimal stand-in for RecommendationService the route needs.

    Carries a ready status and returns a crafted recommendation so the
    ``/recommend`` template's degradation + coverage rendering can be
    exercised without a trained model.
    """

    def __init__(self, rec: ChoiceRecommendation) -> None:
        self._rec = rec
        self.status = ServiceStatus(
            model_loaded=True,
            model_dir=None,
            formats_with_stats=["TST"],
            formats_missing_stats=[],
        )
        self.stats_by_set: dict[str, object] = {}

    def recommend_choice(self, **_kwargs: object) -> ChoiceRecommendation:
        return self._rec


def _client_with_rec(rec: ChoiceRecommendation) -> Iterator[TestClient]:
    @asynccontextmanager
    async def stub_lifespan(_app: FastAPI) -> AsyncIterator[None]:
        app.state.store = make_store()
        app.state.service = _FakeService(rec)
        app.state.scryfall = ScryfallImages.build()
        try:
            yield
        finally:
            await app.state.scryfall.aclose()

    original = app.router.lifespan_context
    app.router.lifespan_context = stub_lifespan
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.lifespan_context = original


def _post_recommend(c: TestClient) -> str:
    resp = c.post(
        "/recommend",
        data={
            "decklist": goblin_decklist(),
            "hand_ids": ["TST:001"] * 7,
            "on_the_play": "true",
            "mulligan_number": "0",
        },
    )
    assert resp.status_code == 200
    return resp.text


def test_recommend_renders_degradations() -> None:
    """A recommendation carrying degradations renders each as a warn line
    plus the stats-coverage summary."""
    rec = _choice_rec(
        degradations=(
            "No 17Lands ratings loaded for TST — per-card win-rate features are zeroed.",
        ),
        stats_coverage=(21, 23),
    )
    for c in _client_with_rec(rec):
        text = _post_recommend(c)
        assert "No 17Lands ratings loaded for TST" in text
        assert "17Lands data: 21/23 spells" in text


def test_recommend_healthy_shows_no_degradations() -> None:
    """A fully-healthy recommendation shows the coverage line but no
    warn-line degradations."""
    rec = _choice_rec(degradations=(), stats_coverage=(23, 23))
    for c in _client_with_rec(rec):
        text = _post_recommend(c)
        assert "17Lands data: 23/23 spells" in text
        assert "win-rate features are zeroed" not in text
        assert "no 17Lands ratings row" not in text


def test_recommend_renders_borderline_verdict() -> None:
    """The no-judgement ``borderline`` verdict renders with its grey CSS
    class and the coin-flip explainer, not a keep/mull colour."""
    rec = _choice_rec(
        degradations=(),
        stats_coverage=(23, 23),
        verdict="borderline",
        p_keep=0.55,
    )
    for c in _client_with_rec(rec):
        text = _post_recommend(c)
        assert "rec-verdict-borderline" in text
        assert "Borderline" in text
        assert "coin flip" in text


def test_healthz_returns_load_status(client: TestClient) -> None:
    """``GET /healthz`` returns a JSON envelope with ``model_loaded`` etc."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["model_loaded"] is False
    assert "n_cards" in payload
