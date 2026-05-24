"""Tests for :mod:`mulligan_coach_overlay.user_data`.

Slice-1 scope: verify that seeding from the bundle into the user dir
behaves correctly across three scenarios — fresh install, idempotent
re-launch on the same bundle version, and EXE upgrade (bundle
version differs from seeded version). Each test uses ``monkeypatch``
to redirect ``user_state_root`` at a ``tmp_path``-rooted dir so we
don't touch the developer's real ``%LOCALAPPDATA%\\MulliganCoach``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from mulligan_coach_overlay import user_data


@pytest.fixture
def fake_user_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """Redirect ``user_state_root()`` at a per-test temporary path.

    We do this at the module level so any helper inside ``user_data``
    that re-resolves ``user_state_root`` (now or later) picks up the
    override transparently.
    """
    user_root = tmp_path / "user_state"
    monkeypatch.setattr(user_data, "user_state_root", lambda: user_root)
    yield user_root


def _fake_bundle(
    tmp_path: Path,
    *,
    bundle_version: str | None,
    data_files: dict[str, str] | None = None,
    model_files: dict[str, str] | None = None,
) -> Path:
    """Build a minimal bundle dir laid out the way PyInstaller produces."""
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)

    if bundle_version is not None:
        (bundle_root / "_bundle_version.txt").write_text(bundle_version, encoding="utf-8")

    for rel, content in (data_files or {}).items():
        path = bundle_root / "data" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    for rel, content in (model_files or {}).items():
        path = bundle_root / "models" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return bundle_root


def test_first_launch_seeds_everything(fake_user_state_root: Path, tmp_path: Path) -> None:
    """User dir is empty -> seed_from_bundle copies the bundled data + model."""
    bundle_root = _fake_bundle(
        tmp_path,
        bundle_version="20260523T120000Z+abc1234",
        data_files={"processed/parsed_cards/TLA.json": '{"tla": true}'},
        model_files={"choice_v6/xgboost.json": '{"weights": []}'},
    )

    copied = user_data.seed_from_bundle(bundle_root)

    assert copied is True
    assert (fake_user_state_root / "data" / "processed" / "parsed_cards" / "TLA.json").read_text(
        encoding="utf-8"
    ) == '{"tla": true}'
    assert (fake_user_state_root / "models" / "choice_v6" / "xgboost.json").read_text(
        encoding="utf-8"
    ) == '{"weights": []}'
    assert (fake_user_state_root / "_seeded_version.txt").read_text(
        encoding="utf-8"
    ) == "20260523T120000Z+abc1234"


def test_same_version_relaunch_is_noop(fake_user_state_root: Path, tmp_path: Path) -> None:
    """A second launch on the same bundle version skips the copy entirely.

    We modify the seeded file after the first launch and confirm the
    modification survives the second call — proving no file copy
    happened the second time.
    """
    # Provide both data and models so the post-seed user dir is
    # "complete" — otherwise the partial-recovery branch re-seeds.
    bundle_root = _fake_bundle(
        tmp_path,
        bundle_version="v1",
        data_files={"processed/parsed_cards/TLA.json": "original"},
        model_files={"choice_v6/xgboost.json": "model"},
    )

    assert user_data.seed_from_bundle(bundle_root) is True

    # Simulate "user (or auto-updater) modified the file post-seed".
    seeded_file = fake_user_state_root / "data" / "processed" / "parsed_cards" / "TLA.json"
    seeded_file.write_text("user-modified", encoding="utf-8")

    assert user_data.seed_from_bundle(bundle_root) is False
    assert seeded_file.read_text(encoding="utf-8") == "user-modified"


def test_exe_upgrade_re_seeds(fake_user_state_root: Path, tmp_path: Path) -> None:
    """A bundle with a newer version stamp overwrites the user dir."""
    # First launch with bundle v1.
    bundle_v1 = _fake_bundle(
        tmp_path / "v1",
        bundle_version="v1",
        data_files={"processed/parsed_cards/TLA.json": "v1-content"},
    )
    user_data.seed_from_bundle(bundle_v1)

    # User-edits between launches — these are deliberately stale and
    # should be overwritten on the v2 launch.
    seeded_file = fake_user_state_root / "data" / "processed" / "parsed_cards" / "TLA.json"
    seeded_file.write_text("user-stale-edit", encoding="utf-8")

    # Second launch with bundle v2.
    bundle_v2 = _fake_bundle(
        tmp_path / "v2",
        bundle_version="v2",
        data_files={"processed/parsed_cards/TLA.json": "v2-content"},
    )
    assert user_data.seed_from_bundle(bundle_v2) is True
    assert seeded_file.read_text(encoding="utf-8") == "v2-content"
    assert (fake_user_state_root / "_seeded_version.txt").read_text(encoding="utf-8") == "v2"


def test_force_re_seeds_even_same_version(fake_user_state_root: Path, tmp_path: Path) -> None:
    """``force=True`` bypasses the version comparison."""
    bundle_root = _fake_bundle(
        tmp_path,
        bundle_version="v1",
        data_files={"processed/parsed_cards/TLA.json": "bundled"},
    )
    user_data.seed_from_bundle(bundle_root)
    seeded_file = fake_user_state_root / "data" / "processed" / "parsed_cards" / "TLA.json"
    seeded_file.write_text("user-edit", encoding="utf-8")

    assert user_data.seed_from_bundle(bundle_root, force=True) is True
    assert seeded_file.read_text(encoding="utf-8") == "bundled"


def test_partial_user_dir_is_completed(fake_user_state_root: Path, tmp_path: Path) -> None:
    """A user dir missing the models/ subtree gets re-seeded even at same version.

    Catches the case where a previous seed crashed mid-copy (or the
    user manually wiped one of the two subdirs) — we shouldn't trust
    the stamp alone.
    """
    bundle_root = _fake_bundle(
        tmp_path,
        bundle_version="v1",
        data_files={"processed/parsed_cards/TLA.json": "data"},
        model_files={"choice_v6/xgboost.json": "model"},
    )
    # Lay down the data subtree + version stamp manually but leave
    # the models subtree missing — simulating a partial install.
    (fake_user_state_root / "data" / "processed" / "parsed_cards").mkdir(parents=True)
    (fake_user_state_root / "data" / "processed" / "parsed_cards" / "TLA.json").write_text(
        "data", encoding="utf-8"
    )
    (fake_user_state_root / "_seeded_version.txt").write_text("v1", encoding="utf-8")

    assert user_data.seed_from_bundle(bundle_root) is True
    assert (fake_user_state_root / "models" / "choice_v6" / "xgboost.json").read_text(
        encoding="utf-8"
    ) == "model"


def test_bundle_without_version_seeds_when_missing(
    fake_user_state_root: Path, tmp_path: Path
) -> None:
    """A bundle without ``_bundle_version.txt`` (older build) still seeds.

    No version stamp means we can't tell upgrade from re-launch, so
    the conservative behaviour is "seed if the user dir is missing
    things, skip otherwise". This test exercises the "missing" arm.
    """
    bundle_root = _fake_bundle(
        tmp_path,
        bundle_version=None,
        data_files={"processed/parsed_cards/TLA.json": "data"},
    )

    assert user_data.seed_from_bundle(bundle_root) is True
    # No stamp written, since the bundle didn't have one.
    assert not (fake_user_state_root / "_seeded_version.txt").exists()


def test_bundle_with_no_data_or_models_is_noop(fake_user_state_root: Path, tmp_path: Path) -> None:
    """A malformed bundle (no data/, no models/) is a clean no-op."""
    bundle_root = _fake_bundle(tmp_path, bundle_version="v1")
    assert user_data.seed_from_bundle(bundle_root) is False
    assert not fake_user_state_root.exists()


def test_user_state_root_default_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Honours ``%LOCALAPPDATA%`` on Windows; falls back to ``~/AppData/Local`` otherwise.

    The platform branches in :func:`user_state_root` are non-trivial,
    so we lock the Windows path resolution here. The macOS / Linux
    branches are exercised indirectly through ``fake_user_state_root``
    on dev machines.
    """
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert user_data.user_state_root() == tmp_path / "local" / "MulliganCoach"

    monkeypatch.delenv("LOCALAPPDATA")
    assert user_data.user_state_root().name == "MulliganCoach"
    assert "AppData" in str(user_data.user_state_root())


def test_user_data_root_and_models_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both helpers route through ``user_state_root`` to keep the layout in sync."""
    fake_root = Path(os.path.normpath("/tmp/mc-test"))
    monkeypatch.setattr(user_data, "user_state_root", lambda: fake_root)
    assert user_data.user_data_root() == fake_root / "data"
    assert user_data.user_models_root() == fake_root / "models"
