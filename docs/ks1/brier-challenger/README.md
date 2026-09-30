# Brier challenger: fixed September comparison

PR #861 only repairs the optional challenger. It does not promote, deploy, change model references, calibration, locks, audit timing, or serving code.

The user selected training through August and a September holdout. The challenger therefore defines its own fixed September 1 boundary rather than changing or importing the rolling production split. Labels must have completed before that boundary and before the earliest held-out prediction. October games are excluded. Feature selection uses training rows only and preserves current coverage exclusions.

## Frozen benchmark

- Source: [retained-data run 34789382041](https://github.com/KirtKurt/parlay-platform/actions/runs/34789382041), artifact `ks1-recent-34789382041`, ID 10327424283.
- Input SHA-256: `799a2ac70d2102b7746afe91953042a86dca5bd07468626d4d10e2c95071e739`.
- Incumbent: [Phase 2 run 34431359524](https://github.com/KirtKurt/parlay-platform/actions/runs/34431359524), artifact ID 10134650953; model hash matches unchanged `ks1/model_refs.json`: `9de79b59e6d846da6210000bf13cde1090cafe871c4a477b075c06a89eb75390`.
- Train: 4,543 games, March 18, 2025–August 31, 2026.
- Test: identical 172 games, September 1–13, 2026.

| Model | Correct | Accuracy | Brier | Log loss |
| --- | ---: | ---: | ---: | ---: |
| Brier challenger | 94/172 | 54.6512% | 0.248837081991 | 0.690615184258 |
| Incumbent | 94/172 | 54.6512% | 0.242500065007 | 0.677495536666 |

**Decision: retain the incumbent.** The challenger is worse on both probability metrics. No holdout tuning or refit was performed. This is retrospective evidence, not new prospective validation; the training period and eligible features differ from the incumbent, so this does not isolate objective alone. Individual starter training coverage is zero for both sides and those features remain excluded. The current rolling 300-game promotion requirements are not relaxed by this 172-game diagnostic.

## Repairs and verification

- LightGBM 4.6 receives the callable through `params["objective"]`; the removed `fobj` API is no longer used. The original Brier gradient and floored exact Hessian are preserved.
- Both models receive log loss, Brier, accuracy, and game counts; full paired probabilities are retained in `holdout_predictions.csv` and the workflow artifact.
- Export preserves the learned tree margins and serializes LightGBM's native binary sigmoid inference header. It does not retrain with binary loss. The manifest records the actual Brier training loss. The ordinary reloaded `Booster.predict()` returns probabilities, with raw-margin and probability parity verified to 1e-12 on all train/test rows.
- The PR-only benchmark workflow checks the exact head, pins LightGBM 4.6.0, verifies the input hash and incumbent hash, downloads frozen artifacts with read-only permissions, and uploads results. It has no AWS credentials or deployment step.

Local reproduction after downloading the two artifacts:

```bash
python -m pytest -q tests/ks1/test_brier_objective.py tests/ks1_brier
python -m ks1.train_brier --input INPUT/input_table.parquet --incumbent-model INCUMBENT/model.txt --output NEW_EMPTY_DIRECTORY
```

Machine-readable metrics include hashes, selected/omitted features, and native reload proof. GitHub source artifacts are retained for 90 days; after expiry, reproduction requires the same hash-verified retained inputs, not substitution of newer data.
