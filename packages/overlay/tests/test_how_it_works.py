"""Unit tests for :mod:`mulligan_coach_overlay.how_it_works`.

The menu entry is only as good as the file resolution behind it: a
wrong path silently ships a bundle whose "How Mulligan Coach works…"
entry pops the fallback stub instead of the document. These tests pin
the source-tree resolution (the frozen path is exercised implicitly —
it's the same ``docs/how_it_works.md`` relative path the PyInstaller
spec bundles) and the graceful-fallback contract. Pure logic; no Qt
context.
"""

from __future__ import annotations

from pathlib import Path

from mulligan_coach_overlay import how_it_works


def test_path_resolves_to_repo_doc_from_source() -> None:
    # From a source checkout (no PyInstaller), the resolver must land on
    # the real repo doc — this breaks loudly if the doc is moved/renamed
    # without updating the resolver (and the spec).
    path = how_it_works.how_it_works_path()
    assert path.name == "how_it_works.md"
    assert path.parent.name == "docs"
    assert path.is_file()


def test_markdown_loads_real_document() -> None:
    text = how_it_works.how_it_works_markdown()
    assert "# How Mulligan Coach Works" in text
    # A couple of load-bearing sections a rewrite shouldn't drop.
    assert "Known shortcomings" in text
    assert text != how_it_works.FALLBACK_TEXT


def test_markdown_falls_back_when_file_missing(tmp_path: Path) -> None:
    missing = tmp_path / "nope" / "how_it_works.md"
    text = how_it_works.how_it_works_markdown(missing)
    assert text == how_it_works.FALLBACK_TEXT
    # The fallback must point users somewhere they can actually reach
    # (the public data repo, not the private main repo).
    assert "github.com/vonbeschwitz/mulligan_coach_data" in text
