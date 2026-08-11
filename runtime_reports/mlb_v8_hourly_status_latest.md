# MLB V8 Hourly Numerical Status

**Updated:** 2026-08-11T23:53:51.737799+00:00

Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.

## Historical backfill / optimizer

| Metric | Current | Change |
|---|---:|---:|
| Historical eligible games | **4195** | **0** |
| Completed slates | **338** | **0** |
| Historical date reached | **2026-08-10** | **—** |
| Current historical cursor | **2026-08-11** | **—** |
| Configured recovery target | **4405** | **0** |
| Remaining games | **Unavailable — unavailable (source-reported; not recomputed)** | **—** |
| Remaining slates | **Unavailable — unavailable (not calculated when omitted)** | **—** |
| Optimizer revision | **6058** | **0** |
| Network requests | **26800** | **0** |
| Credits consumed | **268000** | **0** |
| Optimizer phase | **WAITING_FOR_SETTLED_HORIZON** | — |
| Latest state timestamp | **2026-08-11T05:31:44.843058+00:00** (stale) | — |

Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.

## V8 trainer / retrospective validation

| Metric | Current | Change |
|---|---:|---:|
| Latest trainer workflow | **31545461343 / SUCCESS** | — |
| Trainer report status | **SUCCESS** | — |
| Trainer timestamp | **2026-08-11T23:13:01.886642+00:00** (current) | — |
| Training rows | **3726** | **0** |
| Validation samples | **201** | **0** |
| Walk-forward samples | **268** | **0** |
| Settled games | **Unavailable — unavailable** | **—** |
| Prospective graded predictions | **Unavailable — unavailable_unverified (prospective V8 ledger only)** | **—** |
| Records loaded | **4195** | — |
| Learning status | **LEARNED_CANDIDATE_SELECTED** | — |
| Selected feature group | **market_temporal_team_v8_fullgame_regime** | — |
| Optimization steps | **64060** | — |
| Learned candidates | **96** | — |
| Gate-eligible learned candidates | **1** | — |
| Learned candidate selected | **Yes** | — |
| Promotion gate passed | **No** | — |

### Retrospective validation only

| Evaluation | Sample | Correct | Source-reported accuracy | Calibration ECE |
|---|---:|---:|---:|---:|
| Untouched validation | 201 | 114 | 0.56716418 | 0.02441048 |
| Walk-forward | 268 | 144 | 0.53731343 | 0.02116169 |
| Selected out-of-fold | Unavailable | Unavailable | Unavailable | Unavailable |

These are retrospective historical measurements, not prospective shadow-pick wins and losses.

## Frozen prospective audit

| Metric | Value |
|---|---:|
| Status | **WAITING_FOR_RETROSPECTIVE_GATE** |
| Timestamp | **2026-08-11T23:17:07.291112+00:00 (current)** |
| Sample size | **Unavailable** |
| Wins | **Unavailable** |
| Losses | **Unavailable** |
| Pushes | **Unavailable** |
| Voids | **Unavailable** |
| Source-reported overall accuracy | **Unavailable** |
| Source-reported selected-pick accuracy | **Unavailable** |
| Source-reported confidence-band accuracy | **Unavailable** |
| Source-reported calibration ECE | **Unavailable** |

## V8 shadow simulation / evaluation

| Metric | Value |
|---|---:|
| Workflow run | **Unavailable / UNAVAILABLE** |
| Status | **SHADOW_ONLY** |
| Timestamp | **2026-08-05T04:38:10.928628+00:00 (stale)** |
| Sample size | **Unavailable** |
| Wins | **Unavailable** |
| Losses | **Unavailable** |
| Pushes | **Unavailable** |
| Voids | **Unavailable** |
| Source-reported overall accuracy | **Unavailable** |
| Source-reported selected-pick accuracy | **Unavailable** |
| Source-reported confidence-band accuracy | **Unavailable** |
| Source-reported calibration ECE | **Unavailable** |

## Official historical context backfill

| Metric | Value |
|---|---:|
| Status / authority | **V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY** |
| Timestamp | **2026-08-11T23:11:21.051320+00:00 (current)** |
| Processed games | **670** |
| Eligible games | **670** |
| New eligible games | **5** |
| Ineligible games | **0** |
| Remaining games | **3525** |
| Provider calls | **36** |
| Pointer revision | **202** |
| Progress made | **Yes** |

## Artifacts and production promotion

| Metric | Value |
|---|---:|
| Trainer artifacts | **1 / 174174 bytes** |
| Shadow artifacts | **Unavailable / Unavailable bytes** |
| Deployment artifacts | **Unavailable / Unavailable bytes** |
| Latest deployment workflow | **Unavailable / UNAVAILABLE** |
| New learned V8 model artifact created | **Unavailable** |
| New V8 model promoted | **No** |
| Production authority changed | **No** |

## Autonomous controller and active blockers

- Fully autonomous: **Yes**
- Normal-operation manual intervention required: **No**
- Next action: **CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH**
- Promotion requested: **No**

**Active blockers/gates:** `retrospective_promotion_gate_not_passed`, `untouched_audit_accuracy_worse_than_market`, `untouched_audit_brier_worse_than_market`, `untouched_audit_contains_day_below_80_percent`, `untouched_audit_log_loss_worse_than_market`, `untouched_audit_mean_daily_accuracy_below_80_percent`, `untouched_audit_minimum_daily_accuracy_below_80_percent`, `walk_forward_accuracy_worse_than_market`, `walk_forward_brier_worse_than_market`, `walk_forward_contains_day_below_80_percent`, `walk_forward_log_loss_worse_than_market`, `walk_forward_mean_daily_accuracy_below_80_percent`, `walk_forward_minimum_daily_accuracy_below_80_percent`

A quality gate is not a runtime failure. Promotion remains separate from collection, backfill, training, validation, prospective auditing, and shadow evaluation.

<!-- MLB_V8_HOURLY_STATE:{"completedSlateCount":338,"contextEligibleGames":670,"contextNewEligibleGames":5,"contextPointerRevision":202,"contextProcessedGames":670,"contextProviderCalls":36,"contextRemainingGames":3525,"creditsConsumed":268000,"deploymentArtifactCount":null,"gradedPredictions":null,"historicalCursorDate":"2026-08-11","historicalDateReached":"2026-08-10","historicalEligibleGames":4195,"historicalRemainingGames":null,"historicalRemainingSlates":null,"historicalTargetGames":4405,"learnedCandidateCount":96,"learnedEligibleCandidateCount":1,"learningSteps":64060,"networkRequests":26800,"optimizerRevision":6058,"prospectiveSample":null,"settledGames":null,"shadowArtifactCount":null,"shadowSample":null,"trainingArtifactCount":1,"trainingRows":3726,"validationSamples":201,"walkForwardSamples":268} -->
