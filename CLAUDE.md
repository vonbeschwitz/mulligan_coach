# Mulligan Coach

A real-time mulligan decision helper for Magic: The Gathering Arena Limited (Premier Draft, Sealed). Given an opening hand and decklist, it estimates the win probability of keeping vs. mulliganing and recommends a decision.

## Project owner context

The owner is not a professional software engineer. Default to clear, well-commented code, conservative dependency choices, and explanations of non-obvious design decisions. When introducing new tools or libraries, briefly explain what they do and why they were chosen.

## High-level approach

The recommendation pipeline has three stages:

1. **Card representation.** Each card in the format is converted into a structured representation capturing what it does mechanically (creature, removal, mana dork, card draw, land that ETBs tapped, etc.), its mana cost and color requirements, its 17Lands aggregate stats (GIH WR, OH WR, drawn WR), and any other features useful for simulation or modeling.

2. **Monte Carlo simulation.** Given a hand and the rest of the deck, simulate thousands of games' worth of draws to estimate playability statistics — e.g., probability of making land drops 1–4, probability of casting your 2-drop on turn 2, probability of casting your most expensive spell on time, expected mana available each turn. This produces a vector of "playability features" that summarizes how the hand is likely to play out.

3. **XGBoost model.** Trained on 17Lands public game data, this model takes the playability features from the Monte Carlo simulation, plus hand- and deck-level features derived from card stats (sum of GIH WR, "earliness score" from OH WR vs. drawn WR differential, role counts, etc.), plus context (on play/draw, mulligan number, hand size), and predicts P(win | this hand). The recommendation compares P(win | keep current hand) vs. P(win | mulligan to N-1).

## Sub-projects

The repo is organized as a uv workspace with one package per sub-project. Each sub-project has its own `CLAUDE.md` with detailed instructions. Dependencies flow in one direction: data-download → cards → simulation, model → website, overlay.

```
mulligan-coach/
├── CLAUDE.md                    # This file (project overview)
├── pyproject.toml               # uv workspace root
├── README.md
├── LICENSE                      # MIT
├── .github/workflows/           # CI (lint, tests)
├── data/                        # Gitignored; downloaded data lives here
├── packages/
│   ├── data-download/           # Pulls 17Lands, Scryfall, MTGJSON
│   ├── cards/                   # Shared card representation + role categorization
│   ├── features/                # Derived per-card / hand-level features (e.g. shrunk WRs)
│   ├── simulation/              # Monte Carlo playability engine
│   ├── model/                   # XGBoost training + inference
│   ├── website/                 # FastAPI + HTMX testing interface
│   └── overlay/                 # PyQt6 Arena log-tailing overlay
└── utilities/                   # Dev-only helper tools (workspace members, not shipped)
    └── card_viewer/             # Local web UI for verifying ParsedCard encodings
```

`utilities/` holds developer tools that share the workspace's `.venv` and
dependencies but are not part of the shipped recommendation pipeline.
They're added to `[tool.uv.workspace] members` so `uv sync` installs
them, and listed in the root's `[project.dependencies]` so a plain
`uv sync` puts them on the path. Because the workspace root has
`package = false`, dev-tool deps here can never leak into a downstream
wheel.

### 1. data-download

Downloads and caches all external data the project needs:

- 17Lands public datasets (game data CSVs) for recent Limited formats.
- 17Lands card ratings JSON (GIH WR, OH WR, drawn WR, ALSA, etc.) — refreshed regularly during a format.
- Scryfall bulk data (oracle text, mana costs, types, P/T) for card representation.
- MTGJSON AllPrintings (Arena ID ↔ Scryfall ID mapping).

Stores raw downloads in `data/raw/`, processed/normalized versions in `data/processed/`. Game data goes into a DuckDB database for efficient querying. Supports incremental refresh (only re-downloads changed files).

### 2. cards

Shared library used by simulation, model, and overlay. Converts raw card data into a typed representation with:

- Parsed mana cost (CMC, color requirements, generic component, hybrid).
- Card type and subtypes.
- For creatures: power, toughness, keyword abilities (flying, trample, etc.).
- Mechanical role tags: produces-mana, draws-cards, ramps (searches lands), is-removal, is-counterspell, makes-tokens, etc. — assigned via a combination of structured Scryfall data and LLM-based classification of oracle text (cached per card).
- Land properties: ETB tapped, fetch, dual, basic, utility.
- 17Lands stats (joined in when available).
- Arena ID for log-parsing lookups.

### 3. features

Derived per-card and hand-level features over `ParsedCard` +
`SeventeenLandsStats`. Sits between `cards/` (raw representation) and
`model/` (XGBoost). The first inhabitant is sample-size shrinkage of
17Lands per-card OH/GD/GIH win rates — uses `pick_count` as the
weight numerator (so heavily-picked-but-rarely-played cards aren't
falsely flattered) and a play-rate-conditional decile mean as the
prior (so sideboard-tier cards are shrunk toward "typical sideboard
WR," not the format mean). Hand-level features (mana / castability
turn-by-turn, role-mix counts) land here as they're built.

### 4. simulation

Monte Carlo engine. Pure function: `(hand, deck, on_play) -> playability_features`.

- Fast vectorized implementation (numpy) targeting thousands of sims per second so the overlay can respond in well under a second.
- Models the relevant subset of the rules: drawing, land drops (incl. ETB-tapped lands), casting spells given mana available, mana dorks, ramp spells, cantrips/card draw.
- Outputs features like P(land drop turn N), P(cast on-curve turn N), P(stuck on lands), expected mana turn N, P(cast specific hand card by turn N), etc.
- Does not attempt to model combat, opponent interaction, or game-winning conditions — that's the model's job.

### 5. model

XGBoost training and inference.

- Trains on 17Lands game data: each row is a game, label is win/loss, features are derived from the opening hand, decklist, and on-play/draw.
- Feature set combines simulation outputs (playability) with hand/deck statistics (GIH WR sums, earliness scores, role counts, curve shape) and context (mulligan number, hand size, on play/draw).
- Trains across multiple recent formats to mitigate the ~4-week 17Lands data lag for new sets. Format-specific fine-tuning when sufficient data is available.
- Inference: `(hand, deck, on_play, mulligan_number) -> P(win)`. Calling it twice (current hand vs. simulated mulligan to N-1) gives the comparison needed for a recommendation.

### 6. website

FastAPI backend + HTMX frontend. Lightweight and easy to iterate on. Lets the user:

- Paste or upload an Arena decklist.
- Specify their hand (card pickers).
- Toggle on play / on draw and mulligan number.
- See the playability statistics, the model's win probability for keep vs. mulligan, and the recommendation.

This is the primary testing/validation surface for the simulation and model. It exists before the overlay because it isolates the recommendation pipeline from all the Arena-specific complexity (log parsing, transparent windows, fullscreen handling). Once the website produces good recommendations, the overlay just replaces the manual input step with automatic log-tailing.

### 7. overlay

PyQt6 transparent always-on-top window over MTG Arena.

- Tails Arena's `Player.log` and parses GameStateMessage events to detect mulligan decisions, opening hands, decklists, on play/draw, and mulligan count.
- Calls the same simulation + model pipeline used by the website.
- Renders a small overlay panel near the mulligan UI showing keep vs. mulligan win probabilities and the recommendation.
- Read-only log parsing only — no game memory access, no client interaction. This is the line WotC tolerates.

## Tech stack

- **Language:** Python 3.12 throughout. One language keeps things simple given the owner is not a professional coder, and Python is the obvious choice for the ML/data work.
- **Package management:** [uv](https://github.com/astral-sh/uv) workspace. Fast, modern, handles the monorepo-style layout cleanly.
- **Data:** DuckDB for game data (efficient querying of large CSVs), Parquet for processed feature tables, JSON/CSV for raw downloads.
- **ML:** XGBoost, scikit-learn (preprocessing, evaluation), pandas, numpy.
- **Simulation:** numpy (vectorized). Numba as a fallback if performance becomes a problem.
- **Website:** FastAPI + Jinja2 + HTMX. No JS framework needed.
- **Overlay:** PyQt6.
- **Testing:** pytest. Each sub-project has its own test suite.
- **Linting/formatting:** ruff (lint + format).
- **Type checking:** mypy in strict mode where reasonable.

## Development workflow

- Work in feature branches off `main`. PRs squash-merge.
- Each sub-project is independently testable. Run `uv run pytest packages/<name>` for a single sub-project, or `uv run pytest` for everything.
- Pre-commit hooks run ruff and mypy.
- GitHub Actions runs lint + tests on every PR.
- Data is gitignored. Raw 17Lands CSVs are large and frequently updated; they belong in `data/`, not in version control. Trained models and their training metadata go in `models/` (also gitignored), with a separate mechanism for sharing trained artifacts (likely GitHub Releases or a small object store later).

## Build order

Recommended order for building this out:

1. **data-download** first — without data, nothing else works.
2. **cards** — needed by features, simulation, and model.
3. **features** — derived per-card / hand-level features over cards + 17Lands.
4. **simulation** — pure function, easy to test in isolation, produces features for the model.
5. **model** — trains on simulation + features outputs + 17Lands data.
6. **website** — validates the full pipeline end-to-end with manual input.
7. **overlay** — final integration; reuses everything from website.

Each step should be working and tested before starting the next.

## Scope and non-goals

- **In scope:** Limited only (Premier Draft, Sealed). London mulligan rules. Win probability estimation and keep/mull recommendation.
- **Out of scope (for now):** Constructed formats, suggesting which card to bottom on a mulligan (could be a future feature), in-game advice after mulligan decision, deck building advice, opponent modeling.
- **Out of scope permanently:** Anything that reads Arena's memory, modifies the client, or interacts with the game beyond reading the log file. WotC tolerates read-only log parsing; we will not cross that line.

## Key references

- 17Lands public datasets: https://www.17lands.com/public_datasets
- 17Lands FAQ (publication delay, usage guidelines): https://www.17lands.com/faq
- Scryfall API: https://scryfall.com/docs/api
- MTGJSON: https://mtgjson.com/
- Arena log location (Windows): `%AppData%\LocalLow\Wizards Of The Coast\MTGA\Player.log`

## When working on this project

- Always read the relevant sub-project's `CLAUDE.md` before making changes inside that package.
- Keep dependencies between sub-projects minimal and unidirectional. If a change to `cards` would require updates to four other packages, that's a sign the interface needs more thought before changing it.
- Prefer small, well-tested functions over large integrated scripts. The simulation engine in particular needs to be heavily tested — bugs there silently corrupt the model's training signal.
- When the 17Lands data format or Arena log format changes (and they do), fix it in one place: `data-download` for 17Lands, `overlay` for Arena logs. Don't let parsing logic leak into other packages.
