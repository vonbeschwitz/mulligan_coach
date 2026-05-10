"""XGBoost mulligan-recommendation model.

Public surface is built up across the five model-package PRs:

* PR 1: :mod:`training_rows` — typed TrainingRow plus a streaming
  reader that walks the DuckDB ``games`` view and emits one
  ``TrainingRow`` per game.
* PR 2 (this PR): :mod:`feature_matrix` — per-row simulation + feature
  builder + parquet writer, with the slim feature-cache schema.

Later PRs add ``baseline``, ``train``, and ``inference``. Each is
wired through this module as it lands.
"""

from __future__ import annotations

from .feature_matrix import (
    MaterializationStats,
    build_row,
    iter_feature_rows,
    materialize_feature_matrix,
)
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
    "MaterializationStats",
    "TrainingRow",
    "TrainingRowStats",
    "bucket_user_n_games",
    "bucket_user_wr",
    "build_name_lookup",
    "build_row",
    "iter_feature_rows",
    "iter_training_rows",
    "materialize_feature_matrix",
]
