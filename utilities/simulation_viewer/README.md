# simulation-viewer

Local web utility for inspecting **one** Monte Carlo simulation trace,
turn by turn. Counterpart to `card-viewer`: where that one validates
the *card encoding* step, this one validates the *simulator* step.

This is a developer tool. It is not part of the shipped Mulligan Coach
recommendation pipeline.

## Run it

The simulation-viewer is normally launched from the unified [dev-site
umbrella](../dev_site/README.md), which also serves the card-viewer at
the same time:

```
uv sync
uv run dev-site
```

Then open <http://127.0.0.1:8000/simulation-viewer/> in a browser.

To run *just* the simulation-viewer standalone (e.g. while debugging
this utility in isolation), use its own console script:

```
uv run simulation-viewer
```

That binds <http://127.0.0.1:8000/> directly with no URL prefix.

By default it loads every set with a file in
`data/processed/parsed_cards/` (currently TMT, ECL, TLA). Override via
env var to scope down for faster iteration:

```
# PowerShell
$env:SIMULATION_VIEWER_SETS = "TLA"; uv run simulation-viewer

# bash
SIMULATION_VIEWER_SETS=TLA uv run simulation-viewer
```

The five basic lands (Plains/Island/Swamp/Mountain/Forest) are
synthesised in-process — the persistent store doesn't carry them, but
every Limited decklist references them.

## Workflow

1. **Paste a decklist.** MTGA's "Export to Clipboard" format works
   directly; older `<count> <name>` exports (no `(SET) NUMBER` suffix)
   also resolve via name. Validation runs as you type and surfaces any
   line we couldn't resolve or that the simulator can't handle.
2. **Build a hand.** Click *Random hand* for a 7-card sample, or type
   into the autocomplete (suggestions come from the deck's unique card
   names) and click *Add*. Click ✕ on any chip to remove it.
3. **Run simulation.** *On the play* / *On the draw* and an optional
   seed are exposed; leave the seed blank for a fresh random game on
   each click.

The trace pane shows, for each turn 1–4:

* **Drew** — the cards drawn this turn.
* **Castable** — every card-mode in hand at the start of the main
  phase, with the hypothetical land drop that would unlock it (if
  any).
* **Decisions** — the land drop (with the rule that fired), each spell
  cast (with priority tier), and any scry events.

A *first-castable turn* summary and the raw `GameTrace` JSON sit at
the bottom for cross-checking.

## Tests

```
uv run pytest utilities/simulation_viewer
```

The smoke tests use a 40-Mountain deck so they run without persisted
card data — the synthetic basics are always available.
