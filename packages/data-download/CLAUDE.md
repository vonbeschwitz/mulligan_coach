# data-download — Claude instructions

## Purpose

Single source of truth for fetching the project's external data:
17Lands game data + ratings, Scryfall bulk data, MTGJSON identifiers.

This package is a **leaf node** — it has no consumers in code form. Other
packages (`cards`, `simulation`, `model`) read its outputs from disk
(parquet files, the DuckDB database, raw JSON), not via Python imports.
This keeps the dependency graph clean and lets the data-download package
evolve without forcing churn elsewhere.

## Layout

```
src/mulligan_coach_data_download/
├── paths.py                 # Resolve data/raw, data/processed, etc.
├── config.py                # Default sets / event types; pydantic models.
├── http.py                  # Shared httpx client: retries, conditional GET, streamed downloads, progress.
├── manifest.py              # data/processed/manifest.json: per-source ETag/sha/row_count for incremental refresh.
├── logging_config.py        # Stdlib logging setup.
├── seventeenlands/
│   ├── game_data.py         # gz CSV → parquet (must preserve expansion + event_type columns).
│   ├── ratings.py           # ratings JSON → parquet.
│   └── duckdb_views.py      # `games` and `ratings` unified views over the parquet files.
├── scryfall.py              # Bulk-data endpoint → oracle_cards JSON.
├── mtgjson.py               # AllIdentifiers JSON.
└── cli.py                   # typer app with refresh-all / refresh-17lands / refresh-scryfall / refresh-mtgjson / status.
```

## Critical invariants

1. **Format/event-type marking.** Every row in every game-data parquet must
   carry non-null `expansion` and `event_type` columns. `game_data.py`
   asserts this at write time. The unified DuckDB `games` view selects them
   explicitly. Losing this signal silently corrupts the model's training.
2. **Atomic writes.** All downloads stream to a temp file in the same
   directory, then `os.replace()` into the final path. Partial files must
   never appear at the canonical path.
3. **Incremental refresh.** `manifest.json` records ETag / Last-Modified /
   sha256 / size / row_count per source URL. A re-run with no upstream
   changes should be a no-op.
4. **`uv.lock` is committed.** Don't add `uv.lock` to `.gitignore`.

## Tests

- `tests/conftest.py` provides a tmp data root and `pytest-httpx` fixtures.
- All HTTP is mocked — tests never hit the network.
- Live integration tests use `@pytest.mark.integration` and are opt-in:
  `uv run pytest -m integration packages/data-download`.

## When 17Lands changes

If 17Lands changes its CSV column layout or URL scheme, the change goes
**only** here — no parsing logic should leak into other packages.

## CLI conventions

- `--sets FIN,TDM,DFT` — comma-separated set codes.
- `--event-types PremierDraft,TradDraft,Sealed,TradSealed` — comma-separated.
- Cube is intentionally **not** a default event type (the project is scoped
  to Premier Draft and Sealed; Cube is excluded by user decision).
