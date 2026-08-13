# MLB V8 Hourly Numerical Status

**Updated:** 2026-08-13T06:40:20.038293+00:00

Accuracy is shown only when a source explicitly publishes it. This reporter never derives accuracy from wins and losses. Backfill, training, retrospective validation, prospective audit, shadow evaluation, and production promotion remain separate.

## Historical backfill / optimizer

| Metric | Current | Change |
|---|---:|---:|
| Historical eligible games | **4225** | **+15** |
| Completed slates | **340** | **+1** |
| Historical date reached | **2026-08-12** | **—** |
| Current historical cursor | **2026-08-13** | **—** |
| Configured recovery target | **4355** | **0** |
| Remaining games | **130 (source-reported; not recomputed)** | **—** |
| Remaining slates | **Unavailable — unavailable (not calculated when omitted)** | **—** |
| Optimizer revision | **6085** | **+11** |
| Network requests | **26964** | **+82** |
| Credits consumed | **269640** | **+820** |
| Optimizer phase | **WAITING_FOR_SETTLED_HORIZON** | — |
| Latest state timestamp | **2026-08-13T05:41:44.753993+00:00** (current) | — |

Historical eligible games and settled trainer games are separate, **incomparable** populations unless a source explicitly defines otherwise.

## V8 trainer / retrospective validation

| Metric | Current | Change |
|---|---:|---:|
| Latest trainer workflow | **31672710675 / SUCCESS** | — |
| Trainer report status | **SUCCESS** | — |
| Trainer timestamp | **2026-08-13T06:10:33.617568+00:00** (current) | — |
| Training rows | **3756** | **+15** |
| Validation samples | **205** | **0** |
| Walk-forward samples | **264** | **0** |
| Settled games | **4155** | **—** |
| Prospective graded predictions | **Unavailable — unavailable_unverified (prospective V8 ledger only)** | **—** |
| Records loaded | **4225** | — |
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
| Untouched validation | 205 | 120 | 0.58536585 | 0.07353649 |
| Walk-forward | 264 | 151 | 0.5719697 | 0.02931086 |
| Selected out-of-fold | Unavailable | Unavailable | Unavailable | Unavailable |

These are retrospective historical measurements, not prospective shadow-pick wins and losses.

## Frozen prospective audit

| Metric | Value |
|---|---:|
| Status | **WAITING_FOR_RETROSPECTIVE_GATE** |
| Timestamp | **2026-08-13T06:15:40.550886+00:00 (current)** |
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
| Timestamp | **2026-08-13T06:08:16.799493+00:00 (current)** |
| Processed games | **800** |
| Eligible games | **800** |
| New eligible games | **5** |
| Ineligible games | **0** |
| Remaining games | **3425** |
| Provider calls | **36** |
| Pointer revision | **228** |
| Progress made | **Yes** |

## Artifacts and production promotion

| Metric | Value |
|---|---:|
| Trainer artifacts | **1 / 165705 bytes** |
| Shadow artifacts | **Unavailable / Unavailable bytes** |
| Deployment artifacts | **1 / 5867 bytes** |
| Latest deployment workflow | **31672253739 / SUCCESS** |
| New learned V8 model artifact created | **Unavailable** |
| New V8 model promoted | **No** |
| Production authority changed | **No** |

## Autonomous controller and active blockers

- Fully autonomous: **Yes**
- Normal-operation manual intervention required: **No**
- Next action: **CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH**
- Promotion requested: **No**

**Active blockers/gates:** `learned_candidate_not_selected`, `market_baseline_retained`, `no_active_champion`, `no_stable_oof_uplift_over_market`, `not_cut_over_before_first_promotion`, `retrospective_promotion_gate_not_passed`, `untouched_audit_contains_day_below_80_percent`, `untouched_audit_mean_daily_accuracy_below_80_percent`, `untouched_audit_minimum_daily_accuracy_below_80_percent`, `walk_forward_contains_day_below_80_percent`, `walk_forward_mean_daily_accuracy_below_80_percent`, `walk_forward_minimum_daily_accuracy_below_80_percent`

A quality gate is not a runtime failure. Promotion remains separate from collection, backfill, training, validation, prospective auditing, and shadow evaluation.

<!-- MLB_V8_HOURLY_STATE:{"completedSlateCount":340,"contextEligibleGames":800,"contextNewEligibleGames":5,"contextPointerRevision":228,"contextProcessedGames":800,"contextProviderCalls":36,"contextRemainingGames":3425,"creditsConsumed":269640,"deploymentArtifactCount":1,"gradedPredictions":null,"historicalCursorDate":"2026-08-13","historicalDateReached":"2026-08-12","historicalEligibleGames":4225,"historicalRemainingGames":130,"historicalRemainingSlates":null,"historicalTargetGames":4355,"learnedCandidateCount":96,"learnedEligibleCandidateCount":0,"learningSteps":63360,"networkRequests":26964,"optimizerRevision":6085,"prospectiveSample":null,"settledGames":4155,"shadowArtifactCount":null,"shadowSample":null,"trainingArtifactCount":1,"trainingRows":3756,"validationSamples":205,"walkForwardSamples":264} -->
