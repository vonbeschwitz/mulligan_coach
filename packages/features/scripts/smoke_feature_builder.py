"""End-to-end smoke test: build the 196-feature row on a real TLA deck.

Loads the persisted TLA `ParsedCard` set, the TLA 17Lands ratings parquet,
and the shrunk/z-score pipeline, then assembles a feature row for a
hand-built keep hand.

Run with:
    .venv/Scripts/python.exe packages/features/scripts/smoke_feature_builder.py

Outputs feature counts and a sanity-check sample so the encoder can
eyeball the row without firing up a notebook.
"""

from __future__ import annotations

import random

from mulligan_coach_cards import (
    Cost,
    ManaAbility,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    load_parsed_cards,
    load_premier_draft_stats,
)
from mulligan_coach_features import (
    build_feature_row,
    compute_format_priors,
    compute_format_wr_distribution,
    shrink_stats,
    zscore_stats,
)
from mulligan_coach_simulation import simulate

SET_CODE = "TLA"


def main() -> None:
    # 1. Load TLA ParsedCards from the persisted JSON.
    parsed_by_name = {pc.name: pc for pc in load_parsed_cards(SET_CODE)}
    print(f"Loaded {len(parsed_by_name)} TLA parsed cards.")

    # 2. Load 17Lands ratings + run the shrinkage / z-score chain.
    stats_lookup = load_premier_draft_stats(SET_CODE)
    all_stats = list(stats_lookup.by_arena_id.values())
    priors = compute_format_priors(all_stats)
    shrunk = shrink_stats(all_stats, priors=priors)
    distribution = compute_format_wr_distribution(shrunk.values())
    zscores = zscore_stats(shrunk.values(), distribution=distribution)
    print(
        f"Loaded {len(all_stats)} 17Lands rows; "
        f"computed shrunk WRs and z-scores for {len(shrunk)} arena IDs."
    )

    # 3. Pick a small set of cards from the format and build a 40-card deck.
    #    Restrict to AUTO-status cards so the simulator's encoding check
    #    passes; this is a sanity smoke, not a balanced draft.
    auto_cards = [pc for pc in parsed_by_name.values() if pc.status.value == "auto"]
    spells = [c for c in auto_cards if "Land" not in c.types]
    if not spells:
        raise RuntimeError("Need at least one auto-status spell to build a deck.")

    # Basic lands aren't in the per-set ParsedCard JSON (those are loaded from
    # Scryfall's main bulk). For the smoke test we synthesise a Forest using
    # the test factories' shape — this keeps the smoke local to features +
    # simulation without dragging in Scryfall I/O.
    rng = random.Random(0)
    forest = ParsedCard(
        name="Forest",
        set_code="SMOKE",
        collector_number="forest",
        oracle_id="00000000-0000-0000-0000-smokeforest1",
        rarity="common",
        raw_oracle_text="({T}: Add {G}.)",
        type_line="Basic Land — Forest",
        types=["Land"],
        subtypes=["Forest"],
        supertypes=["Basic"],
        mana_cost=None,
        mana_abilities=[ManaAbility(cost=Cost(tap=True), produces=[["G"]])],
        role_features=RoleFeatures(is_land=True),
        status=ParseStatus.AUTO,
    )
    # 17 lands + 23 spells (mono-colour smoke).
    deck = [forest] * 17 + [rng.choice(spells) for _ in range(23)]
    rng.shuffle(deck)
    print("Built a 40-card smoke deck (17 lands + 23 spells).")

    # 4. Take a 7-card opening hand off the deck.
    hand = deck[:7]
    library = deck[7:]

    # 5. Run a quick simulation.
    aggregate = simulate(hand, library, n_runs=50, seed=42)
    print(
        f"Simulated 50 games. P(land drop turn 4) = "
        f"{aggregate.game_level.p_land_drop_by_turn[2]:.2f}; "
        f"expected mana T4 = {aggregate.game_level.expected_mana_count_turn[2]:.2f}."
    )

    # 6. Build the feature row.
    row = build_feature_row(
        hand=hand,
        deck=deck,
        aggregate_stats=aggregate,
        shrunk=shrunk,
        zscores=zscores,
        on_the_play=True,
        mulligan_number=0,
        event_type="PremierDraft",
        set_code=SET_CODE,
    )
    print(f"\nFeature row has {len(row)} columns.")
    print("Sample features:")
    for key in (
        "on_the_play",
        "event_type_PremierDraft",
        "set_code_TLA",
        "pct_lands_in_deck",
        "n_lands_in_hand",
        "n_creatures_mv_le_2_in_hand",
        "avg_gih_wr_of_hand_spells",  # 0.0 expected — TLA cards have arena_id=None today
        "p_land_drop_by_turn_2",
        "p_land_drop_by_turn_4",
        "expected_mana_count_turn_4",
        "p_any_creature_t2",
        "avg_count_creature_mv_0_2_t3",
        "p_any_creature_mv_3p_high_oh_t3",
        "avg_count_creature_mv_4p_t4",
    ):
        print(f"  {key:50s} = {row[key]}")

    # 7. Invariants.
    for key, value in row.items():
        assert isinstance(value, float), f"{key} is {type(value).__name__}, not float"
    print("\nAll feature values are float — passed type invariant.")


if __name__ == "__main__":
    main()
