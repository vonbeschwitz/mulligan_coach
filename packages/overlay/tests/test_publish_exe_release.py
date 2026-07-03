"""Tests for ``packages/overlay/packaging/publish_exe_release.py``.

The publisher's ``main()`` shells out to ``build_distribution.py``
and ``gh`` — neither of which we can run in CI. The tests focus on
the parts we can verify deterministically:

* :func:`_zip_bundle` produces a zip whose internal layout matches
  "extract → ``MulliganCoach/MulliganCoach.exe``", so the user's
  expectation after right-click → Extract All is satisfied.
* :func:`_read_bundle_version` rejects a bundle missing its build
  stamp (the most likely user error — they forgot to rebuild).
* :func:`_build_version_sidecar` produces a JSON shape the future
  in-app update notifier can rely on (stable field names + URL
  derivation from the same repo/tag the rest of the script targets).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
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

    Returns the dist dir's path. The exact contents don't matter
    for the zip test — only the relative layout does.
    """
    dist = root / "MulliganCoach"
    (dist / "_internal").mkdir(parents=True)
    (dist / "MulliganCoach.exe").write_bytes(b"fake-exe-bytes")
    (dist / "_internal" / "_bundle_version.txt").write_text(
        "20260524T024048Z+c13b9a5", encoding="utf-8"
    )
    (dist / "_internal" / "python312.dll").write_bytes(b"fake-dll-bytes")
    (dist / "_internal" / "data").mkdir()
    (dist / "_internal" / "data" / "x.json").write_text("{}", encoding="utf-8")
    return dist


def test_zip_bundle_stores_relative_to_parent(publisher_module: object, tmp_path: Path) -> None:
    """Members are stored under ``MulliganCoach/...`` so Extract All gives
    the user the expected single-directory unpack."""
    dist = _make_fake_dist(tmp_path)
    dest = tmp_path / "MulliganCoach.zip"

    publisher_module._zip_bundle(dist, dest)  # type: ignore[attr-defined]

    with zipfile.ZipFile(dest) as archive:
        names = set(archive.namelist())
    assert "MulliganCoach/MulliganCoach.exe" in names
    assert "MulliganCoach/_internal/_bundle_version.txt" in names
    assert "MulliganCoach/_internal/python312.dll" in names
    assert "MulliganCoach/_internal/data/x.json" in names
    # No stray top-level entries.
    top_level = {n.split("/", 1)[0] for n in names}
    assert top_level == {"MulliganCoach"}


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


def test_build_version_sidecar_shape(publisher_module: object, tmp_path: Path) -> None:
    """The sidecar JSON carries every field the future notifier needs."""
    fake_zip = tmp_path / "MulliganCoach.zip"
    fake_zip.write_bytes(b"some-content-for-sha")

    payload = publisher_module._build_version_sidecar(  # type: ignore[attr-defined]
        bundle_version="20260524T024048Z+c13b9a5",
        zip_path=fake_zip,
        repo="vonbeschwitz/mulligan_coach_data",
        tag="exe-latest",
    )

    assert payload["schema_version"] == 1
    assert payload["bundle_version"] == "20260524T024048Z+c13b9a5"
    assert payload["download_url"] == (
        "https://github.com/vonbeschwitz/mulligan_coach_data/releases/download/"
        "exe-latest/MulliganCoach.zip"
    )
    assert payload["release_page"] == (
        "https://github.com/vonbeschwitz/mulligan_coach_data/releases/tag/exe-latest"
    )
    # SHA matches the same hash a fresh hashlib call would produce.
    import hashlib

    assert payload["sha256"] == hashlib.sha256(b"some-content-for-sha").hexdigest()
    assert payload["size_bytes"] == len(b"some-content-for-sha")
    # Generated-at lands in ISO8601 UTC with a Z suffix.
    assert payload["generated_at"].endswith("Z")


def test_version_sidecar_is_json_serialisable(publisher_module: object, tmp_path: Path) -> None:
    """Round-trip through ``json.dumps`` / ``json.loads`` so the file the
    publisher writes is always valid JSON the notifier can parse."""
    fake_zip = tmp_path / "MulliganCoach.zip"
    fake_zip.write_bytes(b"x")
    payload = publisher_module._build_version_sidecar(  # type: ignore[attr-defined]
        bundle_version="v1",
        zip_path=fake_zip,
        repo="a/b",
        tag="t",
    )
    encoded = json.dumps(payload)
    assert json.loads(encoded) == payload


def test_stage_artifacts_writes_zip_and_sidecar_without_gh(
    publisher_module: object, tmp_path: Path
) -> None:
    """The CI ``--stage-dir`` path produces the same zip + sidecar the
    publisher would upload, into a durable dir, with no gh interaction."""
    dist = _make_fake_dist(tmp_path / "dist")
    out_dir = tmp_path / "artifacts"

    zip_path, version_json = publisher_module.stage_artifacts(  # type: ignore[attr-defined]
        dist, out_dir, repo="vonbeschwitz/mulligan_coach_data", tag="exe-latest"
    )

    # Both artifacts land in out_dir under the canonical names.
    assert zip_path == out_dir / "MulliganCoach.zip"
    assert version_json == out_dir / "exe_version.json"
    assert zip_path.is_file() and version_json.is_file()

    # The zip has the same "extract → MulliganCoach/..." layout as a publish.
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "MulliganCoach/MulliganCoach.exe" in names

    # The sidecar carries the bundle stamp read from the fake dist.
    payload = json.loads(version_json.read_text(encoding="utf-8"))
    assert payload["bundle_version"] == "20260524T024048Z+c13b9a5"
    assert payload["schema_version"] == 1
