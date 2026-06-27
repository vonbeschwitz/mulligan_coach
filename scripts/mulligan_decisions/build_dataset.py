"""Build a mulligan-decisions dataset from 17Lands replay data.

The 17Lands ``replay_data`` CSVs record, per game, every candidate
opening hand the player saw under London mulligan:

* ``candidate_hand_1`` is the first 7-card draw.
* ``candidate_hand_2`` is the next 7-card draw (after a mulligan).
* ... up through ``candidate_hand_7`` (mulligan all the way to one card).
* ``num_mulligans`` says how many mulligans the player took, so
  ``candidate_hand_{num_mulligans+1}`` is the hand they decided to keep.
* ``opening_hand`` is that kept hand AFTER bottoming (so 7-N cards) —
  we do not use it here because we want the pre-bottom 7-card draw to
  match the convention the model trains on.

This script downloads the replay CSVs for the configured sets (defaults:
TMT and TLA, Premier Draft) plus the global ``cards.csv`` reference
(Arena ID -> card name), and produces a per-set parquet with **one row
per mulligan DECISION**. Each row carries:

* The 7-card candidate hand (resolved to card names).
* The full deck composition (encoded as a sorted
  ``"Name xN | Name xM"`` string).
* ``was_kept`` — True if the player kept this hand, False if they
  mulliganed it.
* ``num_mulligans_in_game`` — total mulligans the player ended up doing.
* ``opp_num_mulligans`` — how many mulligans the opponent took.
* ``won`` — game outcome (same for every decision row from one game).
* ``on_play`` — whether the player was on the play.
* ``user_n_games_bucket`` / ``user_game_win_rate_bucket`` — 17Lands'
  coarsened buckets for the player's lifetime games played / game WR
  (so each decision is anchored to a measure of player skill).
* Various event-context fields (rank, opp_rank, colors, match
  wins/losses, etc.).

The output parquet is saved per set to
``data/processed/seventeenlands/mulligan_decisions/{SET}.{EVENT}.parquet``,
plus a glued ``combined.{EVENT}.parquet`` across sets.

Run with:
    .venv/Scripts/python.exe scripts/mulligan_decisions/build_dataset.py

Re-running is safe: downloads are skipped if the file already exists
with the expected size, and parquet output is overwritten.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sys
import urllib.request
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / "seventeenlands"
OUT_DIR = REPO_ROOT / "data" / "processed" / "seventeenlands" / "mulligan_decisions"

REPLAY_URL = (
    "https://17lands-public.s3.amazonaws.com/analysis_data/replay_data/"
    "replay_data_public.{set_code}.{event_type}.csv.gz"
)
CARDS_URL = "https://17lands-public.s3.amazonaws.com/analysis_data/cards/cards.csv"

DEFAULT_SETS = ("TMT", "TLA")
DEFAULT_EVENT_TYPE = "PremierDraft"

# Game-level columns kept verbatim from the source CSV. Order chosen so
# that the eventual output column ordering is sensible top-to-bottom.
# Note: ``event_match_wins`` / ``event_match_losses`` are draft-data
# fields and are NOT present in the replay CSV, so they're omitted.
GAME_COLS: tuple[str, ...] = (
    "expansion",
    "event_type",
    "draft_id",
    "build_index",
    "match_number",
    "game_number",
    "on_play",
    "rank",
    "opp_rank",
    "main_colors",
    "splash_colors",
    "opp_colors",
    "user_n_games_bucket",
    "user_game_win_rate_bucket",
    "opp_num_mulligans",
    "won",
    "num_turns",
)

log = logging.getLogger("build_mulligan_decisions")


# ---------------------------------------------------------------------------
# Download helpers. We stay on the stdlib (urllib) so this script doesn't
# add httpx to the model package's dep closure for one ad-hoc download.
# ---------------------------------------------------------------------------


def download_if_missing(url: str, dest: Path, expected_min_bytes: int = 1000) -> None:
    """Stream-download ``url`` to ``dest`` with a coarse progress line.

    Skips the download if ``dest`` already exists and is at least
    ``expected_min_bytes`` bytes — that's the cheap "we probably have
    it" check, good enough for ad-hoc rebuilds. To force a fresh
    download, delete the file. Uses an atomic rename so a Ctrl-C
    mid-download never leaves a corrupt file at the canonical path.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size >= expected_min_bytes:
        log.info("Already have %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)
        return

    tmp = dest.with_suffix(dest.suffix + ".tmp")
    log.info("Downloading %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "mulligan-coach-build/0.1"})
    with urllib.request.urlopen(req) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        with tmp.open("wb") as out:
            downloaded = 0
            last_pct = -1
            while True:
                chunk = resp.read(1 << 20)  # 1 MiB
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct != last_pct and pct % 5 == 0:
                        sys.stderr.write(
                            f"\r  {dest.name}: {downloaded / 1e6:6.1f} / "
                            f"{total / 1e6:6.1f} MB  ({pct:3d}%)"
                        )
                        sys.stderr.flush()
                        last_pct = pct
        if total:
            sys.stderr.write("\n")
    tmp.replace(dest)
    log.info("Wrote %s (%.1f MB)", dest.name, dest.stat().st_size / 1e6)


# ---------------------------------------------------------------------------
# Arena-ID -> card-name resolution.
# ---------------------------------------------------------------------------


def load_arena_id_to_name(cards_csv: Path) -> dict[str, str]:
    """Build a global Arena ID -> card name dict from the 17Lands cards.csv.

    IDs are unique per physical printing; reprints across sets get
    different IDs. Basic lands share an ID per printing. We index every
    row in the CSV, not just the sets we care about, since hands may
    include shared/reprinted IDs (e.g. the "Forest" Arena ID 11863 used
    across multiple sets).
    """
    mapping: dict[str, str] = {}
    with cards_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mapping[row["id"]] = row["name"]
    log.info("Loaded %d Arena IDs from cards.csv", len(mapping))
    return mapping


def resolve_hand(arena_id_str: str, id_to_name: dict[str, str]) -> str:
    """Resolve a pipe-delimited Arena ID string to pipe-delimited names.

    Unknown IDs are emitted as ``?<id>`` so we never silently drop
    information. In practice cards.csv covers everything that appears
    in the replay files (it's the same dataset 17Lands itself uses).
    """
    ids = arena_id_str.split("|")
    return "|".join(id_to_name.get(i, f"?{i}") for i in ids)


# ---------------------------------------------------------------------------
# Replay CSV -> decisions DataFrame.
# ---------------------------------------------------------------------------


def _sql_lit(s: str) -> str:
    """SQL string literal — DuckDB's COPY/read_csv path is not parameter-bound."""
    return "'" + s.replace("\\", "/").replace("'", "''") + "'"


def build_decisions(
    csv_gz_path: Path,
    set_code: str,
    event_type: str,
    id_to_name: dict[str, str],
) -> pd.DataFrame:
    """Extract one row per (game, candidate hand) from the replay CSV.

    The CSV is huge and wide (>2400 columns per row in TMT). DuckDB
    does the heavy lifting:

    1. Project only the columns we keep (~25 metadata + 7 candidate
       hands + ~150 deck_*), filtered to the expected (set, event).
    2. Use parallel UNNEST to "zip" the seven ``candidate_hand_i``
       slots into long format with a ``hand_index`` companion column.
       Empty (NULL) candidate slots are dropped — only real decisions
       remain.

    The deck composition is then encoded to a single string per row
    in pandas (faster than reflecting 150 columns through DuckDB into
    a JSON or struct). Since the deck is identical across all
    decision rows for one game, we deduplicate by game-key first so
    the per-row work is ~6x smaller.

    Finally, hand Arena IDs are mapped to card names via the
    pre-loaded ``id_to_name`` dict.
    """
    con = duckdb.connect(":memory:")
    csv_literal = _sql_lit(str(csv_gz_path))

    # Discover deck_* columns dynamically — each set has its own pool of
    # ~150 cards. We use the schema row, not a row count, so this is cheap.
    deck_cols = [
        c
        for c in con.execute(f"SELECT * FROM read_csv_auto({csv_literal}) LIMIT 0")
        .fetchdf()
        .columns
        if c.startswith("deck_")
    ]
    if not deck_cols:
        raise RuntimeError(f"No deck_* columns found in {csv_gz_path}")
    log.info("  %s: %d deck_* columns", set_code, len(deck_cols))

    quoted_deck = ", ".join(f'"{c}"' for c in deck_cols)
    quoted_game = ", ".join(f'"{c}"' for c in GAME_COLS)
    candidate_cols = ", ".join(f"candidate_hand_{i}" for i in range(1, 8))

    # We drop the empty candidate slots *inside DuckDB* (the WHERE on the
    # exploded CTE) rather than after the fact in pandas. The UNNEST turns
    # each game into 7 rows (one per candidate_hand_i), but most slots are
    # NULL — a player who kept their first hand only has candidate_hand_1.
    # Filtering in SQL means the ~6x-larger NULL-laden frame never reaches
    # pandas: it keeps peak memory down and avoids a full-width boolean-mask
    # copy of the wide deck matrix (which OOM'd on larger sets when DuckDB's
    # CSV type-sniffing brought the deck_* columns through as int64).
    sql = f"""
    WITH wide AS (
        SELECT
            {quoted_game},
            num_mulligans,
            {candidate_cols},
            {quoted_deck}
        FROM read_csv_auto({csv_literal})
        WHERE expansion = '{set_code}' AND event_type = '{event_type}'
    ),
    exploded AS (
        SELECT
            wide.* EXCLUDE ({candidate_cols}),
            unnest([{candidate_cols}]) AS hand_arena_ids,
            unnest([1, 2, 3, 4, 5, 6, 7]) AS hand_index
        FROM wide
    )
    SELECT * FROM exploded
    WHERE hand_arena_ids IS NOT NULL AND hand_arena_ids <> ''
    """
    # DuckDB -> Arrow -> pandas preserves int8/int16 dtypes so the wide
    # deck matrix stays memory-efficient (a plain .fetchdf() upcasts to
    # int64 and would balloon by ~8x).
    arrow_table = con.execute(sql).to_arrow_table()
    con.close()
    df = arrow_table.to_pandas()
    del arrow_table  # free the Arrow copy before any further pandas work
    df = df.reset_index(drop=True)
    log.info("  %s: %d decision rows", set_code, len(df))

    if df.empty:
        return df

    # mulligan_number is 0-indexed; was_kept is True iff this is the
    # candidate the player ultimately kept.
    df["mulligan_number"] = (df["hand_index"] - 1).astype("int8")
    df["was_kept"] = df["mulligan_number"] == df["num_mulligans"].astype("int8")
    df = df.rename(columns={"num_mulligans": "num_mulligans_in_game"})
    df["num_mulligans_in_game"] = df["num_mulligans_in_game"].astype("int8")
    df = df.drop(columns=["hand_index"])

    # ---- Encode deck as "Name xN | Name xM" sorted alphabetically. ----
    # The deck is identical for all decision rows from the same game, so
    # build the string once per unique game and broadcast back.
    deck_names = [c.removeprefix("deck_") for c in deck_cols]
    deck_arr = df[deck_cols].to_numpy(dtype=np.int16)

    game_key_cols = ["draft_id", "build_index", "match_number", "game_number"]
    game_key_series = list(zip(*(df[c].tolist() for c in game_key_cols), strict=True))

    first_row_per_game: dict[tuple[object, ...], int] = {}
    for idx, key in enumerate(game_key_series):
        first_row_per_game.setdefault(key, idx)

    encoded_by_game: dict[tuple[object, ...], str] = {}
    for key, first_idx in first_row_per_game.items():
        row = deck_arr[first_idx]
        parts = [f"{deck_names[j]} x{int(row[j])}" for j in np.where(row > 0)[0]]
        # deck_* column order in the CSV is the 17Lands canonical order,
        # which is alphabetical by card name already, but we re-sort here
        # to make the contract explicit and robust to upstream changes.
        encoded_by_game[key] = " | ".join(sorted(parts))

    df["deck"] = [encoded_by_game[k] for k in game_key_series]
    df = df.drop(columns=deck_cols)

    # Resolve hand Arena IDs to card names.
    df["hand"] = df["hand_arena_ids"].map(lambda s: resolve_hand(s, id_to_name))
    df["hand_size"] = df["hand"].map(lambda s: s.count("|") + 1).astype("int8")

    # Sanity: every candidate hand should be 7 cards under London mulligan.
    bad = df[df["hand_size"] != 7]
    if not bad.empty:
        log.warning(
            "  %s: %d/%d rows have hand_size != 7 (unexpected; first: %s)",
            set_code,
            len(bad),
            len(df),
            bad.iloc[0]["hand"],
        )

    # Final column projection.
    final_cols = [
        "expansion",
        "event_type",
        "draft_id",
        "build_index",
        "match_number",
        "game_number",
        "on_play",
        "rank",
        "opp_rank",
        "main_colors",
        "splash_colors",
        "opp_colors",
        "user_n_games_bucket",
        "user_game_win_rate_bucket",
        "num_mulligans_in_game",
        "opp_num_mulligans",
        "num_turns",
        "won",
        "mulligan_number",
        "was_kept",
        "hand_size",
        "hand",
        "hand_arena_ids",
        "deck",
    ]
    return df[final_cols]


# ---------------------------------------------------------------------------
# CLI / orchestration.
# ---------------------------------------------------------------------------


def _summarise(df: pd.DataFrame, label: str) -> None:
    n_games = df.groupby(["draft_id", "build_index", "match_number", "game_number"]).ngroups
    kept = df[df["was_kept"]]
    log.info(
        "  %s: %d decisions across %d games (kept=%d, mulliganed=%d, "
        "won-given-kept=%d/%d = %.3f, on_play_share=%.3f)",
        label,
        len(df),
        n_games,
        len(kept),
        len(df) - len(kept),
        int(kept["won"].sum()),
        len(kept),
        kept["won"].mean() if len(kept) else float("nan"),
        df["on_play"].mean(),
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sets",
        nargs="+",
        default=list(DEFAULT_SETS),
        help="Set codes (3 letters) to process. Default: TMT TLA",
    )
    parser.add_argument(
        "--event-type",
        default=DEFAULT_EVENT_TYPE,
        help="17Lands event type. Default: PremierDraft",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help="Where to write the per-set parquet files.",
    )
    parser.add_argument(
        "--no-combine",
        action="store_true",
        help="Skip writing the combined.<event>.parquet across all sets.",
    )
    args = parser.parse_args(argv)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    # Download cards.csv (once) — used to resolve Arena IDs in hands.
    cards_csv = RAW_DIR / "cards.csv"
    download_if_missing(CARDS_URL, cards_csv, expected_min_bytes=10_000)
    id_to_name = load_arena_id_to_name(cards_csv)

    written: list[Path] = []
    for set_code in args.sets:
        replay_path = RAW_DIR / f"replay_data_public.{set_code}.{args.event_type}.csv.gz"
        url = REPLAY_URL.format(set_code=set_code, event_type=args.event_type)
        download_if_missing(url, replay_path, expected_min_bytes=10 * 1024 * 1024)

        df = build_decisions(replay_path, set_code, args.event_type, id_to_name)
        out_path = args.out_dir / f"{set_code}.{args.event_type}.parquet"
        df.to_parquet(out_path, compression="zstd", index=False)
        written.append(out_path)
        log.info(
            "Wrote %s (%d rows, %.1f MB)",
            out_path,
            len(df),
            out_path.stat().st_size / 1e6,
        )
        _summarise(df, set_code)

    if not args.no_combine and len(written) > 1:
        combined = pd.concat([pd.read_parquet(p) for p in written], ignore_index=True)
        combined_path = args.out_dir / f"combined.{args.event_type}.parquet"
        combined.to_parquet(combined_path, compression="zstd", index=False)
        log.info(
            "Wrote %s (%d rows, %.1f MB)",
            combined_path,
            len(combined),
            combined_path.stat().st_size / 1e6,
        )
        _summarise(combined, "combined")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
