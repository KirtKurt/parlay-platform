# MLB Historical Daily Optimizer — V15.11

## Production objective

V15.11 extends the V15.1 multi-league platform with a fail-closed MLB historical
training and promotion program. The objective is **daily complete-slate winner
accuracy**, not an 80% probability claim for any individual game.

A production candidate cannot activate unless all of the following are true:

- at least **1,000 settled MLB games in the training partition**;
- at least **200 settled games in whole-date walk-forward validation**;
- at least **200 settled games in a separate untouched audit partition**;
- at least 20 walk-forward dates and 15 untouched-audit dates;
- one winner prediction for every official game on every evaluated slate;
- at least **80% correct on every walk-forward and untouched-audit date**;
- non-degrading Brier score and log loss versus the incumbent baseline;
- bounded train/validation accuracy gap and immutable no-leakage evidence; and
- a digest-valid, versioned S3 experiment artifact bound to the DynamoDB
  champion record.

The system reports whether the mean daily rate reaches 90%, but the mandatory
promotion floor is 80% on every held-out day. It does not manufacture or label
an 80–90% result when unseen historical slates do not prove it.

## Relationship to the V15.1 master guide

The V15.1 guide defines The Odds API as the multi-sport odds ingress, identifies
MLB as prewired, and uses scheduled ingestion plus S3 snapshot archives. This
implementation preserves that architecture and adds the MLB-specific historical
collection, whole-slate objective, search, audit, and promotion contracts. The
1:00 a.m. collection start, exact 15-minute cadence, 1,000-game training floor,
and 80% daily promotion gate are V15.11 extensions rather than claims made by
the original guide.

## Historical collection contract

For each Eastern Time MLB slate date:

1. Read the official final schedule and settled winner labels.
2. Build a request grid beginning at **01:00 America/New_York**.
3. Advance in exact **15-minute intervals**.
4. Continue through the last grid point at or before the final game's T-minus-45
   lock.
5. Fetch only `baseball_mlb`, `regions=us`, and `markets=h2h` historical odds.
6. Archive each raw provider response to an immutable, versioned S3 key before
   advancing the DynamoDB cursor.
7. Match provider events to official games using normalized teams, start time,
   and official identity safeguards for doubleheaders.
8. Clip every individual game's features at that game's own T-minus-45 boundary.
   A later sport-level snapshot can support a later game but can never leak into
   an earlier game's feature vector.
9. Reject the entire date from eligible training evidence unless every official
   game has the required pull depth and exact prediction coverage.

## Paid-data safety

The planning phase makes no paid historical calls. It enumerates the complete
configured date range, requires every date to resolve against the official MLB
schedule (including confirmed off-days), calculates every planned timestamp,
estimates the request credits, reads the provider quota headers, and creates:

- a digest of the exact per-date request ledger with zero unresolved schedule dates;
- a fingerprint of the full plan and hard credit cap; and
- an explicit authorization state.

Any unresolved official-schedule date blocks authorization. Paid calls begin only
when the caller supplies the exact plan fingerprint and the literal confirmation
contract. Every subsequent date and timestamp is checked
against that ledger. Changing the ledger, plan fingerprint, date range, interval,
or request count fails closed. Re-deploying the code resumes the existing cursor
and cannot replace an already authorized plan after collection has started.

## Feature and search space

The optimizer evaluates the incumbent baseline first, structured market-only
variants next, and then a deterministic randomized search of up to 25,000 unique
signal-weight combinations per round. The searched dimensions include:

- full-window market movement and clipping;
- underdog-specific movement response and cap;
- heavy-favorite penalty;
- book divergence threshold, penalty, and cap;
- reversal penalty and cap;
- low-pull-depth shrinkage;
- 60-minute velocity;
- 180-minute acceleration and volatility;
- full-window coverage shortfall;
- home, favorite, and underdog bias;
- edge, expected-value, and movement score weights; and
- value, heavy-favorite, divergence, and reversal score adjustments.

Candidates are ranked by daily pass rate, minimum daily accuracy, mean daily
accuracy, all-game accuracy, Brier score, and log loss—in that order. Individual
game correctness alone cannot win the search when full-day behavior is worse.

## Walk-forward and untouched-audit discipline

All partitions are chronological and split only on complete slate dates. The
search may inspect training and walk-forward labels while adjusting weights. It
selects one winner before reading the untouched audit labels.

When a candidate fails the audit:

1. that audit window is permanently recorded as evaluated;
2. its dates can join development evidence in a later round;
3. the collector advances to strictly later slate dates;
4. at least 200 games and 15 dates are accumulated in a new audit window; and
5. any attempt to reuse an evaluated audit date raises a hard orchestration
   error.

The paid request authorization covers the full planned date-range ledger, so a
fresh audit round cannot silently spend beyond the reviewed plan.

## Runtime authority and irreversible production cutover

V15.10 remains the production MLB winner authority only while DynamoDB positively
confirms that both the historical champion and the production-cutover marker are
absent. The V15.11 loader is installed as the outer wrapper, but it remains inert
before the first promotion.

The first valid promotion uses one DynamoDB transaction to write both:

- the digest-valid historical champion pointer in the dedicated
  `MLB_HISTORICAL_CHAMPION#V1` partition; and
- a write-once `PRODUCTION_CUTOVER` marker in the separate
  `MLB_HISTORICAL_PRODUCTION_CUTOVER#V1` partition declaring
  `HISTORICAL_DAILY_OPTIMIZER_ONLY` authority.

The optimizer role can delete only its lease/state partition. It can read and
write the champion and cutover records, and can create them transactionally, but
its IAM policy does not grant deletion against either authority partition.

After that atomic cutover:

- the historical policy is the sole final home/away direction authority for new
  predictions;
- V15.10 has no automatic fallback path and cannot change the selected team;
- the prior source/model may remain quarantined only for feature extraction,
  diagnostics, immutable audit, and an explicitly reviewed emergency rollback;
- deleting, corrupting, or losing either the champion or cutover record causes a
  fail-closed runtime error rather than restoring V15.10;
- immutable locked predictions are never rewritten by a later activation;
- the result explicitly states that the 80% evidence applies to a complete-day
  slate, not an individual game's probability; and
- automatic wagering remains disabled.

Here, "destroy the existing algorithm" means permanently removing V15.10's
production-selection authority at successful cutover. Its source is not erased
before the new champion is proven, because doing so would eliminate auditability
and the possibility of an explicitly reviewed disaster-recovery deployment.

## AWS resources

The separate `parlay-platform-mlb-historical-optimizer` SAM stack creates:

- one reserved-concurrency Lambda for planning, collection, optimization, and
  read-only status;
- a versioned, encrypted, public-blocked S3 evidence bucket;
- a five-minute EventBridge resume schedule;
- a read-only API status route; and
- a CloudWatch error alarm.

State, the champion pointer, and the write-once production-cutover marker are
stored in the existing `parlay_platform_snapshots` table under separate partition
keys. The explicitly dispatched deployment workflow first updates the production
`parlay-platform-dev` stack so its MLB Lambda ZIP contains the fail-closed
historical runtime loader. Only after that guard is verified may the separate
optimizer stack be deployed, planned, and authorized.

## Deployment and paid-call authorization

Pull-request and `main` push events run validation and clean builds only. They do
not deploy AWS resources and do not call The Odds API historical endpoint.
Production actions require `workflow_dispatch` and exact confirmation literals:

- `INSTALL_FAIL_CLOSED_HISTORICAL_RUNTIME_GUARD` installs and verifies the outer
  production authority loader;
- `AUTHORIZE_THE_ODDS_API_HISTORICAL_CREDITS` authorizes the immutable paid-data
  ledger; and
- `DESTROY_V15_10_PRODUCTION_AUTHORITY_AFTER_80_PERCENT_GATE` authorizes the
  future atomic production cutover, but only if the evidence gate later passes.

The runtime guard must be deployed in the same dispatch before the optimizer
stack can run. The workflow shares the production CloudFormation concurrency
lock, verifies the deployed Lambda ZIP, builds a zero-paid-call plan, fingerprints
its complete date ledger, and makes no paid request unless both paid-data and
cutover confirmations match exactly.

Deployment of code is not evidence of model quality. Until a real entitled
historical pull produces at least 1,400 eligible games and the 80% every-day
walk-forward/audit contract passes, no historical champion exists and V15.10
remains the reviewed incumbent.

## Operational states

- `PLANNING`: no paid historical requests are allowed.
- `READY_FOR_AUTHORIZATION`: plan, quota, and hard cap passed.
- `BACKFILLING`: authorized, resumable historical collection is active.
- `PAUSED_QUOTA`: hard cap or provider reserve stopped paid requests.
- `OPTIMIZING`: sufficient eligible games are being searched.
- `CANDIDATE_REJECTED`: no candidate passed and no further authorized evidence
  remains.
- `DATA_RANGE_EXHAUSTED`: the planned historical range ended before another
  valid round.
- `PROMOTED`: a digest-valid candidate passed every production gate and the
  champion plus write-once historical-only cutover were committed atomically.

`PROMOTED` is the only state that creates live historical authority. Deployment,
planning, training-set size, a strong in-sample result, or a high average across
selected days is not sufficient.
