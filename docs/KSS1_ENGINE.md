# KSS1 goals engine

Shipped 12 September 2026 on `soccer_auto` as isolated modules.

## Modules

- `soccer_auto/kss1_markets.py` — Dixon-Coles score matrix and 1X2 / DC / O/U 2.5 / BTTS
- `soccer_auto/kss1_identity.py` — Odds API ↔ BBD mapping; refuse ambiguous joins
- `soccer_auto/kss1_bbd.py` — BBD client (`sport=football`, UUID ids only). Odds stay on The Odds API
- `soccer_auto/kss1_lock.py` — T-60 public engine contract, T-45 training, postponement void
- `soccer_auto/kss1_engine.py` — `predict_match()` shadow engine

## Authority

`SHADOW_LEARNING`. `automatic_prediction_allowed=False`.

Existing soccer_auto public bind remains **T-10** until inference contract tests are cut over. The engine already enforces T-60 for KSS1 picks.

## Not in this drop

- Promotion out of shadow
- Changing `PUBLICATION_CUTOFF_MINUTES`
- Auto-betting
- LLM picks
