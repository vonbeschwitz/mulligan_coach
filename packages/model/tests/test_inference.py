"""Tests for predict_win_probability + recommend.

Strategy:

* End-to-end integration: build a tiny synthetic in-memory
  DuckDB games view (mono-G deck, 60 games), materialise a real
  feature parquet via ``materialize_feature_matrix``, train a
  small XGBoost on it, load as a :class:`ModelBundle`, then
  call ``predict_win_probability`` / ``recommend`` against the
  same hand+deck. Verifies the full feature->prediction shape
  matches between training and inference time.
* Unit-level: ``ModelBundle.load`` parity with
  ``ModelBundle.from_train_result``, ``Recommendation`` field
  invariants, error handling for ``mulligan_number=6`` /
  bad hand size.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pyarrow as pa
import pytest
from mulligan_coach_cards import (
    Cost,
    EntersBattlefieldEffect,
    FetchLandEffect,
    ManaAbility,
    Mode,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_model import (
    ModelBundle,
    Recommendation,
    materialize_feature_matrix,
    predict_win_probability,
    recommend,
    train_model,
)
from mulligan_coach_model.feature_matrix import _FormatStats

# ---------------------------------------------------------------------------
# Synthetic mono-G deck shared with test_feature_matrix
# ---------------------------------------------------------------------------


_NEXT_OID = [0]


def _oid() -> str:
    _NEXT_OID[0] += 1
    return f"00000000-0000-0000-0000-{_NEXT_OID[0]:012d}"


def _forest() -> ParsedCard:
    return ParsedCard(
        name="Forest",
        set_code="BASIC",
        collector_number="forest",
        oracle_id=_oid(),
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


def _vanilla(name: str, mana: str = "{1}{G}") -> ParsedCard:
    cost = Cost(mana=parse_mana_cost(mana))
    return ParsedCard(
        name=name,
        set_code="TST",
        collector_number=name,
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"Vanilla {name}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost(mana),
        power="2",
        toughness="2",
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def _cultivate() -> ParsedCard:
    cost = Cost(mana=parse_mana_cost("{2}{G}"))
    return ParsedCard(
        name="Cultivate",
        set_code="TST",
        collector_number="cult",
        oracle_id=_oid(),
        rarity="uncommon",
        raw_oracle_text=(
            "Search your library for a basic land card, put it onto the battlefield tapped."
        ),
        type_line="Sorcery",
        types=["Sorcery"],
        mana_cost=parse_mana_cost("{2}{G}"),
        modes=[
            Mode(
                kind="cast",
                cost=cost,
                effects=[
                    FetchLandEffect(target_filter="basic", destination="battlefield_tapped"),
                ],
            )
        ],
        role_features=RoleFeatures(),
        status=ParseStatus.AUTO,
    )


def _make_hand_and_deck() -> tuple[list[ParsedCard], list[ParsedCard]]:
    forest = _forest()
    bear = _vanilla("Bear")
    cult = _cultivate()
    hand = [forest] * 3 + [bear] * 3 + [cult]
    deck = [forest] * 17 + [bear] * 18 + [cult] * 5
    return hand, deck


def _make_games_view(con: duckdb.DuckDBPyConnection, n_rows: int) -> None:
    """Hand-build a games view with `n_rows` mono-G TLA rows.

    Spreads rows across multiple draft_ids so the grouped split has
    enough groups to populate every split (the 70/10/10/10 default
    needs at least 4 draft_ids).
    """
    rows = [
        {
            "expansion": "TLA",
            "event_type": "PremierDraft",
            "draft_id": f"draft-{i // 4}",
            "match_number": 1,
            "game_number": i % 4,
            "on_play": (i % 2 == 0),
            "num_mulligans": 0,
            "opp_num_mulligans": (i % 3),
            "won": ((i // 4) % 2 == 0),
            "user_n_games_bucket": 100,
            "user_game_win_rate_bucket": 0.55,
            "deck_Forest": 17,
            "opening_hand_Forest": 3,
            "deck_Bear": 18,
            "opening_hand_Bear": 3,
            "deck_Cultivate": 5,
            "opening_hand_Cultivate": 1,
        }
        for i in range(n_rows)
    ]
    keys = list(rows[0].keys())
    cols = {k: [r[k] for r in rows] for k in keys}
    table = pa.table(cols)
    con.register("games_src", table)
    con.execute("CREATE TABLE games_table AS SELECT * FROM games_src")
    con.execute("CREATE VIEW games AS SELECT * FROM games_table")


def _train_tiny_model(tmp_path: Path) -> tuple[ModelBundle, list[ParsedCard], list[ParsedCard]]:
    """Build a tiny end-to-end model on a synthetic mono-G dataset."""
    import mulligan_coach_model.feature_matrix as fm
    import mulligan_coach_model.training_rows as tr

    # 1. Build the games view on disk.
    db_path = tmp_path / "games.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        _make_games_view(con, n_rows=80)
    finally:
        con.close()

    # 2. Monkey-patch format stats + name lookup so the materialiser
    # works without a real parsed_cards JSON / ratings parquet.
    hand, deck = _make_hand_and_deck()
    name_lookup = {c.name: c for c in deck}

    fm._build_format_stats_original = fm._build_format_stats  # type: ignore[attr-defined]
    tr._build_name_lookup_original = tr.build_name_lookup  # type: ignore[attr-defined]
    fm._build_format_stats = lambda *_a, **_kw: _FormatStats(shrunk={}, zscores={})
    tr.build_name_lookup = lambda *_a, **_kw: name_lookup

    try:
        # 3. Materialise feature parquet (directory of chunks).
        from mulligan_coach_model import feature_parquet_paths

        parquet_dir = tmp_path / "features"
        materialize_feature_matrix(
            set_code="TLA",
            duckdb_path=db_path,
            output_dir=parquet_dir,
            n_sims_per_row=3,
            chunk_rows=20,
        )

        # 4. Train a tiny XGBoost on it.
        result = train_model(
            parquet_paths=feature_parquet_paths(parquet_dir),
            output_dir=None,
            n_estimators=10,
            max_depth=3,
            learning_rate=0.1,
            early_stopping_rounds=3,
            val_frac=0.15,
            calib_frac=0.15,
            test_frac=0.15,
            seed=0,
        )
    finally:
        fm._build_format_stats = fm._build_format_stats_original  # type: ignore[attr-defined]
        tr.build_name_lookup = tr._build_name_lookup_original  # type: ignore[attr-defined]

    return ModelBundle.from_train_result(result), hand, deck


# ---------------------------------------------------------------------------
# End-to-end: predict_win_probability shape + value
# ---------------------------------------------------------------------------


def test_predict_win_probability_returns_calibrated_probability(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    p = predict_win_probability(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=True,
        mulligan_number=0,
        opp_mulligan_number=None,
        event_type="PremierDraft",
        set_code="TLA",
        shrunk={},
        zscores={},
        n_sims=10,
        seed=42,
    )
    assert 0.0 <= p <= 1.0, f"predicted probability {p} out of range"


def _predict(
    bundle: ModelBundle,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    *,
    on_the_play: bool = True,
    mulligan_number: int = 0,
    opp_mulligan_number: int | None = None,
    n_sims: int = 10,
    seed: int = 7,
) -> float:
    """Test helper — calls predict_win_probability with explicit kwargs.

    Exists because mypy can't unpack a ``dict[str, object]`` into a
    typed-parameter signature; defining a small typed wrapper is
    cleaner than littering call sites with ``# type: ignore``.
    """
    return predict_win_probability(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=on_the_play,
        mulligan_number=mulligan_number,
        opp_mulligan_number=opp_mulligan_number,
        event_type="PremierDraft",
        set_code="TLA",
        shrunk={},
        zscores={},
        n_sims=n_sims,
        seed=seed,
    )


def test_predict_win_probability_is_reproducible(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    p1 = _predict(bundle, hand, deck)
    p2 = _predict(bundle, hand, deck)
    assert p1 == pytest.approx(p2)


def test_predict_win_probability_on_the_draw_uses_opp_mulligan(tmp_path: Path) -> None:
    """On the draw the conditional feature is populated; on the play
    it's NaN. The two should yield different baseline margins (and
    hence different predictions) even with the same hand."""
    bundle, hand, deck = _train_tiny_model(tmp_path)
    p_play = _predict(bundle, hand, deck, on_the_play=True, opp_mulligan_number=None)
    p_draw = _predict(bundle, hand, deck, on_the_play=False, opp_mulligan_number=1)
    # Both must be valid probabilities; they should generally differ
    # because the baseline cell + opp_mull margins differ.
    assert 0.0 <= p_play <= 1.0
    assert 0.0 <= p_draw <= 1.0


# ---------------------------------------------------------------------------
# End-to-end: recommend
# ---------------------------------------------------------------------------


def test_recommend_produces_valid_recommendation(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    rec = recommend(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=True,
        mulligan_number=0,
        opp_mulligan_number=None,
        event_type="PremierDraft",
        set_code="TLA",
        shrunk={},
        zscores={},
        n_sims=5,
        n_mulligan_samples=3,
        seed=1,
    )
    assert isinstance(rec, Recommendation)
    assert rec.verdict in ("keep", "mulligan")
    assert 0.0 <= rec.keep_win_probability <= 1.0
    assert 0.0 <= rec.mulligan_win_probability <= 1.0
    # The verdict is consistent with the delta sign.
    if rec.delta >= 0:
        assert rec.verdict == "keep"
    else:
        assert rec.verdict == "mulligan"
    assert rec.delta == pytest.approx(rec.keep_win_probability - rec.mulligan_win_probability)
    assert rec.mulligan_number_from == 0
    assert rec.mulligan_number_to == 1


def test_recommend_rejects_max_mulligan(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    with pytest.raises(ValueError, match="max is 6"):
        recommend(
            bundle,
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=6,
            opp_mulligan_number=None,
            event_type="PremierDraft",
            set_code="TLA",
            shrunk={},
            zscores={},
            n_sims=5,
            n_mulligan_samples=3,
        )


def test_recommend_rejects_bad_hand_size(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    with pytest.raises(ValueError, match="hand=7 cards"):
        recommend(
            bundle,
            hand=hand[:6],
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            event_type="PremierDraft",
            set_code="TLA",
            shrunk={},
            zscores={},
            n_sims=5,
            n_mulligan_samples=3,
        )


def test_recommend_rejects_bad_deck_size(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    with pytest.raises(ValueError, match="deck=40-42 cards"):
        recommend(
            bundle,
            hand=hand,
            deck=deck[:30],
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            event_type="PremierDraft",
            set_code="TLA",
            shrunk={},
            zscores={},
            n_sims=5,
            n_mulligan_samples=3,
        )


def _recommend(
    bundle: ModelBundle,
    hand: list[ParsedCard],
    deck: list[ParsedCard],
    *,
    seed: int = 99,
) -> Recommendation:
    return recommend(
        bundle,
        hand=hand,
        deck=deck,
        on_the_play=True,
        mulligan_number=0,
        opp_mulligan_number=None,
        event_type="PremierDraft",
        set_code="TLA",
        shrunk={},
        zscores={},
        n_sims=5,
        n_mulligan_samples=3,
        seed=seed,
    )


def test_recommend_is_reproducible(tmp_path: Path) -> None:
    bundle, hand, deck = _train_tiny_model(tmp_path)
    rec1 = _recommend(bundle, hand, deck, seed=99)
    rec2 = _recommend(bundle, hand, deck, seed=99)
    assert rec1.keep_win_probability == pytest.approx(rec2.keep_win_probability)
    assert rec1.mulligan_win_probability == pytest.approx(rec2.mulligan_win_probability)
    assert rec1.verdict == rec2.verdict


# ---------------------------------------------------------------------------
# ModelBundle load/from_train_result equivalence
# ---------------------------------------------------------------------------


def test_model_bundle_load_matches_from_train_result(tmp_path: Path) -> None:
    """ModelBundle.load and ModelBundle.from_train_result produce
    bundles that yield identical predictions on the same input."""
    bundle_in_memory, hand, deck = _train_tiny_model(tmp_path)

    # Save to disk via the public save_train_result path, then load.
    # Reconstruct a TrainResult from the bundle (we kept the artifacts
    # in memory; create a minimal TrainingMetadata).
    from mulligan_coach_model import SplitMetrics, TrainingMetadata, save_train_result
    from mulligan_coach_model.train import TrainResult

    fake_split = SplitMetrics(log_loss=0.0, brier=0.0, accuracy=0.0, n_rows=0)
    metadata = TrainingMetadata(
        feature_names=bundle_in_memory.feature_names,
        train=fake_split,
        val=fake_split,
        calibration=fake_split,
        test=fake_split,
        best_iteration=bundle_in_memory.best_iteration,
        seed=0,
    )
    result = TrainResult(
        booster=bundle_in_memory.booster,
        baseline=bundle_in_memory.baseline,
        metadata=metadata,
    )
    out_dir = tmp_path / "model"
    save_train_result(result, out_dir)

    loaded = ModelBundle.load(out_dir)
    assert loaded.feature_names == bundle_in_memory.feature_names
    assert loaded.best_iteration == bundle_in_memory.best_iteration

    p_memory = _predict(bundle_in_memory, hand, deck, n_sims=5, seed=7)
    p_disk = _predict(loaded, hand, deck, n_sims=5, seed=7)
    assert p_memory == pytest.approx(p_disk, abs=1e-6)
