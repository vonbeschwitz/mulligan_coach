"""Validation: interim train/serve skew from the Step 5 name-join.

Between merging Step 5 and the owner's re-materialise + retrain, the
production ``choice_prod`` model receives *populated* per-card stats
features where it was trained on the mostly-zero arena_id distribution
(design-review fact 4 / the transition policy in the spec). This script
quantifies what that costs a website user by predicting ``p_keep``
twice per hand:

* **(a)** empty shrunk / zscore dicts — the training-baseline /
  old-website regime (arena_id join returned nothing for main-set
  cards).
* **(b)** the new folded-name-keyed tables — what every surface now
  feeds the model.

It reports mean / median / max ``|Δp_keep|`` per set over a handful of
plausible 40-card mono-colour decks x ~20 smoothed hands each. It does
NOT gate the merge (the overlay already lived in regime (b) its whole
production life); it tells the owner how urgently to prioritise the
retrain. If median ``|Δ|`` exceeds ~5 pp, flag it in the PR.

Run (needs models/choice_prod + data/processed on disk):

    .venv/Scripts/python.exe scripts/step5_interim_skew_ab.py \\
        | tee logs/step5_interim_skew_ab.log
"""

from __future__ import annotations

import random
import statistics
import sys
from pathlib import Path

from mulligan_coach_cards import (
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    load_parsed_cards,
    load_premier_draft_stats,
)
from mulligan_coach_cards.models import Cost as CardCost
from mulligan_coach_cards.models import ManaAbility
from mulligan_coach_features import build_feature_row, shrink_stats, zscore_stats
from mulligan_coach_features import categories as cat
from mulligan_coach_model import ChoiceModelBundle, predict_keep_probability_from_feature_row
from mulligan_coach_model.feature_matrix import _library_from_deck
from mulligan_coach_simulation import draw_smoothed_hand, simulate
from mulligan_coach_simulation.runtime import Card

SETS = ("TLA", "SOS")
N_DECKS = 3
N_HANDS_PER_DECK = 20
N_SIMS = 200
MODEL_DIR = Path(__file__).resolve().parents[1] / "models" / "choice_prod"

_BASIC_FOR_COLOR = {
    "W": ("Plains", "Plains"),
    "U": ("Island", "Island"),
    "B": ("Swamp", "Swamp"),
    "R": ("Mountain", "Mountain"),
    "G": ("Forest", "Forest"),
}
_OID = [0]


def _basic(color: str) -> ParsedCard:
    """Synthesise a single basic land producing *color*."""
    name, subtype = _BASIC_FOR_COLOR[color]
    _OID[0] += 1
    return ParsedCard(
        name=name,
        set_code="BASIC",
        collector_number=name.lower(),
        oracle_id=f"00000000-0000-0000-0000-{_OID[0]:012d}",
        rarity="common",
        raw_oracle_text=f"({{T}}: Add {{{color}}}.)",
        type_line=f"Basic Land - {subtype}",
        types=["Land"],
        subtypes=[subtype],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=CardCost(tap=True), produces=[[color]])],
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )


def _mono_color_spells(cards: list[ParsedCard], color: str) -> list[ParsedCard]:
    """Castable spells whose colour requirement is a subset of {color}.

    Keeps the synthetic deck mono-colour so the 17 basics of *color* can
    cast everything — the point is a plausible, simulatable deck, not a
    tuned one.
    """
    out: list[ParsedCard] = []
    for c in cards:
        if not cat.is_spell(c) or c.mana_cost is None or not c.modes:
            continue
        pips = set(c.mana_cost.color_pips.keys())
        if pips <= {color} and cat.cmc(c) <= 6:
            out.append(c)
    out.sort(key=lambda c: (cat.cmc(c), c.name))
    return out


def _build_decks(set_code: str) -> list[list[ParsedCard]]:
    """A few plausible 40-card mono-colour decks (17 basics + 23 spells)."""
    cards = load_parsed_cards(set_code)
    decks: list[list[ParsedCard]] = []
    # Try each colour with enough playables; take the first N_DECKS.
    for color in ("W", "U", "B", "R", "G"):
        spells = _mono_color_spells(cards, color)
        if len(spells) < 23:
            continue
        deck = spells[:23] + [_basic(color) for _ in range(17)]
        decks.append(deck)
        if len(decks) >= N_DECKS:
            break
    return decks


def _p_keep(
    bundle: ChoiceModelBundle,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    shrunk: dict,  # type: ignore[type-arg]
    zscores: dict,  # type: ignore[type-arg]
    seed: int,
) -> float:
    library = _library_from_deck(tuple(hand), tuple(deck))
    aggregate = simulate(list(hand), list(library), on_the_play=True, n_runs=N_SIMS, seed=seed)
    row = build_feature_row(
        hand=list(hand),
        deck=list(deck),
        aggregate_stats=aggregate,
        shrunk=shrunk,
        zscores=zscores,
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code=deck_set_code(deck),
    )
    row["opp_mulligan_count_if_known"] = float("nan")
    return predict_keep_probability_from_feature_row(bundle, row)


def deck_set_code(deck: list[ParsedCard]) -> str:
    for c in deck:
        if c.set_code.upper() != "BASIC":
            return c.set_code.upper()
    return "BASIC"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    bundle = ChoiceModelBundle.load(MODEL_DIR)
    print(f"Loaded choice_prod from {MODEL_DIR}")
    if bundle.version_warning is not None:
        print(f"  version_warning: {bundle.version_warning}")
    print(
        f"\nInterim skew A/B: (a) empty stats vs (b) name-keyed stats; "
        f"{N_DECKS} decks x {N_HANDS_PER_DECK} hands, {N_SIMS} sims/hand\n"
    )
    print(f"{'set':>5}  {'n':>4}  {'mean|d|':>8}  {'median|d|':>9}  {'max|d|':>7}")
    print("-" * 46)
    for set_code in SETS:
        lookup = load_premier_draft_stats(set_code)
        shrunk = shrink_stats(lookup.by_name.values())
        zscores = zscore_stats(shrunk.values())
        decks = _build_decks(set_code)
        deltas: list[float] = []
        for di, deck in enumerate(decks):
            rng = random.Random(1000 + di)
            deck_cards = [Card(instance_id=i, parsed=p) for i, p in enumerate(deck)]
            for _hi in range(N_HANDS_PER_DECK):
                hand_cards, _lib = draw_smoothed_hand(deck_cards, rng)
                hand = [c.parsed for c in hand_cards]
                seed = rng.randint(0, 2**31 - 1)
                pa = _p_keep(bundle, hand, deck, {}, {}, seed)
                pb = _p_keep(bundle, hand, deck, shrunk, zscores, seed)
                deltas.append(abs(pa - pb))
        if deltas:
            print(
                f"{set_code:>5}  {len(deltas):>4}  {statistics.mean(deltas):>8.4f}  "
                f"{statistics.median(deltas):>9.4f}  {max(deltas):>7.4f}"
            )
        else:
            print(f"{set_code:>5}  no mono-colour deck with 23 playables found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
