"""Mana-cost parser.

Turns a Magic mana-cost string like ``"{2}{W}{U}"`` into a structured
``ManaCost`` so the rest of the parser (and the simulator) can ask questions
like:

* "What's the converted mana cost?" — convenience flag ``cmc``.
* "How many mandatory white pips does this require?" — ``color_pips``.
* "Is there a hybrid pip we need to branch on when solving the mana CSP?" —
  ``pips`` (the structured per-symbol list) along with ``has_hybrid``.
* "Does this involve X / phyrexian / snow?" — convenience flags.

The parser produces both representations from a single pass:

* ``pips: list[Pip]`` is the structural one. The simulator iterates over it
  and treats hybrid / two-or-color pips as branch points in its cost-solving
  CSP. Order is preserved.
* ``cmc``, ``color_pips``, ``has_hybrid`` etc. are convenience aggregates
  derived from the same walk. The cards/parser code and the XGBoost
  feature work both consume these.

We deliberately keep mana parsing focused on cost shapes; alternative-cost
mechanics like cycling / flashback / kicker are NOT mana-cost concerns —
they live one layer up in the oracle-text parser, which emits them as
additional ``Mode``s on the card.

Reference for Scryfall mana-cost symbols:
    https://scryfall.com/docs/api/colors

Symbols recognised:

* Numeric generic, e.g. ``{0}``, ``{1}`` … ``{20}`` — generic mana.
* ``{X}``, ``{Y}``, ``{Z}`` — variable generic mana (contribute 0 to CMC).
* Single colors: ``{W}``, ``{U}``, ``{B}``, ``{R}``, ``{G}``.
* Colorless: ``{C}``.
* Snow: ``{S}`` (counts toward CMC like a generic).
* Hybrid: ``{W/U}``, ``{U/B}``, … (any pair of colors).
* Two-generic hybrid: ``{2/W}``, ``{2/U}``, … (cost 2 generic OR 1 of color).
* Phyrexian: ``{W/P}``, ``{U/P}``, … (color or 2 life).
* Phyrexian hybrid: ``{B/G/P}`` etc. (rare; treated like phyrexian + hybrid).

Anything we don't recognise raises ``ValueError`` so the caller can decide
to flag the card for LLM review rather than silently miscount mana.
"""

from __future__ import annotations

import re
from typing import Final, Literal

from pydantic import BaseModel, Field

# The five WUBRG colors in the canonical order that Scryfall and most card
# databases use. Order matters because we want stable output (e.g.
# `color_pips` always serializes the same way).
COLORS: Final[tuple[str, ...]] = ("W", "U", "B", "R", "G")
COLOR_SET: Final[frozenset[str]] = frozenset(COLORS)

# Discriminator type used by Pip.kind. Kept as a Literal (not an Enum) for
# easy JSON round-tripping and friendlier autocomplete in IDEs.
PipKind = Literal[
    "color",          # one mandatory pip of a single color (W / U / B / R / G)
    "generic",        # numeric generic ({1}, {3}, ...)
    "x",              # variable generic ({X} / {Y} / {Z}); contributes 0 to cmc
    "hybrid",         # two-color hybrid like {W/U}; payable with either listed color
    "two_or_color",   # generic-OR-color hybrid like {2/W}; cmc counts the higher side
    "phyrexian",      # {W/P} etc.; payable with the color or 2 life
    "colorless",      # the dedicated colorless symbol {C}
    "snow",           # {S}
]

# Match a single ``{...}`` symbol. We pull out the inner text and inspect it
# manually rather than trying to encode every variation in the regex itself —
# the symbol set is small but irregular, so a tiny state machine is clearer.
_SYMBOL_RE: Final[re.Pattern[str]] = re.compile(r"\{([^{}]+)\}")


class Pip(BaseModel):
    """A single ``{…}`` symbol from a mana cost, in structural form.

    The simulator's mana-payment CSP iterates over ``ManaCost.pips`` and
    branches on hybrid / two_or_color / phyrexian pips (each has multiple
    payment options). Plain colored / generic / colorless pips have a
    single payment option and are straightforward to assign to mana sources.

    Field semantics by ``kind``:

    * ``color``: single mandatory color. ``color`` is set (one of WUBRG).
    * ``colorless``: the ``{C}`` symbol. ``color`` is ``"C"``.
    * ``snow``: the ``{S}`` symbol. ``color`` is ``"S"``.
    * ``generic``: numeric generic. ``amount`` is the integer.
    * ``x``: variable generic. No payload; contributes 0 to cmc.
    * ``hybrid``: two-color hybrid like ``{W/U}``. ``colors`` lists both.
    * ``two_or_color``: generic-or-color hybrid like ``{2/W}``. ``amount``
      is the generic side (``2``); ``color`` is the colored side (``W``).
    * ``phyrexian``: ``{W/P}`` etc. ``colors`` lists the color side(s).
    """

    kind: PipKind
    color: str | None = Field(
        default=None,
        description=(
            "Single-color payload — used by 'color', 'colorless', 'snow', "
            "and 'two_or_color' pips. WUBRG, plus 'C' / 'S' for the "
            "dedicated symbols."
        ),
    )
    colors: list[str] | None = Field(
        default=None,
        description=(
            "Multi-color payload for 'hybrid' and 'phyrexian' pips. "
            "Order matches Scryfall's symbol order."
        ),
    )
    amount: int | None = Field(
        default=None,
        description=(
            "Numeric payload for 'generic' (the count) and 'two_or_color' "
            "(the generic side, e.g. 2 in {2/W})."
        ),
    )


class ManaCost(BaseModel):
    """Parsed representation of a mana cost string.

    Two views over the same data:

    * ``pips`` — the ordered structural list. Used by the simulator's mana
      CSP and by anything that needs to inspect individual symbols.
    * ``cmc`` / ``color_pips`` / ``has_*`` — convenience aggregates. Used by
      the cards parser, by simple "can I pay this" checks, and by the
      XGBoost feature engineering downstream.

    All numeric fields are non-negative integers. ``raw`` is preserved so
    downstream code can show the original cost in error messages or UI.
    """

    raw: str = Field(description="The original cost string, e.g. '{2}{W}{U}'.")
    pips: list[Pip] = Field(
        default_factory=list,
        description=(
            "Structural per-symbol breakdown of the cost in source order. "
            "The simulator's CSP solver iterates this list directly."
        ),
    )
    cmc: int = Field(ge=0, description="Converted mana cost — sum of generic plus colored pips.")
    color_pips: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Number of mandatory pips per color. Hybrid pips are NOT counted "
            "here because they don't force a specific color; check ``has_hybrid`` "
            "or walk ``pips`` instead."
        ),
    )
    has_x: bool = Field(default=False, description="True if the cost contains an X (or Y / Z).")
    has_hybrid: bool = Field(
        default=False, description="True if any pip is hybrid ({W/U}, {2/W}, etc.)."
    )
    has_phyrexian: bool = Field(default=False, description="True if any pip is phyrexian ({W/P}).")
    has_colorless: bool = Field(
        default=False, description="True if the cost includes the dedicated colorless symbol {C}."
    )
    has_snow: bool = Field(default=False, description="True if the cost includes snow {S}.")


def parse_mana_cost(raw: str) -> ManaCost:
    """Parse a Scryfall mana-cost string into a ``ManaCost``.

    An empty string is valid and represents "no mana cost" (e.g. lands, or
    cards with no mana cost at all). It returns a ``ManaCost`` with
    ``cmc == 0`` and no pips.

    Raises ``ValueError`` on any symbol we don't recognise — the caller
    should treat this as "this card has something weird going on" and flag
    it for human/LLM review rather than silently mis-parse.
    """
    cost = raw.strip()
    pips: list[Pip] = []
    color_pips: dict[str, int] = {c: 0 for c in COLORS}
    cmc = 0
    has_x = False
    has_hybrid = False
    has_phyrexian = False
    has_colorless = False
    has_snow = False

    if not cost:
        return ManaCost(raw=raw, pips=[], cmc=0, color_pips={})

    # Walk through every {…} symbol in order. The matched substrings form
    # the entire string when concatenated; if anything is left over, the
    # cost contained text outside braces and we reject it.
    pos = 0
    for match in _SYMBOL_RE.finditer(cost):
        if match.start() != pos:
            raise ValueError(f"Unexpected text in mana cost {raw!r} at position {pos}")
        pos = match.end()
        symbol = match.group(1).strip()

        # Numeric generic — {0}, {1}, …, {20}. Scryfall uses {1000000} for
        # one famously absurd card; allow any non-negative integer.
        if symbol.isdigit():
            n = int(symbol)
            cmc += n
            pips.append(Pip(kind="generic", amount=n))
            continue

        # X-cost. We treat X / Y / Z all the same — variable, contributes 0
        # to CMC. (Most cards only use X; Y and Z appear on a handful.)
        if symbol in {"X", "Y", "Z"}:
            has_x = True
            pips.append(Pip(kind="x"))
            continue

        # Single-color or single-symbol pip.
        if symbol in COLOR_SET:
            color_pips[symbol] += 1
            cmc += 1
            pips.append(Pip(kind="color", color=symbol))
            continue
        if symbol == "C":
            has_colorless = True
            cmc += 1
            pips.append(Pip(kind="colorless", color="C"))
            continue
        if symbol == "S":
            has_snow = True
            cmc += 1
            pips.append(Pip(kind="snow", color="S"))
            continue

        # Compound symbols are written with slashes: {W/U} hybrid, {2/W}
        # two-or-color, {W/P} phyrexian, {B/G/P} phyrexian-hybrid, etc.
        if "/" in symbol:
            parts = symbol.split("/")

            # Phyrexian (any number of color letters plus a trailing P).
            if parts[-1] == "P":
                has_phyrexian = True
                color_parts = parts[:-1]
                if len(color_parts) > 1:
                    has_hybrid = True
                cmc += 1  # one pip — payable in life or any of the listed colors
                pips.append(Pip(kind="phyrexian", colors=color_parts))
                continue

            # Two-generic hybrid: {2/W}, {2/U}, etc. CMC counts the higher
            # side (2) because that's the worst-case cost we'd plan around.
            if parts[0].isdigit() and len(parts) == 2 and parts[1] in COLOR_SET:
                has_hybrid = True
                amount = int(parts[0])
                cmc += amount
                pips.append(Pip(kind="two_or_color", amount=amount, color=parts[1]))
                continue

            # Plain two-color hybrid: {W/U}, {B/R}, etc. CMC contribution
            # is 1; either color satisfies the pip, so we don't add to
            # color_pips for either side.
            if all(p in COLOR_SET for p in parts):
                has_hybrid = True
                cmc += 1
                pips.append(Pip(kind="hybrid", colors=parts))
                continue

        raise ValueError(f"Unrecognised mana symbol {{{symbol}}} in cost {raw!r}")

    if pos != len(cost):
        raise ValueError(f"Trailing characters in mana cost {raw!r} after position {pos}")

    # Drop colors with zero pips so the serialized form is tidy.
    return ManaCost(
        raw=raw,
        pips=pips,
        cmc=cmc,
        color_pips={c: n for c, n in color_pips.items() if n > 0},
        has_x=has_x,
        has_hybrid=has_hybrid,
        has_phyrexian=has_phyrexian,
        has_colorless=has_colorless,
        has_snow=has_snow,
    )
