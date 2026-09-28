# Cross-Sport Independent Intelligence Retrofit

Applies to MLB/KS1, NFL, Tennis, and Soccer/KSS1. This layer is diagnostic/shadow-only until each sport passes its existing chronological champion/challenger qualification.

## Contract
1. Produce a fundamentals-only probability with market-derived features excluded.
2. Preserve a market-only baseline.
3. Produce the existing/full market-aware probability.
4. Persist all three with prediction-time timestamps.
5. Record market influence and every market-induced pick flip.
6. Grade favorites and underdogs separately (soccer uses full 1X2 state).
7. Report expected upset count and upset concentration without imposing an underdog quota.
8. Distinguish ACTIVE MODEL SIGNAL from DIAGNOSTIC-ONLY SIGNAL.
9. Never change serving authority, lock behavior, calibration gates, chronology, or promotion rules from this diagnostic layer.

## Sport wiring
- KS1: fundamentals challenger must exclude market_home_prob and any derived market fields. Existing KS1 authority remains unchanged.
- NFL: reuse nfl_auto.market consensus only in market-only/full pathways; fundamentals pathway excludes it.
- Tennis: classify favorite/underdog from prediction-time de-vig match prices; sporting model remains independently auditable.
- KSS1: use three-way home/draw/away probabilities; do not collapse 1X2 into binary for bias analysis.

The shared code is in inqsi_intelligence/. It has no imports from serving modules by design.
