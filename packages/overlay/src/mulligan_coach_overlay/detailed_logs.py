"""Detect whether Arena's *Detailed Logs (Plugin Support)* setting is on.

Why this matters
----------------

The overlay only works when the user has enabled MTG Arena's
**Options → Account → "Detailed Logs (Plugin Support)"** setting.
Without it, Arena still writes ``Player.log``, but it never emits the
``GREMessageType_GameStateMessage`` events the tailer needs to detect a
mulligan — so the overlay sits silent forever and the user reasonably
concludes it's broken. This module lets the first-run wizard detect the
"not enabled" state and walk the user through fixing it.

How Arena signals the setting
-----------------------------

Arena writes an explicit marker line to ``Player.log`` at session
start recording the setting's state — the same signal every log-based
tracker keys on (verified against 17Lands' own ``mtga_follower.py``,
which matches ``line.startswith("DETAILED LOGS: ENABLED"/"DISABLED")``):

* ``DETAILED LOGS: ENABLED``  — the setting is on.
* ``DETAILED LOGS: DISABLED`` — the setting is off.

The marker is emitted once, near the top of the log, regardless of
whether a game has started — so a user sitting at the Arena main menu
still produces a usable signal.

Secondary (functional) signal: if the log already contains detailed GRE
messages (``GREMessageType_``), the setting is definitively on even if
the explicit marker scrolled out of the byte window we scan. We only
fall back to this when no explicit marker is present, because the marker
is authoritative for the *current* session (a user who just toggled the
setting and restarted Arena gets a fresh marker).

This is pure text analysis with no Qt / Arena coupling so it unit-tests
against synthetic log snippets, the same way :mod:`log_tailer` does.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

DetailedLogStatus = Literal["enabled", "disabled", "unknown"]
"""Outcome of a Detailed-Logs check.

* ``enabled``  — the setting is on; the overlay will receive events.
* ``disabled`` — the setting is explicitly off; the overlay will get
  nothing until the user enables it and restarts Arena.
* ``unknown``  — couldn't tell (log missing / unreadable, or no marker
  and no detailed messages yet — e.g. Arena hasn't been launched since
  the setting last changed). The wizard treats this as "probably needs
  attention" but phrases it as a question, not a failure.
"""

# Exact marker strings Arena writes at session start. Matched as
# substrings (the real line is prefixed by the ``[UnityCrossThreadLogger]``
# header + a timestamp) rather than with ``startswith`` so we don't have
# to reconstruct the header framing here.
_MARKER_ENABLED = "DETAILED LOGS: ENABLED"
_MARKER_DISABLED = "DETAILED LOGS: DISABLED"

# Functional evidence that detailed logging is on: the detailed GRE
# message type only appears in the log when the setting is enabled.
_DETAILED_EVIDENCE = "GREMessageType_"

# How many bytes of the log head to scan. The marker lives near the top
# of the file (Arena writes it as it initialises plugin support at
# launch), so the head is where it is — reading the tail could miss it
# on a long session. 2 MB comfortably covers the startup preamble while
# keeping the one-shot read cheap.
_DEFAULT_SCAN_BYTES = 2_000_000


def detect_detailed_logs(text: str) -> DetailedLogStatus:
    """Classify a chunk of ``Player.log`` text.

    Precedence:

    1. The explicit ``DETAILED LOGS: ENABLED`` / ``DISABLED`` marker —
       whichever appears *last* wins (guards against a stale marker from
       earlier in the same byte window, though in practice only one is
       written per session).
    2. Otherwise, the presence of any detailed GRE message ⇒ ``enabled``.
    3. Otherwise ``unknown``.
    """
    last_enabled = text.rfind(_MARKER_ENABLED)
    last_disabled = text.rfind(_MARKER_DISABLED)
    if last_enabled != -1 or last_disabled != -1:
        return "enabled" if last_enabled > last_disabled else "disabled"
    if _DETAILED_EVIDENCE in text:
        return "enabled"
    return "unknown"


def detect_detailed_logs_from_path(
    path: Path, *, scan_bytes: int = _DEFAULT_SCAN_BYTES
) -> DetailedLogStatus:
    """Read the head of ``path`` and classify it via :func:`detect_detailed_logs`.

    Returns ``unknown`` for any read problem (missing file, permission
    error, decode failure) — the caller can't distinguish "off" from
    "couldn't tell", and phrasing it as a question is the safe UX.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(scan_bytes)
    except OSError as exc:
        log.info("detailed-logs: could not read %s: %s", path, exc)
        return "unknown"
    return detect_detailed_logs(head)
