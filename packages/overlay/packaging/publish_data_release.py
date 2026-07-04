"""Publish data + model artifacts to GitHub Releases for the auto-updater.

Companion to ``build_distribution.py``: where that one bundles the
EXE, this one publishes the *data* the EXE will auto-update against.
Run after you refresh 17Lands ratings, re-encode parsed cards for
a set, or train a new choice model.

What it does
------------

1. Walks the workspace's ``data/processed/parsed_cards/<SET>.json``
   and ``data/processed/seventeenlands/ratings/<SET>/PremierDraft.parquet``,
   computes a SHA256 for each, and stages them for upload as-is.
2. Zips ``models/choice_prod/`` into a single asset (the auto-updater's
   model installer expects one zip per model bundle).
3. Builds a manifest JSON shaped like the slice-3 schema
   (:mod:`mulligan_coach_overlay.auto_update.manifest`) describing
   every artifact + the URL it'll live at after upload.
4. Ensures the ``data-current`` GitHub Release exists (creates it
   if missing), snapshots the existing assets' download counts to
   ``logs/download_counts.jsonl`` (``--clobber`` resets them), then
   uploads each artifact + the manifest itself with
   ``gh release upload --clobber`` so re-runs replace assets in place
   rather than tearing down the release.

Why a floating ``data-current`` release
---------------------------------------

* The auto-updater bakes a *stable* manifest URL into the binary
  (``…/releases/download/data-current/manifest.json``). Re-using
  one release tag keeps that URL stable across publishes.
* GitHub's ``--clobber`` flag swaps an asset in place atomically
  on their side — clients downloading mid-publish see either the
  old or the new bytes, never a half-uploaded file.
* Per-set / per-publish dated releases would be nicer for archival
  but require either a "latest" redirect or a manifest of manifests.
  Not worth the complexity for the friends-and-family scope. If you
  want history, git log of *this* script's invocations + the source
  data dirs is enough.

Authentication
--------------

Uses the ambient ``gh`` CLI session. Make sure ``gh auth status``
shows you authenticated against the project's GitHub account before
running. The script never handles tokens directly.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]

_DEFAULT_REPO = "vonbeschwitz/mulligan_coach_data"
"""Slug ``gh release …`` operates against by default.

Why a *separate* repo from ``mulligan_coach``: the main repo is
private (code + training history shouldn't be publicly browsable),
but the auto-updater needs anonymous downloads to work — friends
running the overlay don't carry a GitHub token. The companion
``mulligan_coach_data`` repo is public and exists purely to host
the floating release this publisher uploads to.

If you fork this project, override via ``--repo``. We don't infer
from ``git remote`` so the script doesn't accidentally publish to
the wrong account when a contributor runs it from their fork."""

_DEFAULT_TAG = "data-current"
"""Stable release tag the auto-updater's default URL points at."""

_DEFAULT_SETS = ("TLA", "TMT", "ECL", "SOS")
"""Currently-shipped Premier Draft formats. Update when a new set rotates in.

Adding a set: rerun the publisher with ``--sets TLA TMT ECL SOS NEW``.
Removing a set: drop it from this default OR pass an explicit
``--sets`` list. The auto-updater will keep the locally-cached
artifact for the removed set until the user wipes their dir; the
recommend service stops loading it because the env-driven path
resolution still tries that set but ``_try_load_format_stats``
gracefully returns ``None`` for missing parquets."""

_DEFAULT_MODEL_NAME = "choice_prod"
"""Production choice-model *slot* directory under ``models/``.

Deliberately version-neutral: promoting a newly-trained model means
copying its weights into ``models/choice_prod`` (its actual training
version is recorded in that dir's ``metadata.json``), so this default
— and every client that resolves ``models/choice_prod`` /
``MULLIGAN_COACH_CHOICE_MODEL_DIR`` — stays stable across retrains and
no coordinated client change is needed. The auto-update GUI installer
also runs ``service.reload_choice_model()`` only for artifact names
starting with ``choice_``, which ``choice_prod`` matches."""

_SCHEMA_VERSION = 1
"""Mirrors ``AUTO_UPDATE_SCHEMA_VERSION`` in the overlay manifest module.

Duplicated literally rather than imported so this script doesn't take
a hard dep on the overlay package; the auto-update sub-package's
forward-compat parser also tolerates manifests at this exact version
indefinitely."""


@dataclass
class _Artifact:
    """One artifact + everything needed to write its manifest entry.

    The ``local_path`` is where the file currently lives on disk
    (for ratings / parsed_cards this is the repo's data tree; for
    the model it's a freshly-built zip under a staging dir). The
    ``upload_name`` is the filename it'll be uploaded under, which
    is also the path inside the GitHub Release asset URL.
    """

    kind: str
    """``"ratings"`` | ``"parsed_cards"`` | ``"model"`` — matches the
    auto-update manifest's discriminator."""
    local_path: Path
    """Source bytes to upload."""
    upload_name: str
    """Asset filename inside the Release. Must be unique per Release."""
    set_code: str | None = None
    """Populated for ``ratings`` / ``parsed_cards``; ``None`` for ``model``."""
    name: str | None = None
    """Populated for ``model``; ``None`` for the per-set kinds."""

    def manifest_entry(self, *, base_url: str, sha256: str, version: str) -> dict[str, Any]:
        """Render this artifact as a manifest-JSON entry."""
        entry: dict[str, Any] = {
            "kind": self.kind,
            "url": f"{base_url}/{self.upload_name}",
            "sha256": sha256,
            "version": version,
            "size_bytes": self.local_path.stat().st_size,
        }
        if self.set_code is not None:
            entry["set_code"] = self.set_code
        if self.name is not None:
            entry["name"] = self.name
        return entry


def _sha256(path: Path) -> str:
    """SHA256 of *path* as lowercase hex; chunked so big models don't OOM."""
    hasher = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(64 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _zip_model(model_dir: Path, dest_zip: Path) -> None:
    """Zip *model_dir*'s contents into *dest_zip* (flat layout).

    The auto-update's zip-extraction code (``_extract_zip_safely``
    in :mod:`auto_update.runner`) drops the archive's contents
    straight into the destination model dir, so the zip must store
    files at their bare names (``xgboost.json`` etc.), not under a
    ``choice_prod/`` prefix.

    DEFLATE compression knocks the choice model bundle from ~25 MB
    down to ~5 MB — worth it on every download for the friends'
    bandwidth.
    """
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as archive:
        for child in sorted(model_dir.iterdir()):
            if child.is_file():
                archive.write(child, arcname=child.name)
            elif child.is_dir():
                for nested in sorted(child.rglob("*")):
                    if nested.is_file():
                        arcname = nested.relative_to(model_dir).as_posix()
                        archive.write(nested, arcname=arcname)


def _collect_artifacts(
    *,
    data_root: Path,
    models_root: Path,
    sets: list[str],
    model_name: str,
    staging_dir: Path,
) -> list[_Artifact]:
    """Find every file we want to publish + stage them under unique names.

    Every per-set ratings parquet on disk is named ``PremierDraft.parquet``
    — uploading those four files straight to a release would collide
    because ``gh release upload`` uses the local basename as the asset
    name (its ``#display-name`` syntax is just a label, not a rename).
    We sidestep that by *staging* each artifact in a temp dir under
    its desired ``upload_name`` first, then handing the staged path
    to the uploader.

    Missing per-set files (e.g. a brand-new set not yet encoded) are
    skipped with a warning rather than raising — keeps the publisher
    useful between an EXE update and the data catching up. The model
    is required; missing it raises.
    """
    staging_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[_Artifact] = []

    for set_code in sets:
        parquet = (
            data_root
            / "processed"
            / "seventeenlands"
            / "ratings"
            / set_code
            / "PremierDraft.parquet"
        )
        if parquet.is_file():
            staged = _stage(parquet, staging_dir / f"{set_code}-PremierDraft.parquet")
            artifacts.append(
                _Artifact(
                    kind="ratings",
                    local_path=staged,
                    upload_name=staged.name,
                    set_code=set_code,
                )
            )
        else:
            print(f"!! skipping ratings for {set_code}: not found at {parquet}", file=sys.stderr)

        cards_json = data_root / "processed" / "parsed_cards" / f"{set_code}.json"
        if cards_json.is_file():
            staged = _stage(cards_json, staging_dir / f"{set_code}-parsed-cards.json")
            artifacts.append(
                _Artifact(
                    kind="parsed_cards",
                    local_path=staged,
                    upload_name=staged.name,
                    set_code=set_code,
                )
            )
        else:
            print(
                f"!! skipping parsed_cards for {set_code}: not found at {cards_json}",
                file=sys.stderr,
            )

    model_dir = models_root / model_name
    if not model_dir.is_dir():
        raise SystemExit(
            f"!! model dir {model_dir} does not exist — train {model_name} or "
            f"pass --model-name to point at a different model."
        )
    zip_path = staging_dir / f"{model_name}.zip"
    _zip_model(model_dir, zip_path)
    artifacts.append(
        _Artifact(
            kind="model",
            local_path=zip_path,
            upload_name=zip_path.name,
            name=model_name,
        )
    )
    return artifacts


def _stage(src: Path, dest: Path) -> Path:
    """Copy *src* to *dest* (overwriting), returning *dest*.

    Used to give per-set artifacts unique filenames before upload.
    A plain ``shutil.copy`` is fine — the largest staged artifact is
    the ~1 MB parsed_cards JSON, and we're already paying the cost
    of zipping the model elsewhere in the pipeline.
    """
    shutil.copy(src, dest)
    return dest


def build_manifest(
    *,
    repo: str,
    tag: str,
    artifacts: list[_Artifact],
    version: str | None = None,
) -> dict[str, Any]:
    """Assemble the manifest dict ready for JSON serialisation.

    Each artifact's URL is the *future* GitHub-Releases asset URL —
    we know it before upload because GH's URL scheme is deterministic
    given (repo, tag, filename). Side effect of staging the upload
    before writing the manifest: the manifest itself is just another
    upload, so the asset URL above also points at where the manifest
    lives once published.

    ``version`` defaults to a UTC date stamp; pass a stable string
    if you want to align with a publishing pipeline.
    """
    version = version or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    base_url = f"https://github.com/{repo}/releases/download/{tag}"

    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": datetime.datetime.now(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "artifacts": [
            artifact.manifest_entry(
                base_url=base_url,
                sha256=_sha256(artifact.local_path),
                # Per-artifact versions can diverge later (e.g. ratings
                # refresh every week but parsed_cards refresh per set).
                # For now we stamp every artifact with the same release
                # version — easy to revisit when the cadences split.
                version=version,
            )
            for artifact in artifacts
        ],
    }


# ---------------------------------------------------------------------------
# GitHub Releases
# ---------------------------------------------------------------------------


def _run_gh(
    args: list[str], *, dry_run: bool, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``gh <args...>`` and surface stderr; print under dry-run.

    ``dry_run`` returns a stub result without running the command —
    used by the ``--dry-run`` flag to let the caller validate the
    manifest + see the upload plan without touching GitHub.
    """
    print(f">>> gh {' '.join(args)}", flush=True)
    if dry_run:
        return subprocess.CompletedProcess(args=["gh", *args], returncode=0, stdout="", stderr="")
    return subprocess.run(["gh", *args], check=check, cwd=REPO_ROOT, text=True)


def _ensure_release(repo: str, tag: str, *, dry_run: bool) -> None:
    """Create the release if it doesn't already exist; no-op otherwise.

    Re-runs of the publisher just need a release to upload to; we
    don't want to delete + recreate (that would invalidate asset
    URLs for any client mid-download). ``gh release view`` returns
    a non-zero exit code if the release is missing — we use that as
    the existence probe.
    """
    probe = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if probe.returncode == 0:
        print(f"release {tag} already exists; reusing")
        return

    print(f"release {tag} not found; creating")
    _run_gh(
        [
            "release",
            "create",
            tag,
            "--repo",
            repo,
            "--title",
            f"Mulligan Coach data ({tag})",
            "--notes",
            _RELEASE_NOTES,
        ],
        dry_run=dry_run,
    )


def _snapshot_download_counts(
    repo: str,
    tag: str,
    *,
    dry_run: bool,
    log_path: Path | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> int:
    """Record each release asset's ``download_count`` before a ``--clobber``.

    GitHub resets an asset's download counter when the asset is replaced,
    which every ``--clobber`` upload below does. Download counts are our
    only install/usage signal (``manifest.json`` downloads ~ active
    machines; see the going-public plan), so we append the current counts
    to an append-only log *before* clobbering, giving a reconstructable
    running total across publishes.

    Best-effort + read-only: any failure — ``gh`` missing, auth, network,
    or the release not existing yet (first publish) — is warned about but
    NEVER blocks the publish. Returns the number of asset rows recorded.

    ``runner`` / ``log_path`` are injectable for tests; production shells
    out to ``gh api`` and appends to ``logs/download_counts.jsonl``.

    NOTE: this logic is duplicated (near-verbatim) in
    ``publish_exe_release.py`` on purpose — the same convention the two
    publishers already use for ``_run_gh`` / ``_ensure_release``, so
    neither script takes a dependency on the other.
    """
    if dry_run:
        print(">>> (dry-run) skipping download-count snapshot")
        return 0
    log_path = log_path or (REPO_ROOT / "logs" / "download_counts.jsonl")
    api_args = ["api", f"repos/{repo}/releases/tags/{tag}"]
    try:
        if runner is None:
            result = subprocess.run(
                ["gh", *api_args], cwd=REPO_ROOT, text=True, capture_output=True
            )
        else:
            result = runner(api_args)
    except OSError as exc:
        print(
            f"!! WARNING: could not run gh for download-count snapshot ({exc}); continuing publish",
            file=sys.stderr,
        )
        return 0
    if result.returncode != 0:
        blob = f"{result.stdout}{result.stderr}".lower()
        if "not found" in blob or "404" in blob:
            print(f"no existing release {tag!r} on {repo}; no download counts to snapshot")
        else:
            print(
                f"!! WARNING: download-count snapshot failed (gh exit {result.returncode}): "
                f"{result.stderr.strip()}; continuing publish",
                file=sys.stderr,
            )
        return 0
    try:
        assets = json.loads(result.stdout).get("assets", [])
        stamp = (
            datetime.datetime.now(datetime.UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        rows = [
            {
                "snapshot_at": stamp,
                "repo": repo,
                "tag": tag,
                "name": asset.get("name"),
                "download_count": asset.get("download_count"),
                "updated_at": asset.get("updated_at"),
            }
            for asset in assets
        ]
        if rows:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(json.dumps(row) + "\n")
    except (json.JSONDecodeError, OSError, AttributeError, TypeError) as exc:
        print(
            f"!! WARNING: could not record download counts ({exc}); continuing publish",
            file=sys.stderr,
        )
        return 0
    if rows:
        print(f"snapshotted {len(rows)} asset download-count(s) to {log_path}")
    else:
        print(f"release {tag!r} on {repo} has no assets yet; nothing to snapshot")
    return len(rows)


_RELEASE_NOTES = (
    "Floating data release for the Mulligan Coach auto-updater.\n\n"
    "This release's assets are overwritten each time the publisher runs; "
    "the `manifest.json` asset is what the overlay polls every few hours "
    "to discover refreshed ratings, parsed cards, and choice-model bundles."
)


def _upload_asset(repo: str, tag: str, path: Path, *, dry_run: bool) -> None:
    """Upload one asset to the release, replacing any same-name predecessor.

    The asset name on GitHub is the *basename* of ``path`` — the
    publisher stages each artifact under its target filename in a
    temp dir before getting here (see :func:`_collect_artifacts`)
    precisely so this stays a one-arg call without juggling rename
    syntax. ``gh release upload``'s ``#display-label`` syntax is for
    a display label only, not a rename.

    ``--clobber`` is what makes the publisher idempotent: re-running
    after a single artifact updated just overwrites that one asset
    in place; the rest stay byte-identical and serve the same URL.
    """
    _run_gh(
        ["release", "upload", tag, str(path), "--repo", repo, "--clobber"],
        dry_run=dry_run,
    )


def push_release(
    *,
    repo: str,
    tag: str,
    artifacts: list[_Artifact],
    manifest_path: Path,
    dry_run: bool,
) -> None:
    """End-to-end: ensure release exists, upload assets, upload manifest.

    The manifest goes LAST so a client that catches a publish in
    progress and races us to the manifest URL still sees only fully-
    uploaded artifacts. (GitHub uploads are atomic per-asset but the
    cross-asset ordering is ours to enforce.)
    """
    _ensure_release(repo, tag, dry_run=dry_run)
    # Snapshot the pre-clobber download counts (the uploads below reset
    # them). Best-effort — never blocks the publish.
    _snapshot_download_counts(repo, tag, dry_run=dry_run)
    for artifact in artifacts:
        _upload_asset(repo, tag, artifact.local_path, dry_run=dry_run)
    _upload_asset(repo, tag, manifest_path, dry_run=dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=_DEFAULT_REPO, help="GitHub owner/repo to publish to.")
    parser.add_argument("--tag", default=_DEFAULT_TAG, help="Release tag to (re)use.")
    parser.add_argument(
        "--sets",
        nargs="+",
        default=list(_DEFAULT_SETS),
        help="Set codes whose ratings + parsed_cards to publish.",
    )
    parser.add_argument(
        "--model-name", default=_DEFAULT_MODEL_NAME, help="Model dir under models/ to publish."
    )
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "Version stamp baked into the manifest. Defaults to today's UTC date. "
            "Override for a stable publish tag (e.g. matching a git commit)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Build the manifest + zip the model, but skip every `gh` call. "
            "Prints the manifest JSON and the would-be upload commands for review."
        ),
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="mulligan-coach-publish-") as tmp:
        staging_dir = Path(tmp)
        artifacts = _collect_artifacts(
            data_root=REPO_ROOT / "data",
            models_root=REPO_ROOT / "models",
            sets=args.sets,
            model_name=args.model_name,
            staging_dir=staging_dir,
        )
        manifest = build_manifest(
            repo=args.repo,
            tag=args.tag,
            artifacts=artifacts,
            version=args.version,
        )

        manifest_path = staging_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        if args.dry_run:
            print("\n----- manifest.json (dry-run) -----")
            print(manifest_path.read_text(encoding="utf-8"))
            print("\n----- planned uploads -----")

        push_release(
            repo=args.repo,
            tag=args.tag,
            artifacts=artifacts,
            manifest_path=manifest_path,
            dry_run=args.dry_run,
        )

        # Final useful breadcrumb: total bytes pushed (or that would
        # be pushed). Helps catch "I accidentally bundled the 1 GB
        # training cache" before the upload fires.
        total = sum(a.local_path.stat().st_size for a in artifacts) + manifest_path.stat().st_size
        print(f"\ntotal payload: {total / 1e6:.1f} MB across {len(artifacts) + 1} files")

        # Copy the manifest into the repo's logs/ for the user's
        # own debugging — we don't commit it (it's a publish
        # artifact, not source), but having it on disk after a run
        # makes "what did I just publish?" easy to inspect.
        logs_dir = REPO_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        snapshot = logs_dir / "last_published_manifest.json"
        shutil.copy(manifest_path, snapshot)
        print(f"manifest snapshot saved to {snapshot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
