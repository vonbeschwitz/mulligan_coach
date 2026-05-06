"""Command-line interface for the cards package.

The first useful command is ``parse-demo``: sample N cards from a chosen
set, run the deterministic parser, and print a report showing which were
auto-classified vs. need the LLM. The intent is a quick feedback loop on
how good the deterministic rules are, set by set.

Subsequent commands (LLM classification, 17Lands join, full-set dump) will
be added as the corresponding pipeline stages come online.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Annotated, Any

import typer

from .loader import filter_cards, load_all_cards
from .models import ParsedCard, ParseStatus
from .parser import parse_card

log = logging.getLogger(__name__)

app = typer.Typer(
    name="mulligan-coach-cards",
    help="Card representation: deterministic Scryfall parser, role tagging, 17Lands join.",
    no_args_is_help=True,
    add_completion=False,
)


SetOption = Annotated[
    str,
    typer.Option(
        "--set",
        "-s",
        help="Three-letter set code, e.g. TLA. Defaults to TLA — the latest Premier-Draft set.",
    ),
]
NOption = Annotated[
    int,
    typer.Option(
        "-n",
        "--n",
        min=1,
        help="How many cards to sample from the set.",
    ),
]
SeedOption = Annotated[
    int,
    typer.Option(
        "--seed",
        help="Random seed for the sample. Use the same seed to reproduce the report.",
    ),
]
JsonOption = Annotated[
    Path | None,
    typer.Option(
        "--json",
        help="If given, also dump the structured ParsedCard list as JSON to this path.",
    ),
]
DataRootOption = Annotated[
    Path | None,
    typer.Option(
        "--data-root",
        help="Override the data root. Defaults to <repo>/data or $MULLIGAN_COACH_DATA_ROOT.",
    ),
]


def _ensure_some_lands(
    sample: list[dict[str, Any]],
    pool: list[dict[str, Any]],
    target: int = 3,
) -> list[dict[str, Any]]:
    """If ``sample`` contains no lands, append up to ``target`` from ``pool``.

    Used to make sure the lands branch of the parser is exercised in the
    demo report — TLA's first 20 collector numbers are all white spells, so
    a small random sample can easily miss every land.
    """
    has_land = any("Land" in str(c.get("type_line", "")) for c in sample)
    if has_land:
        return sample
    in_sample = {c.get("oracle_id") for c in sample}
    extras = [
        c for c in pool
        if "Land" in str(c.get("type_line", "")) and c.get("oracle_id") not in in_sample
    ]
    return sample + extras[:target]


def _format_status(status: ParseStatus) -> str:
    """Pad / colourise lightly so the report scans easily on a terminal."""
    if status is ParseStatus.AUTO:
        return "AUTO     "
    return "NEEDS_LLM"


def _short_type(type_line: str) -> str:
    """Single-word abbreviation of the card's primary type for the table."""
    primary = type_line.split("—", 1)[0].strip()
    for keyword in ("Land", "Creature", "Instant", "Sorcery", "Enchantment", "Artifact",
                    "Planeswalker", "Battle"):
        if keyword in primary:
            return keyword
    return primary or "?"


def _print_report(parsed: list[ParsedCard]) -> None:
    auto = [p for p in parsed if p.status is ParseStatus.AUTO]
    llm = [p for p in parsed if p.status is ParseStatus.NEEDS_LLM]

    typer.echo("")
    typer.echo("=" * 100)
    typer.echo(
        f"Parsed {len(parsed)} cards: "
        f"{len(auto)} auto-classified, {len(llm)} need LLM "
        f"({100 * len(auto) / max(len(parsed), 1):.0f}% auto)."
    )
    typer.echo("=" * 100)

    typer.echo("")
    typer.echo(f"{'#':<5} {'Name':<35} {'Type':<13} {'Status':<10} Reasons")
    typer.echo("-" * 100)
    # Sort by collector number numerically when possible so the table reads
    # naturally; non-numeric collector numbers (e.g. "12a") are pushed to
    # the back rather than crashing.
    def _coll_key(p: ParsedCard) -> tuple[int, str]:
        digits = ""
        for ch in p.collector_number:
            if ch.isdigit():
                digits += ch
            else:
                break
        return (int(digits) if digits else 10**9, p.collector_number)

    for p in sorted(parsed, key=_coll_key):
        reason_summary = "; ".join(p.reasons) if p.reasons else "—"
        if len(reason_summary) > 60:
            reason_summary = reason_summary[:57] + "..."
        typer.echo(
            f"{p.collector_number:<5} {p.name[:34]:<35} {_short_type(p.type_line):<13} "
            f"{_format_status(p.status):<10} {reason_summary}"
        )

    if llm:
        typer.echo("")
        typer.echo("=" * 100)
        typer.echo("Cards needing LLM — full oracle text and parser notes:")
        typer.echo("=" * 100)
        for p in sorted(llm, key=_coll_key):
            typer.echo("")
            typer.echo(f"#{p.collector_number}  {p.name}   [{p.type_line}]   ({p.rarity})")
            if p.mana_cost:
                typer.echo(f"  cost: {p.mana_cost.raw}")
            if p.power is not None or p.toughness is not None:
                typer.echo(f"  P/T: {p.power}/{p.toughness}")
            if p.evergreen_keywords:
                typer.echo(f"  evergreens: {', '.join(p.evergreen_keywords)}")
            typer.echo("  oracle:")
            for line in p.raw_oracle_text.splitlines() or [""]:
                typer.echo(f"    {line}")
            typer.echo("  parser notes:")
            for r in p.reasons:
                typer.echo(f"    - {r}")


@app.command("parse-demo")
def parse_demo(
    set_code: SetOption = "TLA",
    n: NOption = 30,
    seed: SeedOption = 0,
    json_path: JsonOption = None,
    data_root: DataRootOption = None,
) -> None:
    """Sample N cards from a set, run the deterministic parser, print a report."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Loading Scryfall snapshot…")
    all_cards = load_all_cards(data_root)
    pool = filter_cards(all_cards, set_code=set_code)
    log.info("Set %s has %d candidate cards.", set_code, len(pool))
    if not pool:
        typer.echo(f"No cards found for set {set_code!r}. Aborting.")
        raise typer.Exit(code=1)

    rng = random.Random(seed)
    sample = rng.sample(pool, k=min(n, len(pool)))
    sample = _ensure_some_lands(sample, pool)

    parsed = [parse_card(card) for card in sample]
    _print_report(parsed)

    if json_path is not None:
        # ``model_dump`` (pydantic v2) gives a JSON-friendly dict.
        payload = [p.model_dump(mode="json") for p in parsed]
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        typer.echo(f"\nWrote {len(parsed)} ParsedCard records to {json_path}.")


if __name__ == "__main__":
    app()
