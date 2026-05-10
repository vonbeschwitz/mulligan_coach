"""Saturated-cell logistic regression baseline for residualising context.

Predicts a per-row baseline ``P(win)`` on the logit scale; the
XGBoost predictor (PR 4) then learns the *delta* on top of this
baseline, fed in through XGBoost's ``base_margin`` mechanism. The
composition still emits a calibrated probability — XGBoost adds
zero-mean residual log-odds on top of the baseline's log-odds.

Model form
----------

``logit P(win) = β_cell[wr x n_games x on_play] + β_opp[opp_mulligan_number]``

The cell term is **saturated** in the (wr_bucket, n_games_bucket,
on_the_play) factor: one coefficient per cell, no additive
decomposition. That's the key correctness fix — the wr-by-n_games
interaction is real (low-n-games / high-wr players regress more to
the population mean than low-n-games / low-wr players; the
direction of regression depends on WR), and an additive
``β_wr + β_n_games`` can't express that.

opp_mulligan is treated additively because its effect on win rate
is roughly independent of player skill: the opponent keeping a
mulligan-to-5 hand benefits everyone roughly equally.

With ~5 WR buckets x ~5 n_games buckets x 2 on_play values = 50
cells, plus ~5 opp_mulligan levels = ~55 free coefficients against
~1M training rows. Comfortable degrees of freedom; the mild L2
penalty (``C=10.0``) only guards the rarest cells (e.g. very few
``">=60%" x "<10"`` rows) without distorting well-populated ones.

Inference semantics
-------------------

The XGBoost predictor sees the SAME baseline margin in both arms
of the keep-vs-mull comparison (same user, same opp_mull, same
on_play). So the cell margin cancels and the recommendation only
reflects the XGBoost-learned delta — perfect for our purposes.

Three info-set cases at inference time:

* **Training time**: every row has known user buckets and a known
  opp_mulligan_number.
* **Deploy time on the draw**: user buckets unknown (we don't
  query 17Lands at runtime); opp_mulligan_number known.
  ``BaselineModel.margin`` falls back to the precomputed
  population-marginal cell margin for ``on_the_play``.
* **Deploy time on the play**: user buckets AND opp_mull both
  unknown. Falls back to the population marginals for both.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pandas as pd
from sklearn.linear_model import LogisticRegression

log = logging.getLogger(__name__)


# The columns the baseline reads. Every parquet shard produced by
# :mod:`feature_matrix` carries these — keep this list in sync with
# the cache schema there.
_REQUIRED_COLS = (
    "user_wr_bucket",
    "user_n_games_bucket",
    "on_the_play",
    "opp_mulligan_number",
    "won",
)


CellKey = tuple[str, str, bool]
"""(user_wr_bucket, user_n_games_bucket, on_the_play) — the
saturated-cell identity used as a dict key throughout this module."""


@dataclass(frozen=True)
class BaselineModel:
    """Fitted saturated-cell logistic baseline.

    Stores the per-cell logit margins directly so inference is a
    dict lookup rather than a re-running of the sklearn pipeline
    (or even loading sklearn at deploy time).

    The regression intercept is folded into each ``cell_margins``
    entry — that way :meth:`margin` is a clean
    ``cell_margin + opp_mulligan_margin`` sum.
    """

    cell_margins: dict[CellKey, float]
    """Logit margin per ``(user_wr_bucket, user_n_games_bucket,
    on_the_play)`` cell. Each value includes the regression
    intercept; opp_mulligan margins are separate so they can be
    added independently."""

    opp_mulligan_margins: dict[int, float]
    """Logit margin contribution per ``opp_mulligan_number`` value.
    Additive over the cell margin."""

    population_marginal_margins: dict[bool, float]
    """Cell margins marginalised over the (wr x n_games) buckets
    weighted by their empirical frequency in training, indexed by
    ``on_the_play``. Used at deploy time when the user's buckets
    are unknown."""

    population_mean_opp_mulligan_margin: float
    """Expected ``opp_mulligan_margins`` value under the training
    distribution. Used at deploy time on the play, when the
    opponent's mulligan count isn't yet observable."""

    n_train_rows: int = 0
    """Number of rows the baseline was fit on. For audit / debug."""

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def margin(
        self,
        *,
        user_wr_bucket: str | None,
        user_n_games_bucket: str | None,
        on_the_play: bool,
        opp_mulligan_number: int | None,
    ) -> float:
        """Return the baseline logit-scale margin for one row.

        ``user_wr_bucket`` and ``user_n_games_bucket`` must either
        both be set (training-time / known-user info set) or both
        be ``None`` (deploy-time / unknown-user info set). A mixed
        state isn't a case the codebase actually generates and is
        rejected loudly.

        Unknown cells (e.g. a never-seen-in-training bucket combo)
        fall through to the on-play population-marginal margin
        rather than failing — this matches sklearn's L2 behaviour
        of pulling rare cells toward the mean.
        """
        if (user_wr_bucket is None) != (user_n_games_bucket is None):
            raise ValueError(
                "user_wr_bucket and user_n_games_bucket must both be set "
                "or both be None; mixed states are not supported."
            )

        if user_wr_bucket is None:
            cell_part = self.population_marginal_margins.get(on_the_play, 0.0)
        else:
            # Mypy needs the paired assertion to narrow the second arg.
            assert user_n_games_bucket is not None  # paired check above
            key: CellKey = (user_wr_bucket, user_n_games_bucket, on_the_play)
            cell_part = self.cell_margins.get(
                key, self.population_marginal_margins.get(on_the_play, 0.0)
            )

        if opp_mulligan_number is None:
            opp_part = self.population_mean_opp_mulligan_margin
        else:
            opp_part = self.opp_mulligan_margins.get(
                int(opp_mulligan_number),
                self.population_mean_opp_mulligan_margin,
            )

        return cell_part + opp_part

    # ------------------------------------------------------------------
    # Fitting
    # ------------------------------------------------------------------

    @classmethod
    def fit(
        cls,
        parquet_paths: Iterable[Path] | Path,
        *,
        l2_C: float = 10.0,
    ) -> BaselineModel:
        """Fit the baseline on one or more parquet shards.

        Multiple paths are concatenated row-wise so a multi-format
        unified baseline can be fit in one call (the plan calls for
        a unified multi-format model; passing TLA + ECL + TMT
        parquets here matches that).

        ``l2_C`` is sklearn's inverse-regularisation strength.
        ``C=10.0`` is a mild penalty that guards rare cells without
        materially distorting well-populated ones; lower values
        (``C=1.0`` or below) increase shrinkage. Tune via the
        eval log-loss in PR 4 if needed.
        """
        paths = [parquet_paths] if isinstance(parquet_paths, Path) else list(parquet_paths)
        if not paths:
            raise ValueError("Need at least one parquet path to fit.")

        frames = [pd.read_parquet(p, columns=list(_REQUIRED_COLS)) for p in paths]
        df = pd.concat(frames, ignore_index=True)
        return cls._fit_dataframe(df, l2_C=l2_C)

    @classmethod
    def _fit_dataframe(cls, df: pd.DataFrame, *, l2_C: float) -> BaselineModel:
        """Fit on an already-loaded dataframe.

        Internal entry point — tests use this directly to avoid
        parquet round-trips. Public callers go through :meth:`fit`.
        """
        for col in _REQUIRED_COLS:
            if col not in df.columns:
                raise ValueError(
                    f"Missing required column {col!r}; expected all of {list(_REQUIRED_COLS)}"
                )
        if len(df) == 0:
            raise ValueError("Empty training dataframe; nothing to fit.")

        # Build a single cell label column ("wr|n_games|on_play") so
        # pd.get_dummies produces exactly one indicator per cell.
        # Joining strings is cheaper than a MultiIndex one-hot for our
        # row counts and keeps the coefficient decomposition trivial.
        df = df.copy()
        df["__cell__"] = (
            df["user_wr_bucket"].astype(str)
            + "|"
            + df["user_n_games_bucket"].astype(str)
            + "|"
            + df["on_the_play"].astype(int).astype(str)
        )

        cell_dummies = pd.get_dummies(df["__cell__"], prefix="cell")
        opp_dummies = pd.get_dummies(df["opp_mulligan_number"].astype(int), prefix="opp")
        X = pd.concat([cell_dummies, opp_dummies], axis=1)
        y = df["won"].astype(int).to_numpy()

        # solver="lbfgs" handles the small dimensionality (~50 cells +
        # ~5 opp_mull = ~55 features) trivially. max_iter=200 is
        # generous; convergence is fast at this size. L2 is sklearn's
        # default penalty so we omit the explicit kwarg (passing
        # `penalty="l2"` raises a FutureWarning in sklearn 1.10+).
        model = LogisticRegression(
            C=l2_C,
            solver="lbfgs",
            max_iter=200,
            fit_intercept=True,
        )
        model.fit(X.to_numpy(), y)

        intercept = float(model.intercept_[0])
        coefs = model.coef_[0]
        n_cell_cols = len(cell_dummies.columns)

        # Decompose coefficients back to per-cell and per-opp margins.
        cell_margins: dict[CellKey, float] = {}
        for col, coef in zip(cell_dummies.columns, coefs[:n_cell_cols], strict=True):
            label = col[len("cell_") :]
            wr, ngames, on_play_str = label.split("|")
            # Fold the intercept into each cell margin so margin() is
            # a clean cell + opp sum at inference time.
            cell_margins[(wr, ngames, on_play_str == "1")] = intercept + float(coef)

        opp_margins: dict[int, float] = {}
        for col, coef in zip(opp_dummies.columns, coefs[n_cell_cols:], strict=True):
            opp_str = col[len("opp_") :]
            opp_margins[int(opp_str)] = float(coef)

        # Population-marginal cell margins per on_the_play value:
        # weighted average of the cell-level fitted margins, weighted
        # by per-cell row counts within the on_the_play stratum.
        marg_by_on_play: dict[bool, float] = {}
        for on_play in (True, False):
            sub = df.loc[df["on_the_play"] == on_play, "__cell__"]
            if len(sub) == 0:
                # No training rows for this on_play value -> fall back
                # to bare intercept. Vanishingly rare in practice.
                marg_by_on_play[on_play] = intercept
                continue
            counts = sub.value_counts()
            total = float(counts.sum())
            weighted = 0.0
            for label, count in counts.items():
                wr, ngames, op = cast(str, label).split("|")
                weighted += (float(count) / total) * cell_margins[(wr, ngames, op == "1")]
            marg_by_on_play[on_play] = weighted

        # Expected opp_mulligan margin under the training distribution.
        # Used on the play at inference time, where the opponent's
        # mulligan count isn't observable yet.
        opp_counts = df["opp_mulligan_number"].astype(int).value_counts()
        opp_total = float(opp_counts.sum())
        opp_marginal = 0.0
        for k, count in opp_counts.items():
            opp_marginal += (float(count) / opp_total) * opp_margins.get(int(k), 0.0)

        log.info(
            "BaselineModel fit on %d rows: %d cells, %d opp_mulligan values, intercept=%.4f",
            len(df),
            len(cell_margins),
            len(opp_margins),
            intercept,
        )

        return cls(
            cell_margins=cell_margins,
            opp_mulligan_margins=opp_margins,
            population_marginal_margins=marg_by_on_play,
            population_mean_opp_mulligan_margin=opp_marginal,
            n_train_rows=len(df),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """Serialise to a small JSON file.

        Human-readable on purpose: a fitted baseline is tiny
        (~50 cells x a few floats) and shipping the coefficients in
        JSON lets a human eyeball them. Use a stable key ordering
        for diff-friendliness.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cell_margins": [
                {"wr": k[0], "n_games": k[1], "on_the_play": k[2], "margin": v}
                for k, v in sorted(self.cell_margins.items())
            ],
            "opp_mulligan_margins": [
                {"opp_mulligan_number": k, "margin": v}
                for k, v in sorted(self.opp_mulligan_margins.items())
            ],
            "population_marginal_margins": [
                {"on_the_play": k, "margin": v}
                for k, v in sorted(self.population_marginal_margins.items())
            ],
            "population_mean_opp_mulligan_margin": (self.population_mean_opp_mulligan_margin),
            "n_train_rows": self.n_train_rows,
        }
        path.write_text(json.dumps(payload, indent=2))

    @classmethod
    def load(cls, path: Path) -> BaselineModel:
        payload = json.loads(path.read_text())
        cell_margins = {
            (row["wr"], row["n_games"], bool(row["on_the_play"])): float(row["margin"])
            for row in payload["cell_margins"]
        }
        opp_mulligan_margins = {
            int(row["opp_mulligan_number"]): float(row["margin"])
            for row in payload["opp_mulligan_margins"]
        }
        population_marginal_margins = {
            bool(row["on_the_play"]): float(row["margin"])
            for row in payload["population_marginal_margins"]
        }
        return cls(
            cell_margins=cell_margins,
            opp_mulligan_margins=opp_mulligan_margins,
            population_marginal_margins=population_marginal_margins,
            population_mean_opp_mulligan_margin=float(
                payload["population_mean_opp_mulligan_margin"]
            ),
            n_train_rows=int(payload.get("n_train_rows", 0)),
        )
