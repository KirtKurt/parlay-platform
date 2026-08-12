# MLB V8 Hourly Numerical Status

**Updated:** 2026-08-12T19:16:44.933346+00:00

Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.

## Historical backfill / optimizer

| Metric | Current | Change |
|---|---:|---:|
| Historical eligible games | **4210** | **0** |
| Completed slates | **339** | **0** |
| Historical date reached | **2026-08-11** | **—** |
| Current historical cursor | **2026-08-12** | **—** |
| Configured recovery target | **4355** | **-50** |
| Remaining games | **Unavailable — unavailable (source-reported; not recomputed)** | **—** |
| Remaining slates | **Unavailable — unavailable (not calculated when omitted)** | **—** |
| Optimizer revision | **6072** | **+1** |
| Network requests | **26882** | **0** |
| Credits consumed | **268820** | **0** |
| Optimizer phase | **WAITING_FOR_SETTLED_HORIZON** | — |
| Latest state timestamp | **2026-08-12T18:11:45.048920+00:00** (current) | — |

Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.

## V8 trainer / retrospective validation

| Metric | Current | Change |
|---|---:|---:|
| Latest trainer workflow | **31628929315 / SUCCESS** | — |
| Trainer report status | **SUCCESS** | — |
| Trainer timestamp | **2026-08-12T18:43:55.205172+00:00** (current) | — |
| Training rows | **3741** | **0** |
| Validation samples | **205** | **0** |
| Walk-forward samples | **264** | **0** |
| Settled games | **Unavailable — unavailable** | **—** |
| Prospective graded predictions | **Unavailable — unavailable_unverified (prospective V8 ledger only)** | **—** |
| Records loaded | **4210** | — |
| Learning status | **LEARNING_EXECUTED_MARKET_BASELINE_RETAINED** | — |
| Selected feature group | **market_baseline** | — |
| Optimization steps | **63360** | — |
| Learned candidates | **96** | — |
| Gate-eligible learned candidates | **1** | — |
| Learned candidate selected | **No** | — |
| Promotion gate passed | **No** | — |

### Retrospective validation only

| Evaluation | Sample | Correct | Source-reported accuracy | Calibration ECE |
|---|---:|---:|---:|---:|
| Untouched validation | 205 | 120 | 0.58536585 | 0.06533465 |
| Walk-forward | 264 | 149 | 0.56439394 | 0.02395528 |
| Selected out-of-fold | Unavailable | Unavailable | Unavailable | Unavailable |

These are retrospective historical measurements, not prospective shadow-pick wins and losses.

## Frozen prospective audit

| Metric | Value |
|---|---:|
| Status | **WAITING_FOR_RETROSPECTIVE_GATE** |
| Timestamp | **2026-08-12T18:47:54.488888+00:00 (current)** |
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
| Timestamp | **2026-08-12T18:42:12.825027+00:00 (current)** |
| Processed games | **760** |
| Eligible games | **760** |
| New eligible games | **5** |
| Ineligible games | **0** |
| Remaining games | **3450** |
| Provider calls | **36** |
| Pointer revision | **220** |
| Progress made | **Yes** |

## Artifacts and production promotion

| Metric | Value |
|---|---:|
| Trainer artifacts | **1 / 165522 bytes** |
| Shadow artifacts | **Unavailable / Unavailable bytes** |
| Deployment artifacts | **0 / 0 bytes** |
| Latest deployment workflow | **31630772245 / SKIPPED** |
| New learned V8 model artifact created | **Unavailable** |
| New V8 model promoted | **No** |
| Production authority changed | **No** |

## Autonomous controller and active blockers

- Fully autonomous: **Yes**
- Normal-operation manual intervention required: **No**
- Next action: **CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH**
- Promotion requested: **No**

**Active blockers/gates:** `learned_candidate_not_selected`, `market_baseline_retained`, `no_stable_oof_uplift_over_market`, `retrospective_promotion_gate_not_passed`, `untouched_audit_contains_day_below_80_percent`, `untouched_audit_mean_daily_accuracy_below_80_percent`, `untouched_audit_minimum_daily_accuracy_below_80_percent`, `walk_forward_contains_day_below_80_percent`, `walk_forward_mean_daily_accuracy_below_80_percent`, `walk_forward_minimum_daily_accuracy_below_80_percent`

A quality gate is not a runtime failure. Promotion remains separate from collection, backfill, training, validation, prospective auditing, and shadow evaluation.

<!-- MLB_V8_HOURLY_STATE:{"completedSlateCount":339,"contextEligibleGames":760,"contextNewEligibleGames":5,"contextPointerRevision":220,"contextProcessedGames":760,"contextProviderCalls":36,"contextRemainingGames":3450,"creditsConsumed":268820,"deploymentArtifactCount":0,"gradedPredictions":null,"historicalCursorDate":"2026-08-12","historicalDateReached":"2026-08-11","historicalEligibleGames":4210,"historicalRemainingGames":null,"historicalRemainingSlates":null,"historicalTargetGames":4355,"learnedCandidateCount":96,"learnedEligibleCandidateCount":1,"learningSteps":63360,"networkRequests":26882,"optimizerRevision":6072,"prospectiveSample":null,"settledGames":null,"shadowArtifactCount":null,"shadowSample":null,"trainingArtifactCount":1,"trainingRows":3741,"validationSamples":205,"walkForwardSamples":264} -->
