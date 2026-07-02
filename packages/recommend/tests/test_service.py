"""Tests for the production choice path: ``recommend_choice`` + helpers.

The reload seam is covered by ``test_recommend_reload.py``; this file
covers what review #5 flagged as the least-tested production layer:

* ``_classify_choice_verdict`` — the four-band verdict boundaries
  (inclusive at the upper edge, so exactly 0.75 is ``marginal_keep``).
* Deterministic seed derivation — ``_stable_seed`` /
  ``_deck_signature`` are stable + order-independent, and
  ``recommend_choice`` derives the same seed from the same inputs.
* The ``opp_mulligan_count_if_known`` NaN convention — must match what
  training wrote into the cache (NaN on the play / when the opponent's
  count is unknown; the count itself on the draw).
* Deck-size bounds — ``recommend_choice`` accepts 40-42-card decks
  (matching the training pipeline) and rejects anything else, plus the
  hand-size / mulligan-number / model-not-loaded guards.

Strategy: the verdict + seed helpers are pure, so they're tested
directly. ``recommend_choice`` is exercised against a real (tiny)
synthetic deck through the real ``simulate`` + ``build_feature_row``
pipeline with only the final XGBoost predictor stubbed out — so the
captured feature row (and thus the NaN convention) is the genuine one
the model would see, without needing a trained bundle on disk.
"""

from __future__ import annotations

import math
import random
from typing import Any, cast

import pytest
from mulligan_coach_cards import (
    Cost,
    EntersBattlefieldEffect,
    ManaAbility,
    Mode,
    ParsedCard,
    ParseStatus,
    RoleFeatures,
    parse_mana_cost,
)
from mulligan_coach_model import ChoiceModelBundle
from mulligan_coach_recommend import service as service_module
from mulligan_coach_recommend.service import (
    CHOICE_CLEAR_KEEP_THRESHOLD,
    CHOICE_MARGINAL_KEEP_THRESHOLD,
    CHOICE_MARGINAL_MULL_THRESHOLD,
    ChoiceRecommendation,
    RecommendationService,
    ServiceStatus,
    _classify_choice_verdict,
    _deck_signature,
    _MulliganCacheKey,
    _stable_seed,
)
from mulligan_coach_simulation import AggregateStats, simulate

# ---------------------------------------------------------------------------
# Synthetic card factories (mono-G forest + bear — enough for the
# simulator + feature builder to run; the model itself is stubbed).
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


def _bear(name: str = "Bear", set_code: str = "TLA") -> ParsedCard:
    cost = Cost(mana=parse_mana_cost("{1}{G}"))
    return ParsedCard(
        name=name,
        set_code=set_code,
        collector_number=name.lower(),
        oracle_id=_oid(),
        rarity="common",
        raw_oracle_text=f"Vanilla {name}.",
        type_line="Creature",
        types=["Creature"],
        mana_cost=parse_mana_cost("{1}{G}"),
        power="2",
        toughness="2",
        modes=[Mode(kind="cast", cost=cost, effects=[EntersBattlefieldEffect()])],
        role_features=RoleFeatures(is_creature=True),
        status=ParseStatus.AUTO,
    )


def _make_hand_and_deck(deck_size: int = 40) -> tuple[list[ParsedCard], list[ParsedCard]]:
    """A 7-card hand + ``deck_size``-card mono-G deck (hand ⊆ deck by count)."""
    forest = _forest()
    bear = _bear()
    hand = [forest] * 3 + [bear] * 4
    n_forest = 17
    deck = [forest] * n_forest + [bear] * (deck_size - n_forest)
    return hand, deck


# ---------------------------------------------------------------------------
# Service construction + predictor stub
# ---------------------------------------------------------------------------


def _service(*, model_loaded: bool = True) -> RecommendationService:
    """A service whose choice model is a stand-in sentinel.

    ``recommend_choice`` only passes ``choice_bundle`` to the predictor,
    which the tests replace, so a bare sentinel is enough. When
    ``model_loaded`` is False the bundle is ``None`` (the "model not
    loaded" guard path).
    """
    bundle = cast(ChoiceModelBundle, object()) if model_loaded else None
    return RecommendationService(
        bundle=None,
        choice_bundle=bundle,
        stats_by_set={},
        status=ServiceStatus(
            model_loaded=model_loaded,
            model_dir=None,
            formats_with_stats=[],
            formats_missing_stats=[],
        ),
    )


class _Capture:
    """Records the feature rows + seeds seen by the stubbed pipeline."""

    def __init__(self) -> None:
        self.rows: list[dict[str, float]] = []
        self.seeds: list[int] = []


def _install_stubs(monkeypatch: pytest.MonkeyPatch, *, p_keep: float = 0.6) -> _Capture:
    """Stub the XGBoost predictor + spy on the simulator seed.

    ``simulate`` still runs for real (so ``build_feature_row`` gets a
    genuine aggregate and the captured row is authentic) — we just wrap
    it to record the seed it was handed. Only the final predictor is
    replaced, so no trained model is needed.
    """
    cap = _Capture()

    def _spy_simulate(*args: Any, **kwargs: Any) -> AggregateStats:
        cap.seeds.append(int(kwargs["seed"]))
        return simulate(*args, **kwargs)

    def _stub_predict(bundle: object, row: dict[str, float]) -> float:
        cap.rows.append(row)
        return p_keep

    monkeypatch.setattr(service_module, "simulate", _spy_simulate)
    monkeypatch.setattr(service_module, "predict_keep_probability_from_feature_row", _stub_predict)
    return cap


# ---------------------------------------------------------------------------
# Verdict-threshold boundaries
# ---------------------------------------------------------------------------


class TestClassifyChoiceVerdict:
    @pytest.mark.parametrize(
        ("p_keep", "expected"),
        [
            (1.0, "clear_keep"),
            (0.90, "clear_keep"),
            # Boundaries are inclusive at the upper edge: exactly on a
            # threshold lands in the *lower* band; a hair above jumps up.
            (math.nextafter(CHOICE_CLEAR_KEEP_THRESHOLD, 1.0), "clear_keep"),
            (CHOICE_CLEAR_KEEP_THRESHOLD, "marginal_keep"),
            (0.60, "marginal_keep"),
            (math.nextafter(CHOICE_MARGINAL_KEEP_THRESHOLD, 1.0), "marginal_keep"),
            (CHOICE_MARGINAL_KEEP_THRESHOLD, "marginal_mulligan"),
            (0.40, "marginal_mulligan"),
            (math.nextafter(CHOICE_MARGINAL_MULL_THRESHOLD, 1.0), "marginal_mulligan"),
            (CHOICE_MARGINAL_MULL_THRESHOLD, "clear_mulligan"),
            (0.10, "clear_mulligan"),
            (0.0, "clear_mulligan"),
        ],
    )
    def test_boundaries(self, p_keep: float, expected: str) -> None:
        assert _classify_choice_verdict(p_keep) == expected

    def test_thresholds_are_ordered(self) -> None:
        # Guards against someone reshuffling the constants and inverting
        # the bands.
        assert (
            CHOICE_MARGINAL_MULL_THRESHOLD
            < CHOICE_MARGINAL_KEEP_THRESHOLD
            < CHOICE_CLEAR_KEEP_THRESHOLD
        )


# ---------------------------------------------------------------------------
# Deterministic seed derivation
# ---------------------------------------------------------------------------


def _key(**overrides: Any) -> _MulliganCacheKey:
    base: dict[str, Any] = {
        "deck_signature": ("TLA:1", "TLA:2"),
        "on_the_play": True,
        "mulligan_number_to": 0,
        "opp_mulligan_number": None,
        "n_sims_per_mulligan": 5,
        "n_mulligan_samples": 0,
    }
    base.update(overrides)
    return _MulliganCacheKey(**base)


class TestSeedDerivation:
    def test_stable_seed_is_deterministic_and_32_bit(self) -> None:
        seed = _stable_seed(_key())
        assert _stable_seed(_key()) == seed
        assert 0 <= seed < 2**32

    def test_stable_seed_varies_with_key(self) -> None:
        assert _stable_seed(_key()) != _stable_seed(_key(on_the_play=False))
        assert _stable_seed(_key()) != _stable_seed(_key(mulligan_number_to=1))

    def test_deck_signature_order_independent(self) -> None:
        _hand, deck = _make_hand_and_deck()
        shuffled = deck[:]
        random.Random(1).shuffle(shuffled)
        assert _deck_signature(deck) == _deck_signature(shuffled)

    def test_deck_signature_is_set_aware(self) -> None:
        # Same name + collector number, different printing → distinct.
        assert _deck_signature([_bear(set_code="TLA")]) != _deck_signature([_bear(set_code="TMT")])

    def test_recommend_choice_derives_stable_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck()

        for _ in range(2):
            svc.recommend_choice(
                hand=hand,
                deck=deck,
                on_the_play=True,
                mulligan_number=0,
                opp_mulligan_number=None,
                n_sims=5,
            )
        # Two identical calls with seed=None derive the SAME simulator
        # seed — that reproducibility is what makes a cached result
        # meaningful.
        assert cap.seeds[0] == cap.seeds[1]

    def test_recommend_choice_honours_explicit_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cap = _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck()

        svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
            seed=12345,
        )
        assert cap.seeds == [12345]


# ---------------------------------------------------------------------------
# opp_mulligan_count_if_known NaN convention (must match training)
# ---------------------------------------------------------------------------


class TestOppMulliganConvention:
    @pytest.mark.parametrize(
        ("on_the_play", "opp_mulligan_number", "expect_nan", "expect_value"),
        [
            # On the play: the feature is always masked, even if a count
            # somehow arrives.
            (True, None, True, None),
            (True, 2, True, None),
            # On the draw: the count is passed through, including 0.
            (False, 1, False, 1.0),
            (False, 0, False, 0.0),
            # On the draw but the count is unknown → still masked.
            (False, None, True, None),
        ],
    )
    def test_feature_masking(
        self,
        monkeypatch: pytest.MonkeyPatch,
        on_the_play: bool,
        opp_mulligan_number: int | None,
        expect_nan: bool,
        expect_value: float | None,
    ) -> None:
        cap = _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck()

        svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=on_the_play,
            mulligan_number=0,
            opp_mulligan_number=opp_mulligan_number,
            n_sims=5,
        )
        (row,) = cap.rows
        value = row["opp_mulligan_count_if_known"]
        if expect_nan:
            assert math.isnan(value)
        else:
            assert value == expect_value


# ---------------------------------------------------------------------------
# Deck-size + input-guard bounds
# ---------------------------------------------------------------------------


class TestRecommendChoiceGuards:
    @pytest.mark.parametrize("deck_size", [40, 41, 42])
    def test_accepts_legal_deck_sizes(
        self, monkeypatch: pytest.MonkeyPatch, deck_size: int
    ) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        svc = _service()
        hand, deck = _make_hand_and_deck(deck_size)

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        assert isinstance(rec, ChoiceRecommendation)
        assert rec.p_keep == pytest.approx(0.6)
        assert rec.verdict == "marginal_keep"
        assert 0.0 <= rec.mulligan_percent <= 100.0

    @pytest.mark.parametrize("deck_size", [39, 43, 60])
    def test_rejects_illegal_deck_sizes(
        self, monkeypatch: pytest.MonkeyPatch, deck_size: int
    ) -> None:
        _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck(deck_size)

        with pytest.raises(ValueError, match="deck=40-42"):
            svc.recommend_choice(
                hand=hand,
                deck=deck,
                on_the_play=True,
                mulligan_number=0,
                opp_mulligan_number=None,
                n_sims=5,
            )

    @pytest.mark.parametrize("hand_size", [6, 8])
    def test_rejects_wrong_hand_size(self, monkeypatch: pytest.MonkeyPatch, hand_size: int) -> None:
        _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck()
        wrong_hand = [*hand, _bear()][:hand_size] if hand_size > 7 else hand[:hand_size]

        with pytest.raises(ValueError, match="hand=7"):
            svc.recommend_choice(
                hand=wrong_hand,
                deck=deck,
                on_the_play=True,
                mulligan_number=0,
                opp_mulligan_number=None,
                n_sims=5,
            )

    def test_rejects_mulligan_number_at_max(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch)
        svc = _service()
        hand, deck = _make_hand_and_deck()

        with pytest.raises(ValueError, match="max is 6"):
            svc.recommend_choice(
                hand=hand,
                deck=deck,
                on_the_play=True,
                mulligan_number=7,
                opp_mulligan_number=None,
                n_sims=5,
            )

    def test_raises_when_model_not_loaded(self) -> None:
        svc = _service(model_loaded=False)
        hand, deck = _make_hand_and_deck()

        with pytest.raises(RuntimeError, match="Choice model not loaded"):
            svc.recommend_choice(
                hand=hand,
                deck=deck,
                on_the_play=True,
                mulligan_number=0,
                opp_mulligan_number=None,
                n_sims=5,
            )
