# MLB V8 Hourly Numerical Status

**Updated:** 2026-08-09T13:11:21.327907+00:00

Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.

## Historical backfill / optimizer

| Metric | Current | Change |
|---|---:|---:|
| Historical eligible games | **4155** | **0** |
| Completed slates | **335** | **0** |
| Historical date reached | **2026-08-08** | **—** |
| Current historical cursor | **2026-08-08** | **—** |
| Configured recovery target | **4149** | **0** |
| Remaining games | **Unavailable — unavailable (source-reported; not recomputed)** | **—** |
| Remaining slates | **Unavailable — unavailable (not calculated when omitted)** | **—** |
| Optimizer revision | **5792** | **+19** |
| Network requests | **26643** | **0** |
| Credits consumed | **266430** | **0** |
| Optimizer phase | **BACKFILLING** | — |
| Latest state timestamp | **2026-08-09T13:11:02.775651+00:00** (current) | — |

Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.

## V8 trainer / retrospective validation

| Metric | Current | Change |
|---|---:|---:|
| Latest trainer workflow | **31313392563 / SUCCESS** | — |
| Trainer report status | **SUCCESS** | — |
| Trainer timestamp | **2026-08-09T12:31:01.970433+00:00** (current) | — |
| Training rows | **3690** | **0** |
| Validation samples | **206** | **0** |
| Walk-forward samples | **259** | **0** |
| Settled games | **Unavailable — unavailable** | **—** |
| Prospective graded predictions | **Unavailable — unavailable_unverified (prospective V8 ledger only)** | **—** |
| Records loaded | **4155** | — |
| Learning status | **LEARNING_EXECUTED_MARKET_BASELINE_RETAINED** | — |
| Selected feature group | **market_baseline** | — |
| Optimization steps | **63360** | — |
| Learned candidates | **96** | — |
| Gate-eligible learned candidates | **0** | — |
| Learned candidate selected | **No** | — |
| Promotion gate passed | **No** | — |

### Retrospective validation only

| Evaluation | Sample | Correct | Source-reported accuracy | Calibration ECE |
|---|---:|---:|---:|---:|
| Untouched validation | 206 | 121 | 0.58737864 | 0.03899396 |
| Walk-forward | 259 | 149 | 0.57528958 | 0.02932856 |
| Selected out-of-fold | Unavailable | Unavailable | Unavailable | Unavailable |

These are retrospective historical measurements, not prospective shadow-pick wins and losses.

## Frozen prospective audit

| Metric | Value |
|---|---:|
| Status | **WAITING_FOR_RETROSPECTIVE_GATE** |
| Timestamp | **2026-08-09T12:34:48.086746+00:00 (current)** |
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
| Timestamp | **2026-08-09T12:29:26.941123+00:00 (current)** |
| Processed games | **385** |
| Eligible games | **385** |
| New eligible games | **5** |
| Ineligible games | **0** |
| Remaining games | **3770** |
| Provider calls | **36** |
| Pointer revision | **145** |
| Progress made | **Yes** |

## Artifacts and production promotion

| Metric | Value |
|---|---:|
| Trainer artifacts | **1 / 167127 bytes** |
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

**Active blockers/gates:** `OrchestrationError:untouched audit dates were reused after label evaluation: 2026-07-20,2026-07-21,2026-07-22,2026-07-23,2026-07-24,2026-07-25,2026-07-26,2026-07-27,2026-07-28,2026-07-29,2026-07-30,2026-07-31,2026-08-01,2026-08-02,2026-08-03,2026-08-04,2026-08-05,2026-08-06,2026-08-07`, `learned_candidate_not_selected`, `market_baseline_retained`, `no_stable_oof_uplift_over_market`, `retrospective_promotion_gate_not_passed`, `untouched_audit_contains_day_below_80_percent`, `untouched_audit_mean_daily_accuracy_below_80_percent`, `untouched_audit_minimum_daily_accuracy_below_80_percent`, `walk_forward_contains_day_below_80_percent`, `walk_forward_mean_daily_accuracy_below_80_percent`, `walk_forward_minimum_daily_accuracy_below_80_percent`

A quality gate is not a runtime failure. Promotion remains separate from collection, backfill, training, validation, prospective auditing, and shadow evaluation.

<!-- MLB_V8_HOURLY_STATE:{"completedSlateCount":335,"contextEligibleGames":385,"contextNewEligibleGames":5,"contextPointerRevision":145,"contextProcessedGames":385,"contextProviderCalls":36,"contextRemainingGames":3770,"creditsConsumed":266430,"deploymentArtifactCount":null,"gradedPredictions":null,"historicalCursorDate":"2026-08-08","historicalDateReached":"2026-08-08","historicalEligibleGames":4155,"historicalRemainingGames":null,"historicalRemainingSlates":null,"historicalTargetGames":4149,"learnedCandidateCount":96,"learnedEligibleCandidateCount":0,"learningSteps":63360,"networkRequests":26643,"optimizerRevision":5792,"prospectiveSample":null,"settledGames":null,"shadowArtifactCount":null,"shadowSample":null,"trainingArtifactCount":1,"trainingRows":3690,"validationSamples":206,"walkForwardSamples":259} -->
