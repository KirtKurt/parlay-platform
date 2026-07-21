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
| 8. AWS-native learning | EventBridge runs the full trainer/evaluator every six hours and a lightweight immutable pre-outcome selection capture every 15 minutes. Training, selection capture, and manual shadow review share one global, atomic, expiring, owner-checked DynamoDB execution lease across experiment versions; versioned datasets and models live in S3, while experiment state and approved shadow pointers live in DynamoDB. Trainer Lambda and EventBridge delivery have zero asynchronous retry fan-out and no failure destination; the next fixed-cadence event is the retry. This requires neither SQS nor account-level reserved concurrency. Official labels are written separately after FINAL. | GitHub tests and deploys code only. The first candidate approval is manual and shadow-only; live authority requires a separately reviewed V2 inference integration. |

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
- The r3 cohort begins at the explicit next-slate prospective boundary
  `2026-07-22T04:00:00+00:00`. Any July 20 or July 21 game, or any lock timestamp before that
  instant is historical, even if its record happens to resemble the V2 schema,
  and cannot enter an r3 partition.
- A game counts toward the milestones below only after its current lock,
  complete pregame V2 snapshot, frozen vector, write-once official label, and
  full-slate-final status all pass their current validators. Historical rows
  are not upgraded into V2 by reconstructing data after the game.
- AWS-native training remains fail-closed without an SQS dead-letter queue:
  invocation failures are written to the durable trainer status when possible,
  propagate as Lambda errors, and do not fan out into overlapping asynchronous
  retries. The next fixed-cadence event is the retry; the failed event payload
  is not archived, and stale or missing health blocks promotion until a later
  scheduled run succeeds. Adding a durable failure archive is an explicit
  future IAM-expansion milestone. The account cannot allocate another
  reserved Lambda concurrency slot while retaining AWS's required unreserved
  pool, so all three mutating modes and all experiment versions instead share
  a 960-second DynamoDB lease. Its permanent partition key deliberately reuses
  the live r2 experiment lease partition. That migration anchor makes an
  in-flight r2 owner and the new r3 runtime contend on the same item during the
  first deployment; future experiment versions continue using that same key.
  That lease outlives the 900-second Lambda timeout, lets the next recurring
  invocation recover a timed-out owner, reclaims expired owners,
  and can be released only by its current owner. This preserves single-writer
  execution without a new AWS resource or IAM permission. Removing SQS and the
  account-level reservation does not change S3 versioning, partition gates,
  promotion controls, or shadow-only runtime authority.
- The lease safety proof is bound to the deployed 900-second Lambda timeout,
  which the live deployment verifier checks together with the 960-second lease
  and the absence of reserved concurrency. Authoritative writes retain their
  existing compare-and-swap/transaction conditions; they do not add a separate
  fencing token to every write. An early crash may therefore leave the lease
  until expiry. The next 15-minute capture or six-hour training schedule
  recovers it, while stale mode-specific
  health keeps audit and promotion authority fail-closed in the interim.

## Lambda capacity safety record

The July 21 deployment proved that the AWS account has only ten unreserved
Lambda concurrency slots and can reject even a single trainer initialization
with `ConcurrentInvocationLimitExceeded`. Capacity protection is therefore a
release contract, not an optional optimization:

- The one-minute lock keeps its cadence under a 330-second, owner-checked
  DynamoDB single-flight lease. Before the first lifecycle checkpoint it skips
  `_progress`; non-due active minutes use one read-only progress snapshot; and
  T-45 plus missed-lock repair retain the complete fail-closed path. After the
  last start, one write-once reconciliation marks missing locks terminal for the
  exact game-ID/start-time manifest. Matching later ticks still re-read the
  current verified manifest to detect schedule changes, but skip `_progress`
  and canonicalization. A timed-out owner cannot delete a successor lease, and
  the next minute repairs work after lease expiry.
- Recurring lock, settlement, soccer, and trainer work has zero async retry
  fan-out because the next fixed-cadence invocation is the retry. Canonical
  audited ingestion keeps one five-minute-bounded retry because a missing
  evidence slot is material and its slot write is conditionally idempotent. The
  shared trainer/selection Lambda queue expires accepted events after five
  minutes; only delivery of the six-hour training event to Lambda retains a
  one-hour age bound. A starved selection capture therefore cannot execute as
  stale evidence after its next slot.
- Quarter-hour work is staggered: odds pull at minute 0, selection capture at
  minute 4, settlement at minute 6, and soccer at minute 9. Six-hour training
  starts at minute 11 of hours 1/7/13/19 UTC, and daily autopsy starts at
  06:13 UTC instead of colliding with minute-zero ingestion.
- The old production verifier schedule is disabled because its recomputation
  path is known to time out. Direct invocation remains available. It may be
  re-enabled at minute 2/7/12/... only after a regression proves it reads
  persisted summaries without calling `predict_all` or full lock status. The
  GitHub production-acceptance workflow that invokes this diagnostic is also
  manual-only; neither an hourly timer nor a `main` push can bypass the disabled
  AWS schedule.
- Reserved concurrency remains forbidden until the account quota is raised;
  restoring it at the current quota would reproduce the failed deployment.

Any prediction policy, feature definition, label rule, cohort schema, partition
boundary, or threshold change creates a new experiment version and a new future
prospective test. Historical games are never relabeled or backfilled to make a
milestone appear complete.

## Big Balls Sports Data shadow-source milestones

The canonical repository credential name is `BBS_API_KEY`. Deployment passes
it once as a `NoEcho` CloudFormation parameter into AWS Secrets Manager. Only
the scheduled audited-pull Lambda may read that secret; public reads, locks,
settlement, and AWS training receive neither the key nor its secret ARN.

BBS begins as an untrusted supplemental shadow source. It receives no scoring,
fundamentals-completeness, or training credit merely because authentication or
an endpoint succeeds. Each response is captured once per canonical 15-minute
slot in a versioned, encrypted, write-once S3 object bound to the persisted
canonical pull. BBS currently documents `match_id` and `kickoff_utc` but no MLB
`gamePk` or external-ID map, so every match row remains quarantined from
official identity credit. Team/time similarity is not promoted to an identity
join, including for doubleheaders. Any future game crosswalk must agree on the
official MLB game ID, both teams, and authoritative start time; ambiguous
doubleheaders remain quarantined.

Deployment authentication, account state, the standard response envelope, and
the match-data array type are release blocking. A live row shape or source label
that differs from the published BBS fields is fingerprinted and reported only
through value-free reason counts or a redacted attribution fingerprint, then
admitted solely as quarantined raw shadow evidence. Provider schema drift never
earns identity, completeness, prediction, or training credit and does not block
the canonical odds/lock/settlement deployment.

| BBS milestone | Required proof | ML effect |
|---|---|---|
| Authenticated | CI proves `/v1/user/me` and the filtered MLB match envelope using the exact secret without persisting account data or key material. | None |
| Shadow active | AWS proves the secret is scoped only to the audited pull and writes one immutable artifact for a canonical slot. | None |
| First bounded UTC-date probe | A canonical-slot-bound artifact preserves one documented UTC-date envelope without delaying or changing the odds pull. It explicitly makes no complete Eastern-slate claim. | Partial raw validation evidence only; official identity and slate coverage remain unsatisfied |
| Complete slate capture designed | A separately reviewed asynchronous capture queries every distinct UTC date represented by the official Eastern slate, merges and deduplicates provider match IDs, and remains bound to one canonical pull. | Defines the evidence needed before any multi-slate review milestone may be created |
| First complete shadow slate | Every official game has one exact, non-ambiguous provider crosswalk and every expected capture slot is present. | Validation evidence only |
| Seven-slate validation | Seven complete consecutive eligible slates pass availability, timeliness, identity, schema-drift, and missingness checks. | A human may review individual fields for a future schema version |
| Schema activated | A reviewed field mapping starts on the next complete future slate under a new cohort/feature version. | Only explicitly approved fields may earn completeness; historical artifacts are never retrofitted |

The existing 15/140/300/400/500 eligible-game milestones do not advance from
BBS artifacts until a new schema is activated. BBS documentation currently
does not prove production-ready FIP/xFIP/xERA, K-BB%, pitch mix/velocity,
bullpen availability/roles, or populated confirmed-lineup/injury feeds, so
those groups remain fail-closed.

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
