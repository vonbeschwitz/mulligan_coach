"""Reconstruct the gitignored build inputs from the public data release.

Why this exists
---------------

``mulligan_coach.spec`` bundles three inputs that live under
``models/`` and ``data/`` — both **gitignored** — so a bare checkout
(a fresh clone, or a CI runner) can't build the overlay: the spec's
``_check_required`` raises the moment it can't find
``models/choice_prod``, the parsed-cards JSON, or the ratings
parquets.

Those exact bytes are already published, though: ``publish_data_release.py``
uploads them to the floating ``data-current`` release on the public
companion repo (``vonbeschwitz/mulligan_coach_data``). This script is
the inverse of that publisher — it downloads the current assets and
lays them back out into the repo tree at the paths the spec expects:

    <SET>-PremierDraft.parquet  ->  data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet
    <SET>-parsed-cards.json     ->  data/processed/parsed_cards/<SET>.json
    choice_prod.zip             ->  models/choice_prod/   (unzipped, flat)

After this runs, ``build_distribution.py`` / ``mulligan_coach.spec``
find everything they need. It's used by the CI build workflow
(``.github/workflows/build-release.yml``) and is equally handy for a
human who just cloned the repo and wants to build without first
re-materialising the whole data pipeline.

Auth
----

Uses the ambient ``gh`` CLI, same as the publishers. The data repo is
public, so a read-only token is enough — in CI that's the default
``GITHUB_TOKEN`` (exported as ``GH_TOKEN``); locally it's your normal
``gh auth`` session. If the data repo is ever made private, this needs
a token with read access to *that* repo (the workflow's repo-scoped
token would no longer suffice).

Usage
-----

::

    uv run python packages/overlay/packaging/fetch_release_inputs.py
    # or against a fork / alternate tag:
    uv run python packages/overlay/packaging/fetch_release_inputs.py \
        --repo you/your_data --tag data-current
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_REPO = "vonbeschwitz/mulligan_coach_data"
"""Public companion repo hosting the data feed. Mirrors the publishers'
default so the round-trip (publish -> fetch) targets one place."""

_DEFAULT_TAG = "data-current"
"""Floating tag ``publish_data_release.py`` uploads to."""

# Asset-name suffixes / names as written by ``publish_data_release.py``'s
# ``_collect_artifacts``. Kept in lock-step with that script: if the
# upload naming there changes, update these too.
_RATINGS_SUFFIX = "-PremierDraft.parquet"
_PARSED_CARDS_SUFFIX = "-parsed-cards.json"
_MODEL_ZIP_NAME = "choice_prod.zip"
_MODEL_DIR_NAME = "choice_prod"

# ``gh release download`` glob patterns for exactly the assets we lay
# out below (deliberately NOT ``manifest.json`` — the build doesn't
# consume it). ``*`` matches any set code, so whatever sets are
# published get fetched with no hardcoded set list to drift.
_DOWNLOAD_PATTERNS = (f"*{_RATINGS_SUFFIX}", f"*{_PARSED_CARDS_SUFFIX}", _MODEL_ZIP_NAME)


@dataclass
class LayoutPlan:
    """Where each downloaded asset gets copied / extracted in the repo tree.

    ``copies`` are straight ``(src, dest)`` file copies (ratings parquets
    and parsed-cards JSON). ``model_zip`` is the one archive that gets
    extracted into ``models/choice_prod/`` rather than copied. ``model_zip``
    is ``None`` if the model asset wasn't among the downloaded files —
    the caller treats that as fatal, since the spec requires the model.
    """

    copies: list[tuple[Path, Path]] = field(default_factory=list)
    model_zip: Path | None = None


def plan_layout(staged: list[Path], repo_root: Path) -> LayoutPlan:
    """Map downloaded ``data-current`` asset paths to their repo-tree targets.

    Pure function (no I/O) so the naming-to-path mapping is unit-testable
    without touching ``gh`` or the network. Unrecognised filenames are
    ignored — a forward-compatible stance so a future extra asset in the
    release doesn't break the build.
    """
    data_processed = repo_root / "data" / "processed"
    ratings_root = data_processed / "seventeenlands" / "ratings"
    parsed_cards_root = data_processed / "parsed_cards"

    copies: list[tuple[Path, Path]] = []
    model_zip: Path | None = None
    for path in staged:
        name = path.name
        if name.endswith(_RATINGS_SUFFIX):
            set_code = name[: -len(_RATINGS_SUFFIX)]
            copies.append((path, ratings_root / set_code / "PremierDraft.parquet"))
        elif name.endswith(_PARSED_CARDS_SUFFIX):
            set_code = name[: -len(_PARSED_CARDS_SUFFIX)]
            copies.append((path, parsed_cards_root / f"{set_code}.json"))
        elif name == _MODEL_ZIP_NAME:
            model_zip = path
    return LayoutPlan(copies=copies, model_zip=model_zip)


def _download_assets(repo: str, tag: str, dest: Path) -> list[Path]:
    """Download the wanted assets from ``tag`` into ``dest``; return their paths.

    Shells out to ``gh release download`` with one ``--pattern`` per
    asset family. ``gh`` writes each asset under its own name into
    ``dest``; we then list the dir to hand concrete paths to
    :func:`plan_layout`.
    """
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["gh", "release", "download", tag, "--repo", repo, "--dir", str(dest)]
    for pattern in _DOWNLOAD_PATTERNS:
        cmd += ["--pattern", pattern]
    print(f">>> {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)
    return sorted(p for p in dest.iterdir() if p.is_file())


def _apply_plan(plan: LayoutPlan, repo_root: Path) -> None:
    """Execute a :class:`LayoutPlan`: copy per-set files, unzip the model."""
    for src, dest in plan.copies:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        print(f"  {src.name} -> {dest.relative_to(repo_root)}")

    if plan.model_zip is None:
        raise SystemExit(
            f"!! {_MODEL_ZIP_NAME} not found in the release assets — the build "
            "needs the choice model. Check that the data release has been "
            "published with a model artifact."
        )
    model_dir = repo_root / "models" / _MODEL_DIR_NAME
    model_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(plan.model_zip) as archive:
        archive.extractall(model_dir)
    print(f"  {plan.model_zip.name} -> {model_dir.relative_to(repo_root)}/ (unzipped)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=_DEFAULT_REPO, help="Public data release host.")
    parser.add_argument("--tag", default=_DEFAULT_TAG, help="Release tag to fetch from.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mulligan-coach-fetch-") as tmp:
        staged = _download_assets(args.repo, args.tag, Path(tmp))
        if not staged:
            raise SystemExit(
                f"!! no assets downloaded from {args.repo}@{args.tag}. Is the "
                "release published and are its assets named as this script expects?"
            )
        plan = plan_layout(staged, REPO_ROOT)
        print(f"laying out {len(plan.copies)} data file(s) + the choice model:")
        _apply_plan(plan, REPO_ROOT)

    print("\nBuild inputs reconstructed. build_distribution.py can now run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
