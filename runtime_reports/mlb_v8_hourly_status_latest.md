# MLB V8 Hourly Numerical Status

**Updated:** 2026-08-07T00:11:59.090976+00:00

Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.

## Historical backfill / optimizer

| Metric | Current | Change |
|---|---:|---:|
| Historical eligible games | **4129** | **0** |
| Completed slates | **333** | **0** |
| Historical date reached | **2026-08-05** | **—** |
| Current historical cursor | **2026-08-06** | **—** |
| Configured recovery target | **4149** | **0** |
| Remaining games | **Unavailable — unavailable (source-reported; not recomputed)** | **—** |
| Remaining slates | **Unavailable — unavailable (not calculated when omitted)** | **—** |
| Optimizer revision | **5664** | **0** |
| Network requests | **26400** | **0** |
| Credits consumed | **264000** | **0** |
| Optimizer phase | **WAITING_FOR_SETTLED_HORIZON** | — |
| Latest state timestamp | **2026-08-06T05:01:44.775033+00:00** (stale) | — |

Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.

## V8 trainer / retrospective validation

| Metric | Current | Change |
|---|---:|---:|
| Latest trainer workflow | **31125530780 / FAILURE** | — |
| Trainer report status | **SUCCESS** | — |
| Trainer timestamp | **2026-08-06T14:43:44.212610+00:00** (stale) | — |
| Training rows | **3662** | **0** |
| Validation samples | **202** | **0** |
| Walk-forward samples | **265** | **0** |
| Settled games | **Unavailable — unavailable** | **—** |
| Prospective graded predictions | **Unavailable — unavailable_unverified (prospective V8 ledger only)** | **—** |
| Records loaded | **4129** | — |
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
| Untouched validation | 202 | 119 | 0.58910891 | 0.03284473 |
| Walk-forward | 265 | 156 | 0.58867925 | 0.04368324 |
| Selected out-of-fold | Unavailable | Unavailable | Unavailable | Unavailable |

These are retrospective historical measurements, not prospective shadow-pick wins and losses.

## Frozen prospective audit

| Metric | Value |
|---|---:|
| Status | **WAITING_FOR_RETROSPECTIVE_GATE** |
| Timestamp | **2026-08-06T14:46:41.884191+00:00 (stale)** |
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
| Timestamp | **2026-08-06T14:41:52.634064+00:00 (stale)** |
| Processed games | **55** |
| Eligible games | **55** |
| New eligible games | **5** |
| Ineligible games | **0** |
| Remaining games | **4074** |
| Provider calls | **36** |
| Pointer revision | **79** |
| Progress made | **Yes** |

## Artifacts and production promotion

| Metric | Value |
|---|---:|
| Trainer artifacts | **0 / 0 bytes** |
| Shadow artifacts | **Unavailable / Unavailable bytes** |
| Deployment artifacts | **1 / 6048 bytes** |
| Latest deployment workflow | **31111794666 / SUCCESS** |
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

<!-- MLB_V8_HOURLY_STATE:{"completedSlateCount":333,"contextEligibleGames":55,"contextNewEligibleGames":5,"contextPointerRevision":79,"contextProcessedGames":55,"contextProviderCalls":36,"contextRemainingGames":4074,"creditsConsumed":264000,"deploymentArtifactCount":1,"gradedPredictions":null,"historicalCursorDate":"2026-08-06","historicalDateReached":"2026-08-05","historicalEligibleGames":4129,"historicalRemainingGames":null,"historicalRemainingSlates":null,"historicalTargetGames":4149,"learnedCandidateCount":96,"learnedEligibleCandidateCount":0,"learningSteps":63360,"networkRequests":26400,"optimizerRevision":5664,"prospectiveSample":null,"settledGames":null,"shadowArtifactCount":null,"shadowSample":null,"trainingArtifactCount":0,"trainingRows":3662,"validationSamples":202,"walkForwardSamples":265} -->
