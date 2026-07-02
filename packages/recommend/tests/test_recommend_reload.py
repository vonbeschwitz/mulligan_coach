"""Tests for :meth:`RecommendationService.reload_*`.

The reload methods are the seams the future manifest fetcher will
call after writing a fresh parquet / model bundle into the user
state dir. Each test monkeypatches the slow disk-loader the reload
delegates to so we can exercise the swap + status-update logic
without manufacturing a real 17Lands parquet or training a
throwaway model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from mulligan_coach_model import ChoiceModelBundle, ModelBundle
from mulligan_coach_recommend import service as service_module
from mulligan_coach_recommend.service import (
    FormatStats,
    RecommendationService,
    ServiceStatus,
)


def _stub_format_stats() -> FormatStats:
    """Return a non-empty FormatStats shaped like a real one.

    The reload paths don't inspect the contents, only the identity of
    the object, so a single empty shrunk dict + empty zscores dict is
    sufficient for the swap to be observable.
    """
    return FormatStats(shrunk={}, zscores={})


def _empty_service(
    *,
    stats_by_set: dict[str, FormatStats] | None = None,
    status: ServiceStatus | None = None,
) -> RecommendationService:
    """Build a RecommendationService with nothing loaded.

    The reload tests don't need a real model bundle or executor —
    only the in-memory swap logic is under test here.
    """
    return RecommendationService(
        bundle=None,
        choice_bundle=None,
        stats_by_set=stats_by_set or {},
        status=status
        or ServiceStatus(
            model_loaded=False,
            model_dir=None,
            formats_with_stats=[],
            formats_missing_stats=[],
        ),
    )


def test_reload_ratings_swaps_in_new_format_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful reload populates ``stats_by_set`` with the fresh object."""
    fresh = _stub_format_stats()
    monkeypatch.setattr(service_module, "_try_load_format_stats", lambda set_code: fresh)
    svc = _empty_service()

    outcome = svc.reload_ratings(["TLA"])

    assert outcome == {"TLA": True}
    assert svc.stats_by_set["TLA"] is fresh
    assert "TLA" in svc.status.formats_with_stats


def test_reload_ratings_keeps_previous_stats_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed reload (parquet missing or corrupt) preserves the in-memory copy.

    The future manifest fetcher will sometimes hand off a partially-
    downloaded file or a parquet from a transient mirror outage; we
    shouldn't wipe out the previously-good ratings just because the
    download landed broken.
    """
    initial = _stub_format_stats()
    svc = _empty_service(stats_by_set={"TLA": initial})
    monkeypatch.setattr(service_module, "_try_load_format_stats", lambda set_code: None)

    outcome = svc.reload_ratings(["TLA"])

    assert outcome == {"TLA": False}
    assert svc.stats_by_set["TLA"] is initial


def test_reload_ratings_independent_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-set success/failure are reported independently."""
    fresh_tla = _stub_format_stats()
    loader_outputs = {"TLA": fresh_tla, "TMT": None}
    monkeypatch.setattr(
        service_module,
        "_try_load_format_stats",
        lambda set_code: loader_outputs[set_code],
    )
    svc = _empty_service()

    outcome = svc.reload_ratings(["TLA", "TMT"])

    assert outcome == {"TLA": True, "TMT": False}
    assert svc.stats_by_set == {"TLA": fresh_tla}


def test_reload_choice_model_swaps_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A successful ChoiceModelBundle.load updates the bundle + status."""
    target = tmp_path / "choice_v6"
    target.mkdir()

    sentinel = object()

    def _fake_load(model_dir: Path) -> object:
        assert model_dir == target
        return sentinel

    monkeypatch.setattr(ChoiceModelBundle, "load", _fake_load)
    svc = _empty_service()

    assert svc.reload_choice_model(target) is True
    assert svc.choice_bundle is sentinel
    assert svc.status.model_loaded is True
    assert svc.status.model_dir == target
    assert svc.status.error is None


def test_reload_choice_model_missing_dir_preserves_state(tmp_path: Path) -> None:
    """A missing model dir reports the error without clobbering anything."""
    svc = _empty_service()
    missing = tmp_path / "does_not_exist"

    assert svc.reload_choice_model(missing) is False
    assert svc.choice_bundle is None
    assert svc.status.model_loaded is False
    assert svc.status.error is not None
    assert "does not exist" in svc.status.error


def test_reload_choice_model_load_failure_preserves_previous_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A load that raises (corrupt artifacts) keeps the prior bundle in place."""
    target = tmp_path / "choice_v6"
    target.mkdir()
    previous = object()
    svc = RecommendationService(
        bundle=None,
        choice_bundle=previous,  # type: ignore[arg-type]
        status=ServiceStatus(
            model_loaded=True,
            model_dir=target,
            formats_with_stats=[],
            formats_missing_stats=[],
        ),
    )

    def _broken_load(model_dir: Path) -> object:
        raise RuntimeError("corrupt artifact")

    monkeypatch.setattr(ChoiceModelBundle, "load", _broken_load)

    assert svc.reload_choice_model(target) is False
    assert svc.choice_bundle is previous
    assert svc.status.model_loaded is True
    assert svc.status.error is not None
    assert "corrupt artifact" in svc.status.error


def test_reload_win_model_returns_false_when_missing(tmp_path: Path) -> None:
    """The legacy win model is optional; a missing dir is a quiet ``False``."""
    svc = _empty_service()
    assert svc.reload_win_model(tmp_path / "absent") is False
    assert svc.bundle is None


def test_reload_win_model_swaps_on_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "all3_v2"
    target.mkdir()
    sentinel = object()
    monkeypatch.setattr(ModelBundle, "load", lambda model_dir: sentinel)
    svc = _empty_service()

    assert svc.reload_win_model(target) is True
    assert svc.bundle is sentinel
