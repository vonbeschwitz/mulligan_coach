"""Smoke tests for the card-viewer FastAPI app.

The viewer reads its data from ``data/raw/scryfall/oracle_cards.<date>.json``
which is downloaded by the ``data-download`` package and gitignored. On
fresh checkouts (CI, a new contributor) that file isn't present, so the
fixture skips rather than failing — the viewer is a dev tool, not a CI gate.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from mulligan_coach_cards.loader import latest_oracle_cards_path


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    try:
        latest_oracle_cards_path()
    except FileNotFoundError:
        pytest.skip("Scryfall snapshot not present; skipping card-viewer smoke test")

    # Import inside the fixture so the skip path doesn't trigger app import,
    # which would otherwise build the store and fail on missing data.
    from card_viewer.app import app

    with TestClient(app) as c:
        yield c


def test_index_renders(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "Card encoding viewer" in r.text


def test_search_no_filter_returns_results(client: TestClient) -> None:
    r = client.get("/search")
    assert r.status_code == 200
    # The partial includes the "Showing N of M" header.
    assert "Showing" in r.text


def test_search_status_filter(client: TestClient) -> None:
    r = client.get("/search", params={"status": "needs_llm"})
    assert r.status_code == 200


def test_search_llm_encoded_status_filter(client: TestClient) -> None:
    r = client.get("/search", params={"status": "llm_encoded"})
    assert r.status_code == 200


def test_search_needs_human_status_filter(client: TestClient) -> None:
    r = client.get("/search", params={"status": "needs_human"})
    assert r.status_code == 200


def test_search_role_flag_filter(client: TestClient) -> None:
    r = client.get("/search", params={"role_flags": "is_creature"})
    assert r.status_code == 200


def test_search_saga_role_flag_filter(client: TestClient) -> None:
    r = client.get("/search", params={"role_flags": "is_saga"})
    assert r.status_code == 200


def test_search_class_role_flag_filter(client: TestClient) -> None:
    r = client.get("/search", params={"role_flags": "is_class"})
    assert r.status_code == 200


def test_card_detail_renders(client: TestClient) -> None:
    """Pull the first loaded entry from the store and fetch its detail page."""
    from card_viewer.app import app

    store = app.state.store
    assert store.entries, "store should not be empty if we got past the fixture"
    entry = store.entries[0]

    r = client.get(f"/card/{entry.parsed.set_code}/{entry.parsed.collector_number}")
    assert r.status_code == 200
    assert entry.parsed.name in r.text
    # The JSON pre-block contains autoescaped quotes (`&#34;status&#34;`),
    # plus the chip-summary row labels the status as a chip class.
    assert "ParsedCard JSON" in r.text
    assert f"chip-{entry.parsed.status.value}" in r.text


def test_card_detail_404(client: TestClient) -> None:
    r = client.get("/card/zzz/9999")
    assert r.status_code == 404


def test_healthz(client: TestClient) -> None:
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["n_cards"] > 0
