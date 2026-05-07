# card-viewer

Local web utility for visually verifying the `ParsedCard` encodings produced
by the `mulligan-coach-cards` package. Shows the actual Scryfall card image
alongside the structured JSON encoding, with name search and structured
filtering on encoding fields (parser status, types, colors, role flags,
mode kinds).

This is a developer tool. It is not part of the shipped Mulligan Coach
recommendation pipeline.

## Run it

From the workspace root:

```
uv sync
uv run card-viewer
```

Then open <http://127.0.0.1:8000> in a browser.

By default it loads the current Premier-Draft sets (TMT, ECL, TLA — about
740 cards). Override via env var:

```
# PowerShell
$env:CARD_VIEWER_SETS = "TLA"; uv run card-viewer

# bash
CARD_VIEWER_SETS=TLA uv run card-viewer
```

## What it shows

- **Index page**: filter sidebar (name search, parser status, set, colors,
  primary type, role flags, mode kinds, presence of mana abilities) and a
  grid of matching cards. Filtering is live via HTMX — no page reloads.
- **Card detail page**: large card image on the left; on the right, the
  card's chips (status, types, colors), the `reasons` list from the parser
  (helpful for `NEEDS_LLM` cards), and the full `ParsedCard` JSON
  pretty-printed.

The card images are hotlinked from Scryfall's CDN; nothing is downloaded
or cached locally beyond what's already in `data/raw/scryfall/`.

## Tests

```
uv run pytest utilities/card_viewer
```

The smoke test boots the FastAPI app via `TestClient` and hits each route.
It auto-skips if `data/raw/scryfall/oracle_cards.*.json` is missing (so
fresh checkouts without downloaded data don't fail CI).
