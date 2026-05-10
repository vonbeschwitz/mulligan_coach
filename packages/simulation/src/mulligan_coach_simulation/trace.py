"""Pydantic models for per-game traces and per-turn snapshots.

These types are the *output* of the simulator — they're JSON-serialised
and travel to downstream stages (the model package, the website).
Pydantic gives us free validation and serialisation; the runtime types
in :mod:`runtime` use plain dataclasses for speed.

:func:`pretty_print` renders a :class:`GameTrace` (recorded with
``verbose=True``) into a human-readable transcript; useful for
debugging the policies and for explaining a recommendation in the
overlay.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class CastabilityRecord(BaseModel):
    """One row of the castability snapshot: a single mode of a single
    card in hand, on a single turn, plus whether it's castable.

    ``witness_land_choice`` is the ``instance_id`` of the land we'd
    have to play this turn to make the mode castable, or ``None`` if
    the card is castable without any land drop. Both ``castable=False``
    and ``castable=True with witness=None`` are meaningful — the latter
    means "already castable from current battlefield".
    """

    card_instance_id: int = Field(description="instance_id of the card in hand")
    card_name: str = Field(description="Display name for traces and aggregates.")
    mode_index: int = Field(ge=0, description="Index into card.parsed.modes.")
    mode_kind: str = Field(description="Mode kind (cast / cycle / land_cycle / channel).")
    castable: bool
    witness_land_choice: int | None = Field(
        default=None,
        description=(
            "instance_id of the hypothetical land drop this turn that makes "
            "the mode castable. None means either uncastable (when castable=False) "
            "or castable without any new land (when castable=True)."
        ),
    )


class TurnSnapshot(BaseModel):
    """The recorded state at the start of one turn's main phase.

    Aggregated across games and turns, the ``castability`` rows feed
    the per-card aggregate stats. ``aggregate_castable`` is the
    per-card "any mode castable" rollup, useful for quick lookups.

    ``lands_in_play_after_drop`` and ``mana_sources_at_start_of_main``
    are set by the engine after each turn's land drop, and feed the
    game-level "did you make your Nth land drop?" / "how much mana
    did you have?" aggregates downstream.

    Turn 5 is a partial snapshot — the engine runs only draw + land
    drop on turn 5, so ``castability`` / ``aggregate_castable`` are
    empty there. It exists solely so ``p_land_drop_by_turn_5`` is
    computable without extending the full turn pipeline.
    """

    turn: int = Field(ge=1, le=5)
    cards_drawn: list[str] = Field(
        default_factory=list, description="Names drawn this turn (empty for turn 1 on the play)."
    )
    castability: list[CastabilityRecord] = Field(default_factory=list)
    aggregate_castable: dict[int, bool] = Field(
        default_factory=dict,
        description="instance_id → 'any hand-mode castable this turn'",
    )
    lands_in_play_after_drop: int = Field(
        default=0,
        ge=0,
        description=(
            "Number of lands on the battlefield after this turn's land drop "
            "(or after the choice to skip it). Used by the game-level "
            "``p_land_drop_by_turn`` aggregate."
        ),
    )
    mana_sources_at_start_of_main: int = Field(
        default=0,
        ge=0,
        description=(
            "Lands plus non-creature mana permanents plus non-summoning-sick "
            "mana dorks on the battlefield, at start of main phase after this "
            "turn's land drop. Feeds ``expected_mana_count_turn`` downstream. "
            "Always zero on turn 5 (the lookahead step doesn't track mana use)."
        ),
    )


class ModeFirstTurn(BaseModel):
    """First turn a particular mode of a particular card instance was
    castable. ``first_castable_turn = 5`` means it was never castable
    in the 4-turn window (the simulator's '5+' bucket)."""

    mode_index: int
    mode_kind: str
    first_castable_turn: int = Field(ge=1, le=5)


class InstanceOutcome(BaseModel):
    """Per-card-instance summary across a single game.

    Captures everything the aggregator needs to compute per-name stats
    without re-walking the per-turn records:

    * ``first_in_hand_turn``: 0 if in opening hand, 1..4 if drawn that
      turn, 5 if never reached the hand in this game.
    * ``first_castable_turn``: 1..4 if any hand-mode was castable on
      that turn, 5 if never castable in the 4-turn window.
    * ``modes``: per-mode first-castable-turn for the modes the
      simulator evaluates (cast / cycle / land_cycle / channel).
    """

    instance_id: int
    name: str
    oracle_id: str
    first_in_hand_turn: int = Field(ge=0, le=5)
    first_castable_turn: int = Field(ge=1, le=5)
    modes: list[ModeFirstTurn] = Field(default_factory=list)


class DrawEvent(BaseModel):
    """A single card was drawn this turn."""

    kind: Literal["draw"] = "draw"
    turn: int
    card_name: str


class LandDropEvent(BaseModel):
    """The land-drop policy made (or did not make) a play."""

    kind: Literal["land_drop"] = "land_drop"
    turn: int
    chosen_instance_id: int | None
    chosen_name: str | None
    rule_fired: str
    candidates: list[int] = Field(
        default_factory=list, description="instance_ids of all lands considered."
    )


class SpellCastEvent(BaseModel):
    """One spell cast or activated ability resolved during the main phase."""

    kind: Literal["spell_cast"] = "spell_cast"
    turn: int
    card_instance_id: int
    card_name: str
    mode_index: int
    mode_kind: str
    priority: str
    payment_sources: list[int] = Field(
        default_factory=list,
        description="instance_ids of permanents tapped to pay the mana cost.",
    )


class ScryEvent(BaseModel):
    """A scry effect resolved by the policy heuristic."""

    kind: Literal["scry"] = "scry"
    turn: int
    saw: list[str] = Field(default_factory=list, description="Names looked at.")
    kept_on_top: list[str] = Field(default_factory=list)
    bottomed: list[str] = Field(default_factory=list)


# Discriminated union over ``kind``. Pydantic v2 picks the right
# concrete class on parse based on the discriminator string, so a
# ``GameTrace`` round-trips through JSON cleanly.
ActionEvent = Annotated[
    DrawEvent | LandDropEvent | SpellCastEvent | ScryEvent,
    Field(discriminator="kind"),
]


class GameTrace(BaseModel):
    """Output of one simulated game.

    Three layers:

    * ``turns`` is the per-turn snapshot (drawn cards + castability).
      Aggregation doesn't need this — it's preserved for verbose mode
      and unit tests.
    * ``instances`` is the per-card-instance summary the aggregator
      consumes.
    * ``actions`` is the verbose-only stream of structured events
      (land drops, spell casts, scrys, draws). Empty when the engine
      was called with ``verbose=False``.
    """

    seed: int
    on_the_play: bool
    turns: list[TurnSnapshot] = Field(default_factory=list)
    instances: list[InstanceOutcome] = Field(default_factory=list)
    actions: list[ActionEvent] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Pretty printer
# ---------------------------------------------------------------------------


def pretty_print(trace: GameTrace) -> str:
    """Render *trace* as a human-readable transcript.

    Group events by turn; within each turn show draws, the castability
    snapshot, the chosen land drop with the deciding rule, and each
    spell cast in order. Returns a single string (caller decides
    whether to ``print`` it).

    Designed for ``verbose=True`` traces — with an empty ``actions``
    list, the output still includes the turn-by-turn snapshot but no
    decision narrative.
    """
    lines: list[str] = []
    play_or_draw = "play" if trace.on_the_play else "draw"
    lines.append(f"=== Game (seed={trace.seed}, on the {play_or_draw}) ===")

    actions_by_turn: dict[int, list[ActionEvent]] = {t: [] for t in (1, 2, 3, 4)}
    for ev in trace.actions:
        actions_by_turn.setdefault(ev.turn, []).append(ev)

    for snap in trace.turns:
        lines.append(f"-- Turn {snap.turn} --")
        events = actions_by_turn.get(snap.turn, [])
        _render_turn(lines, snap, events)

    lines.append("=== End of trace ===")
    castable_summary = _summarise_first_castable(trace)
    if castable_summary:
        lines.append("First castable turn (any mode), by name:")
        lines.extend(castable_summary)
    return "\n".join(lines)


def _render_turn(lines: list[str], snap: TurnSnapshot, events: Iterable[ActionEvent]) -> None:
    if snap.cards_drawn:
        lines.append(f"  drew: {', '.join(snap.cards_drawn)}")
    elif snap.turn == 1:
        lines.append("  no draw (on the play)")

    castable_names = sorted({r.card_name for r in snap.castability if r.castable})
    if castable_names:
        lines.append(f"  castable: {', '.join(castable_names)}")
    else:
        lines.append("  castable: (none)")

    for ev in events:
        if isinstance(ev, DrawEvent):
            # Already covered by snap.cards_drawn — skip duplicates.
            continue
        if isinstance(ev, LandDropEvent):
            if ev.chosen_name is None:
                lines.append("  land drop: skipped (no land in hand)")
            else:
                lines.append(f"  land drop: {ev.chosen_name} (rule {ev.rule_fired})")
        elif isinstance(ev, SpellCastEvent):
            lines.append(
                f"  cast: {ev.card_name} ({ev.mode_kind}, {ev.priority}) "
                f"using sources {ev.payment_sources}"
            )
        elif isinstance(ev, ScryEvent):
            lines.append(
                f"    scry: saw {ev.saw}; kept_top={ev.kept_on_top}; bottomed={ev.bottomed}"
            )


def _summarise_first_castable(trace: GameTrace) -> list[str]:
    """One line per (name, first-castable-turn) bucket, sorted by name.

    Helps a reader sanity-check at a glance: "did the bombs ever come
    online?".
    """
    by_name: dict[str, list[int]] = {}
    for inst in trace.instances:
        by_name.setdefault(inst.name, []).append(inst.first_castable_turn)
    out: list[str] = []
    for name, values in sorted(by_name.items()):
        labels = [str(v) if v < 5 else "5+" for v in sorted(values)]
        out.append(f"  {name}: {labels}")
    return out
