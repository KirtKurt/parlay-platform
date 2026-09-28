# InQsi NBA Intelligence — foundation

Status: **SHADOW ONLY. No production authority.**

## Design
Independent modules: source adapters -> canonical point-in-time warehouse -> identity -> feature store -> target-specific models -> calibration -> simulator -> immutable T-60/T-30/T-10 ledger -> grading -> challenger evaluation -> reporting/health.

Every observation carries source, retrieved_at, effective_at, game/entity identity and feature version. Missing values remain explicit. Feature construction rejects observations not demonstrably available at prediction time.

## Model ladder
A fundamentals; B + player availability; C + lineup; D + fatigue/rest/travel; E + de-vigged multi-book market. Evaluate market/no-market ablations. Baselines: logistic, Elo, possession model; candidates: LightGBM and XGBoost/equivalent. Ensembles only after component validation.

Targets are separate: ML, spread, total, team totals, 1H, 1Q, alternates. Player props remain schema-ready but cannot be exposed until separately validated.

## Promotion
Chronological walk-forward only. A challenger needs >=300 genuinely later untouched games where sufficient history exists, provenance/leakage/missingness gates, Brier/log-loss/calibration non-inferiority or improvement according to target policy, and explicit promotion. Training accuracy never promotes a model.

## Next build slices
1 BBD historical adapter and receipt-bearing raw archive.
2 The Odds API NBA current/historical adapter with quota/freshness evidence.
3 canonical NBA team/player identity maps and quarantine.
4 backfill + as-of warehouse manifests.
5 efficiency, shot-profile, pace, player-value, lineup, fatigue and matchup features.
6 A-E reproducible training + chronological evaluation.
7 validated possession simulator distributions for spread/total derivatives.
8 temperature/isotonic/Platt calibration on completed locked predictions only.
9 durable immutable ledger + final-score grader.
10 SHAP active-signal attribution and diagnostic-only separation.
11 drift/health controller and prospective shadow accumulation.

No deployment is authorized by this foundation.
