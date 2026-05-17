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
from mulligan_coach_recommend import RecommendationService, ServiceStatus
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


def test_validate_returns_validation_partial(client: TestClient) -> None:
    """``POST /validate`` parses the deck and returns a 200 HTML fragment."""
    resp = client.post("/validate", data={"decklist": goblin_decklist()})
    assert resp.status_code == 200
    assert "Deck parsed" in resp.text
    # Datalist for the autocomplete should be present with deck names.
    assert "deck-cards" in resp.text
    assert "Test Goblin" in resp.text


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


def test_healthz_returns_load_status(client: TestClient) -> None:
    """``GET /healthz`` returns a JSON envelope with ``model_loaded`` etc."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["model_loaded"] is False
    assert "n_cards" in payload
