"""Publish the Mulligan Coach overlay installer to GitHub Releases.

Companion to ``publish_data_release.py`` (which publishes the data
auto-update feed). This one publishes the installer itself so users can
download a fresh copy from a stable URL rather than getting a Discord
DM with a Drive link every release.

Workflow
--------

1. **Build the distributable first — this script does NOT build.** Run
   ``build_distribution.py`` to freeze the PyInstaller bundle, then
   Inno Setup (``mulligan_coach.iss``) to wrap it into
   ``dist/installer/MulliganCoachSetup.exe``. The publisher uploads that
   pre-built installer *as-is* and never rebuilds, so the build stamp it
   reads always matches the bundle actually inside the installer.
2. Read the bundle's ``_internal/_bundle_version.txt`` stamp (written by
   ``build_distribution.py``) and write a small ``exe_version.json``
   sidecar describing the installer (build stamp + SHA256 + URL). This is
   what the in-app "update available" notification polls.
3. Ensure the ``exe-latest`` release exists on the public
   ``mulligan_coach_data`` repo, snapshot the existing assets' download
   counts to ``logs/download_counts.jsonl`` (``--clobber`` resets them),
   then upload the installer + sidecar with ``gh release upload
   --clobber`` so re-runs replace the assets in place.

Why the installer and not a zip
-------------------------------

We ship a single per-user Inno Setup installer
(``MulliganCoachSetup.exe``) as the one and only download: it needs no
admin rights, gives a Start-menu entry + clean uninstall + upgrade in
place, and is a *smaller* download than the raw bundle zip (lzma2 solid
compression). Handing a non-technical MTG Arena player one file to run
beats "extract this 325 MB folder and find the right .exe." (An earlier
version of this script published a ``MulliganCoach.zip`` of the raw
bundle; that was dropped when the installer became the distribution.)

Why ``exe-latest`` and not ``exe-vN.M``
---------------------------------------

For this scope, a floating tag is enough — users download "the current
version," not a specific historical build. Stable URL, no
manifest-of-manifests, no "which version do I run?" question. The
``exe_version.json`` sidecar carries the actual build stamp for
diagnostics + the update-notification path. If we ever want true
versioned releases, the upgrade path is per-release tags plus a
``latest`` redirect — not a blocker for now.

Why a separate release from ``data-current``
--------------------------------------------

* Different cadences: data refreshes weekly+; the installer only ships
  on code-affecting changes (~3-4x / year for new mechanics).
* Different lifecycles: the data publisher re-clobbers each weekly run.
  Separation keeps each script focused and the Releases page readable.

Authentication
--------------

Same as the data publisher — uses ambient ``gh auth`` against the
``vonbeschwitz/mulligan_coach_data`` repo.
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
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DIST_DIR = REPO_ROOT / "dist" / "MulliganCoach"
INSTALLER_PATH = REPO_ROOT / "dist" / "installer" / "MulliganCoachSetup.exe"

_DEFAULT_REPO = "vonbeschwitz/mulligan_coach_data"
"""Public companion repo that hosts both the data feed and the installer.

Same target as the data publisher — users already trust one URL for
updates; adding a second host would just add a place to break."""

_DEFAULT_TAG = "exe-latest"
"""Floating release tag the installer always lives at.

The data publisher uses ``data-current``. Separate tags so the data
release's frequent re-clobbers don't disturb the installer release's
stable URL — and so anyone browsing the Releases page sees a clear
split."""

_INSTALLER_NAME = "MulliganCoachSetup.exe"
"""Asset name on the release. Matches the file the user downloads and
runs — no version in the name, so the URL stays stable across builds."""

_VERSION_JSON_NAME = "exe_version.json"
"""Tiny JSON sidecar carrying the bundle stamp + installer SHA + URL.

This is the file the "update available" notification polls. Lightweight
enough that fetching it every overlay launch is essentially free."""


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
            "Mulligan Coach installer (latest)",
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
    only install/usage signal (installer downloads ~ cumulative installs;
    see the going-public plan), so we append the current counts to an
    append-only log *before* clobbering, giving a reconstructable running
    total across publishes.

    Best-effort + read-only: any failure — ``gh`` missing, auth, network,
    or the release not existing yet (first publish) — is warned about but
    NEVER blocks the publish. Returns the number of asset rows recorded.

    ``runner`` / ``log_path`` are injectable for tests; production shells
    out to ``gh api`` and appends to ``logs/download_counts.jsonl``.

    NOTE: this logic is duplicated (near-verbatim) in
    ``publish_data_release.py`` on purpose — the same convention the two
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
    "Floating release for the Mulligan Coach overlay installer.\n\n"
    "Download `MulliganCoachSetup.exe` and run it (per-user install, no "
    "admin prompt). The build is unsigned, so Windows SmartScreen shows a "
    "warning on first run — click **More info -> Run anyway**. The "
    "`exe_version.json` sidecar carries the build stamp + SHA256 for the "
    "in-app update notification."
)


def _read_bundle_version(dist_dir: Path) -> str:
    """Read ``_internal/_bundle_version.txt`` written by build_distribution.

    Returns the stamp string (UTC timestamp + short git hash). The
    publisher refuses to ship a bundle without this stamp because the
    in-app update check needs a stable identifier to compare against.
    The stamp is read from the *bundle* (``dist/MulliganCoach/``) that
    Inno Setup wrapped, so it always matches what's inside the installer.
    """
    stamp_file = dist_dir / "_internal" / "_bundle_version.txt"
    if not stamp_file.is_file():
        raise SystemExit(
            f"!! {stamp_file} missing — build the bundle first with "
            "build_distribution.py (then Inno Setup to produce the installer)."
        )
    return stamp_file.read_text(encoding="utf-8").strip()


def _build_version_sidecar(
    *,
    bundle_version: str,
    artifact_path: Path,
    repo: str,
    tag: str,
) -> dict[str, object]:
    """Return the JSON-ready dict written to ``exe_version.json``.

    The fields are what the in-app notifier reads: it fetches this URL,
    compares ``bundle_version`` to the running EXE's stamp, and if they
    differ surfaces the release page to the user. ``download_url`` points
    at the installer asset (the notifier opens the release *page* for the
    install instructions, but the direct link is recorded here too).

    ``artifact_path`` is the installer whose bytes we SHA + size.
    """
    sha256 = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    base = f"https://github.com/{repo}/releases/download/{tag}"
    return {
        "schema_version": 1,
        "generated_at": datetime.datetime.now(datetime.UTC)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "bundle_version": bundle_version,
        "download_url": f"{base}/{_INSTALLER_NAME}",
        "sha256": sha256,
        "size_bytes": artifact_path.stat().st_size,
        "release_page": f"https://github.com/{repo}/releases/tag/{tag}",
    }


def _require_installer(installer_path: Path) -> None:
    """Fail early + helpfully if the pre-built installer is missing.

    The publisher never builds — building the bundle and wrapping it with
    Inno Setup are separate, explicit steps. A missing installer almost
    always means the user forgot the ISCC step, so say exactly that.
    """
    if not installer_path.is_file():
        raise SystemExit(
            f"!! installer not found at {installer_path}.\n"
            "   Build it first:\n"
            "     .venv/Scripts/python.exe packages/overlay/packaging/build_distribution.py\n"
            "     ISCC /DMyAppVersion=<x.y.z> packages/overlay/packaging/mulligan_coach.iss\n"
            "   (the publisher uploads the existing installer; it does not build one)."
        )


def stage_artifacts(
    dist_dir: Path,
    installer_path: Path,
    out_dir: Path,
    *,
    repo: str,
    tag: str,
) -> tuple[Path, Path]:
    """Write ``exe_version.json`` for *installer_path* into *out_dir*.

    The "produce the release metadata" half of the publish flow, with the
    "upload to GitHub" half omitted. Shared by :func:`main`'s upload path
    (staging the sidecar into a temp dir before ``gh release upload``) and
    by the CI build workflow, which calls this via ``--stage-dir`` to grab
    the same sidecar as a build output *without* touching any release.

    The installer itself is not copied — it's already at
    ``installer_path`` and gets uploaded (or artifact-collected) from
    there. Returns ``(installer_path, version_json_path)``.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    _require_installer(installer_path)
    bundle_version = _read_bundle_version(dist_dir)
    version_payload = _build_version_sidecar(
        bundle_version=bundle_version, artifact_path=installer_path, repo=repo, tag=tag
    )
    version_json = out_dir / _VERSION_JSON_NAME
    version_json.write_text(json.dumps(version_payload, indent=2), encoding="utf-8")
    return installer_path, version_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=_DEFAULT_REPO, help="Public release host.")
    parser.add_argument("--tag", default=_DEFAULT_TAG, help="Release tag to (re)use.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the version JSON locally but skip every gh call.",
    )
    parser.add_argument(
        "--stage-dir",
        default=None,
        help=(
            "Write exe_version.json into this directory and stop — no gh calls, "
            "no release touched. Used by the CI build workflow to collect the "
            "same sidecar a publish would, without publishing."
        ),
    )
    args = parser.parse_args(argv)

    if not DIST_DIR.exists():
        raise SystemExit(
            f"!! {DIST_DIR} missing — run build_distribution.py first "
            "(the sidecar's build stamp is read from the bundle)."
        )
    _require_installer(INSTALLER_PATH)

    # Non-publishing path: stage the sidecar to a durable directory and
    # return. Everything below this branch is GitHub-release plumbing the
    # CI build deliberately avoids (it uploads via actions/upload-artifact
    # and never creates a release).
    if args.stage_dir is not None:
        installer_path, version_json = stage_artifacts(
            DIST_DIR, INSTALLER_PATH, Path(args.stage_dir), repo=args.repo, tag=args.tag
        )
        print(
            f"staged {version_json.name} to {args.stage_dir} "
            f"(installer stays at {installer_path}; no upload)."
        )
        return 0

    bundle_version = _read_bundle_version(DIST_DIR)
    print(f"bundle_version = {bundle_version}")
    print(f"installer: {INSTALLER_PATH} ({INSTALLER_PATH.stat().st_size / 1e6:.1f} MB)")

    with tempfile.TemporaryDirectory(prefix="mulligan-coach-exe-publish-") as tmp:
        staging = Path(tmp)
        _, version_json = stage_artifacts(
            DIST_DIR, INSTALLER_PATH, staging, repo=args.repo, tag=args.tag
        )

        if args.dry_run:
            print("\n----- exe_version.json (dry-run) -----")
            print(version_json.read_text(encoding="utf-8"))
            print("\n----- planned uploads -----")

        _ensure_release(args.repo, args.tag, dry_run=args.dry_run)
        # Snapshot the pre-clobber download counts (the uploads below reset
        # them). Best-effort — never blocks the publish.
        _snapshot_download_counts(args.repo, args.tag, dry_run=args.dry_run)
        _run_gh(
            ["release", "upload", args.tag, str(INSTALLER_PATH), "--repo", args.repo, "--clobber"],
            dry_run=args.dry_run,
        )
        _run_gh(
            ["release", "upload", args.tag, str(version_json), "--repo", args.repo, "--clobber"],
            dry_run=args.dry_run,
        )

        # Snapshot the published metadata for offline inspection — same
        # convention the data publisher uses. The installer itself isn't
        # snapshotted (it's ~90 MB; pulling it from the live release is
        # cheaper if you actually need a copy).
        logs_dir = REPO_ROOT / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        snapshot = logs_dir / "last_published_exe_version.json"
        shutil.copy(version_json, snapshot)
        print(f"\nversion snapshot saved to {snapshot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
