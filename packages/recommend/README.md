# mulligan-coach-recommend

Shared keep/mulligan recommendation service. Consumed by the
website (`packages/website`) and the overlay (`packages/overlay`)
plus a handful of analysis scripts in `packages/model/scripts/`.

## Public surface

The production verdict comes from `recommend_choice` (the choice
model). Both the website and the overlay call it:

```python
from mulligan_coach_recommend import (
    ChoiceRecommendation,
    RecommendationService,
    load_service,
)

service = load_service(set_codes=["TLA", "TMT", "ECL", "SOS"])
rec = service.recommend_choice(
    hand=hand_cards,
    deck=deck_cards,
    on_the_play=True,
    mulligan_number=0,
    opp_mulligan_number=None,
)
print(rec.verdict, rec.p_keep, rec.mulligan_percent)
```

### Legacy: `recommend_asymmetric`

`recommend_asymmetric` runs the older **win model** twice (keep vs.
simulated mulligan-to-N-1) and returns an `AsymmetricRecommendation`.
No shipped surface displays it anymore; it survives as an ensemble /
sanity signal and for a few analysis scripts under
`packages/model/scripts/`.

```python
rec = service.recommend_asymmetric(
    hand=hand_cards,
    deck=deck_cards,
    on_the_play=True,
    mulligan_number=0,
    opp_mulligan_number=None,
)
print(rec.verdict, rec.keep_win_probability, rec.mulligan_win_probability)
```

See `packages/website/CLAUDE.md` for the design rationale of that
legacy path (asymmetric sim budget, mulligan-arm prefetch cache,
+4 pp mulligan bias, deeper-mulligan floor).
