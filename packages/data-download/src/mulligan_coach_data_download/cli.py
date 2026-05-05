"""Command-line interface for the data-download package.

Run ``mulligan-coach-data --help`` for the full menu. The most useful
commands:

* ``refresh-all``     — pull every source.
* ``refresh-17lands`` — pull just 17Lands data for a chosen set/event-type
  selection.
* ``refresh-scryfall`` / ``refresh-mtgjson`` — pull the standalone sources.
* ``status``          — summarize what's cached and how fresh it is.

The CLI is intentionally small. Most of the interesting logic lives in the
per-source modules; this file just wires arguments to function calls.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from . import mtgjson, paths, scryfall
from .config import DEFAULT_EVENT_TYPES, DEFAULT_SETS, DownloadSelection
from .http import make_client
from .logging_config import configure_logging
from .manifest import Manifest
from .seventeenlands import duckdb_views, game_data
from .seventeenlands import ratings as ratings_mod

log = logging.getLogger(__name__)

# Pre-joined defaults for typer arguments — typer requires literal defaults
# (no function calls in the signature), and ruff B008 enforces the same.
_DEFAULT_SETS_STR = ",".join(DEFAULT_SETS)
_DEFAULT_EVENT_TYPES_STR = ",".join(DEFAULT_EVENT_TYPES)

app = typer.Typer(
    name="mulligan-coach-data",
    help="Download and cache 17Lands, Scryfall, and MTGJSON data for the Mulligan Coach project.",
    no_args_is_help=True,
    add_completion=False,
)


# Reusable typer options ----------------------------------------------------

SetsOption = Annotated[
    str,
    typer.Option(
        "--sets",
        help=(
            f"Comma-separated set codes (e.g. FIN,TDM,DFT). Defaults to: {','.join(DEFAULT_SETS)}."
        ),
    ),
]
EventsOption = Annotated[
    str,
    typer.Option(
        "--event-types",
        help=(
            "Comma-separated event types from "
            "{PremierDraft,TradDraft,Sealed,TradSealed}. "
            f"Defaults to all four ({','.join(DEFAULT_EVENT_TYPES)})."
        ),
    ),
]
ProgressOption = Annotated[
    bool,
    typer.Option(
        "--progress/--no-progress",
        help="Show tqdm progress bars on long downloads.",
    ),
]
DataRootOption = Annotated[
    Path | None,
    typer.Option(
        "--data-root",
        help=(
            "Override the data directory. "
            "Defaults to <repo>/data, or $MULLIGAN_COACH_DATA_ROOT if set."
        ),
    ),
]


def _selection(sets: str, event_types: str) -> DownloadSelection:
    return DownloadSelection(sets=sets, event_types=event_types)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command("refresh-17lands")
def refresh_17lands(
    sets: SetsOption = _DEFAULT_SETS_STR,
    event_types: EventsOption = _DEFAULT_EVENT_TYPES_STR,
    progress: ProgressOption = True,
    data_root: DataRootOption = None,
) -> None:
    """Download 17Lands game data + card ratings, then refresh DuckDB views."""
    configure_logging()
    selection = _selection(sets, event_types)
    paths.ensure_layout(data_root)
    manifest = Manifest.load(paths.manifest_path(data_root))

    with make_client() as client:
        for set_code, event_type in selection.pairs():
            log.info("17Lands: %s / %s", set_code, event_type)
            game_data.refresh_game_data(
                set_code,
                event_type,
                client=client,
                manifest=manifest,
                root=data_root,
                show_progress=progress,
            )
            ratings_mod.refresh_ratings(
                set_code,
                event_type,
                client=client,
                manifest=manifest,
                root=data_root,
                show_progress=progress,
            )

    manifest.save(paths.manifest_path(data_root))
    counts = duckdb_views.build_views(root=data_root)
    typer.echo(
        f"Done. Views updated: games={counts['games']:,} rows, ratings={counts['ratings']:,} rows."
    )


@app.command("refresh-scryfall")
def refresh_scryfall(
    progress: ProgressOption = True,
    data_root: DataRootOption = None,
) -> None:
    """Download the latest Scryfall oracle-cards JSON."""
    configure_logging()
    paths.ensure_layout(data_root)
    manifest = Manifest.load(paths.manifest_path(data_root))
    with make_client() as client:
        entry = scryfall.refresh_oracle_cards(
            client=client,
            manifest=manifest,
            root=data_root,
            show_progress=progress,
        )
    manifest.save(paths.manifest_path(data_root))
    typer.echo(f"Scryfall oracle_cards: {entry.row_count} cards, saved to {entry.local_path}.")


@app.command("refresh-mtgjson")
def refresh_mtgjson(
    progress: ProgressOption = True,
    data_root: DataRootOption = None,
) -> None:
    """Download MTGJSON AllIdentifiers.json."""
    configure_logging()
    paths.ensure_layout(data_root)
    manifest = Manifest.load(paths.manifest_path(data_root))
    with make_client() as client:
        entry = mtgjson.refresh_all_identifiers(
            client=client,
            manifest=manifest,
            root=data_root,
            show_progress=progress,
        )
    manifest.save(paths.manifest_path(data_root))
    size = entry.size or 0
    typer.echo(f"MTGJSON AllIdentifiers: {size:,} bytes saved to {entry.local_path}.")


@app.command("refresh-all")
def refresh_all(
    sets: SetsOption = _DEFAULT_SETS_STR,
    event_types: EventsOption = _DEFAULT_EVENT_TYPES_STR,
    progress: ProgressOption = True,
    data_root: DataRootOption = None,
) -> None:
    """Download every source (17Lands + Scryfall + MTGJSON)."""
    refresh_17lands(
        sets=sets,
        event_types=event_types,
        progress=progress,
        data_root=data_root,
    )
    refresh_scryfall(progress=progress, data_root=data_root)
    refresh_mtgjson(progress=progress, data_root=data_root)


@app.command("status")
def status(data_root: DataRootOption = None) -> None:
    """Summarize what's cached on disk and how fresh each source is."""
    configure_logging()
    manifest_file = paths.manifest_path(data_root)
    if not manifest_file.exists():
        typer.echo("No manifest yet — run a refresh command first.")
        raise typer.Exit(code=0)

    manifest = Manifest.load(manifest_file)
    if not manifest.sources:
        typer.echo("Manifest exists but is empty.")
        return

    typer.echo(f"Sources tracked in {manifest_file}:")
    for url, entry in sorted(manifest.sources.items()):
        size_mb = (entry.size or 0) / (1024 * 1024)
        rows = f"{entry.row_count:,} rows" if entry.row_count is not None else "—"
        typer.echo(
            f"  {url}\n"
            f"    local:        {entry.local_path}\n"
            f"    size:         {size_mb:.2f} MiB\n"
            f"    rows:         {rows}\n"
            f"    downloaded:   {entry.downloaded_at}\n"
            f"    etag:         {entry.etag or '—'}"
        )
