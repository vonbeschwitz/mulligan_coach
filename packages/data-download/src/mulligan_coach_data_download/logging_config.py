"""Stdlib logging configuration.

Centralized so every entry-point (CLI, tests, ad-hoc scripts) gets the same
format. Usage::

    from mulligan_coach_data_download.logging_config import configure_logging
    configure_logging()
    log = logging.getLogger(__name__)
    log.info("Downloading %s", url)
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False
_DEFAULT_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def configure_logging(level: int | str | None = None) -> None:
    """Initialize root logging once.

    Subsequent calls are no-ops (so the CLI calling this is harmless even if
    a library user already configured logging).

    Level resolution order:
        explicit ``level`` argument > ``LOG_LEVEL`` env var > ``INFO``.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    chosen = level or os.environ.get("LOG_LEVEL") or "INFO"
    logging.basicConfig(level=chosen, format=_DEFAULT_FORMAT)
    _CONFIGURED = True
