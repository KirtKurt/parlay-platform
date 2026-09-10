# KS1 — existing MLB data inventory

Inventory only. No game-table build, training, provider archive ingestion, production change, or deployment is included. KS1 is separate from R8. The draft PR is [#695](https://github.com/KirtKurt/parlay-platform/pull/695).

Primary evidence: [read-only inventory run 34426148629](https://github.com/KirtKurt/parlay-platform/actions/runs/34426148629), observed 2026-09-10T01:36:56.416473+00:00. Repository baseline: `3971870089e5dff9ee8dd2307c7ef6c0a10d7f7d`. Counts describe stored data, not model-ready, complete-slate, or point-in-time eligibility.

## Repository jobs

Cadences below come from committed configuration; this inventory did not invoke any production function or test scheduler execution.

| Job | Definition | Trigger | Code / output |
|---|---|---|---|
| Live MLB odds ingestion | template.yaml: MLBAuditedPullFunction / MLBHotEvery15Min | Every 15 minutes (runtime gates apply) | hello_world/mlb_manual_pull_protected.py |
| R8 training owner | template.yaml: MLBMLTrainingFunction / MLBMLTrainingEvery6Hours | 01:11, 07:11, 13:11, 19:11 UTC | hello_world/mlb_ml_aws_training_v1_compat.py |
| Pregame selection capture owner | template.yaml: MLBMLSelectionCaptureEvery2Minutes | Every two minutes, starting at minute 01 | Same training function; dispatches the existing research worker |
| Existing research worker | template.yaml: MLBResearchFunction | Invoked by the existing owner; no independent schedule | mlb_research/mlb_research_runtime_v1.py |
| Historical preparation and daily admission | .github/workflows/mlb-data-expansion.yml | 10:37 UTC daily, selected main changes, or manual dispatch | scripts/run_mlb_data_expansion.py; scripts/publish_mlb_research_dataset.py |
| Recent game/Statcast ingestion | .github/workflows/mlb-research-ingestion.yml | Hourly at :23, selected main changes, or manual dispatch | scripts/run_mlb_research_ingestion.py |
| Immutable lock check / playability check | template.yaml: MLBDailyPickLockFunction / MLBPlayabilityCheckpointFunction | Every minute | hello_world/mlb_daily_pick_lock_protected.py; hello_world/mlb_playability_checkpoint_scheduler.py |
| Production reporting | .github/workflows/mlb-30m-progress-pulse.yml | :11 and :41 UTC, plus workflow completion/manual triggers | Existing MLB progress issue #567 |
| Research execution proof | .github/workflows/mlb-research-postdeploy-proof.yml | Successful Deploy SAM to AWS, diagnostic-file main changes, or manual dispatch | scripts/verify_mlb_research_deployment.py |
| Results API proof | .github/workflows/verify-mlb-results-api-read-only-postdeploy.yml | Successful Deploy SAM to AWS or manual dispatch | Read-only verification |
| Historical optimizer (legacy tooling) | .github/workflows/mlb-historical-optimizer.yml | Scoped PR/push validation or explicit manual inputs | Existing separate stack; not started by KS1 |
| KS1 inventory (new draft PR only) | .github/workflows/ks1-phase1.yml | Same-repository PR or manual dispatch; no cron | ks1/inventory.py; CloudFormation/Lambda config/S3 reads only |

## Existing datasets

The active MLB artifact bucket resolved from the deployed trainer environment is `parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet`. Its environment reference is `MLB_ML_ARTIFACTS_BUCKET`. The following paths and schemas were read from existing S3 objects; no external provider data was downloaded. Date ranges follow the named source date field: `slateDateEt` is Eastern time, `startAtUtc` and `gameDate` use the source UTC date, and Statcast uses `game_date`.

### 1. game; reconstructed pregame features and separate label

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/reconstructed-v1/752590eb846038a43e4824225978b0b8b527c6d89c370e54c39837bccaf35269/dataset.json`

Rows: **4,481**. Dates: **2025-04-01 through 2026-09-08**.

Columns: `awayTeam`, `commenceTime`, `evidenceKind`, `featureCutoffUtc`, `featureFingerprint`, `features`, `historicalCorrectionsMayBePresent`, `homeTeam`, `label`, `marketSourceAtUtc`, `missingFeatures`, `officialGamePk`, `originalObservation`, `productionAuthority`, `prospectiveQualificationEvidence`, `reconstructedAtUtc`, `slateDateEt`, `sourceArtifact`, `version`.

### 2. game; whole-slate research inputs and final scores

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/research-v1/dataset.json`

Rows: **3,747**. Dates: **2025-04-01 through 2026-09-08**.

Columns: `awayRuns`, `evidenceKind`, `featureFingerprint`, `features`, `homeRuns`, `homeWon`, `officialGamePk`, `originalObservation`, `slateComplete`, `slateDateEt`.

### 3. completed game; team batting, starter-group pitching, relief usage

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/reconstructed-v1/source-games/`

Rows: **4,610**. Dates: **2025-03-18 through 2026-09-09**.

Columns: `completedAtUtc`, `fingerprint`, `gameType`, `officialGamePk`, `receipt`, `startAtUtc`, `teams`.

### 4. completed game; full team/player boxes

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/research-v1/prior-games.json`

Rows: **409**. Dates: **2026-08-10 through 2026-09-09**.

Columns: `completedAtUtc`, `gameType`, `homeWon`, `officialGamePk`, `receipt`, `startAtUtc`, `teams`, `venue`.

### 5. official scheduled game

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/research-v1/prior-games.json#schedule`

Rows: **419**. Dates: **2026-08-10 through 2026-09-10**.

Columns: `calendarEventID`, `content`, `dayNight`, `description`, `doubleHeader`, `gameDate`, `gameGuid`, `gameNumber`, `gamePk`, `gameType`, `gamedayType`, `gamesInSeries`, `ifNecessary`, `ifNecessaryDescription`, `inningBreakLength`, `isTie`, `link`, `officialDate`, `publicFacing`, `recordSource`, `rescheduledFrom`, `rescheduledFromDate`, `reverseHomeAwayStatus`, `scheduledInnings`, `season`, `seasonDisplay`, `seriesDescription`, `seriesGameNumber`, `status`, `teams`, `tiebreaker`, `venue`.

### 6. pitch

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/research-v1/statcast.json`

Rows: **119,830**. Dates: **2026-08-10 through 2026-09-08**.

Columns: `at_bat_number`, `batter`, `estimated_woba_using_speedangle`, `events`, `game_date`, `game_pk`, `launch_speed`, `p_throws`, `pitch_number`, `pitch_type`, `pitcher`, `release_speed`, `stand`, `type`.

### 7. original pregame game/checkpoint

Path: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/development-data/research-v1/snapshots/`

Rows: **3**. Dates: **2026-09-09 through 2026-09-09**.

Columns: `capturedAtUtc`, `checkpoint`, `commenceTime`, `conditions`, `deploymentGitSha`, `featureCutoffUtc`, `featureFingerprint`, `features`, `feedReceipt`, `ks1SourceKey`, `officialGamePk`, `originalObservation`, `outcomeKnownAtCapture`, `playerWindows`, `productionAuthority`, `slateDateEt`, `version`.

### 8. archived slate; records are games, snapshotAudit records market observations

Path: `s3://parlay-platform-mlb-histo-historicalartifactsbucke-zzah30lodfn5/mlb/historical-daily-v1/datasets-versioned/2026-05-03/4190c93172b56cde0c69d4fa86aa85c743db3f49b7b9bd5455e2f6f44ea8bb26.json`

Referenced slate artifacts: **367**. Dates: **2025-04-01 through 2026-09-08**.

Columns: `completeSlate`, `eligibleGameCount`, `exactSlateCoverage`, `exclusions`, `featureDatasetVersion`, `fingerprint`, `gameSpecificLockClipping`, `grid`, `historicalFirstFiveEnrichmentReady`, `officialGameCount`, `paidHistoricalCallsMade`, `plannedSnapshotCount`, `postLockDataExcluded`, `providerEventIdCoverage`, `providerEventIdRecordCount`, `quarantineContractVersion`, `quarantinedRequests`, `quarantinedSnapshotCount`, `records`, `rematerializationVersion`, `rematerializedAtUtc`, `requestLedgerComplete`, `sameSlateOutcomeFeaturesProhibited`, `skippedStaleArchivedSlots`, `slateDateEt`, `snapshotAudit`, `sourceHistoricalOddsRequestsReused`, `strictlyPastTeamHistoryDerivedAtTraining`, `supervisedFeatureContractVersion`, `v8TrainableCoverage`, `v8TrainableRecordCount`, `validSnapshotCount`, `version`.

Game record columns: `awaySignal`, `awayTeam`, `commenceTime`, `gameSpecificLockClipping`, `homeSignal`, `homeTeam`, `homeWon`, `observedAwayPullCount`, `observedHomePullCount`, `officialGamePk`, `postLockDataExcluded`, `predictionLockAtUtc`, `providerEventId`, `providerEventIdAvailable`, `requestedSlotCount`, `slateDateEt`, `version`, `winner`.

Scope: date range covers references; one source archive inspected.


## Retained legacy stores — additionally verified

### parlay-platform-mlb-v8-fundamentals-shadow

The read-only CloudFormation lookup returned `ValidationError`. A physical bucket was not resolved from this stack reference. This does not establish whether retained objects exist elsewhere; nothing was recreated or activated.

### parlay-platform-mlb-odds-v8-shadow

Resolved bucket: `parlay-platform-mlb-odds-v8--shadowartifactsbucket-i9zj7b1lvqey`.

Path: `s3://parlay-platform-mlb-odds-v8--shadowartifactsbucket-i9zj7b1lvqey/mlb/odds-v8-shadow/`

Stored objects: **1868**. Latest sampled object has **24 event records**. These counts are not additive with the newer datasets or with older versions of the same manifest.

Object-key date range: **2026-07-26 through 2026-08-14**.

Latest sampled object's row date range: **2026-08-14 through 2026-08-16**.

Object columns: `affordableEventLimit`, `budget`, `collectedAtUtc`, `contract`, `discoveries`, `eventEnrichment`, `eventEnrichmentErrors`, `featuredEvents`, `featuredHeaders`, `historicalAtUtc`, `marketExpansionVersion`, `productionAuthorityChanged`, `selectedEventCount`, `selectedMarketRequestCount`, `version`.

Row columns: `awayTeam`, `bookmakers`, `commenceTime`, `eventId`, `fingerprint`, `homeTeam`, `sportKey`, `version`.

Scope: all object keys listed; latest object schema sampled; no availability or training eligibility claim.

### parlay-platform-mlb-historical-optimizer

Resolved bucket: `parlay-platform-mlb-histo-historicalartifactsbucke-zzah30lodfn5`.

Path: `s3://parlay-platform-mlb-histo-historicalartifactsbucke-zzah30lodfn5/mlb/v8/historical-bbs/manifests/`

Stored objects: **18**. Latest sampled object has **1611 game records**. These counts are not additive with the newer datasets or with older versions of the same manifest.

Latest sampled object's row date range: **2026-03-25 through 2026-07-28**.

Object columns: `authority`, `backfillVersion`, `coverageStartDate`, `createdAtUtc`, `eligibleGameCount`, `ineligibleGameCount`, `manifestDigest`, `pointInTimeRequired`, `processedGameCount`, `productionAuthorityChanged`, `records`, `remainingGameCount`, `remainingSupportedGameCount`, `sameDayResultsExcluded`, `selectionRule`, `selectionUsedOutcomes`, `sourceCorpusFingerprint`, `sourceFeatureDatasetVersion`, `sourceHistoricalStateRevision`, `sourceSha`, `supportedCanonicalGameCount`, `supportedTrainingCoverage`, `targetGameOutcomeUsed`, `totalCanonicalGameCount`, `trainingCoverage`, `unsupportedCanonicalGameCount`, `version`.

Row columns: `awayTeam`, `eligibilityErrors`, `featureSource`, `homeTeam`, `officialGamePk`, `predictionLockAtUtc`, `providerMatchId`, `slateDateEt`, `snapshot`, `trainingEligible`.

Nested snapshot columns: `authority`, `away`, `awayTeam`, `createdAtUtc`, `eligibilityErrors`, `fingerprint`, `historyBoundary`, `home`, `homeTeam`, `officialGamePk`, `pointInTimeVerified`, `postgameFieldsExcluded`, `predictionLockAtUtc`, `priorCompletedGamesUsed`, `productionAuthorityChanged`, `providerEvidence`, `providerMatchId`, `sameDayResultsExcluded`, `selectionUsedOutcomes`, `slateDateEt`, `snapshotRole`, `targetGameOutcomeUsed`, `trainingEligible`, `version`.

Recorded feature source: `MLB-V8-HISTORICAL-BBS-PRIOR-GAME-v1`.

The existing manifest marks 1611 rows eligible under its own contract; KS1 eligibility has not been assessed.

Scope: all object keys listed; latest object schema sampled; no availability or training eligibility claim.


## Nested source schemas

- Reconstructed `features`: archived no-vig market probability and movement; both teams' prior 14-day batting OPS and starting-pitcher-group statistics; relief workload for 1/3/5 days. `label.homeWon` is separate from the feature fingerprint. These are reconstructed historical development rows.
- Compact `teams.home/away`: stable MLB team `id` and `name`, batting count fields, `priorStarters` pitching counts, and `relief` pitch/out totals. Individual target-game pregame starters and batting orders are not retained.
- Recent full boxes: `teams.home/away.team`, `teamStats.batting`, `players`, and `pitchers`; player identities and per-game pitching/batting counts. Final boxes must not be treated as archived pregame lineup observations.
- Original snapshots: `playerWindows.teams.home/away` contains team ID, active roster count, probable starter ID, confirmed batting order when available, and player observations with 7/15/30-day windows. `capturedAtUtc`, `featureCutoffUtc`, `featureFingerprint`, and source receipts bind timing and content.
- Statcast: pitch identity is `(game_pk, at_bat_number, pitch_number)`. Stored fields include pitcher/batter IDs, release speed, exit velocity (`launch_speed`), and estimated wOBA using speed/angle. `launch_angle`, a barrel flag, xERA, SIERA, FIP, and xFIP are not present in this cached pitch schema. An estimated contact value must not be mislabeled as full xwOBA.

## Other repo data and declared DynamoDB schemas

`runtime_reports/mlb_ml_training_dataset_latest.csv` contains **6 rows**, dated **2026-07-18 through 2026-07-20**. Grain: legacy prediction/example row, with matchup, predicted/actual winner, market and movement features, and a label. It is not the large historical corpus.

The SAM template declares these existing tables with string `PK` and `SK` keys. Table contents/counts were not scanned in this inventory:

| Logical resource | Declared table name | Role from repository code |
|---|---|---|
| SnapshotsTable | parlay_platform_snapshots | Pulls, immutable locks, experiment state, historical archive pointers |
| OutcomesTable | parlay_platform_outcomes | Final outcomes and settlement evidence |
| SignalLedgerTable | parlay_platform_signal_ledger | Signal history |
| PredictionsTable | parlay_platform_predictions | Prediction records |

The historical archive state key is `PK=MLB_HISTORICAL_OPTIMIZER#V1`, `SK=STATE`. Additional retained BBS/context pointer references exist at `MLB_V8_HISTORICAL_BBS#V1 / ACTIVE` and `MLB_V8_HISTORICAL_CONTEXT#V1 / ACTIVE`; their presence alone is not evidence that a BBS service is available now.

## Secret and configuration names

No secret values are recorded.

- Confirmed available to the inventory job and deployed trainer: `ODDS_API_KEY`.
- Existing deployment credentials referenced by GitHub Actions: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`.
- BBS aliases referenced in the repo: `BBD_API_KEY`, `BBD_API_TOKEN`, `BBD_KEY`, `BIGBALLSDATA_API_KEY`, `BIGBALLSDATA_KEY`, `BIGBALLS_API_KEY`, `BIGBALLS_DATA_API_KEY`, `BIGBALLS_DATA_KEY`, `BIG_BALLS_API_KEY`, `BIG_BALLS_DATA_API_KEY`, `BIG_BALLS_DATA_KEY`, `BIG_BALLS_KEY`. None was available to the initial inventory job or active trainer. This does not establish whether a differently named secret exists elsewhere.
- Non-secret environment/configuration references: `MLB_ML_ARTIFACTS_BUCKET`, `MLB_HISTORICAL_ARTIFACTS_BUCKET`, `MLB_V8_FUNDAMENTALS_BUCKET`, `MLB_V8_SHADOW_BUCKET`, `SNAPSHOTS_TABLE`, `OUTCOMES_TABLE`.
- Legacy provider URL/endpoint configuration names: `BIG_BALLS_DATA_API_BASE_URL`, `BBD_API_BASE_URL`, `BIG_BALLS_DATA_OPENAPI_URL`, `BBD_OPENAPI_URL`, `BIG_BALLS_DATA_MLB_ENDPOINTS_JSON`, `BBD_MLB_ENDPOINTS_JSON`. Names found in source are not proof of configured values.
- `RAW_ARCHIVE_BUCKET` is referenced by `hello_world/mlb_raw_s3_archive.py`, which would write `raw/odds_api/mlb/date=.../asof=.../`. It is not configured on the inspected trainer; this potential archive was not assumed to exist.

## Deployment and existing model storage

Canonical deployment: **Deploy SAM to AWS**, `.github/workflows/deploy.yml`, root `template.yaml`, stack `parlay-platform-dev`, Python 3.11. It deploys selected main-branch changes or manual dispatch; checks out the exact commit and builds/deploys through SAM. KS1 is not included in this deployment configuration and this PR was not merged.

Existing model/artifact namespace: `s3://parlay-platform-dev-mlbmlartifactsbucket-rtsuugrwreet/mlb/experiments/`. The trainer uses experiment/run subdirectories for `dataset.json`, `manifest.json`, `evaluation.json`, `bundle.json`, and frozen challenger artifacts. No KS1 model location or AWS resource has been created.

## What the next authorized step would need to handle

- Reuse these stores; a replacement historical archive is unnecessary.
- Resolve missing archived pregame starter/lineup evidence explicitly. Final-box identities do not prove when pregame changes were known.
- The available player windows are 7/15/30 days; KS1's requested 10/30/75-day windows have not been built. Recent full individual boxes cover about 30 days, while the larger historical cache contains team/starter-group aggregates.
- The largest prepared dataset retains winner labels but not both final run totals on every row. The 3,747-row research dataset supplies both run totals for its cohort; they need a stable-ID join and coverage report later.
- Archived H2H probabilities exist. Moneyline/spread/total raw-market coverage should be established from retained odds stores before considering a new provider call.
- Weather and park-factor completeness is not established. Missing metrics must remain explicit rather than assumed neutral or zero.

## Reproduce the inventory

```bash
python -m venv /tmp/ks1-inventory-env
/tmp/ks1-inventory-env/bin/python -m pip install 'boto3>=1.34,<2'
/tmp/ks1-inventory-env/bin/python -m ks1.inventory --output /tmp/ks1-inventory
```

This requires existing AWS read credentials for the discovered resources. It performs configuration reads and S3 list/get operations only. It writes only the inventory metadata report locally, and the workflow uploads that report. Raw source bundles are not exported. The run above completed with **zero provider calls** and **zero AWS writes**. The repository is left at inventory only, awaiting Kurt's next instruction. The unfinished builder and modeling dependency files were removed before publication.
