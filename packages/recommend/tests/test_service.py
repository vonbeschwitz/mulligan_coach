"""Tests for the production choice path: ``recommend_choice`` + helpers.

The reload seam is covered by ``test_recommend_reload.py``; this file
covers what review #5 flagged as the least-tested production layer:

* ``_classify_choice_verdict`` — the five-band verdict boundaries
  (inclusive at the upper edge, so exactly 0.85 is ``marginal_keep``),
  including the no-judgement ``borderline`` band at 0.45-0.65.
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
from types import SimpleNamespace
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
from mulligan_coach_features import CardZScores, ShrunkWinRates
from mulligan_coach_model import ChoiceModelBundle
from mulligan_coach_recommend import service as service_module
from mulligan_coach_recommend.service import (
    CHOICE_BORDERLINE_THRESHOLD,
    CHOICE_CLEAR_KEEP_THRESHOLD,
    CHOICE_MARGINAL_KEEP_THRESHOLD,
    CHOICE_MARGINAL_MULL_THRESHOLD,
    ChoiceRecommendation,
    FormatStats,
    RecommendationService,
    ServiceStatus,
    _classify_choice_verdict,
    _deck_signature,
    _MulliganCacheKey,
    _stable_seed,
)
from mulligan_coach_simulation import AggregateStats, simulate

# Default choice-model feature vocabulary the stub bundle advertises.
# Includes ``set_code_TLA`` so the synthetic mono-G deck (bears are
# ``set_code="TLA"``) is in-vocabulary and doesn't trip producer 3
# unless a test deliberately drops it.
_STUB_FEATURE_NAMES: tuple[str, ...] = ("set_code_TLA", "on_the_play", "mulligan_number")

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


def _stub_bundle(
    *,
    feature_names: tuple[str, ...] = _STUB_FEATURE_NAMES,
    version_warning: str | None = None,
) -> ChoiceModelBundle:
    """A stand-in choice bundle.

    ``recommend_choice`` passes ``choice_bundle`` to the (stubbed)
    predictor and reads ``feature_names`` / ``version_warning`` for the
    degradation checks — so a light ``SimpleNamespace`` carrying just
    those two attributes is enough; the real booster is never touched.
    """
    return cast(
        ChoiceModelBundle,
        SimpleNamespace(feature_names=feature_names, version_warning=version_warning),
    )


def _service(
    *,
    model_loaded: bool = True,
    feature_names: tuple[str, ...] = _STUB_FEATURE_NAMES,
    version_warning: str | None = None,
    stats_by_set: dict[str, FormatStats] | None = None,
) -> RecommendationService:
    """A service whose choice model is a light stand-in bundle.

    ``recommend_choice`` only passes ``choice_bundle`` to the predictor
    (which the tests replace) plus reads its ``feature_names`` /
    ``version_warning`` for degradation surfacing, so the stub bundle is
    enough. When ``model_loaded`` is False the bundle is ``None`` (the
    "model not loaded" guard path).
    """
    bundle = (
        _stub_bundle(feature_names=feature_names, version_warning=version_warning)
        if model_loaded
        else None
    )
    return RecommendationService(
        bundle=None,
        choice_bundle=bundle,
        stats_by_set=stats_by_set or {},
        status=ServiceStatus(
            model_loaded=model_loaded,
            model_dir=None,
            formats_with_stats=sorted(stats_by_set or {}),
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
            (0.70, "marginal_keep"),
            (math.nextafter(CHOICE_MARGINAL_KEEP_THRESHOLD, 1.0), "marginal_keep"),
            (CHOICE_MARGINAL_KEEP_THRESHOLD, "borderline"),
            (0.50, "borderline"),
            (math.nextafter(CHOICE_BORDERLINE_THRESHOLD, 1.0), "borderline"),
            (CHOICE_BORDERLINE_THRESHOLD, "marginal_mulligan"),
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
            < CHOICE_BORDERLINE_THRESHOLD
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
        assert rec.verdict == "borderline"
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


# ---------------------------------------------------------------------------
# Degradation surfacing + stats coverage (roadmap Step 5)
# ---------------------------------------------------------------------------


def _shrunk_row(name: str) -> ShrunkWinRates:
    """A minimal ShrunkWinRates row for the given card name."""
    return ShrunkWinRates(
        mtga_id=0,
        name=name,
        shrunk_opening_hand_win_rate=0.55,
        shrunk_drawn_win_rate=0.54,
        shrunk_ever_drawn_win_rate=0.545,
        weight=0.8,
    )


def _format_stats(*names: str) -> FormatStats:
    """A FormatStats whose shrunk table has an entry per given card name.

    Keys are the (ASCII, already-folded) names; ``zscores`` is left
    empty — the degradation coverage count only reads ``shrunk``.
    """
    shrunk: dict[str, ShrunkWinRates] = {name: _shrunk_row(name) for name in names}
    zscores: dict[str, CardZScores] = {}
    return FormatStats(shrunk=shrunk, zscores=zscores)


def _mixed_deck() -> tuple[list[ParsedCard], list[ParsedCard]]:
    """7-card hand + 40-card deck with two distinct spell names.

    17 Forest + 20 Bear + 3 Wolf → 23 deck spells across two names, so a
    ratings table holding only "Bear" leaves exactly 3 spells uncovered.
    """
    forest = _forest()
    bear = _bear(name="Bear")
    wolf = _bear(name="Wolf")
    hand = [forest] * 3 + [bear] * 4
    deck = [forest] * 17 + [bear] * 20 + [wolf] * 3
    return hand, deck


class TestDegradations:
    @pytest.fixture(autouse=True)
    def _pin_skip_count_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default every degradation test to 'no cards skipped on load'.

        Producer 5 reads the *process-global* skip registry populated by
        ``cards.load_parsed_cards`` (via ``service_module.parsed_cards_skip_count``).
        Because that registry persists across the whole test session, another
        test module that loaded a set could otherwise leave it non-zero and
        flip the exact-tuple assertions below. Pinning it to 0 here — and
        letting the dedicated producer-5 test override it — keeps these tests
        deterministic regardless of test order.
        """
        monkeypatch.setattr(service_module, "parsed_cards_skip_count", lambda set_code: 0)

    def test_no_stats_loaded_producer_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        svc = _service(stats_by_set={})  # no ratings for any set
        hand, deck = _make_hand_and_deck()  # 17 lands + 23 Bear spells

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        # Producer 1: no ratings loaded; coverage is (0, n_spells).
        assert rec.stats_coverage == (0, 23)
        assert len(rec.degradations) == 1
        assert "No 17Lands ratings loaded for TLA" in rec.degradations[0]

    def test_partial_coverage_producer_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        # Table covers "Bear" but not "Wolf" → 3 of 23 spells uncovered.
        svc = _service(stats_by_set={"TLA": _format_stats("Bear")})
        hand, deck = _mixed_deck()

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        assert rec.stats_coverage == (20, 23)
        assert len(rec.degradations) == 1
        assert "3 of 23 deck spells have no 17Lands ratings row" in rec.degradations[0]

    def test_set_unknown_to_model_producer_3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        # Model vocabulary lacks set_code_TLA → producer 3. Stats present
        # and fully covering, so producers 1 and 2 stay quiet.
        svc = _service(
            feature_names=("on_the_play", "mulligan_number"),
            stats_by_set={"TLA": _format_stats("Bear")},
        )
        hand, deck = _make_hand_and_deck()

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        assert rec.stats_coverage == (23, 23)
        assert rec.degradations == (
            "Model was trained before TLA — format-specific adjustments unavailable.",
        )

    def test_version_mismatch_producer_4(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        svc = _service(
            version_warning="model versions {sim:0} differ from live {sim:1}",
            stats_by_set={"TLA": _format_stats("Bear")},
        )
        hand, deck = _make_hand_and_deck()

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        assert rec.degradations == (
            "Model was trained on an older feature pipeline — retrain pending.",
        )

    def test_cards_skipped_on_load_producer_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        # Simulate a data-only push whose set encoded 2 cards using an enum
        # value this app version can't read: load_parsed_cards skipped them,
        # so the process-global registry reports 2 for TLA.
        monkeypatch.setattr(service_module, "parsed_cards_skip_count", lambda set_code: 2)
        svc = _service(stats_by_set={"TLA": _format_stats("Bear")})
        hand, deck = _make_hand_and_deck()

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        # Producers 1-4 stay quiet (stats present + covering, set in-vocab,
        # no version warning); only producer 5 fires.
        assert rec.degradations == (
            "2 card(s) in TLA couldn't be read by this app version — "
            "update the app for full coverage of this set.",
        )

    def test_healthy_call_has_no_degradations(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_stubs(monkeypatch, p_keep=0.6)
        svc = _service(stats_by_set={"TLA": _format_stats("Bear")})
        hand, deck = _make_hand_and_deck()

        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        assert rec.degradations == ()
        assert rec.stats_coverage == (23, 23)

    def test_construct_without_new_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Constructing ChoiceRecommendation without the new fields still
        works (immutable defaults) — legacy call sites keep compiling."""
        _install_stubs(monkeypatch, p_keep=0.6)
        svc = _service(stats_by_set={"TLA": _format_stats("Bear")})
        hand, deck = _make_hand_and_deck()
        rec = svc.recommend_choice(
            hand=hand,
            deck=deck,
            on_the_play=True,
            mulligan_number=0,
            opp_mulligan_number=None,
            n_sims=5,
        )
        # Reuse the real explanation but omit degradations / stats_coverage.
        bare = ChoiceRecommendation(
            verdict=rec.verdict,
            p_keep=rec.p_keep,
            mulligan_number_from=0,
            mulligan_number_to=1,
            n_sims=5,
            explanation=rec.explanation,
        )
        assert bare.degradations == ()
        assert bare.stats_coverage is None
