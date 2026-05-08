"""Smoke tests for the simulation-viewer FastAPI app.

The viewer needs ``data/processed/parsed_cards/<SET>.json`` (written by
``mulligan-coach-cards run-detector``) to load real cards. On a fresh
checkout that directory may be empty; the fixture skips rather than
failing — this is a dev tool, not a CI gate.

The smoke deck is "40 Mountain" because we synthesise the five basic
lands ourselves (see ``simulation_viewer.data.synthetic_basics``), so
the test runs even with zero persisted sets. That's intentional: it
exercises the HTTP pipeline (validate → hand → simulate) end-to-end
without depending on whatever the parser happens to know about today.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

# 40 mountains — the simplest valid input. Validates resolves them via
# the synthesized basics; iter_traces is happy with a 7-card hand and
# a 33-card library.
SMOKE_DECKLIST = "Deck\n" + "\n".join(["40 Mountain"])


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    # Import inside the fixture so tests collected before the import
    # runs don't choke on an unresolved package path.
    from simulation_viewer.app import app

    with TestClient(app) as c:
        yield c


def test_index_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Simulation viewer" in r.text
    # The form must be present so HTMX can post into it.
    assert 'id="decksim"' in r.text


def test_healthz(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["ok"] is True
    # Synthetic basics alone guarantee at least 5 cards in the store.
    assert body["n_cards"] >= 5


def test_validate_empty(client: TestClient) -> None:
    r = client.post("/validate", data={"decklist": ""})
    assert r.status_code == 200
    assert "Empty decklist" in r.text
    assert 'id="deck-cards"' in r.text  # datalist always rendered


def test_validate_smoke_deck(client: TestClient) -> None:
    r = client.post("/validate", data={"decklist": SMOKE_DECKLIST})
    assert r.status_code == 200
    assert "All resolved" in r.text
    # Mountain ends up in the autocomplete datalist.
    assert "Mountain" in r.text


def test_validate_unknown_card(client: TestClient) -> None:
    r = client.post(
        "/validate",
        data={"decklist": "1 Nonexistent Bogus Card"},
    )
    assert r.status_code == 200
    assert "not_found" in r.text
    assert "Nonexistent Bogus Card" in r.text


def test_validate_skips_sideboard(client: TestClient) -> None:
    """Lines after a stop-header (Sideboard) shouldn't enter the deck."""
    deck = "40 Mountain\n\nSideboard\n5 Forest"
    r = client.post("/validate", data={"decklist": deck})
    assert r.status_code == 200
    # 40, not 45 — the Forests stay out.
    assert "40 cards" in r.text


def test_hand_random_seven(client: TestClient) -> None:
    r = client.post(
        "/hand",
        data={"decklist": SMOKE_DECKLIST, "action": "random"},
    )
    assert r.status_code == 200
    # Seven hidden inputs of name="hand_ids" — one per slot.
    assert r.text.count('name="hand_ids"') == 7


def test_hand_add_single(client: TestClient) -> None:
    r = client.post(
        "/hand",
        data={
            "decklist": SMOKE_DECKLIST,
            "action": "add",
            "add_name": "Mountain",
        },
    )
    assert r.status_code == 200
    assert r.text.count('name="hand_ids"') == 1


def test_hand_add_unknown_name(client: TestClient) -> None:
    r = client.post(
        "/hand",
        data={
            "decklist": SMOKE_DECKLIST,
            "action": "add",
            "add_name": "Not A Real Card",
        },
    )
    assert r.status_code == 200
    assert "No card named" in r.text


def test_hand_remove(client: TestClient) -> None:
    r = client.post(
        "/hand",
        data={
            "decklist": SMOKE_DECKLIST,
            "action": "remove",
            "index": "0",
            "hand_ids": ["BASIC:Mountain", "BASIC:Mountain"],
        },
    )
    assert r.status_code == 200
    # Started with 2, removed 1.
    assert r.text.count('name="hand_ids"') == 1


def test_simulate_runs_and_renders_four_turns(client: TestClient) -> None:
    """End-to-end: 40-Mountain deck, all-Mountain hand → simulator must
    produce a 4-turn trace."""
    r = client.post(
        "/simulate",
        data={
            "decklist": SMOKE_DECKLIST,
            "on_play": "true",
            "seed": "42",
            "hand_ids": ["BASIC:Mountain"] * 7,
        },
    )
    assert r.status_code == 200
    # 4 turn-block headers. Note: the heading also appears on the index
    # page ("Run simulation"), but that's served from a different route.
    for turn in (1, 2, 3, 4):
        assert f"Turn {turn}" in r.text
    assert "seed = " in r.text


def test_simulate_wrong_hand_size_renders_error(client: TestClient) -> None:
    """Hand of 3 with 7 required → inline error, no 5xx."""
    r = client.post(
        "/simulate",
        data={
            "decklist": SMOKE_DECKLIST,
            "on_play": "true",
            "hand_ids": ["BASIC:Mountain"] * 3,
        },
    )
    assert r.status_code == 200
    assert "needs exactly 7" in r.text


def test_simulate_invalid_deck_renders_error(client: TestClient) -> None:
    """Decklist with at least one issue → simulate refuses, inline error."""
    bad_deck = "40 Mountain\n1 Definitely Not A Card"
    r = client.post(
        "/simulate",
        data={
            "decklist": bad_deck,
            "on_play": "true",
            "hand_ids": ["BASIC:Mountain"] * 7,
        },
    )
    assert r.status_code == 200
    assert "fix the validation panel" in r.text
