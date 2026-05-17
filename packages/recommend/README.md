# mulligan-coach-recommend

Shared keep/mulligan recommendation service. Consumed by the
website (`packages/website`) and the overlay (`packages/overlay`)
plus a handful of analysis scripts in `packages/model/scripts/`.

## Public surface

```python
from mulligan_coach_recommend import (
    DEFAULT_N_MULLIGAN_SAMPLES,
    DEFAULT_N_SIMS_KEEP,
    DEFAULT_N_SIMS_PER_MULLIGAN,
    AsymmetricRecommendation,
    FormatStats,
    MulliganArmResult,
    RecommendationService,
    ServiceStatus,
    load_service,
)

service = load_service(set_codes=["TLA", "TMT", "ECL", "SOS"])
rec = service.recommend_asymmetric(
    hand=hand_cards,
    deck=deck_cards,
    on_the_play=True,
    mulligan_number=0,
    opp_mulligan_number=None,
)
print(rec.verdict, rec.keep_win_probability, rec.mulligan_win_probability)
```

See `packages/website/CLAUDE.md` for the design rationale (asymmetric
sim budget, mulligan-arm prefetch cache, +4 pp mulligan bias, deeper-
mulligan floor).
