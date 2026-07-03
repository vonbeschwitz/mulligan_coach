"""Publish the Mulligan Coach overlay EXE bundle to GitHub Releases.

Companion to ``publish_data_release.py`` (which publishes the data
auto-update feed). This one publishes the EXE itself so friends can
download a fresh copy from a stable URL rather than getting a Discord
DM with a Drive link every release.

Workflow
--------

1. (Optional) Run ``build_distribution.py`` first to rebuild the bundle
   from current source. Skipped with ``--skip-build`` if you've
   already built and don't want the ~2 min rebuild cost.
2. Zip ``dist/MulliganCoach/`` into a single ``MulliganCoach.zip``
   (DEFLATE compression takes ~150 MB → ~150 MB. The bundle is
   mostly already-compressed DLLs + a model JSON, so DEFLATE only
   shaves a few percent — we still compress for tradition + so the
   download is one file rather than a sprawling tree).
3. Read the bundle's ``_internal/_bundle_version.txt`` stamp (written
   by ``build_distribution.py``) and write a small ``exe_version.json``
   sibling describing it. This is what a future in-app "update
   available" notification (tier 2) will poll.
4. Ensure the ``exe-latest`` release exists on the public
   ``mulligan_coach_data`` repo, then upload both files with
   ``gh release upload --clobber``.

Why ``exe-latest`` and not ``exe-vN.M``
---------------------------------------

For the friends-and-family scope, a floating tag is enough — friends
download "the current version", not a specific historical build.
Stable URL, no manifest-of-manifests, no "which version do I run?"
question. The ``exe_version.json`` sidecar carries the actual build
stamp for diagnostics + the future notification path. If we ever
want true versioned releases, the upgrade path is per-release tags
plus a ``latest`` redirect — not a blocker for now.

Why a separate release from ``data-current``
--------------------------------------------

* Different cadences: data refreshes weekly+; the EXE only ships
  on code-affecting changes (~3-4x / year for new mechanics).
  Mixing them in one release would clutter the asset list and
  confuse the lifecycle.
* Different lifecycles: the data publisher re-clobbers each weekly
  run. The EXE publisher runs maybe ten times a year. Separation
  keeps each script focused and the GitHub Releases page readable.

Why a separate Python script, not a CLI flag on the data publisher
-----------------------------------------------------------------

The two publishers share <30 lines of ``gh`` plumbing — duplicating
that is cheaper than juggling a ``--what=data|exe|all`` flag. The
EXE script also has a long build step that doesn't belong in the
hot loop of the data publisher.

Authentication
--------------

Same as the data publisher — uses ambient ``gh auth`` against the
``vonbeschwitz/mulligan_coach_data`` repo.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DIST_DIR = REPO_ROOT / "dist" / "MulliganCoach"

_DEFAULT_REPO = "vonbeschwitz/mulligan_coach_data"
"""Public companion repo that hosts both the data feed and the EXE.

Same target as the data publisher — friends already trust one URL
for updates; adding a second host would just add a place to break."""

_DEFAULT_TAG = "exe-latest"
"""Floating release tag the EXE always lives at.

The data publisher uses ``data-current``. Separate tags so the data
release's frequent re-clobbers (potentially weekly) don't disturb
the EXE release's stable URL — and so anyone browsing the Releases
page sees a clear split."""

_ZIP_NAME = "MulliganCoach.zip"
"""Asset name on the release. Matches the directory the user gets
after extraction — keeps the rename invariant obvious."""

_VERSION_JSON_NAME = "exe_version.json"
"""Tiny JSON sidecar carrying the bundle stamp + zip SHA + URL.

This is the file a future "update available" notification (tier 2)
will poll. Lightweight enough that fetching it every overlay launch
is essentially free."""


def _run_gh(
    args: list[str], *, dry_run: bool, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run ``gh <args>``; surface stdout/stderr; print under dry-run."""
    print(f">>> gh {' '.join(args)}", flush=True)
    if dry_run:
        return subprocess.CompletedProcess(args=["gh", *args], returncode=0, stdout="", stderr="")
    return subprocess.run(["gh", *args], check=check, cwd=REPO_ROOT, text=True)


def _ensure_release(repo: str, tag: str, *, dry_run: bool) -> None:
    """Create ``tag`` on ``repo`` if missing; no-op otherwise.

    Same logic as the data publisher's ``_ensure_release`` — kept
    here (duplicated) rather than imported so the two scripts have
    no cross-dependency.
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
            "Mulligan Coach EXE (latest)",
            "--notes",
            _RELEASE_NOTES,
        ],
        dry_run=dry_run,
    )


_RELEASE_NOTES = (
    "Floating release for the Mulligan Coach overlay's shipped Windows binary.\n\n"
    "Download `MulliganCoach.zip`, extract anywhere, and double-click "
    "`MulliganCoach.exe`. The `exe_version.json` sidecar carries the bundle's "
    "build stamp + SHA256 for an in-app update notification (future)."
)


def _zip_bundle(dist_dir: Path, dest_zip: Path) -> None:
    """Recursively zip *dist_dir* into *dest_zip*, paths relative to *dist_dir*.

    Files inside the archive are stored under ``MulliganCoach/...``
    (mirroring the directory the user gets after extraction), so
    ``Compress-Archive``'s default "right-click → Extract All →
    \\MulliganCoach\\MulliganCoach.exe" matches what the user expects.

    ZIP_DEFLATED is used even though most of the bundle's bytes are
    already-compressed DLLs — the small savings on the Python stdlib
    + parsed-cards JSON files cumulatively shave a few MB.
    """
    parent = dist_dir.parent
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(dist_dir.rglob("*")):
            if not path.is_file():
                continue
            arcname = path.relative_to(parent).as_posix()
            archive.write(path, arcname=arcname)


def _read_bundle_version(dist_dir: Path) -> str:
    """Read ``_internal/_bundle_version.txt`` written by build_distribution.

    Returns the stamp string (UTC timestamp + short git hash). The
    publisher refuses to ship a bundle without this stamp because
    the future in-app update check needs a stable identifier to
    compare against.
    """
    stamp_file = dist_dir / "_internal" / "_bundle_version.txt"
    if not stamp_file.is_file():
        raise SystemExit(
            f"!! {stamp_file} missing — rebuild via build_distribution.py "
            "or pass --skip-build only when you're certain the bundle is current."
        )
    return stamp_file.read_text(encoding="utf-8").strip()


def _build_version_sidecar(
    *,
    bundle_version: str,
    zip_path: Path,
    repo: str,
    tag: str,
) -> dict[str, object]:
    """Return the JSON-ready dict written to ``exe_version.json``.

    The fields are intentionally tier-2-ready: an in-app notifier
    will fetch this URL, compare ``bundle_version`` to the running
    EXE's stamp, and if they differ, surface ``download_url`` to
    the user.

    Splitting it from the manifest used by the data updater so the
    two evolve independently — the EXE doesn't need a "list of
    artifacts", just "is there a newer build?".
    """
    import hashlib

    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    base = f"https://github.com/{repo}/releases/download/{tag}"
    return {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundle_version": bundle_version,
        "download_url": f"{base}/{_ZIP_NAME}",
        "sha256": sha256,
        "size_bytes": zip_path.stat().st_size,
        "release_page": f"https://github.com/{repo}/releases/tag/{tag}",
    }


def _invoke_build() -> None:
    """Run ``build_distribution.py`` from inside this script.

    Subprocess rather than import so PyInstaller's clean-start
    semantics aren't disturbed (PyInstaller mucks with sys.modules
    during analysis; we'd rather isolate that).
    """
    script = REPO_ROOT / "packages" / "overlay" / "packaging" / "build_distribution.py"
    python = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    print(f">>> {python} {script}", flush=True)
    subprocess.run([str(python), str(script)], check=True, cwd=REPO_ROOT)


def stage_artifacts(dist_dir: Path, out_dir: Path, *, repo: str, tag: str) -> tuple[Path, Path]:
    """Zip the bundle + write ``exe_version.json`` into *out_dir*.

    The "produce the release artifacts" half of the publish flow, with
    the "upload to GitHub" half omitted. Shared by :func:`main`'s upload
    path (staging into a temp dir before ``gh release upload``) and by
    the CI build workflow, which calls this via ``--stage-dir`` to grab
    the same zip + sidecar as build outputs *without* touching any
    release. Reusing one function keeps the zip layout and the sidecar
    schema identical between a local publish and a CI build.

    Returns ``(zip_path, version_json_path)``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_version = _read_bundle_version(dist_dir)
    zip_path = out_dir / _ZIP_NAME
    _zip_bundle(dist_dir, zip_path)
    version_payload = _build_version_sidecar(
        bundle_version=bundle_version, zip_path=zip_path, repo=repo, tag=tag
    )
    version_json = out_dir / _VERSION_JSON_NAME
    version_json.write_text(json.dumps(version_payload, indent=2), encoding="utf-8")
    return zip_path, version_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=_DEFAULT_REPO, help="Public release host.")
    parser.add_argument("--tag", default=_DEFAULT_TAG, help="Release tag to (re)use.")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Use the existing dist/MulliganCoach (skip the ~2 min rebuild).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the zip + version JSON locally but skip every gh call.",
    )
    parser.add_argument(
        "--stage-dir",
        default=None,
        help=(
            "Write MulliganCoach.zip + exe_version.json into this directory and "
            "stop — no gh calls, no release touched. Used by the CI build workflow "
            "to collect the same artifacts a publish would, without publishing."
        ),
    )
    args = parser.parse_args(argv)

    if not args.skip_build:
        _invoke_build()
    if not DIST_DIR.exists():
        raise SystemExit(
            f"!! {DIST_DIR} missing — run without --skip-build, or "
            "build_distribution.py failed (check its output)."
        )

    # Non-publishing path: stage the artifacts to a durable directory and
    # return. Everything below this branch is GitHub-release plumbing the
    # CI build deliberately avoids (it uploads via actions/upload-artifact
    # and never creates a release).
    if args.stage_dir is not None:
        zip_path, version_json = stage_artifacts(
            DIST_DIR, Path(args.stage_dir), repo=args.repo, tag=args.tag
        )
        print(
            f"staged {zip_path.name} ({zip_path.stat().st_size / 1e6:.1f} MB) + "
            f"{version_json.name} to {args.stage_dir} (no upload)."
        )
        return 0

    bundle_version = _read_bundle_version(DIST_DIR)
    print(f"bundle_version = {bundle_version}")

    with tempfile.TemporaryDirectory(prefix="mulligan-coach-exe-publish-") as tmp:
        staging = Path(tmp)

        print(f"zipping {DIST_DIR} -> {staging / _ZIP_NAME}")
        zip_path, version_json = stage_artifacts(DIST_DIR, staging, repo=args.repo, tag=args.tag)
        print(f"zip size: {zip_path.stat().st_size / 1e6:.1f} MB")

        if args.dry_run:
            print("\n----- exe_version.json (dry-run) -----")
            print(version_json.read_text(encoding="utf-8"))
            print("\n----- planned uploads -----")

        _ensure_release(args.repo, args.tag, dry_run=args.dry_run)
        _run_gh(
            ["release", "upload", args.tag, str(zip_path), "--repo", args.repo, "--clobber"],
            dry_run=args.dry_run,
        )
        _run_gh(
            ["release", "upload", args.tag, str(version_json), "--repo", args.repo, "--clobber"],
            dry_run=args.dry_run,
        )

        # Snapshot the published metadata for offline inspection —
        # same convention the data publisher uses. The zip itself
        # isn't snapshotted (it's ~150 MB; pulling it from the live
        # release is cheaper if you actually need a copy).
        logs_dir = REPO_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        snapshot = logs_dir / "last_published_exe_version.json"
        snapshot.write_text(version_json.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\nversion snapshot saved to {snapshot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
