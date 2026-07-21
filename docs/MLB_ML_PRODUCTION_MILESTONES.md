# MLB ML production milestones

Last updated: 2026-07-21 UTC

## Objective

Operate the MLB learning cycle in AWS from immutable, pregame evidence and
official final labels. Learned models remain shadow-only until the prospective
promotion contract is satisfied. The first promotion requires manual review;
the release described here does not change live prediction direction, weights,
or wagering playability.

## Integrity workstream

| Fix | Production contract | Milestone evidence |
|---|---|---|
| 1. Canonical pulls | One integrity-valid record per UTC 15-minute slot; retries read the existing record; all scoring independently canonicalizes raw history. | Raw count, unique-slot count, slot IDs, duplicate count, and canonical fingerprint are frozen with each candidate. |
| 2. Current clean-cohort authority | Every candidate is re-read and revalidated against the current immutable lock/stage and unique official/provider aliases. | A current rejection overrides every older embedded approval; invalid rows are quarantined. |
| 3. One prediction authority | Public pre-lock responses serve the persisted canonical candidate; probabilities, winner, side, book, and price are internally consistent. | Home/away model probabilities are complementary and the displayed winner is the side at or above 50%; corrections remain displayed but non-playable and training-ineligible. |
| 4. Exact labels | The scheduled settlement path joins each immutable lock to exactly one MLB official `FINAL` game and writes one label without mutating the locked vector. | Zero completed games is `WAITING`, never a false pass; doubleheaders use official game IDs and fail closed on ambiguous aliases. The first persisted label must revalidate against the then-current lock before this fix is operationally proven. |
| 5. Fundamentals V2 | Each persisted candidate carries an immutable, fingerprinted pregame snapshot with per-group source identity, retrieval time, applicable effective time, and explicit missingness. The T-45 vector binds that already-persisted snapshot; it does not fetch or reconstruct fundamentals while locking. | No neutral zero fill, postgame reconstruction, or closing-line value in pregame features; T-30/T-15 news can block release but cannot rewrite T-45. Unavailable or incomplete source groups make the row training-ineligible without suppressing its winner lock. |
| 6. Fixed experiment | Whole slate dates are assigned once to 300 training, 100 validation, and 100 future prospective-test games. | The first model uses ten prespecified features, regularization, frozen missingness masks, and no cross-date leakage. |
| 7. Realistic promotion | Challenger must beat the same-time de-vigged market on Brier score and log loss, remain calibrated, and avoid accuracy regression. | Minimum 500 clean games, 100 prospective-test games, calibration error <= 0.08, at least +1 percentage-point accuracy lift, and 100 prospectively selected recommendations before playability authority. |
| 8. AWS-native learning | EventBridge runs the full trainer/evaluator every six hours and a lightweight immutable pre-outcome selection capture every 15 minutes, both with single-run concurrency; versioned datasets and models live in S3, while experiment state and approved shadow pointers live in DynamoDB. Official labels are written separately after FINAL. | GitHub tests and deploys code only. The first candidate approval is manual and shadow-only; live authority requires a separately reviewed V2 inference integration. |

## Realistic data milestones

These are evidence milestones, not calendar promises. A “full slate” estimate
uses 15 clean, fully settled games; postponements, missing sources, invalid
locks, schema changes, or ambiguous labels reduce the eligible count.
The 15-game figure is planning math, not the first-slate achievement rule.
`FIRST_FULL_CLEAN_SLATE_PROOF_ACHIEVED` requires one nonempty, officially
finalized slate whose fingerprinted MLB `gamePk` set exactly equals that same
immutable slate's unique, current, post-cutoff clean eligible `gamePk` set.
Clean games from different dates are never combined for this proof. A terminal
no-prediction game, duplicate ID, missing row, unexpected row, stale vector, or
tampered official-set fingerprint keeps the milestone unachieved.

| Milestone | Eligible games | Approximate full 15-game slates | Authority unlocked |
|---|---:|---:|---|
| First trustworthy post-fix slate | 15 | 1 | End-to-end collection proof only |
| Mechanical compatibility checkpoint | 140 | 10 | Diagnostic trainer operation only; no promotion |
| Frozen training partition complete | 300 | 20 | Fit shadow candidates |
| Frozen validation partition complete | 400 | 27 | Select/freeze one challenger policy |
| Prospective test complete | 500 | 34 | Direction-promotion review may begin if every quality gate passes |
| Selected recommendation reliability | 100 prospectively selected | Data-dependent | Playability-promotion review may begin |

## Release boundary and proof status

The release candidate supplies the Fix 4 settlement authority and the Fix 5
snapshot contract. That is implementation readiness, not evidence that either
milestone has already been earned:

- The Results Scheduler may read the immutable snapshots table and write only
  the separate outcomes/labels record. Exact-label proof remains
  `WAITING_FOR_FIRST_CANONICAL_FINAL_LABEL` until at least one persisted label
  revalidates against the current immutable lock. A dry run or a zero-label
  report cannot satisfy the proof.
- Fundamentals V2 is the new prospective schema boundary. The authoritative
  snapshot is the copy persisted with the candidate and fingerprint-bound into
  the T-45 vector; postgame code may read it but may not create, refresh, or
  repair it.
- Schema coverage is not source coverage. MLB Stats API probable-pitcher and
  venue data are only partial inputs. FIP/xFIP, K-BB%, pitch mix/velocity,
  bullpen availability, confirmed batting orders, injuries/scratches, park
  factors, weather/roof, and travel/rest remain missing unless a genuine
  pre-lock source supplies the required values and provenance. Missing groups
  remain null and exclude the game from the V2 training cohort.
- The r2 cohort begins at the explicit post-build boundary
  `2026-07-22T04:00:00+00:00`. Any July 20 or July 21 game, or any lock timestamp before that
  instant is historical, even if its record happens to resemble the V2 schema,
  and cannot enter an r2 partition.
- A game counts toward the milestones below only after its current lock,
  complete pregame V2 snapshot, frozen vector, write-once official label, and
  full-slate-final status all pass their current validators. Historical rows
  are not upgraded into V2 by reconstructing data after the game.

Any prediction policy, feature definition, label rule, cohort schema, partition
boundary, or threshold change creates a new experiment version and a new future
prospective test. Historical games are never relabeled or backfilled to make a
milestone appear complete.

## Promotion decision record

The AWS experiment record must include the immutable dataset fingerprint,
partition date assignments, feature schema, model artifact fingerprint,
same-time market baseline fingerprint, Brier score, log loss, calibration
error, accuracy lift, recommendation count, and the exact code/deployment SHA.
An eligible challenger remains shadow-only when a human records the first
approval. That approval stores `directionApproved` and/or
`playabilityApproved`, while live `directionAuthorityEnabled` and
`playabilityAuthorityEnabled` remain false. A separately reviewed V2 inference
consumer must verify the artifact before runtime authority can be activated.
“No eligible challenger” is a healthy expected state while evidence accumulates.
