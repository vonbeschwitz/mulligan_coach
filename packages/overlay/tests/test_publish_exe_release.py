"""Tests for ``packages/overlay/packaging/publish_exe_release.py``.

The publisher's ``main()`` shells out to ``gh`` and reads a real
installer built by Inno Setup — neither of which we can run in CI. The
tests focus on the parts we can verify deterministically:

* :func:`_read_bundle_version` rejects a bundle missing its build stamp
  (the most likely user error — they forgot to rebuild).
* :func:`_require_installer` rejects a missing installer (the other
  likely error — they forgot the ISCC step).
* :func:`_build_version_sidecar` produces a JSON shape the in-app update
  notifier relies on (stable field names + the installer download URL).
* :func:`stage_artifacts` (the CI ``--stage-dir`` path) writes that
  sidecar without any gh interaction.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def publisher_module() -> Iterator[object]:
    """Load ``publish_exe_release.py`` by file path.

    Same trick as the data publisher's test fixture: the file lives
    in ``packaging/`` which isn't a package, so we go through
    :mod:`importlib.util` and register the module in ``sys.modules``
    (dataclasses' forward-annotation resolution looks there).
    """
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "packages" / "overlay" / "packaging" / "publish_exe_release.py"
    spec = importlib.util.spec_from_file_location("publish_exe_release", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["publish_exe_release"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("publish_exe_release", None)


def _make_fake_dist(root: Path) -> Path:
    """Build a tmp_path tree shaped like ``dist/MulliganCoach/``.

    Returns the dist dir's path. Only the ``_internal/_bundle_version.txt``
    stamp is read by the publisher; the rest mirrors a real bundle so the
    layout is realistic.
    """
    dist = root / "MulliganCoach"
    (dist / "_internal").mkdir(parents=True)
    (dist / "MulliganCoach.exe").write_bytes(b"fake-exe-bytes")
    (dist / "_internal" / "_bundle_version.txt").write_text(
        "20260524T024048Z+c13b9a5", encoding="utf-8"
    )
    return dist


def _make_fake_installer(root: Path) -> Path:
    """Write a stand-in ``MulliganCoachSetup.exe`` and return its path."""
    installer = root / "MulliganCoachSetup.exe"
    installer.write_bytes(b"fake-installer-bytes")
    return installer


def test_read_bundle_version_returns_stamp(publisher_module: object, tmp_path: Path) -> None:
    """Happy path: stamp file present and readable."""
    dist = _make_fake_dist(tmp_path)
    stamp = publisher_module._read_bundle_version(dist)  # type: ignore[attr-defined]
    assert stamp == "20260524T024048Z+c13b9a5"


def test_read_bundle_version_missing_stamp_raises(publisher_module: object, tmp_path: Path) -> None:
    """A dist without a stamp file is fatal — refusing to ship is the right
    call because the in-app update notifier needs the stamp to know if
    a newer build is available."""
    dist = _make_fake_dist(tmp_path)
    (dist / "_internal" / "_bundle_version.txt").unlink()
    with pytest.raises(SystemExit, match="missing"):
        publisher_module._read_bundle_version(dist)  # type: ignore[attr-defined]


def test_require_installer_missing_raises(publisher_module: object, tmp_path: Path) -> None:
    """A missing installer is fatal with a build-it-first hint — the
    publisher never builds one itself."""
    missing = tmp_path / "MulliganCoachSetup.exe"
    with pytest.raises(SystemExit, match="installer not found"):
        publisher_module._require_installer(missing)  # type: ignore[attr-defined]


def test_build_version_sidecar_shape(publisher_module: object, tmp_path: Path) -> None:
    """The sidecar JSON carries every field the notifier needs, with the
    installer as the download target."""
    installer = _make_fake_installer(tmp_path)

    payload = publisher_module._build_version_sidecar(  # type: ignore[attr-defined]
        bundle_version="20260524T024048Z+c13b9a5",
        artifact_path=installer,
        repo="vonbeschwitz/mulligan_coach_data",
        tag="exe-latest",
    )

    assert payload["schema_version"] == 1
    assert payload["bundle_version"] == "20260524T024048Z+c13b9a5"
    assert payload["download_url"] == (
        "https://github.com/vonbeschwitz/mulligan_coach_data/releases/download/"
        "exe-latest/MulliganCoachSetup.exe"
    )
    assert payload["release_page"] == (
        "https://github.com/vonbeschwitz/mulligan_coach_data/releases/tag/exe-latest"
    )
    # SHA matches the same hash a fresh hashlib call would produce.
    import hashlib

    assert payload["sha256"] == hashlib.sha256(b"fake-installer-bytes").hexdigest()
    assert payload["size_bytes"] == len(b"fake-installer-bytes")
    # Generated-at lands in ISO8601 UTC with a Z suffix.
    assert payload["generated_at"].endswith("Z")


def test_version_sidecar_is_json_serialisable(publisher_module: object, tmp_path: Path) -> None:
    """Round-trip through ``json.dumps`` / ``json.loads`` so the file the
    publisher writes is always valid JSON the notifier can parse."""
    installer = _make_fake_installer(tmp_path)
    payload = publisher_module._build_version_sidecar(  # type: ignore[attr-defined]
        bundle_version="v1",
        artifact_path=installer,
        repo="a/b",
        tag="t",
    )
    encoded = json.dumps(payload)
    assert json.loads(encoded) == payload


def test_stage_artifacts_writes_sidecar_without_gh(
    publisher_module: object, tmp_path: Path
) -> None:
    """The CI ``--stage-dir`` path produces the sidecar the publisher would
    upload, into a durable dir, with no gh interaction — and leaves the
    installer where it is (not copied)."""
    dist = _make_fake_dist(tmp_path / "dist")
    installer = _make_fake_installer(tmp_path / "dist")
    out_dir = tmp_path / "artifacts"

    installer_path, version_json = publisher_module.stage_artifacts(  # type: ignore[attr-defined]
        dist, installer, out_dir, repo="vonbeschwitz/mulligan_coach_data", tag="exe-latest"
    )

    # The installer path is returned unchanged (uploaded from its build
    # location); only the sidecar lands in out_dir.
    assert installer_path == installer
    assert version_json == out_dir / "exe_version.json"
    assert version_json.is_file()

    # The sidecar carries the bundle stamp read from the fake dist and
    # points at the installer asset.
    payload = json.loads(version_json.read_text(encoding="utf-8"))
    assert payload["bundle_version"] == "20260524T024048Z+c13b9a5"
    assert payload["schema_version"] == 1
    assert payload["download_url"].endswith("/MulliganCoachSetup.exe")


def test_stage_artifacts_missing_installer_raises(publisher_module: object, tmp_path: Path) -> None:
    """Staging without a built installer fails loudly rather than writing a
    sidecar that points at a nonexistent asset."""
    dist = _make_fake_dist(tmp_path / "dist")
    missing_installer = tmp_path / "dist" / "MulliganCoachSetup.exe"
    with pytest.raises(SystemExit, match="installer not found"):
        publisher_module.stage_artifacts(  # type: ignore[attr-defined]
            dist, missing_installer, tmp_path / "artifacts", repo="a/b", tag="t"
        )
