# dev-site

Umbrella FastAPI app that mounts every Mulligan Coach dev utility under
one URL, so all of them can be browsed simultaneously on the same port.

This is a developer tool. It is not part of the shipped Mulligan Coach
recommendation pipeline.

## Run it

From the workspace root:

```
uv sync
uv run dev-site
```

Then open <http://127.0.0.1:8000>:

| URL                                            | What it is                                                   |
| ---------------------------------------------- | ------------------------------------------------------------ |
| <http://127.0.0.1:8000/>                       | Landing page with links to each mounted utility              |
| <http://127.0.0.1:8000/card-viewer/>           | [card-viewer](../card_viewer/README.md) — ParsedCard viewer  |
| <http://127.0.0.1:8000/simulation-viewer/>     | [simulation-viewer](../simulation_viewer/README.md) — trace  |

The mounted utilities' env-var overrides still apply, e.g.

```
# PowerShell — narrow card-viewer to one set, run both at once
$env:CARD_VIEWER_SETS = "TLA"; $env:SIMULATION_VIEWER_SETS = "TLA"; uv run dev-site
```

For autoreload during template hacking on the umbrella itself:

```
uv run uvicorn dev_site.app:app --reload
```

## When to use the standalone entry points instead

Each utility keeps its own console script (`uv run card-viewer`,
`uv run simulation-viewer`). Useful when you want to debug just one
utility without the umbrella's lifespan composition, or when running
`uvicorn --reload` against a single utility's source tree.

## Tests

```
uv run pytest utilities/dev_site
```

The smoke test boots the umbrella via `TestClient` and verifies the
landing page plus each mounted utility's `/healthz` under its prefix.
It auto-skips if `data/raw/scryfall/oracle_cards.*.json` is missing
(card-viewer needs the Scryfall snapshot at startup).
