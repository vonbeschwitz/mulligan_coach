"""XGBoost mulligan-recommendation model.

Two parallel models share most of the upstream pipeline:

* **Win model** (``training_rows`` -> ``feature_matrix`` -> ``baseline``
  -> ``train`` -> ``inference``): predicts ``P(win | hand)`` from
  17Lands game-data rows. Labels are game outcomes.
* **Choice model** (``choice_rows`` -> ``choice_feature_matrix`` ->
  ``choice_train`` -> ``choice_inference``): predicts
  ``P(skilled player would keep | hand)`` from 17Lands replay-data
  mulligan decisions, filtered to competent players. Labels are
  keep/mull choices.

The choice pipeline reuses kept-hand simulations from the win
pipeline's cache so only mulled-away hands need fresh sims.
"""

from __future__ import annotations

from .baseline import BaselineModel, CellKey
from .choice_feature_matrix import (
    ChoiceMaterializationStats,
    KeptHandCache,
    choice_parquet_paths,
    load_kept_hand_cache,
    materialize_choice_feature_matrix,
)
from .choice_inference import (
    ChoiceModelBundle,
    predict_keep_probability,
    predict_keep_probability_from_feature_row,
)
from .choice_rows import (
    DEFAULT_MIN_N_GAMES_TO_JUDGE,
    DEFAULT_MIN_WIN_RATE,
    ChoiceRow,
    ChoiceRowStats,
    iter_choice_rows,
    should_keep_player,
)
from .choice_train import (
    ChoiceTrainingMetadata,
    ChoiceTrainResult,
    load_choice_train_result,
    save_choice_train_result,
    train_choice_model,
)
from .feature_matrix import (
    MaterializationStats,
    build_row,
    feature_parquet_paths,
    iter_feature_rows,
    materialize_feature_matrix,
)
from .inference import (
    ModelBundle,
    Recommendation,
    predict_win_probability,
    recommend,
)
from .train import (
    SplitMetrics,
    TrainingMetadata,
    TrainResult,
    load_train_result,
    save_train_result,
    train_model,
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
    "DEFAULT_MIN_N_GAMES_TO_JUDGE",
    "DEFAULT_MIN_WIN_RATE",
    "UNKNOWN_BUCKET",
    "BaselineModel",
    "CellKey",
    "ChoiceMaterializationStats",
    "ChoiceModelBundle",
    "ChoiceRow",
    "ChoiceRowStats",
    "ChoiceTrainResult",
    "ChoiceTrainingMetadata",
    "KeptHandCache",
    "MaterializationStats",
    "ModelBundle",
    "Recommendation",
    "SplitMetrics",
    "TrainResult",
    "TrainingMetadata",
    "TrainingRow",
    "TrainingRowStats",
    "bucket_user_n_games",
    "bucket_user_wr",
    "build_name_lookup",
    "build_row",
    "choice_parquet_paths",
    "feature_parquet_paths",
    "iter_choice_rows",
    "iter_feature_rows",
    "iter_training_rows",
    "load_choice_train_result",
    "load_kept_hand_cache",
    "load_train_result",
    "materialize_choice_feature_matrix",
    "materialize_feature_matrix",
    "predict_keep_probability",
    "predict_keep_probability_from_feature_row",
    "predict_win_probability",
    "recommend",
    "save_choice_train_result",
    "save_train_result",
    "should_keep_player",
    "train_choice_model",
    "train_model",
]
