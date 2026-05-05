# Mulligan Coach

A real-time mulligan decision helper for Magic: The Gathering Arena Limited
(Premier Draft, Sealed). Given an opening hand and decklist, it estimates the
win probability of keeping vs. mulliganing and recommends a decision.

See [`CLAUDE.md`](CLAUDE.md) for the full project overview, design, and
sub-project layout. Each sub-project under `packages/` has its own
`CLAUDE.md` with package-specific details.

## Requirements

- Python 3.12 (uv will install it on demand if not present).
- [uv](https://docs.astral.sh/uv/) for dependency management. Install with
  `pip install --user uv` and add `%APPDATA%\Python\Python<ver>\Scripts` to
  your `PATH`, or follow the official installer at
  <https://docs.astral.sh/uv/getting-started/installation/>.

## Getting started

```sh
uv sync                 # create .venv and install everything
uv run pytest           # run all tests
uv run ruff check       # lint
uv run mypy             # type-check
```

To run a single sub-project's tests:

```sh
uv run pytest packages/data-download
```

## Sub-projects

| Package | Status | Description |
|---|---|---|
| `data-download` | scaffolding | Fetches & caches 17Lands, Scryfall, MTGJSON data. |
| `cards`         | not started | Shared card representation (parsed cost, types, roles, 17Lands stats). |
| `simulation`    | not started | Monte Carlo playability simulator. |
| `model`         | not started | XGBoost win-probability model. |
| `website`       | not started | FastAPI + HTMX testing/validation UI. |
| `overlay`       | not started | PyQt6 in-game overlay. |

## License

MIT — see [`LICENSE`](LICENSE).
