"""Scryfall card-image URL resolution.

Returns the normal-size PNG URL for a card by name. Used by the
``/card-image/{name}`` route so the hand grid can ``<img src="...">``
straight to Scryfall's CDN (hot-linking is permitted by their TOS;
we don't have to host any images ourselves).

The lookup is two-stage:

1. **In-memory cache** populated from the loaded ``CardStore``'s
   Scryfall data. Each ``ParsedCard`` is built from the same
   ``oracle_cards.<date>.json`` dump the cards package loads at
   detector time; that dump includes ``image_uris``. To avoid
   re-reading the 170 MiB JSON at website startup we instead lazy-
   fetch from Scryfall's named-card endpoint on first miss and
   cache the result for the lifetime of the process.
2. **Live HTTP fetch** to ``https://api.scryfall.com/cards/named``
   on cache miss. Rate-limited courtesy delay built into httpx's
   default settings; we keep one shared client.

The Scryfall API is unauthenticated and permits up to ~10 req/s;
we don't expect more than a few dozen hand cards per session, so
no throttling beyond httpx's default connection pool is needed.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)

# Scryfall's "named" endpoint resolves a printed card name to its
# canonical card record (including image_uris.normal). ``fuzzy``
# tolerates minor misspellings, ``exact`` is strict; we use exact
# because ParsedCard.name is the canonical Scryfall name already.
_SCRYFALL_NAMED = "https://api.scryfall.com/cards/named"

# A 1x1 transparent PNG, base64-encoded. Used as a placeholder when
# Scryfall returns 404 (e.g. for our synthetic "BASIC" set code —
# we'd never hit Scryfall for those if the card is a basic land,
# but the fallback exists so a typo doesn't break the page).
_PLACEHOLDER_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="


@dataclass
class ScryfallImages:
    """Process-lifetime in-memory cache of card-name -> normal-image URL.

    Built lazily on first lookup miss. The cache key is the
    lower-cased card name (post-strip) so MTGA's mixed-case exports
    resolve through the same entry as the ParsedCard's canonical
    name.

    Access is async-safe via :attr:`lock` because the FastAPI
    handler is async and httpx is async; two concurrent hand updates
    could otherwise issue duplicate fetches for the same card.
    """

    client: httpx.AsyncClient
    cache: dict[str, str] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def build(cls) -> ScryfallImages:
        """Construct an instance with a fresh httpx ``AsyncClient``.

        The client is held for the app's lifetime; FastAPI's
        ``lifespan`` handler closes it on shutdown. We set a polite
        ``User-Agent`` because Scryfall's docs request one.
        """
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(5.0, connect=2.0),
            headers={
                "User-Agent": "mulligan-coach-website/0.0.0 (https://github.com/Bastian-Beschwitz/mulligan-coach)",
                "Accept": "application/json",
            },
        )
        return cls(client=client)

    async def url_for(self, card_name: str) -> str:
        """Return the normal-size image URL for *card_name*.

        On Scryfall miss, returns the 1x1 transparent placeholder
        data URL — the route's 302 to the data URL keeps the hand
        grid from showing broken-image icons.

        Lookup keys are lower-cased; ``card_name`` is stripped so
        trailing whitespace doesn't poison the cache.
        """
        key = card_name.strip().lower()
        if not key:
            return _PLACEHOLDER_DATA_URL

        async with self.lock:
            cached = self.cache.get(key)
        if cached is not None:
            return cached

        url = await self._fetch(card_name)
        async with self.lock:
            self.cache[key] = url
        return url

    async def _fetch(self, card_name: str) -> str:
        """Issue one Scryfall ``cards/named?exact=...`` request.

        Returns the placeholder on any failure (network, 404, JSON
        shape mismatch, missing ``image_uris``). DFCs surface the
        front-face image via Scryfall's ``card_faces[0].image_uris``;
        we fall back to that when the top-level ``image_uris`` is
        absent.
        """
        try:
            resp = await self.client.get(_SCRYFALL_NAMED, params={"exact": card_name})
        except httpx.HTTPError:
            log.warning("scryfall request failed for %r", card_name, exc_info=True)
            return _PLACEHOLDER_DATA_URL
        if resp.status_code != 200:
            log.info("scryfall %s for %r", resp.status_code, card_name)
            return _PLACEHOLDER_DATA_URL
        data = resp.json()
        return _extract_image_url(data)

    async def aclose(self) -> None:
        """Close the underlying ``httpx`` client at app shutdown."""
        await self.client.aclose()


def _extract_image_url(card_json: dict[str, object]) -> str:
    """Pluck ``image_uris.normal`` (or the DFC front-face equivalent)."""
    image_uris = card_json.get("image_uris")
    if isinstance(image_uris, dict):
        normal = image_uris.get("normal")
        if isinstance(normal, str):
            return normal
    # DFCs put image_uris under card_faces[0].
    faces = card_json.get("card_faces")
    if isinstance(faces, list) and faces:
        front = faces[0]
        if isinstance(front, dict):
            face_images = front.get("image_uris")
            if isinstance(face_images, dict):
                normal = face_images.get("normal")
                if isinstance(normal, str):
                    return normal
    return _PLACEHOLDER_DATA_URL
