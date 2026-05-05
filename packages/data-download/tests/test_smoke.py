"""Smoke test — confirm the package imports and the test runner is wired up."""

from __future__ import annotations


def test_package_import() -> None:
    import mulligan_coach_data_download as pkg

    assert pkg.__version__ == "0.0.0"
