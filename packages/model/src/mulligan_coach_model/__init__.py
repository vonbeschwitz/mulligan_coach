"""XGBoost mulligan-recommendation model.

Public surface is built up across the five model-package PRs:

* PR 1 (this PR): :mod:`training_rows` — typed TrainingRow plus a
  streaming reader that walks the DuckDB ``games`` view and emits
  one ``TrainingRow`` per game.

Later PRs add ``feature_matrix``, ``baseline``, ``train``, and
``inference``. Each is wired through this module as it lands.
"""

from __future__ import annotations

from .training_rows import (
    UNKNOWN_BUCKET,
    TrainingRow,
    TrainingRowStats,
    bucket_user_n_games,
    bucket_user_wr,
    build_name_lookup,
    iter_training_rows,
)

__all__ = [
    "UNKNOWN_BUCKET",
    "TrainingRow",
    "TrainingRowStats",
    "bucket_user_n_games",
    "bucket_user_wr",
    "build_name_lookup",
    "iter_training_rows",
]
