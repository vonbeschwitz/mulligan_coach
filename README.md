# Mulligan Coach

A real-time mulligan helper for Magic: The Gathering Arena Limited.
When Arena offers you a mulligan, a small overlay shows the probability that a good player would mulligan the hand
— computed from your actual hand and decklist — and recommends a decision.

**[Download for Windows](https://github.com/vonbeschwitz/mulligan_coach_data/releases/tag/exe-latest)**
— the installer, release notes, and SHA256 live on the public
[`mulligan_coach_data`](https://github.com/vonbeschwitz/mulligan_coach_data)
repo, which also hosts the app's auto-updating data feed and the
[issue tracker](https://github.com/vonbeschwitz/mulligan_coach_data/issues).

## Privacy and how it stays inside the lines

- The overlay reads **only** MTG Arena's `Player.log` — a file Arena
  writes when you enable its own "Detailed Logs (Plugin Support)"
  setting — to detect your hand, deck, and play/draw. It never reads
  Arena's memory, never modifies the client, and never automates any
  game action.
- It only uses information you can already see on your own screen.
  No opponent data, no hidden information.
- **Nothing about your games ever leaves your computer.** Updates are
  pull-only; there is no telemetry. This repo is public so you can
  verify all of the above in the source.

## How it works

Three stages (the user-facing explanation ships in the app — see
[`docs/how_it_works.md`](docs/how_it_works.md)):

1. **Card representation.** Every card in the format is encoded into a
   structured form capturing what it does mechanically — mana cost and
   color requirements, creature stats, roles like removal / ramp /
   card draw, land properties — built from Scryfall data plus reviewed
   LLM classification of oracle text.
2. **Monte Carlo simulation.** Given the hand and the rest of the deck,
   ~200 simulated games estimate how the hand actually plays out:
   land drops, on-curve casts, mana available each turn.
3. **Model.** An XGBoost model trained on 17Lands public game and replay
   data combines the simulation output with card-quality statistics and
   context (play/draw, mulligan count) to produce the keep/mulligan
   verdict. The model is trained on Premier and Traditional Draft data;
   other Limited events (Sealed, Quick Draft) get an approximate answer.

## Repository layout

A [uv](https://docs.astral.sh/uv/) workspace with one package per stage.
See [`CLAUDE.md`](CLAUDE.md) for the full architecture; each package has
its own `CLAUDE.md` with details.

| Package | Description |
|---|---|
| `data-download` | Fetches & caches 17Lands, Scryfall, and MTGJSON data. |
| `cards` | Typed card representation (parsed cost, types, mechanical roles). |
| `features` | Derived per-card and hand-level features (e.g. shrunk win rates). |
| `simulation` | Monte Carlo playability engine. |
| `model` | XGBoost training + inference (win and choice models). |
| `recommend` | Shared keep/mulligan service composing the packages above. |
| `website` | Local FastAPI + HTMX testing/validation interface (not hosted). |
| `overlay` | PyQt6 Arena overlay — the shipped app. |

`scripts/` holds ad-hoc analysis tools; `utilities/` holds local dev
tools (card/simulation viewers). `packages/overlay/packaging/` builds
the Windows installer.

## Developing

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.12
on demand). The overlay targets Windows; the data/model/simulation
packages are platform-independent.

```sh
uv sync                 # create .venv and install everything
uv run pytest           # run all tests
uv run ruff check       # lint
uv run mypy             # type-check
```

To run a single sub-project's tests:

```sh
uv run pytest packages/overlay
```

## Data sources and attribution

- **[17Lands](https://www.17lands.com)** — card statistics shown in the
  app and the model's training data are derived from 17Lands public
  data. The public game/replay datasets are released under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). 17Lands
  does not endorse this tool.
- **[Scryfall](https://scryfall.com)** — card data (oracle text, mana
  costs, types). Scryfall does not endorse this tool.
- **[MTGJSON](https://mtgjson.com)** — Arena card identifiers.

## Fan Content Policy

Mulligan Coach is unofficial Fan Content permitted under the Fan Content
Policy. Not approved/endorsed by Wizards. Portions of the materials used
are property of Wizards of the Coast. ©Wizards of the Coast LLC.

## License

MIT — see [`LICENSE`](LICENSE).
