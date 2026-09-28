# NBA Independent Intelligence (shadow)

Applies the cross-sport independent-vs-market contract to NBA without changing serving authority.

## Existing NBA system

The current published NBA combo ranker is `hello_world/nba_algorithm.py` (`NBA-B1.1C.1`). It is moneyline-only and is not modified by this layer. The generic winner card still uses market direction. This package does not import those serving modules.

## Contract

1. `p_fundamental` may only be built from basketball features. Odds, implied probability, spread, total, movement, steam, public percentages, and favorite/underdog labels are banned from that path.
2. `p_market_aware` may add de-vigged market evidence.
3. Persist `market_delta = p_market_aware - p_fundamental` and `market_flip`.
4. Grade favorites and underdogs separately.
5. Flag vulnerable favorites and credible underdogs as diagnostics. Do not auto-pick the other side.
6. No underdog quota. A 10-favorite slate is allowed.
7. Ablation compares fundamentals-only vs fundamentals+market on caller-supplied graded rows. Market is not assumed to help.
8. Moneyline, spread, total, team total, and player markets use the same two-path math with a market label. Predictive value is not assumed equal across those markets.
9. SHAP is reported only when the caller supplies contributions from the model that actually scored the pick.
10. Qualification, chronology, leakage, calibration, locks, and serving stay with the existing NBA champion until a challenger qualifies.

## Not complete until

- A market-blind basketball model is trained on reconstructable pregame features.
- Chronological ablation exists on held-out NBA games.
- Shadow predictions accumulate live.
- Production authority is still unchanged.
