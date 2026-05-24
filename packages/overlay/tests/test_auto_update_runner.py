"""End-to-end tests for :class:`UpdateRunner`.

These spin up a local HTTP server hosting both a manifest JSON and
the artifact bytes the manifest references, then run an
:class:`UpdateRunner` against it to verify the full chain:

* manifest fetch + parse
* per-artifact SHA comparison against the local copy
* download into the user-data dir
* reload-callback invocation
* model-zip extraction + per-dir swap
"""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import socketserver
import threading
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from http import HTTPStatus
from pathlib import Path

import pytest
from mulligan_coach_overlay.auto_update import UpdateRunner
from mulligan_coach_overlay.auto_update.manifest import ManifestArtifact
from mulligan_coach_overlay.auto_update.runner import _assert_within


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Build an in-memory zip with the given filename → bytes entries."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@contextmanager
def _serve(serve_root: Path, status_overrides: dict[str, int] | None = None) -> Iterator[str]:
    """Serve *serve_root* over HTTP on an ephemeral port; yield base URL.

    Optional ``status_overrides`` maps URL paths to HTTP status codes
    the server should return instead of the on-disk file. Lets a test
    inject a single 404 / 500 to exercise the error paths.
    """
    overrides = status_overrides or {}

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, directory=str(serve_root), **kwargs)  # type: ignore[arg-type]

        def log_message(self, format: str, *args: object) -> None:
            pass  # silence access log

        def do_GET(self) -> None:
            code = overrides.get(self.path)
            if code is not None:
                self.send_error(code, "test-injected")
                return
            super().do_GET()

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.shutdown()
            thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Helper for assembling a manifest + serving the artifacts
# ---------------------------------------------------------------------------


def _publish(
    serve_root: Path,
    *,
    ratings: dict[str, bytes] | None = None,
    parsed_cards: dict[str, bytes] | None = None,
    model_zips: dict[str, bytes] | None = None,
    extra_artifacts: list[dict] | None = None,
) -> dict:
    """Write the served files and return the corresponding manifest dict.

    Helpers for each kind: ``ratings={"TLA": b"..."}`` produces both
    a served file at ``/ratings/TLA.parquet`` and a matching manifest
    entry pointing back at the served URL. The URL is a placeholder
    — the test fills in the base URL at fetch time via :func:`_serve`.
    """
    artifacts: list[dict] = []

    if ratings:
        for set_code, payload in ratings.items():
            path = serve_root / "ratings" / f"{set_code}.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifacts.append(
                {
                    "kind": "ratings",
                    "set_code": set_code,
                    "url": f"__BASE__/ratings/{set_code}.parquet",
                    "sha256": _sha256(payload),
                    "version": f"r-{set_code}",
                    "size_bytes": len(payload),
                }
            )

    if parsed_cards:
        for set_code, payload in parsed_cards.items():
            path = serve_root / "parsed_cards" / f"{set_code}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifacts.append(
                {
                    "kind": "parsed_cards",
                    "set_code": set_code,
                    "url": f"__BASE__/parsed_cards/{set_code}.json",
                    "sha256": _sha256(payload),
                    "version": f"pc-{set_code}",
                    "size_bytes": len(payload),
                }
            )

    if model_zips:
        for name, payload in model_zips.items():
            path = serve_root / "models" / f"{name}.zip"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            artifacts.append(
                {
                    "kind": "model",
                    "name": name,
                    "url": f"__BASE__/models/{name}.zip",
                    "sha256": _sha256(payload),
                    "version": f"m-{name}",
                    "size_bytes": len(payload),
                }
            )

    if extra_artifacts:
        artifacts.extend(extra_artifacts)

    return {
        "schema_version": 1,
        "generated_at": "2026-05-23T12:00:00Z",
        "artifacts": artifacts,
    }


def _write_manifest(serve_root: Path, manifest: dict, base_url: str) -> None:
    """Substitute the base URL into the manifest and write it to disk.

    The published manifest's URLs need to point at the actual base
    URL of the test's HTTP server, which we only know after the
    server is bound. We use a ``__BASE__`` placeholder during
    construction and rewrite it here.
    """
    rewritten = json.dumps(manifest).replace("__BASE__", base_url)
    (serve_root / "manifest.json").write_text(rewritten, encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_first_run_downloads_everything(tmp_path: Path) -> None:
    """No local data → every manifest artifact gets downloaded + callback fires."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(
        serve_root,
        ratings={"TLA": b"TLA-ratings"},
        parsed_cards={"TLA": b'{"tla":true}'},
        model_zips={"choice_v6": _make_zip({"xgboost.json": b'{"x":1}'})},
    )

    refreshed_ratings: list[set[str]] = []
    refreshed_pc: list[set[str]] = []
    refreshed_models: list[str] = []

    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_ratings=refreshed_ratings.append,
            reload_parsed_cards=refreshed_pc.append,
            reload_model=refreshed_models.append,
        )
        report = runner.run()

    assert report.status == "ok"
    assert report.refreshed_ratings == {"TLA"}
    assert report.refreshed_parsed_cards == {"TLA"}
    assert report.refreshed_models == {"choice_v6"}
    assert refreshed_ratings == [{"TLA"}]
    assert refreshed_pc == [{"TLA"}]
    assert refreshed_models == ["choice_v6"]

    # Files landed at the canonical user-dir locations.
    assert (
        user_data / "processed" / "seventeenlands" / "ratings" / "TLA" / "PremierDraft.parquet"
    ).read_bytes() == b"TLA-ratings"
    assert (user_data / "processed" / "parsed_cards" / "TLA.json").read_bytes() == b'{"tla":true}'
    assert (user_models / "choice_v6" / "xgboost.json").read_bytes() == b'{"x":1}'
    # Model install marker stamped so the next run short-circuits.
    assert (user_models / "choice_v6" / ".installed_sha").is_file()


def test_second_run_is_noop_when_local_matches(tmp_path: Path) -> None:
    """Identical local artifacts → no downloads, no reload callbacks."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(serve_root, ratings={"TLA": b"TLA-ratings"})

    refreshed_ratings: list[set[str]] = []

    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_ratings=refreshed_ratings.append,
        )
        # First run: downloads.
        assert runner.run().any_refreshed is True
        # Mtime-check sentinel — second run should not touch the file.
        target = (
            user_data / "processed" / "seventeenlands" / "ratings" / "TLA" / "PremierDraft.parquet"
        )
        mtime_before = target.stat().st_mtime_ns

        refreshed_ratings.clear()
        report = runner.run()

        assert report.status == "ok"
        assert report.any_refreshed is False
        assert refreshed_ratings == []
        assert target.stat().st_mtime_ns == mtime_before


def test_manifest_fetch_failure_yields_failed_status(tmp_path: Path) -> None:
    """Bad manifest URL → status=failed; no per-artifact attempts."""
    serve_root = tmp_path / "served"
    serve_root.mkdir()
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"
    refreshed: list[set[str]] = []

    with _serve(serve_root, status_overrides={"/manifest.json": HTTPStatus.NOT_FOUND}) as base:
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_ratings=refreshed.append,
        )
        report = runner.run()

    assert report.status == "failed"
    assert report.manifest_error is not None
    assert "404" in report.manifest_error or "HTTP" in report.manifest_error
    assert report.outcomes == []
    assert refreshed == []


def test_one_artifact_failure_partial_status(tmp_path: Path) -> None:
    """A single 404 → status=partial; other artifacts still applied."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(
        serve_root,
        ratings={"TLA": b"TLA-bytes", "TMT": b"TMT-bytes"},
    )

    refreshed_ratings: list[set[str]] = []
    with _serve(
        serve_root, status_overrides={"/ratings/TMT.parquet": HTTPStatus.NOT_FOUND}
    ) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_ratings=refreshed_ratings.append,
        )
        report = runner.run()

    assert report.status == "partial"
    assert report.refreshed_ratings == {"TLA"}
    # TMT artifact recorded as failed; TLA recorded as refreshed.
    statuses = {o.label: o.status for o in report.outcomes}
    assert statuses == {"ratings:TLA": "refreshed", "ratings:TMT": "failed"}
    # TLA still made it onto the callback's batch.
    assert refreshed_ratings == [{"TLA"}]


def test_model_upgrade_swaps_directory(tmp_path: Path) -> None:
    """An updated model zip overwrites the live ``choice_v6/`` dir."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    # First publication: v1 of the model.
    manifest_v1 = _publish(
        serve_root,
        model_zips={"choice_v6": _make_zip({"xgboost.json": b"v1", "metadata.json": b"meta-v1"})},
    )
    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest_v1, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
        )
        report_v1 = runner.run()
        assert report_v1.refreshed_models == {"choice_v6"}
        assert (user_models / "choice_v6" / "xgboost.json").read_bytes() == b"v1"

        # Publish v2 with different bytes; the manifest's sha changes
        # so the install marker triggers a re-download.
        manifest_v2 = _publish(
            serve_root,
            model_zips={
                "choice_v6": _make_zip({"xgboost.json": b"v2", "metadata.json": b"meta-v2"})
            },
        )
        _write_manifest(serve_root, manifest_v2, base)
        report_v2 = runner.run()

    assert report_v2.refreshed_models == {"choice_v6"}
    assert (user_models / "choice_v6" / "xgboost.json").read_bytes() == b"v2"
    assert (user_models / "choice_v6" / "metadata.json").read_bytes() == b"meta-v2"


def test_model_install_marker_short_circuits_repeat_runs(tmp_path: Path) -> None:
    """Same model SHA twice → second run skips both download and reload."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(
        serve_root,
        model_zips={"choice_v6": _make_zip({"xgboost.json": b"weights"})},
    )

    reload_calls: list[str] = []
    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_model=reload_calls.append,
        )
        first = runner.run()
        second = runner.run()

    assert first.refreshed_models == {"choice_v6"}
    assert second.refreshed_models == set()
    # The reload callback fires only when something changed.
    assert reload_calls == ["choice_v6"]


def test_callback_exception_does_not_break_other_reloads(tmp_path: Path) -> None:
    """A buggy reload callback shouldn't cancel the rest of the batch.

    Real-world example: a transient I/O blip in the recommend
    service's parquet reader could raise. We should log it but not
    skip the model reload that needs to follow.
    """
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(
        serve_root,
        ratings={"TLA": b"r"},
        model_zips={"choice_v6": _make_zip({"xgboost.json": b"weights"})},
    )

    def _broken_ratings_reload(_codes: set[str]) -> None:
        raise RuntimeError("simulated")

    seen_models: list[str] = []
    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
            reload_ratings=_broken_ratings_reload,
            reload_model=seen_models.append,
        )
        report = runner.run()

    # Both artifacts downloaded successfully; only the ratings-side
    # callback raised. The model reload still ran.
    assert report.refreshed_ratings == {"TLA"}
    assert seen_models == ["choice_v6"]


def test_unknown_artifact_kinds_skipped_silently(tmp_path: Path) -> None:
    """Future-schema kinds the client doesn't understand are dropped."""
    serve_root = tmp_path / "served"
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"

    manifest = _publish(
        serve_root,
        ratings={"TLA": b"tla"},
        extra_artifacts=[
            {
                "kind": "opponent_cache",  # unknown
                "url": "__BASE__/op.bin",
                "sha256": "f" * 64,
                "version": "v1",
            }
        ],
    )

    with _serve(serve_root) as base:
        _write_manifest(serve_root, manifest, base)
        runner = UpdateRunner(
            manifest_url=f"{base}/manifest.json",
            user_data_root=user_data,
            user_models_root=user_models,
        )
        report = runner.run()

    # Only the known artifact appears in outcomes.
    assert [o.label for o in report.outcomes] == ["ratings:TLA"]
    assert report.status == "ok"


# ---------------------------------------------------------------------------
# Defence-in-depth: containment assertion on the constructed destination.
# Manifest-level validation already rejects traversal sequences, but this
# second check means a future parser regression can't silently turn into a
# path-traversal write primitive.
# ---------------------------------------------------------------------------


def test_assert_within_accepts_descendant(tmp_path: Path) -> None:
    candidate = tmp_path / "sub" / "file.bin"
    _assert_within(candidate, tmp_path)  # does not raise


def test_assert_within_rejects_traversal(tmp_path: Path) -> None:
    candidate = tmp_path / ".." / "outside.bin"
    with pytest.raises(ValueError, match="escapes root"):
        _assert_within(candidate, tmp_path)


def test_assert_within_rejects_sibling(tmp_path: Path) -> None:
    sibling = tmp_path.parent / "neighbour"
    with pytest.raises(ValueError, match="escapes root"):
        _assert_within(sibling, tmp_path)


def test_runner_rejects_traversal_destination(tmp_path: Path) -> None:
    """If the parser ever lets a traversal through, the runner still refuses.

    Bypass the parser by handing the runner a hand-built
    :class:`ManifestArtifact` whose ``name`` would escape the user-models
    root. The destination resolver must raise before any download is
    attempted.
    """
    user_data = tmp_path / "user" / "data"
    user_models = tmp_path / "user" / "models"
    runner = UpdateRunner(
        manifest_url="http://example.invalid/manifest.json",
        user_data_root=user_data,
        user_models_root=user_models,
    )
    hostile = ManifestArtifact(
        kind="model",
        url="http://example.invalid/x.zip",
        sha256="a" * 64,
        version="v1",
        # Enough ``..`` segments to escape past user_models_root.
        name="../../../../../../../../etc/evil",
    )
    with pytest.raises(ValueError, match="escapes root"):
        runner._artifact_destination(hostile)


# ---------------------------------------------------------------------------
# Manifest-URL scheme allowlist. The runner must refuse to hand
# ``file:///...`` (and friends) to urlopen, even though the default URL is
# HTTPS. Closes the case where a future code path lets the manifest URL be
# rebound at runtime.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///C:/Users/victim/secret.txt",
        "ftp://example.invalid/manifest.json",
        "data:application/json,%7B%7D",
        "github.com/manifest.json",
    ],
)
def test_runner_rejects_non_http_manifest_url(bad_url: str, tmp_path: Path) -> None:
    """A manifest URL that isn't http(s) yields a clean failed UpdateReport."""
    runner = UpdateRunner(
        manifest_url=bad_url,
        user_data_root=tmp_path / "data",
        user_models_root=tmp_path / "models",
    )
    report = runner.run()
    assert report.status == "failed"
    assert report.manifest_error is not None
    assert "disallowed scheme" in report.manifest_error
