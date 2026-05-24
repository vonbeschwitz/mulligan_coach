"""HTTP fetch + SHA256 verification + atomic write.

Pure stdlib (``urllib.request``, ``hashlib``, ``os.replace``). The
downloader is the load-bearing piece for "an update doesn't corrupt
the user's working state": we never overwrite the target file until
the bytes are fully downloaded AND the SHA256 matches the manifest's
expectation. A truncated transfer or a mid-flight network drop
leaves the original file intact and a stale ``.tmp`` sidecar on
disk; the next update attempt sweeps the sidecar and retries.

Why ``urllib`` instead of ``requests``
--------------------------------------

No new dependency. The overlay's bundle already ships urllib via
stdlib. ``requests`` would add ~500 KB transitive (certifi,
charset-normalizer, urllib3) for a one-call API we don't need;
``urllib.request.urlopen`` covers HTTPS-with-verification natively
on Python 3.12.

Retry policy
------------

A single retry on the transient errors that show up in real-world
home-network use: connection reset, DNS timeout, HTTP 5xx. We don't
loop forever — the updater is called on a timer, so giving up
quickly and trying again in an hour is the right shape. The
``DownloadError`` raised on permanent failure carries the underlying
exception's message so the log line is diagnosable.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_SECONDS = 30.0
"""Per-request socket timeout.

GitHub Releases' CDN typically sends headers in well under a second
and streams a 25 MB artifact in a couple of seconds on a residential
connection. 30 s leaves plenty of head-room for slower connections
without making a hung CDN edge wedge the launch flow indefinitely.
"""

_CHUNK_SIZE = 64 * 1024
"""64 KB chunks for the streaming download.

Big enough to amortise per-syscall overhead, small enough that
streaming SHA computation doesn't visibly slow on commodity hardware
(SHA256 over a 25 MB model takes ~50 ms at this chunk size on the
target machine class).
"""

_RETRY_DELAY_SECONDS = 2.0
"""Sleep between the first attempt and the single retry.

Short enough not to add visible latency on stable connections,
long enough to clear most transient DNS / connection-reset blips.
"""


class DownloadError(RuntimeError):
    """A download failed despite the single retry.

    Wraps the underlying exception's message so the log line tells
    the user-supporter what actually broke (timeout vs. 404 vs. SHA
    mismatch). The updater catches this per-artifact so one failure
    doesn't sink the rest of the refresh batch.
    """


def sha256_file(path: Path) -> str:
    """Compute the SHA256 of *path* as a lowercase hex string.

    Returns the empty string if the file doesn't exist — convenient
    for the "is this artifact already up to date?" comparison, where
    a missing local file should always trigger a download.
    """
    if not path.is_file():
        return ""
    hasher = hashlib.sha256()
    with path.open("rb") as fp:
        while True:
            chunk = fp.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def download_to_file(
    url: str,
    dest: Path,
    *,
    expected_sha256: str,
    expected_size_bytes: int | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """Download *url* to *dest*, verifying SHA256 before swap-in.

    Workflow:

    1. Stream the URL into ``dest.tmp`` (sibling temp file). SHA256
       is computed in the same pass.
    2. If the streamed bytes exceed ``expected_size_bytes`` (when
       provided), abort early — protects against a misconfigured
       URL pointing at something gigabytes-large.
    3. After the read finishes, compare the streamed SHA against
       ``expected_sha256``. Mismatch → delete the tmp file, raise
       :class:`DownloadError`.
    4. On success, ``os.replace(tmp, dest)`` — atomic on Windows
       (since Python 3.3) and on POSIX.

    Retried once on transient errors (connection reset, timeout,
    5xx). Non-transient errors (404, 403, bad SHA) fail immediately.

    Raises
    ------
    DownloadError:
        Permanent failure after retry. Includes the underlying
        error type + message in the string.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    # Best-effort cleanup of a stale tmp from a previously crashed
    # download. If it's locked we'll get a clearer error on rename
    # later; better than carrying corrupt bytes forward.
    if tmp.exists():
        try:
            tmp.unlink()
        except OSError as exc:
            log.warning("could not remove stale tmp file %s: %s", tmp, exc)

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            actual_sha = _stream_download(
                url,
                tmp,
                expected_size_bytes=expected_size_bytes,
                timeout_seconds=timeout_seconds,
            )
        except _Transient as exc:
            last_error = exc.__cause__ or exc
            log.info(
                "auto-update download attempt %d failed transiently: %s — retrying", attempt, exc
            )
            if attempt == 1:
                time.sleep(_RETRY_DELAY_SECONDS)
                continue
            break
        except Exception as exc:
            last_error = exc
            break
        else:
            # Compare SHAs case-insensitively but normalised; the
            # manifest parser already lowercases, so this is belt-
            # and-braces.
            if actual_sha != expected_sha256.lower():
                _safe_unlink(tmp)
                raise DownloadError(
                    f"SHA256 mismatch downloading {url}: "
                    f"expected {expected_sha256[:12]}…, got {actual_sha[:12]}…"
                )
            tmp.replace(dest)
            log.info("auto-update: wrote %s (%s…)", dest, actual_sha[:12])
            return

    # All attempts exhausted.
    _safe_unlink(tmp)
    raise DownloadError(f"download failed for {url}: {type(last_error).__name__}: {last_error}")


def _stream_download(
    url: str,
    tmp: Path,
    *,
    expected_size_bytes: int | None,
    timeout_seconds: float,
) -> str:
    """Stream URL into *tmp* and return the streamed SHA256.

    Wraps transient socket errors into a :class:`_Transient` so the
    caller can decide whether to retry. Permanent HTTP errors (404,
    403, etc.) propagate as :class:`urllib.error.HTTPError`.
    """
    hasher = hashlib.sha256()
    total_bytes = 0
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with (
            urllib.request.urlopen(request, timeout=timeout_seconds) as response,
            tmp.open("wb") as out,
        ):
            while True:
                try:
                    chunk = response.read(_CHUNK_SIZE)
                except TimeoutError as exc:
                    raise _Transient("read timeout") from exc
                if not chunk:
                    break
                total_bytes += len(chunk)
                if expected_size_bytes is not None and total_bytes > expected_size_bytes + 1024:
                    # The +1024 cushion absorbs HTTP framing quirks
                    # without letting a misconfigured URL flood us
                    # with gigabytes; a 1 KB slack is well below
                    # any artifact size we'd legitimately ship.
                    raise DownloadError(
                        f"download from {url} exceeded expected_size_bytes "
                        f"({expected_size_bytes}) at {total_bytes} bytes"
                    )
                hasher.update(chunk)
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code < 600:
            raise _Transient(f"server error {exc.code}") from exc
        # Permanent HTTP failures (404/403/etc) propagate as-is so the
        # caller's outer except sees them and skips retry.
        raise
    except urllib.error.URLError as exc:
        # DNS resolution / connection-reset / TLS handshake fallout
        # all surface as URLError; treat them as transient.
        raise _Transient(f"URL error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise _Transient("connect timeout") from exc

    return hasher.hexdigest()


def _safe_unlink(path: Path) -> None:
    """Delete *path* if it exists; swallow errors.

    Used for the .tmp cleanup. If we can't delete it now (locked by
    AV scanner, permission flake), the next download attempt's
    ``tmp.exists()`` check will retry; carrying the stale file
    forward is fine because we always overwrite from scratch.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not remove %s: %s", path, exc)


class _Transient(Exception):
    """Internal marker for "retry-able" errors inside _stream_download.

    Not part of the public API — the caller catches it and re-raises
    as :class:`DownloadError` once retries are exhausted.
    """


_USER_AGENT = "mulligan-coach-overlay/auto-update"
"""Identifies this client in server logs.

Not used for authentication. Set so GitHub's traffic dashboards
distinguish overlay refreshes from random scrapers.
"""
