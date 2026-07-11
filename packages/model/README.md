# mulligan-coach-model

XGBoost mulligan-recommendation models: training data prep, feature
materialisation, fit, and inference. The production **choice model**
predicts P(a skilled player would keep this hand) from 17Lands replay
data; the legacy **win model** (P(win), baseline residualization) is
retained for analysis and as the choice pipeline's kept-hand
simulation cache donor. See `CLAUDE.md` for design details.
