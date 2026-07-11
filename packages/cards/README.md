# cards

Typed card representation for the Mulligan Coach project. Reads
Scryfall's `oracle_cards` JSON (downloaded by the `data-download`
package), turns each card into a `ParsedCard`, and reports which cards
the deterministic parser fully understood vs. which need LLM-based
classification of their oracle text.

Cards the deterministic parser can't fully encode go through an
LLM-based encoding pass (hand-reviewed per set, persisted as
`LLM_ENCODED`); 17Lands per-card stats are joined in by folded card
name. See `CLAUDE.md` for design details.

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
