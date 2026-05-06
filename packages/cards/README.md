# cards

Typed card representation for the Mulligan Coach project. Reads
Scryfall's `oracle_cards` JSON (downloaded by the `data-download`
package), turns each card into a `ParsedCard`, and reports which cards
the deterministic parser fully understood vs. which need LLM-based
classification of their oracle text.

This is the **first pass** — only deterministic parsing. The LLM
classifier and 17Lands stat join come in later stages. The strategy is
documented at `<repo>/.claude/plans/we-successfully-downloaded-the-noble-pancake.md`.

## Quick start

```
uv sync
uv run mulligan-coach-cards parse-demo --set TLA --n 30
```

The demo samples 30 cards (deterministic seed) from the named set, runs
the parser on each, and prints a table plus the full oracle text + parser
notes for every card it had to flag for LLM review. Use that report to
review which cards the deterministic rules miss and decide whether to
extend the rules or accept the LLM path.

## Tests

```
uv run pytest packages/cards
```
