"""Qt-free HTML builder for the overlay's stats panel.

Extracted from :mod:`mulligan_coach_overlay.gui` so the helpers can
be unit-tested on Linux CI (where importing PyQt6 fails because
``libEGL.so.1`` isn't installed). The module produces a single
QLabel-safe HTML string from a :class:`RecommendationExplanation` —
no Qt types, no widget construction.

Two stacked tables:

1. **Turns-by-category grid (4 rows x 3 cols)** — per-turn
   ``P(land drop)``, ``P(cast any creature)``, ``P(cast any spell)``.
   Each cell is colour-coded green / amber / red against fixed
   empirical thresholds (see :data:`_PROB_THRESHOLDS`). The numbers
   are first-pass guesses for typical 17-land Premier Draft hands —
   the pipeline has no "typical hand" baseline to calibrate against
   today, so refine once that exists.

2. **Per-card playability** — lands excluded, sorted by mana value
   ascending. Card | Cost | OH | T1..T4. Mana cost prints as a
   compact ``"1G"`` / ``"3WW"`` (braces kept around multi-character
   pips so hybrid stays readable); OH is the shrunk 17Lands OH WR
   the choice model itself reads, colour-coded against the format
   average.

The colour palette duplicates the strings used by ``gui.py`` (kept
in lockstep with the website's dark theme). Owned here rather than
imported from ``gui.py`` to avoid the Qt-import roundtrip — gui.py
imports the colour constants from this module.
"""

from __future__ import annotations

from mulligan_coach_recommend import RecommendationExplanation

# ---------------------------------------------------------------------------
# Colour palette (shared with gui.py — gui imports from here)
# ---------------------------------------------------------------------------

_TEXT_PRIMARY = "#e8e8ec"
_TEXT_MUTED = "#909098"
_KEEP_COLOR = "#7be57b"  # green
_MULL_COLOR = "#e57b7b"  # red
_MARGINAL_COLOR = "#e5c87b"  # amber


# ---------------------------------------------------------------------------
# Colour-coding thresholds
# ---------------------------------------------------------------------------

# Per-(category, turn) (green_min, yellow_min) thresholds for the
# turns-by-category grid. Cells >= green_min render green;
# >= yellow_min render amber; below that render red. Numbers picked
# from intuition about typical 17-land Premier-Draft hands —
# explicitly NOT calibrated against a real distribution, since none
# of those summary stats exist in the pipeline today. Treat as a
# first pass; revisit once we can benchmark them against a sample
# of mulligan-decision rows.
_PROB_THRESHOLDS: dict[str, tuple[tuple[float, float], ...]] = {
    # (T1, T2, T3, T4) — each tuple (green_min, yellow_min).
    "land": (
        (0.98, 0.90),  # T1: nearly always a land drop in hand
        (0.87, 0.75),  # T2
        (0.78, 0.65),  # T3
        (0.70, 0.55),  # T4
    ),
    "creature": (
        (0.25, 0.10),  # T1: 1-drops are scarce
        (0.80, 0.60),  # T2
        (0.88, 0.75),  # T3
        (0.93, 0.85),  # T4
    ),
    "spell": (
        (0.40, 0.20),  # T1
        (0.85, 0.70),  # T2
        (0.92, 0.83),  # T3
        (0.96, 0.90),  # T4
    ),
}

# OH-WR colour bands. 17Lands Premier Draft averages ~0.53; we anchor
# the bands a couple of points either side. Like the table thresholds
# above, picked by intuition.
_OH_WR_GREEN_MIN = 0.55
_OH_WR_YELLOW_MIN = 0.50


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def _format_mana_cost(raw: str | None) -> str:
    """Render Scryfall mana cost (``"{1}{G}"``) as a compact form (``"1G"``).

    Drops braces around single-character pips (digits, single colors,
    X / Y / Z, colorless / snow). Keeps braces for multi-character
    pips so hybrid (``{2/W}``) and phyrexian (``{W/P}``) stay readable.
    ``None`` and empty cost return an empty string.
    """
    if not raw:
        return ""
    out: list[str] = []
    i = 0
    n = len(raw)
    while i < n:
        if raw[i] != "{":
            # Stray text outside braces — shouldn't happen for parsed
            # Scryfall costs but pass through defensively so we never
            # silently drop characters.
            out.append(raw[i])
            i += 1
            continue
        end = raw.find("}", i + 1)
        if end == -1:
            # Malformed cost; emit the remainder verbatim and stop.
            out.append(raw[i:])
            break
        inner = raw[i + 1 : end]
        if len(inner) == 1:
            out.append(inner)
        else:
            out.append("{" + inner + "}")
        i = end + 1
    return "".join(out)


def _prob_color(prob: float, thresholds: tuple[float, float]) -> str:
    """Pick green/yellow/red for a probability against (green_min, yellow_min)."""
    green_min, yellow_min = thresholds
    if prob >= green_min:
        return _KEEP_COLOR
    if prob >= yellow_min:
        return _MARGINAL_COLOR
    return _MULL_COLOR


def _oh_wr_color(oh_wr: float | None) -> str:
    """Colour for a shrunk OH WR. ``None`` falls back to muted text."""
    if oh_wr is None:
        return _TEXT_MUTED
    if oh_wr >= _OH_WR_GREEN_MIN:
        return _KEEP_COLOR
    if oh_wr >= _OH_WR_YELLOW_MIN:
        return _MARGINAL_COLOR
    return _MULL_COLOR


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_stats_html(exp: RecommendationExplanation) -> str:
    """Render the keep-arm playability stats as a QLabel-safe HTML string.

    QLabel's rich-text rendering supports a small HTML subset
    (paragraphs, basic tables, inline ``<b>`` / ``<span>``, percent
    widths). Stick to that — no CSS in style attributes other than
    ``color`` / ``font-size`` / ``text-align`` / ``padding``.
    """

    def pct(p: float) -> str:
        return f"{round(p * 100):d}%"

    # --- 1. Turns by (Land / Creature / Spell) grid -------------------
    turn_header = (
        "<tr>"
        f"<th align='left' style='color:{_TEXT_MUTED};font-weight:600;'>Turn</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>Land</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>Creature</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>Spell</th>"
        "</tr>"
    )
    grid_rows: list[str] = []
    for t_idx in range(4):
        turn_label = f"T{t_idx + 1}"
        land_p = exp.p_make_land_drop_by_turn[t_idx]
        creature_p = exp.p_cast_any_creature_by_turn[t_idx]
        spell_p = exp.p_cast_any_spell_by_turn[t_idx]
        land_color = _prob_color(land_p, _PROB_THRESHOLDS["land"][t_idx])
        creature_color = _prob_color(creature_p, _PROB_THRESHOLDS["creature"][t_idx])
        spell_color = _prob_color(spell_p, _PROB_THRESHOLDS["spell"][t_idx])
        grid_rows.append(
            "<tr>"
            f"<td style='color:{_TEXT_MUTED};'>{turn_label}</td>"
            f"<td align='center' style='color:{land_color};font-weight:600;'>{pct(land_p)}</td>"
            f"<td align='center' style='color:{creature_color};font-weight:600;'>"
            f"{pct(creature_p)}</td>"
            f"<td align='center' style='color:{spell_color};font-weight:600;'>"
            f"{pct(spell_p)}</td>"
            "</tr>"
        )
    grid_table = (
        "<table width='100%' cellspacing='0' cellpadding='2'>"
        f"{turn_header}{''.join(grid_rows)}"
        "</table>"
    )

    # --- 2. Per-card playability (lands dropped, sorted by MV) --------
    # Sort spells by ascending mana_value; cards without a numeric MV
    # (shouldn't happen for non-lands, but defensive) sort last.
    spells = [hc for hc in exp.hand_cards if not hc.is_land]
    spells.sort(key=lambda hc: (hc.mana_value if hc.mana_value is not None else 99, hc.name))

    card_header = (
        "<tr>"
        f"<th align='left' style='color:{_TEXT_MUTED};font-weight:600;'>Card</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>Cost</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>OH</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T1</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T2</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T3</th>"
        f"<th align='center' style='color:{_TEXT_MUTED};font-weight:600;'>T4</th>"
        "</tr>"
    )
    card_rows: list[str] = []
    for hc in spells:
        cost_str = _format_mana_cost(hc.mana_cost) or "—"
        if hc.opening_hand_win_rate is None:
            oh_cell = f"<td align='center' style='color:{_TEXT_MUTED};'>&mdash;</td>"
        else:
            oh_color = _oh_wr_color(hc.opening_hand_win_rate)
            oh_cell = (
                f"<td align='center' style='color:{oh_color};'>"
                f"{round(hc.opening_hand_win_rate * 100):d}%</td>"
            )
        turn_cells = "".join(f"<td align='center'>{pct(p)}</td>" for p in hc.p_castable_by_turn)
        card_rows.append(
            "<tr>"
            f"<td style='color:{_TEXT_PRIMARY};'>{hc.name}</td>"
            f"<td align='center' style='color:{_TEXT_PRIMARY};'>{cost_str}</td>"
            f"{oh_cell}"
            f"{turn_cells}"
            "</tr>"
        )
    card_table = (
        "<p style='margin-top:6px;margin-bottom:2px;'><b>Per-card playability</b></p>"
        "<table width='100%' cellspacing='0' cellpadding='1'>"
        f"{card_header}{''.join(card_rows)}"
        "</table>"
    )

    return grid_table + card_table
