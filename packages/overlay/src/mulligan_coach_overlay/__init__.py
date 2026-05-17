"""Transparent overlay for MTG Arena — log tailing + Keep/Mull pane.

Public surface is deliberately minimal. The headless CLI lives in
:mod:`mulligan_coach_overlay.headless`; the PyQt6 widget (when it
lands) will live in :mod:`mulligan_coach_overlay.gui`.

Importing this top-level module does NOT pull in PyQt6 — so the
headless path is safe on systems without a display. Anything Qt-
related is gated behind a deliberate import in the GUI module.
"""

from __future__ import annotations
