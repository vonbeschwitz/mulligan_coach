"""Tests for the data-release publisher in
``packages/overlay/packaging/publish_data_release.py``.

We can't exercise the full CLI end-to-end without a real GitHub
account, so the tests focus on the pure transforms that turn the
on-disk artifacts into the manifest JSON:

* every artifact kind is detected and routed to the right
  destination filename;
* the produced manifest validates against the slice-3 parser the
  shipped overlay will use to consume it;
* missing per-set files are skipped (warning only), but a missing
  model dir is fatal — both behaviours the user would care about
  if they accidentally pointed the script at the wrong tree.

We import the publisher as a plain module via :mod:`importlib` so
the test stays independent of the ``packaging/`` dir being on
sys.path — the script is meant to be invoked as
``python publish_data_release.py``, not imported via the workspace.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

# Re-use the slice-3 parser so the test's "is the manifest valid?"
# assertion goes through the exact code path the shipped overlay runs.
from mulligan_coach_overlay.auto_update import parse_manifest


@pytest.fixture
def publisher_module() -> Iterator[object]:
    """Load ``publish_data_release.py`` as a module by file path.

    The script lives in ``packages/overlay/packaging/`` which isn't
    a package, so we go through :mod:`importlib.util` rather than
    a regular ``from ... import`` (the latter would require touching
    the workspace's ``pyproject.toml``).
    """
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "packages" / "overlay" / "packaging" / "publish_data_release.py"
    spec = importlib.util.spec_from_file_location("publish_data_release", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves forward annotations via ``sys.modules[cls.__module__]``,
    # so the module has to be findable there before exec_module runs.
    sys.modules["publish_data_release"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("publish_data_release", None)


def _make_fake_data_tree(
    *,
    root: Path,
    sets: list[str],
    model_name: str,
    skip_ratings_for: set[str] | None = None,
) -> tuple[Path, Path]:
    """Populate a tmp_path with the on-disk layout the publisher expects.

    Returns ``(data_root, models_root)``. ``skip_ratings_for`` lets a
    test simulate a set whose parquet hasn't been generated yet — the
    publisher should warn + skip but not raise.
    """
    skip = skip_ratings_for or set()
    data_root = root / "data"
    models_root = root / "models"

    for set_code in sets:
        if set_code not in skip:
            parquet = (
                data_root
                / "processed"
                / "seventeenlands"
                / "ratings"
                / set_code
                / "PremierDraft.parquet"
            )
            parquet.parent.mkdir(parents=True, exist_ok=True)
            parquet.write_bytes(b"fake-parquet-for-" + set_code.encode())

        cards = data_root / "processed" / "parsed_cards" / f"{set_code}.json"
        cards.parent.mkdir(parents=True, exist_ok=True)
        cards.write_text(f'{{"set": "{set_code}"}}', encoding="utf-8")

    model_dir = models_root / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "xgboost.json").write_text('{"w": []}', encoding="utf-8")
    (model_dir / "metadata.json").write_text('{"features": ["f1"]}', encoding="utf-8")

    return data_root, models_root


def test_collect_artifacts_covers_every_set_and_model(
    publisher_module: object, tmp_path: Path
) -> None:
    """All three artifact kinds are produced with the canonical upload names."""
    data_root, models_root = _make_fake_data_tree(
        root=tmp_path, sets=["TLA", "TMT"], model_name="choice_v6"
    )

    artifacts = publisher_module._collect_artifacts(  # type: ignore[attr-defined]
        data_root=data_root,
        models_root=models_root,
        sets=["TLA", "TMT"],
        model_name="choice_v6",
        staging_dir=tmp_path / "staging",
    )

    by_upload = {a.upload_name: a for a in artifacts}
    assert "TLA-PremierDraft.parquet" in by_upload
    assert "TMT-PremierDraft.parquet" in by_upload
    assert "TLA-parsed-cards.json" in by_upload
    assert "TMT-parsed-cards.json" in by_upload
    assert "choice_v6.zip" in by_upload
    # Model is the only "kind=model" entry; the per-set ones split
    # between ratings / parsed_cards.
    kinds = {a.kind for a in artifacts}
    assert kinds == {"ratings", "parsed_cards", "model"}

    # Sanity-check that the model zip is real (the publisher wrote it).
    assert by_upload["choice_v6.zip"].local_path.is_file()


def test_collect_artifacts_skips_missing_ratings_with_warning(
    publisher_module: object, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A missing per-set parquet → skipped (with a stderr warning), not fatal.

    This is the "publisher runs between a new set being announced
    and us having 17Lands data for it" case.
    """
    data_root, models_root = _make_fake_data_tree(
        root=tmp_path,
        sets=["TLA", "TMT"],
        model_name="choice_v6",
        skip_ratings_for={"TMT"},
    )

    artifacts = publisher_module._collect_artifacts(  # type: ignore[attr-defined]
        data_root=data_root,
        models_root=models_root,
        sets=["TLA", "TMT"],
        model_name="choice_v6",
        staging_dir=tmp_path / "staging",
    )

    upload_names = {a.upload_name for a in artifacts}
    assert "TMT-PremierDraft.parquet" not in upload_names
    # Parsed cards still present even though ratings was skipped.
    assert "TMT-parsed-cards.json" in upload_names
    err = capsys.readouterr().err
    assert "skipping ratings for TMT" in err


def test_collect_artifacts_missing_model_raises(publisher_module: object, tmp_path: Path) -> None:
    """Missing model dir is fatal — auto-update has no fallback for that."""
    data_root, models_root = _make_fake_data_tree(
        root=tmp_path, sets=["TLA"], model_name="choice_v6"
    )

    with pytest.raises(SystemExit, match="does not exist"):
        publisher_module._collect_artifacts(  # type: ignore[attr-defined]
            data_root=data_root,
            models_root=models_root,
            sets=["TLA"],
            model_name="choice_v999",  # not present
            staging_dir=tmp_path / "staging",
        )


def test_build_manifest_produces_consumable_json(publisher_module: object, tmp_path: Path) -> None:
    """The shipped overlay's manifest parser accepts what we produce.

    The integration matters more than any unit assertion: round-trip
    through :func:`parse_manifest` proves we got the schema, field
    names, and URL convention right.
    """
    data_root, models_root = _make_fake_data_tree(
        root=tmp_path, sets=["TLA"], model_name="choice_v6"
    )
    artifacts = publisher_module._collect_artifacts(  # type: ignore[attr-defined]
        data_root=data_root,
        models_root=models_root,
        sets=["TLA"],
        model_name="choice_v6",
        staging_dir=tmp_path / "staging",
    )

    manifest = publisher_module.build_manifest(  # type: ignore[attr-defined]
        repo="owner/repo",
        tag="data-current",
        artifacts=artifacts,
        version="2026-05-23",
    )

    raw = json.dumps(manifest)
    parsed = parse_manifest(raw)

    assert parsed.schema_version == 1
    # 1 ratings + 1 parsed_cards + 1 model = 3 artifacts.
    assert len(parsed.artifacts) == 3
    by_kind = {a.kind: a for a in parsed.artifacts}
    assert (
        by_kind["ratings"].url
        == "https://github.com/owner/repo/releases/download/data-current/TLA-PremierDraft.parquet"
    )
    assert (
        by_kind["model"].url
        == "https://github.com/owner/repo/releases/download/data-current/choice_v6.zip"
    )
    # All artifacts share the version stamp we passed in.
    assert {a.version for a in parsed.artifacts} == {"2026-05-23"}


def test_manifest_sha_matches_underlying_file(publisher_module: object, tmp_path: Path) -> None:
    """The SHA in the manifest entry matches a fresh SHA of the same bytes.

    Catches a regression where ``_collect_artifacts`` and the
    downloader would compute hashes differently (e.g. one
    streaming + the other slurping).
    """
    import hashlib

    data_root, models_root = _make_fake_data_tree(
        root=tmp_path, sets=["TLA"], model_name="choice_v6"
    )
    artifacts = publisher_module._collect_artifacts(  # type: ignore[attr-defined]
        data_root=data_root,
        models_root=models_root,
        sets=["TLA"],
        model_name="choice_v6",
        staging_dir=tmp_path / "staging",
    )
    manifest = publisher_module.build_manifest(  # type: ignore[attr-defined]
        repo="owner/repo",
        tag="data-current",
        artifacts=artifacts,
        version="v",
    )

    # Pick the ratings artifact (single, predictable file) and verify
    # the manifest's sha lines up with a direct hash of the bytes.
    ratings_entry = next(a for a in manifest["artifacts"] if a["kind"] == "ratings")
    ratings_file = (
        data_root / "processed" / "seventeenlands" / "ratings" / "TLA" / "PremierDraft.parquet"
    )
    expected = hashlib.sha256(ratings_file.read_bytes()).hexdigest()
    assert ratings_entry["sha256"] == expected


def test_zip_model_uses_flat_layout(publisher_module: object, tmp_path: Path) -> None:
    """The model zip stores files at their bare names, not under ``choice_v6/``.

    The auto-updater's extraction code drops the zip's contents
    straight into the destination model dir; a nested layout would
    end up with ``choice_v6/choice_v6/xgboost.json``.
    """
    import zipfile

    model_dir = tmp_path / "models" / "choice_v6"
    model_dir.mkdir(parents=True)
    (model_dir / "xgboost.json").write_text("{}", encoding="utf-8")
    (model_dir / "metadata.json").write_text("{}", encoding="utf-8")

    dest = tmp_path / "choice_v6.zip"
    publisher_module._zip_model(model_dir, dest)  # type: ignore[attr-defined]

    with zipfile.ZipFile(dest) as archive:
        names = set(archive.namelist())
    assert names == {"xgboost.json", "metadata.json"}
