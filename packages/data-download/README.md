# mulligan-coach-data-download

Downloads and caches all external data the Mulligan Coach project needs:

- **17Lands** game data CSVs (one row per game) for recent Limited formats.
- **17Lands** card-ratings JSON (GIH WR, OH WR, drawn WR, ALSA, …).
- **Scryfall** bulk data (oracle text, mana costs, types, P/T).
- **MTGJSON** identifiers (Arena ID ↔ Scryfall ID mapping).

Raw downloads live in `data/raw/`, processed normalized versions in
`data/processed/`. The `data/` directory is gitignored.

## Quick start

```sh
# One-time: install deps for the workspace.
uv sync

# Show what the CLI can do.
uv run mulligan-coach-data --help

# Download everything for a custom set of formats:
uv run mulligan-coach-data refresh-all \
    --sets FIN,TDM,DFT \
    --event-types PremierDraft,TradDraft,Sealed,TradSealed

# Just one source:
uv run mulligan-coach-data refresh-17lands --sets FIN
uv run mulligan-coach-data refresh-scryfall
uv run mulligan-coach-data refresh-mtgjson

# What's cached and how fresh is it?
uv run mulligan-coach-data status
```

`refresh-all` is **incremental** — it uses ETag / Last-Modified to skip
unchanged files, so re-running it during a format only downloads what changed.

## Layout produced on disk

```
data/
├── raw/
│   ├── seventeenlands/
│   │   ├── game_data_public.<SET>.<EVENT_TYPE>.csv.gz
│   │   └── card_ratings.<SET>.<EVENT_TYPE>.json
│   ├── scryfall/
│   │   └── oracle_cards.<date>.json
│   └── mtgjson/
│       └── AllIdentifiers.json
└── processed/
    ├── seventeenlands/
    │   ├── games/<SET>/<EVENT_TYPE>.parquet
    │   └── ratings/<SET>/<EVENT_TYPE>.parquet
    ├── games.duckdb           # DuckDB views over the parquet files.
    └── manifest.json          # incremental-refresh bookkeeping.
```

## Tests

```sh
uv run pytest packages/data-download
```

Tests use `pytest-httpx` to mock the live endpoints — they never hit the
network. Live integration tests (those marked `@pytest.mark.integration`)
are skipped by default.
