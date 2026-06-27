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

from .loader import filter_cards, load_all_cards, load_arena_id_index
from .models import ParsedCard, ParseStatus
from .parser import parse_card
from .seventeenlands_stats import load_premier_draft_stats
from .store import (
    load_parsed_cards,
    merge_detector_run,
    save_parsed_cards,
    status_histogram,
    update_parsed_card,
)

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
        c
        for c in pool
        if "Land" in str(c.get("type_line", "")) and c.get("oracle_id") not in in_sample
    ]
    return sample + extras[:target]


_STATUS_LABELS: dict[ParseStatus, str] = {
    ParseStatus.AUTO: "AUTO       ",
    ParseStatus.NEEDS_LLM: "NEEDS_LLM  ",
    ParseStatus.LLM_ENCODED: "LLM_ENCODED",
    ParseStatus.NEEDS_HUMAN: "NEEDS_HUMAN",
}


def _format_status(status: ParseStatus) -> str:
    """Pad lightly so the report scans easily on a terminal."""
    return _STATUS_LABELS.get(status, str(status))


def _short_type(type_line: str) -> str:
    """Single-word abbreviation of the card's primary type for the table."""
    primary = type_line.split("—", 1)[0].strip()
    for keyword in (
        "Land",
        "Creature",
        "Instant",
        "Sorcery",
        "Enchantment",
        "Artifact",
        "Planeswalker",
        "Battle",
    ):
        if keyword in primary:
            return keyword
    return primary or "?"


def _print_report(parsed: list[ParsedCard]) -> None:
    auto = [p for p in parsed if p.status is ParseStatus.AUTO]
    llm = [p for p in parsed if p.status is ParseStatus.NEEDS_LLM]
    llm_enc = [p for p in parsed if p.status is ParseStatus.LLM_ENCODED]
    needs_human = [p for p in parsed if p.status is ParseStatus.NEEDS_HUMAN]

    typer.echo("")
    typer.echo("=" * 100)
    parts = [f"{len(auto)} auto"]
    if llm_enc:
        parts.append(f"{len(llm_enc)} llm-encoded")
    if needs_human:
        parts.append(f"{len(needs_human)} needs-human")
    parts.append(f"{len(llm)} needs-llm")
    typer.echo(
        f"Parsed {len(parsed)} cards: "
        + ", ".join(parts)
        + f" ({100 * len(auto) / max(len(parsed), 1):.0f}% auto)."
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


SetsOption = Annotated[
    str,
    typer.Option(
        "--sets",
        "-s",
        help="Comma-separated set codes, e.g. 'TMT,ECL,TLA'.",
    ),
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Overwrite even llm_encoded / needs_human entries.",
    ),
]
ReparseNeedsHumanOption = Annotated[
    bool,
    typer.Option(
        "--reparse-needs-human",
        help=(
            "Re-parse needs_human entries with the current parser; preserve "
            "llm_encoded entries. Use after widening the parser to pick up "
            "cards that previously fell to human review."
        ),
    ),
]
LimitOption = Annotated[
    int,
    typer.Option(
        "--limit",
        "-l",
        min=1,
        help="Max cards to emit.",
    ),
]
JsonFlagOption = Annotated[
    bool,
    typer.Option(
        "--json",
        help="Emit machine-readable JSON instead of a human-readable report.",
    ),
]


def _parse_set_list(sets: str) -> list[str]:
    """Split a comma-separated list of set codes; uppercase + dedupe."""
    seen: list[str] = []
    for raw in sets.split(","):
        code = raw.strip().upper()
        if code and code not in seen:
            seen.append(code)
    return seen


def _bonus_sheet_scryfall_entries(
    set_code: str,
    primary_pool: list[dict[str, Any]],
    scryfall_by_name: dict[str, dict[str, Any]],
    *,
    data_root: Path | None,
) -> list[dict[str, Any]]:
    """For a set whose 17Lands Premier-Draft ratings parquet exists,
    return the Scryfall entries for any cards in the format that
    aren't in the primary set pool.

    Modern Limited formats often include a "bonus sheet" of reprints
    from older sets (TLA's Special Guests, TMT's Through the Omenpaths
    bonus slot, etc.). Those cards exist in Scryfall under their
    original set's code, not the current set's, so a naive
    ``set=<code>`` filter misses them. The 17Lands ratings parquet
    is the right source of truth for *which* cards are in the format
    — it lists every card a Premier-Draft player can encounter.

    Each returned dict is a Scryfall card ready to be parsed, with two
    identifying fields rewritten:

    * ``set`` → *set_code*, so the resulting ``ParsedCard.set_code``
      reports the current format rather than the reprint's origin set.
    * ``collector_number`` → ``bonus-<srcset>-<srccn>``, so a
      bonus-sheet card's source-set number can't collide with a primary
      card's (which would break the collector_number-keyed ``mark`` and
      apply-patches flows).

    arena_id for these reprints is *not* set here — MTGJSON's
    ``(oracle_id, set_code)`` index keys on the reprint's original
    printing and returns None under the current set code. It's
    backfilled from the same 17Lands ratings parquet by
    :func:`_backfill_arena_ids_from_ratings`, which covers primary cards
    (MTGJSON lags new sets) and bonus-sheet reprints alike.

    If the ratings parquet for *set_code* doesn't exist (e.g., a
    set whose download step hasn't run yet), returns ``[]``.
    """
    try:
        stats = load_premier_draft_stats(set_code, data_root=data_root)
    except FileNotFoundError:
        return []
    # Names covered by the primary pool, including DFC front-face fallback.
    primary_names: set[str] = set()
    for c in primary_pool:
        n = c.get("name")
        if n is None:
            continue
        primary_names.add(str(n))
        if " // " in str(n):
            primary_names.add(str(n).split(" // ", 1)[0])
    extras: list[dict[str, Any]] = []
    missed: list[str] = []
    for name in stats.by_name:
        if name in primary_names:
            continue
        match = scryfall_by_name.get(name)
        if match is None:
            missed.append(name)
            continue
        extra = dict(match)
        source_set = str(match.get("set", "?")).lower()
        source_cn = str(match.get("collector_number", "?"))
        extra["set"] = set_code.lower()
        extra["collector_number"] = f"bonus-{source_set}-{source_cn}"
        extras.append(extra)
    if missed:
        log.warning(
            "[%s] %d card name(s) from 17Lands ratings not in Scryfall: %s",
            set_code,
            len(missed),
            sorted(missed)[:10],
        )
    return extras


def _backfill_arena_ids_from_ratings(
    cards: list[ParsedCard],
    set_code: str,
    *,
    data_root: Path | None,
) -> int:
    """Stamp ``arena_id`` from 17Lands' ``mtga_id`` onto any card whose
    arena_id MTGJSON hasn't supplied yet. Mutates *cards* in place and
    returns the number of cards backfilled.

    Two populations need this on a freshly-rotated format:

    * **Primary-set cards** — MTGJSON's ``(oracle_id, set_code)`` index
      lags a set's Arena release by weeks, so every primary card parses
      with ``arena_id=None`` until MTGJSON catches up.
    * **Bonus-sheet reprints** — MTGJSON keys arena_id on the reprint's
      *original* printing, so the lookup returns None under the current
      format's set code.

    17Lands publishes ``mtga_id`` for every card in the format the moment
    ratings exist, so it's the right interim source for both. The
    three-tier :meth:`StatsLookup.match` (arena_id → exact name →
    front-face name) resolves DFCs whose ``ParsedCard.name`` is the joint
    ``Front // Back`` form. Cards that already carry an arena_id (from
    MTGJSON) are left untouched — MTGJSON stays authoritative; this only
    fills gaps.

    No-ops (returns 0) when the ratings parquet for *set_code* doesn't
    exist yet.
    """
    try:
        ratings = load_premier_draft_stats(set_code, data_root=data_root)
    except FileNotFoundError:
        return 0
    n_backfilled = 0
    for card in cards:
        if card.arena_id is not None:
            continue
        hit = ratings.match(card)
        if hit is not None:
            card.arena_id = hit.mtga_id
            n_backfilled += 1
    return n_backfilled


@app.command("run-detector")
def run_detector(
    sets: SetsOption = "TMT,ECL,TLA",
    data_root: DataRootOption = None,
    force: ForceOption = False,
    reparse_needs_human: ReparseNeedsHumanOption = False,
) -> None:
    """Re-run the deterministic parser across each set, persisting results.

    Cards in the existing per-set file with status ``llm_encoded`` or
    ``needs_human`` are preserved; everything else is rewritten with the
    fresh parse. Pass ``--force`` to overwrite even those.

    The pool for each set is the union of:

    * Scryfall cards with ``set=<code>`` (the primary set), and
    * "Bonus sheet" reprints — cards listed in the 17Lands Premier-Draft
      ratings parquet for the format but not in the primary Scryfall
      set. Each reprint's Scryfall dict is rewritten to claim ``set=<code>``
      before parsing, so the resulting ``ParsedCard.set_code`` reports
      the current format.

    After parsing, ``arena_id`` is backfilled from 17Lands' ``mtga_id``
    for every card MTGJSON hasn't ingested yet — both primary cards on a
    freshly-rotated format (MTGJSON lags a set's Arena release by weeks)
    and bonus-sheet reprints (MTGJSON keys arena_id on the original
    printing). See :func:`_backfill_arena_ids_from_ratings`.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("Loading Scryfall snapshot…")
    all_cards = load_all_cards(data_root)
    log.info("Loading MTGJSON arena_id index…")
    # One-shot load — the file is ~120 MiB and parsing it per set would
    # be silly. Build once, reuse across every parse_card call below.
    arena_id_index = load_arena_id_index(data_root)
    log.info("MTGJSON arena_id index covers %d printings.", len(arena_id_index))

    # Name → Scryfall dict over all normal-layout entries; used by the
    # bonus-sheet fallback to find the canonical printing of each
    # reprint by name.  Built once.
    scryfall_by_name: dict[str, dict[str, Any]] = {}
    for card in all_cards:
        if str(card.get("layout", "normal")) != "normal":
            continue
        n = card.get("name")
        if n is None:
            continue
        scryfall_by_name.setdefault(str(n), card)

    set_codes = _parse_set_list(sets)
    if not set_codes:
        typer.echo("No set codes provided. Aborting.")
        raise typer.Exit(code=1)

    typer.echo("")
    typer.echo("=" * 80)
    typer.echo(f"Re-running detector across {len(set_codes)} set(s): {', '.join(set_codes)}")
    typer.echo("=" * 80)

    for code in set_codes:
        pool = filter_cards(all_cards, set_code=code)
        if not pool:
            typer.echo(f"\n[{code}] no cards found in Scryfall snapshot — skipping.")
            continue
        bonus_extras = _bonus_sheet_scryfall_entries(
            code, pool, scryfall_by_name, data_root=data_root
        )
        if bonus_extras:
            log.info(
                "[%s] Including %d bonus-sheet card(s) from 17Lands ratings.",
                code,
                len(bonus_extras),
            )
            pool.extend(bonus_extras)
        freshly_parsed = [parse_card(card, arena_id_index=arena_id_index) for card in pool]
        # Backfill arena_id from 17Lands' mtga_id for every card MTGJSON
        # hasn't ingested yet — primary cards on a freshly-rotated format
        # and bonus-sheet reprints alike (see _backfill_arena_ids_from_ratings).
        # Runs before merge_detector_run so preserved llm_encoded entries
        # pick up the fresh arena_id via its carry-forward.
        n_backfilled = _backfill_arena_ids_from_ratings(freshly_parsed, code, data_root=data_root)
        if n_backfilled:
            log.info(
                "[%s] Backfilled arena_id for %d card(s) from 17Lands mtga_id.",
                code,
                n_backfilled,
            )
        merged, n_preserved, n_rewritten = merge_detector_run(
            code,
            freshly_parsed,
            data_root=data_root,
            force=force,
            reparse_needs_human=reparse_needs_human,
        )
        path = save_parsed_cards(code, merged, data_root=data_root)
        hist = status_histogram(code, data_root=data_root)
        total = sum(hist.values())
        typer.echo("")
        typer.echo(f"[{code}] wrote {len(merged)} cards to {path}")
        typer.echo(f"  rewritten: {n_rewritten}, preserved: {n_preserved}")
        for status in ParseStatus:
            count = hist.get(status, 0)
            pct = (100 * count / total) if total else 0.0
            typer.echo(f"  {status.value:<13} {count:>5}  ({pct:5.1f}%)")


@app.command("list-needs-llm")
def list_needs_llm(
    set_code: SetOption = "TLA",
    limit: LimitOption = 20,
    data_root: DataRootOption = None,
    json_out: JsonFlagOption = False,
) -> None:
    """Emit cards in the per-set file that still have status ``needs_llm``.

    Default output is a human-readable report listing collector number,
    name, mana cost, oracle text, and parser reasons. With ``--json``,
    emits a JSON list of full ParsedCard records so a script (or Claude)
    can ingest them directly.
    """
    cards = load_parsed_cards(set_code, data_root)
    needs = [c for c in cards if c.status == ParseStatus.NEEDS_LLM]
    needs = sorted(
        needs,
        key=lambda c: (
            int("".join(ch for ch in c.collector_number if ch.isdigit()) or "9999999"),
            c.collector_number,
        ),
    )[:limit]

    if json_out:
        typer.echo(json.dumps([c.model_dump(mode="json") for c in needs], indent=2))
        return

    if not needs:
        typer.echo(f"[{set_code}] no cards with status needs_llm.")
        return

    typer.echo("")
    typer.echo("=" * 100)
    typer.echo(f"[{set_code}] {len(needs)} cards needing LLM review (showing up to {limit}):")
    typer.echo("=" * 100)
    for c in needs:
        typer.echo("")
        typer.echo(f"#{c.collector_number}  {c.name}   [{c.type_line}]   ({c.rarity})")
        if c.mana_cost:
            typer.echo(f"  cost: {c.mana_cost.raw}  (cmc={c.mana_cost.cmc})")
        if c.power is not None or c.toughness is not None:
            typer.echo(f"  P/T: {c.power}/{c.toughness}")
        if c.evergreen_keywords:
            typer.echo(f"  evergreens: {', '.join(c.evergreen_keywords)}")
        typer.echo("  oracle:")
        for line in c.raw_oracle_text.splitlines() or [""]:
            typer.echo(f"    {line}")
        typer.echo("  parser notes:")
        for r in c.reasons:
            typer.echo(f"    - {r}")


CollectorOption = Annotated[
    str,
    typer.Option("--collector", "-c", help="Collector number of the card to update."),
]
StatusOption = Annotated[
    str,
    typer.Option(
        "--status",
        help="New status: auto / needs_llm / llm_encoded / needs_human.",
    ),
]
PatchOption = Annotated[
    Path | None,
    typer.Option(
        "--patch",
        help=(
            "Path to a JSON file containing a partial ParsedCard "
            "(at minimum the fields you want to update). If omitted, only "
            "--status is applied."
        ),
    ),
]


@app.command("mark")
def mark(
    set_code: SetOption = "TLA",
    collector: CollectorOption = "1",
    status: StatusOption = "llm_encoded",
    patch: PatchOption = None,
    data_root: DataRootOption = None,
) -> None:
    """Apply a JSON patch and/or update the status on a single stored card.

    Used by the iterative LLM-encoding loop. The card must already exist
    in the per-set parsed file.
    """
    try:
        new_status = ParseStatus(status)
    except ValueError as exc:
        typer.echo(f"Invalid status {status!r}. Valid: " + ", ".join(s.value for s in ParseStatus))
        raise typer.Exit(code=1) from exc

    cards = load_parsed_cards(set_code, data_root)
    target = next((c for c in cards if c.collector_number == collector), None)
    if target is None:
        typer.echo(
            f"No card with collector number {collector!r} in set {set_code!r}. "
            "Run 'run-detector' first to populate the per-set file."
        )
        raise typer.Exit(code=1)

    if patch is not None:
        patch_data = json.loads(patch.read_text(encoding="utf-8"))
        if not isinstance(patch_data, dict):
            typer.echo(f"Patch file must contain a JSON object, got {type(patch_data).__name__}.")
            raise typer.Exit(code=1)
        # Merge: start from the existing card's dict, apply patch keys.
        merged_dict = target.model_dump(mode="json")
        for k, v in patch_data.items():
            merged_dict[k] = v
        # Always apply the requested status last so it wins over patch.status
        # when both are present.
        merged_dict["status"] = new_status.value
        updated = ParsedCard.model_validate(merged_dict)
    else:
        updated = target.model_copy(update={"status": new_status})

    update_parsed_card(set_code, updated, data_root=data_root)
    typer.echo(f"[{set_code}] updated #{collector} '{updated.name}' → status={new_status.value}")


if __name__ == "__main__":
    app()
