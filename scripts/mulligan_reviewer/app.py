"""Tiny FastAPI utility: hand-review one mulligan decision at a time.

Use case: we have ~thousands of skilled-player decisions in
``models/all3_v2/compare_win_choice.parquet``. We want to *eyeball*
the model-disagreement quadrants to figure out what the models are
missing, without scrolling through CSVs.

This utility:

* Loads the per-row predictions parquet (default
  ``models/all3_v2/compare_win_choice.parquet``).
* Filters to one of a handful of preset quadrants (default
  ``both_keep_player_mulled`` — both win+choice said keep, skilled
  player mulled anyway).
* Renders one hand at a time as a page with Scryfall card images +
  two big buttons (Keep / Mulligan).
* Appends each click to a JSONL vote log
  (``data/processed/mulligan_review/<stem>.jsonl``) and auto-advances
  to the next un-reviewed hand.
* Skips already-voted decisions on startup so you can come back to it.

Run:
    .venv/Scripts/python.exe scripts/mulligan_reviewer/app.py
    # then open http://127.0.0.1:8765/  (auto-opened by default)

Why a FastAPI app rather than a Qt dialog: the website already
demonstrates the pattern (FastAPI + Jinja2 + Scryfall image
hot-linking), the templates need no JS framework, and a browser is
the most ergonomic surface for "look at this picture and click a
button."
"""

from __future__ import annotations

import argparse
import json
import urllib.parse
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock, Timer
from typing import Any

import pandas as pd
import uvicorn
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PARQUET = REPO_ROOT / "models" / "all3_v2" / "compare_win_choice.parquet"
DEFAULT_VOTES_DIR = REPO_ROOT / "data" / "processed" / "mulligan_review"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Matches packages/recommend/src/.../service.py — kept duplicated here
# so this script has no dependency on the recommend package's import
# closure (just pandas + fastapi).
MULLIGAN_BIAS = 0.04


# ---------------------------------------------------------------------------
# Filter presets
# ---------------------------------------------------------------------------


# Each preset returns a mask on the per-row dataframe. The defaults
# focus on the quadrants from the compare-win-choice analysis where
# the model and the player disagree most strongly.
def _preset_both_keep_player_mulled(df: pd.DataFrame) -> pd.Series:
    """Both models said keep, skilled player mulled."""
    return (
        (df["p_keep_win"] >= df["p_mull_win"] + MULLIGAN_BIAS)
        & (df["p_keep_choice"] >= 0.5)
        & (~df["was_kept"])
    )


def _preset_both_mull_player_kept(df: pd.DataFrame) -> pd.Series:
    """Both models said mulligan, skilled player kept."""
    return (
        (df["p_keep_win"] < df["p_mull_win"] + MULLIGAN_BIAS)
        & (df["p_keep_choice"] < 0.5)
        & (df["was_kept"])
    )


def _preset_win_mull_player_kept(df: pd.DataFrame) -> pd.Series:
    """Win model said mull, player kept (interesting because choice usually agrees with player)."""
    return (df["p_keep_win"] < df["p_mull_win"] + MULLIGAN_BIAS) & (df["was_kept"])


def _preset_win_keep_choice_mull(df: pd.DataFrame) -> pd.Series:
    """The two models disagree: win says keep, choice says mull (66 hands in our sample)."""
    return (df["p_keep_win"] >= df["p_mull_win"] + MULLIGAN_BIAS) & (df["p_keep_choice"] < 0.5)


def _preset_win_mull_choice_keep(df: pd.DataFrame) -> pd.Series:
    """The two models disagree: win says mull, choice says keep (142 hands in our sample)."""
    return (df["p_keep_win"] < df["p_mull_win"] + MULLIGAN_BIAS) & (df["p_keep_choice"] >= 0.5)


def _preset_all(df: pd.DataFrame) -> pd.Series:
    return pd.Series([True] * len(df), index=df.index)


PRESETS: dict[str, Any] = {
    "both_keep_player_mulled": _preset_both_keep_player_mulled,
    "both_mull_player_kept": _preset_both_mull_player_kept,
    "win_mull_player_kept": _preset_win_mull_player_kept,
    "win_keep_choice_mull": _preset_win_keep_choice_mull,
    "win_mull_choice_keep": _preset_win_mull_choice_keep,
    "all": _preset_all,
}


# ---------------------------------------------------------------------------
# Decision queue + vote log
# ---------------------------------------------------------------------------


def _decision_key(row: pd.Series) -> str:
    """Stable identifier for one decision row.

    The mulligan_decisions parquet records ``(draft_id, build_index,
    match_number, game_number)`` per row; their combination uniquely
    identifies a mulligan decision.
    """
    return f"{row['draft_id']}|{row['build_index']}|{row['match_number']}|{row['game_number']}"


def load_decisions(parquet_path: Path, preset: str) -> pd.DataFrame:
    """Read the per-row parquet and apply the named filter preset."""
    if preset not in PRESETS:
        raise SystemExit(f"unknown preset {preset!r}; choose from {sorted(PRESETS)}")
    df = pd.read_parquet(parquet_path)
    # Only ``p_keep_choice`` is universally required — some review
    # parquets are choice-model-only (e.g. the clear-keep-but-mulled
    # cohort), with win-model arms left NaN. Presets that reference
    # the win arms will exclude those rows via comparison-with-NaN.
    df = df.dropna(subset=["p_keep_choice"]).copy()
    mask = PRESETS[preset](df)
    df = df.loc[mask].reset_index(drop=True)
    df["decision_key"] = df.apply(_decision_key, axis=1)
    # Stable shuffle so the order is consistent across sessions (handy
    # if multiple reviewers want to vote on the same queue).
    return df.sort_values("decision_key").reset_index(drop=True)


def load_existing_votes(path: Path) -> set[str]:
    """Build the set of already-voted decision keys from the JSONL log."""
    if not path.exists():
        return set()
    voted: set[str] = set()
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                voted.add(rec["decision_key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return voted


# ---------------------------------------------------------------------------
# Scryfall image helper
# ---------------------------------------------------------------------------


def _scryfall_image_url(card_name: str) -> str:
    """Return a Scryfall hot-link URL that resolves directly to a card image.

    Uses the ``named?exact=…&format=image`` endpoint, which 302s to
    the Scryfall CDN's normal-size PNG. The browser caches the
    redirect target, so re-renders are free after the first load.

    DFC handling: 17Lands hand strings carry the front-face name
    only; Scryfall's ``named?exact`` resolves the front face to the
    canonical card, which is what we want.
    """
    quoted = urllib.parse.quote(card_name, safe="")
    return f"https://api.scryfall.com/cards/named?exact={quoted}&format=image&version=normal"


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def make_app(parquet_path: Path, votes_path: Path, preset: str) -> FastAPI:
    df = load_decisions(parquet_path, preset)
    voted = load_existing_votes(votes_path)
    # The vote-log writer + the voted-set update need to be atomic so
    # rapid clicks don't race. One lock is plenty — this is a
    # local-only single-user utility.
    vote_lock = Lock()

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app = FastAPI()

    @app.get("/")
    def home(request: Request) -> Any:
        remaining = df[~df["decision_key"].isin(voted)]
        total = len(df)
        reviewed = total - len(remaining)
        if remaining.empty:
            return templates.TemplateResponse(
                request,
                "done.html",
                {
                    "total": total,
                    "preset": preset,
                    "votes_path": str(votes_path),
                },
            )
        row = remaining.iloc[0]
        hand_names = [c.strip() for c in str(row["hand"]).split("|") if c.strip()]
        hand = [{"name": n, "image": _scryfall_image_url(n)} for n in hand_names]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "decision_key": str(row["decision_key"]),
                "hand": hand,
                "expansion": str(row["expansion"]),
                "on_play": bool(row["on_play"]),
                "mulligan_number": int(row["mulligan_number"]),
                "p_keep_win": float(row["p_keep_win"]),
                "p_mull_win": float(row["p_mull_win"]),
                "p_keep_choice": float(row["p_keep_choice"]),
                "was_kept": bool(row["was_kept"]),
                "won": bool(row["won"]) if not pd.isna(row.get("won")) else None,
                "reviewed": reviewed,
                "total": total,
                "preset": preset,
            },
        )

    @app.post("/vote")
    def vote(decision_key: str = Form(...), choice: str = Form(...)) -> RedirectResponse:
        if choice not in ("keep", "mulligan", "skip"):
            raise HTTPException(400, detail=f"invalid choice: {choice!r}")
        match = df[df["decision_key"] == decision_key]
        if match.empty:
            raise HTTPException(404, detail=f"unknown decision_key: {decision_key!r}")
        row = match.iloc[0]
        with vote_lock:
            if decision_key in voted:
                # Already voted (probably a double-submit) — just skip.
                return RedirectResponse("/", status_code=303)
            payload = {
                "decision_key": decision_key,
                "choice": choice,
                "timestamp": datetime.now(UTC).isoformat(),
                "preset": preset,
                "hand": str(row["hand"]),
                "expansion": str(row["expansion"]),
                "on_play": bool(row["on_play"]),
                "mulligan_number": int(row["mulligan_number"]),
                "p_keep_win": float(row["p_keep_win"]),
                "p_mull_win": float(row["p_mull_win"]),
                "p_keep_choice": float(row["p_keep_choice"]),
                "was_kept": bool(row["was_kept"]),
                "won": bool(row["won"]) if not pd.isna(row.get("won")) else None,
            }
            votes_path.parent.mkdir(parents=True, exist_ok=True)
            with votes_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload) + "\n")
            voted.add(decision_key)
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "preset": preset,
            "total": len(df),
            "voted": len(voted),
            "remaining": len(df) - len(voted),
            "votes_path": str(votes_path),
        }

    return app


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--parquet",
        type=Path,
        default=DEFAULT_PARQUET,
        help="Per-row predictions parquet from compare_win_choice_models.py.",
    )
    ap.add_argument(
        "--preset",
        type=str,
        default="both_keep_player_mulled",
        choices=sorted(PRESETS),
        help="Decision filter to review.",
    )
    ap.add_argument(
        "--votes-dir",
        type=Path,
        default=DEFAULT_VOTES_DIR,
        help="Directory the vote-log JSONL will live in.",
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't auto-open a browser tab on launch.",
    )
    args = ap.parse_args()

    votes_path = args.votes_dir / f"{args.preset}.jsonl"
    app = make_app(args.parquet, votes_path, args.preset)

    if not args.no_browser:
        Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()

    print(f"Preset: {args.preset}")
    print(f"Votes file: {votes_path}")
    print(f"Open http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
