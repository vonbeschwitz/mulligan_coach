"""Headless CLI: tail the log, run the recommender, print verdicts.

Two purposes:

1. End-to-end validation without booting the GUI. Iterating on the
   log parser, the card index, and the recommendation wiring is much
   easier when the output is just text.
2. A usable execution mode for users on machines without a display
   (or who prefer not to run the GUI overlay).

The CLI is intentionally thin: build the
:class:`ArenaCardIndex` + :class:`RecommendationService`, wrap them
in an :class:`OverlayCoordinator`, then loop over events from
:class:`LogTailer` and print whatever the coordinator returns.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from threading import Event

from mulligan_coach_recommend import load_service

from ._frozen import configure_bundle_paths
from .arena_paths import default_log_path
from .card_index import ArenaCardIndex
from .coordinator import (
    CoordinatorOutput,
    DeckLoadedOutput,
    MatchResetOutput,
    MissingDataOutput,
    OverlayCoordinator,
    RecommendationOutput,
)
from .log_tailer import LogTailer

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    """``uv run mulligan-coach-overlay-headless`` entry point."""
    parser = argparse.ArgumentParser(
        description="Tail MTG Arena's Player.log and print keep/mulligan recommendations."
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help=(
            "Path to Arena Player.log. Defaults to the platform's "
            "standard location (override via MULLIGAN_COACH_OVERLAY_LOG)."
        ),
    )
    parser.add_argument(
        "--from-start",
        action="store_true",
        help="Read the log from the start instead of tailing from EOF.",
    )
    parser.add_argument(
        "--no-follow",
        action="store_true",
        help="Read once to EOF then exit (useful for replay).",
    )
    parser.add_argument(
        "--sets",
        type=str,
        default="",
        help=(
            "Comma-separated set codes to load parsed_cards for "
            "(e.g. 'TLA,TMT,ECL,SOS'). Empty / unset auto-discovers "
            "every set with parsed_cards on disk."
        ),
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging (DEBUG level on the tailer + coordinator).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # When frozen, route upstream path resolvers at the bundled copies
    # before any of them are touched. No-op from source. See
    # ``_frozen.configure_bundle_paths`` for details.
    configure_bundle_paths()

    log_path = args.log or default_log_path()
    if not log_path.parent.exists() and not args.no_follow:
        print(
            f"Arena log directory does not exist: {log_path.parent}\nIs MTG Arena installed?",
            file=sys.stderr,
        )
        return 2

    set_codes = (
        [s.strip().upper() for s in args.sets.split(",") if s.strip()] if args.sets else None
    )
    print("Loading card index and recommendation service...", file=sys.stderr)
    card_index = ArenaCardIndex.build(set_codes)
    service = load_service(card_index.loaded_sets)
    coordinator = OverlayCoordinator(card_index, service)
    db_summary = (
        f"Arena DB: {card_index.arena_db_path}"
        if card_index.arena_db_path is not None
        else "Arena DB: not found (only MTGJSON arena_ids available)"
    )
    print(
        f"Ready. {len(card_index.by_arena_id)} arena_ids indexed "
        f"({card_index.n_mtgjson_resolved} from MTGJSON, "
        f"{card_index.n_arena_db_resolved} from Arena DB) across "
        f"{len(card_index.loaded_sets)} set(s): "
        f"{', '.join(card_index.loaded_sets) or 'none'}. "
        f"Model loaded: {service.status.model_loaded}.\n"
        f"{db_summary}",
        file=sys.stderr,
    )

    stop_event = Event()

    def _stop(*_: object) -> None:
        log.info("stop signal received; shutting down")
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop)

    tailer = LogTailer(log_path, start_at_end=not args.from_start)
    print(f"Tailing {log_path}", file=sys.stderr)
    try:
        for event in tailer.tail(follow=not args.no_follow, stop_event=stop_event):
            output = coordinator.handle_event(event)
            if output is not None:
                print(_format_output(output))
                sys.stdout.flush()
    finally:
        service.shutdown()
    return 0


def _format_output(output: CoordinatorOutput) -> str:
    """One-line summary of a coordinator output.

    Designed for live tailing: shows the verdict prominently, prefixes
    with a tag the user can grep on. Verbose detail goes to the
    --verbose log channel, not stdout.
    """
    if isinstance(output, DeckLoadedOutput):
        if output.n_unresolved:
            return (
                f"[deck]    {output.n_cards} cards ({output.n_resolved} resolved, "
                f"{output.n_unresolved} missing encoding). Set: "
                f"{output.primary_set or 'unknown'}"
            )
        return (
            f"[deck]    {output.n_cards} cards, fully resolved. "
            f"Set: {output.primary_set or 'unknown'}"
        )
    if isinstance(output, RecommendationOutput):
        rec = output.recommendation
        assert rec is not None  # RecommendationOutput always carries one
        verdict_label = {
            "clear_keep": "CLEAR KEEP",
            "marginal_keep": "marginal keep",
            "marginal_mulligan": "marginal mulligan",
            "clear_mulligan": "CLEAR MULLIGAN",
        }[rec.verdict]
        play_draw = "play" if output.on_the_play else "draw"
        names = ", ".join(c.name for c in output.hand)
        return (
            f"[verdict] {verdict_label:>18}  mull {rec.mulligan_percent:5.1f}%  "
            f"(mull#{output.mulligan_count}, on the {play_draw})\n"
            f"          hand: {names}"
        )
    if isinstance(output, MissingDataOutput):
        return f"[wait]    {output.reason}"
    if isinstance(output, MatchResetOutput):
        return "[reset]   match ended; deck state cleared"
    return f"[unknown] {output!r}"


if __name__ == "__main__":
    sys.exit(main())
