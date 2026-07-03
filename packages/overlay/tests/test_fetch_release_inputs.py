"""Tests for ``packages/overlay/packaging/fetch_release_inputs.py``.

The download + unzip paths shell out to ``gh`` / touch the filesystem,
so — mirroring ``test_publish_exe_release.py`` — the tests focus on the
one deterministic, network-free piece: :func:`plan_layout`, which maps
``data-current`` asset filenames to their repo-tree destinations. That
mapping is the load-bearing contract with ``mulligan_coach.spec`` (it
must reproduce exactly the paths ``publish_data_release.py`` publishes
from), so it's the part worth pinning.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def fetch_module() -> Iterator[object]:
    """Load ``fetch_release_inputs.py`` by file path (it's not a package)."""
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "packages" / "overlay" / "packaging" / "fetch_release_inputs.py"
    spec = importlib.util.spec_from_file_location("fetch_release_inputs", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fetch_release_inputs"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("fetch_release_inputs", None)


def test_plan_layout_maps_assets_to_spec_paths(fetch_module: object, tmp_path: Path) -> None:
    """Each asset family lands where ``mulligan_coach.spec`` expects it."""
    staging = tmp_path / "staging"
    repo_root = tmp_path / "repo"
    staged = [
        staging / "TLA-PremierDraft.parquet",
        staging / "TLA-parsed-cards.json",
        staging / "SOS-PremierDraft.parquet",
        staging / "choice_prod.zip",
    ]

    plan = fetch_module.plan_layout(staged, repo_root)  # type: ignore[attr-defined]

    # The model zip is picked out for extraction, not a plain copy.
    assert plan.model_zip == staging / "choice_prod.zip"
    dests = {src.name: dest for src, dest in plan.copies}
    assert dests["TLA-PremierDraft.parquet"] == (
        repo_root
        / "data"
        / "processed"
        / "seventeenlands"
        / "ratings"
        / "TLA"
        / "PremierDraft.parquet"
    )
    assert dests["SOS-PremierDraft.parquet"] == (
        repo_root
        / "data"
        / "processed"
        / "seventeenlands"
        / "ratings"
        / "SOS"
        / "PremierDraft.parquet"
    )
    assert dests["TLA-parsed-cards.json"] == (
        repo_root / "data" / "processed" / "parsed_cards" / "TLA.json"
    )


def test_plan_layout_ignores_unknown_assets(fetch_module: object, tmp_path: Path) -> None:
    """A stray asset (e.g. manifest.json) is ignored, not mis-filed."""
    staging = tmp_path / "staging"
    plan = fetch_module.plan_layout(  # type: ignore[attr-defined]
        [staging / "manifest.json", staging / "README.txt"], tmp_path / "repo"
    )
    assert plan.copies == []
    assert plan.model_zip is None


def test_plan_layout_missing_model_zip_leaves_model_none(
    fetch_module: object, tmp_path: Path
) -> None:
    """Without the model asset, ``model_zip`` stays None so the caller can
    fail loudly (the spec requires the model)."""
    staging = tmp_path / "staging"
    plan = fetch_module.plan_layout(  # type: ignore[attr-defined]
        [staging / "TLA-parsed-cards.json"], tmp_path / "repo"
    )
    assert plan.model_zip is None
    assert len(plan.copies) == 1
