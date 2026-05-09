"""Smoke tests for the dev-site umbrella.

The umbrella delegates all real work to the mounted utilities, so these
tests focus on the wiring:

* The landing page renders with links to each mounted utility.
* The umbrella's own /healthz responds.
* Each mounted utility's healthz responds under its prefix
  (proves the mount + lifespan composition works).
* The mounted utilities' templates resolve `request.url_for(...)`
  against the mount prefix (proves the url_for migration is correct).

Tests that exercise the mounted utilities' actual data loading live in
those utilities' own test suites.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from mulligan_coach_cards.loader import latest_oracle_cards_path


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    # card-viewer's lifespan needs the Scryfall snapshot, so the umbrella
    # can't boot without it. Skip rather than fail on a clean checkout.
    try:
        latest_oracle_cards_path()
    except FileNotFoundError:
        pytest.skip("Scryfall snapshot not present; skipping dev-site smoke test")

    # Import inside the fixture so the skip path doesn't trigger app import,
    # which would otherwise cascade into card-viewer's import of the loader.
    from dev_site.app import app

    with TestClient(app) as c:
        yield c


def test_index_renders_with_links(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    # The two tile links point at the mounted prefixes.
    assert "/card-viewer/" in r.text
    assert "/simulation-viewer/" in r.text
    # The umbrella's own brand link must point at the umbrella root,
    # not at a sibling sub-app's index. Regression: when the unnamed
    # /card-viewer mount was registered before the umbrella's own `/`
    # route, url_for("index") descended into card_viewer and returned
    # /card-viewer/ for the umbrella's brand.
    assert 'class="brand" href="http://testserver/"' in r.text


def test_umbrella_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_card_viewer_healthz_under_prefix(client: TestClient) -> None:
    r = client.get("/card-viewer/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["n_cards"] > 0


def test_simulation_viewer_healthz_under_prefix(client: TestClient) -> None:
    r = client.get("/simulation-viewer/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    # Synthetic basics alone guarantee at least 5 cards in the store.
    assert body["n_cards"] >= 5


def test_card_viewer_index_uses_prefix_in_links(client: TestClient) -> None:
    """Mounted index page must point HTMX at /card-viewer/search, not /search."""
    r = client.get("/card-viewer/")
    assert r.status_code == 200
    assert "/card-viewer/search" in r.text


def test_simulation_viewer_index_uses_prefix_in_links(client: TestClient) -> None:
    """Mounted simulation viewer's HTMX endpoints must include the prefix."""
    r = client.get("/simulation-viewer/")
    assert r.status_code == 200
    assert "/simulation-viewer/validate" in r.text
    assert "/simulation-viewer/hand" in r.text
    assert "/simulation-viewer/simulate" in r.text


def test_no_cross_utility_url_collisions(client: TestClient) -> None:
    """Common route names (`index`, `static`) collide across sub-apps.

    Without the per-app ``url_for`` override, the umbrella's router
    resolves these against the FIRST matching mount, so the
    simulation-viewer's brand link points at /card-viewer/ and its
    stylesheet href points at the umbrella's /static/style.css. Catch
    that regression by asserting each sub-app's brand and static URL
    use its own prefix, never a sibling's.
    """
    sim = client.get("/simulation-viewer/").text
    # Simulation-viewer's brand link must point at its own root.
    assert 'href="http://testserver/simulation-viewer/"' in sim, (
        "simulation-viewer brand link must use its own prefix, not a sibling's"
    )
    # And its stylesheet must come from its own /static, not the umbrella's.
    assert 'href="http://testserver/simulation-viewer/static/style.css"' in sim
    # Sanity check: the wrong (cross-utility) prefix isn't in the page.
    assert "/card-viewer/" not in sim

    cv = client.get("/card-viewer/").text
    assert 'href="http://testserver/card-viewer/"' in cv
    assert 'href="http://testserver/card-viewer/static/style.css"' in cv
    assert "/simulation-viewer/" not in cv
