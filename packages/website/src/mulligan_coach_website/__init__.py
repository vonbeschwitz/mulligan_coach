"""FastAPI + HTMX user-facing app for Mulligan Coach.

Public surface is intentionally minimal: importers go through
:mod:`mulligan_coach_website.app` (the FastAPI ``app`` and ``main``
entry point). Helpers are kept private to the package.
"""

from __future__ import annotations

from .app import app, main

__all__ = ["app", "main"]
