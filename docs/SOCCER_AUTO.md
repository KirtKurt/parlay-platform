# Soccer Auto v1

`soccer_auto` is a standalone soccer data, LLM-research, and ML-prediction
system. It shares the existing all-sports The Odds API subscription, but it does
not share code, tables, queues, buckets, models, API routes, schedules, or a
CloudFormation stack with MLB, tennis, or the legacy soccer implementation.

## Collection contract

- Match-days use `America/New_York`, matching the platform's established daily
  scheduling convention.
- Free `/sports?all=true` and `/events` metadata discovery runs every 15 minutes
  so newly supported soccer leagues and newly posted fixtures are admitted
  automatically. Fixture calls run directly in the inventory Lambda, outside
  the paid-market work queue, so global odds fan-out cannot starve the T-10
  planner.
- For each local match-day, the earliest kickoff across every known active
  soccer league is calculated. Game bookmaker, market, and odds collection is
  blocked until exactly ten hours before that kickoff.
- A second gate runs inside the queue worker. A stale, retried, or manually
  submitted job cannot call market or odds endpoints early, and a job carrying
  an old kickoff revision is rejected.
- EventBridge checks the boundary every minute. The enforceable invariant is no
  early provider call; the first actual call and positive drift are persisted.
  Drift over two minutes is recorded as an SLA violation.
- After opening, every game is collected at least every 15 minutes and every
  five minutes inside six hours of kickoff. Match-market collection is strictly
  pre-match; score settlement continues after kickoff.
- A future fixture not seen for three consecutive inventory intervals is
  excluded from the day planner. A newly discovered earlier fixture opens the
  window immediately and records the resulting late-discovery drift.
- Once a match-day has made its first gated provider call, the persisted
  earliest kickoff/opening boundary may move earlier for a newly discovered
  game but never later merely because the original first game completed.
- The queue worker rejects and acknowledges stale game-data jobs at or after
  kickoff without making a paid provider call or poisoning the dead-letter
  queue.

## Coverage

The live source of truth is the provider catalog, not the static fallback list.
Every response whose group is `Soccer` or whose key begins with `soccer_` is
stored, including future leagues added by the provider.

For every event, market inventory is probed in one combined request across all
current bookmaker-region groups:

`us`, `us2`, `us_dfs`, `us_ex`, `uk`, `eu`, `fr`, `se`, and `au`.

The combined inventory call uses the endpoint's single-credit multi-region
request rather than nine separate calls. Bookmaker keys are de-duplicated for
reconciliation. Odds requests span all nine provider region groups, which
returns every sportsbook with far fewer HTTP calls than separate bookmaker
batches. Market and region batches are independently
bisected when the provider rejects a scope, preserving every valid remainder.
The inventory is a rolling 24-hour cumulative union, so a transiently absent
bookmaker or market is not forgotten while genuinely delisted scopes age out.
Runtime-discovered market keys always win over the static seed list, including
newly introduced player props in leagues outside the provider's currently
documented six-league prop set.

Raw payloads preserve every returned bookmaker, market, outcome, price, point,
link, source ID, limit, rotation number, and DFS multiplier in versioned S3.
Tournament outright data is collected on its own five-minute cadence. Scores
and completed results are permanently settled while they remain inside the
provider's three-day result window.

Historical featured and additional-market raw backfills are enabled by default,
resumable per league, monotonically checkpointed, and archive provider
timestamps. A kill switch leaves the function/status surface deployed while
disabling its schedules and paid work. Historical odds remain
training-ineligible unless a real result label exists: The Odds API does not
provide historical results, and odds must never be treated as labels.

## Prediction system

The production prediction target is calibrated three-class
`home / draw / away` probability.

1. Every pre-match response is stored as an immutable attempt and a
   deterministic canonical time slot.
2. At T-45, a one-time feature lock is built only from observations finalized
   before the lock.
3. The feature schema includes de-vigged 1X2 consensus and movement, bookmaker
   disagreement, totals and spread lines, league buckets, and hashed presence
   and movement features for every other dynamically returned market. This
   allows cards, corners, periods, team totals, correct score, player props, and
   future unknown market keys to contribute without changing the schema.
4. A deterministic residual softmax model learns corrections to the same-time
   market consensus.
5. Training, validation, and untouched audit sets are chronological and
   embargoed. Promotion requires positive lower-confidence log-loss skill over
   the market baseline, acceptable calibration, and at least 200 new
   prospective predictions. A champion must also be beaten on the same games.
6. Promotion is atomic and fail-closed. Accuracy is reported but never used as
   the sole promotion criterion.

Every event revision carries a full schedule-identity digest over sport, event,
kickoff, and both teams. Provider responses are revalidated after network I/O;
late or mismatched payloads are archived but never canonicalized. The first
champion evaluated for an event's T-45 public decision is immutably bound to
that lock. A later champion cannot repaint the pick, stale revisions are hidden
from the public API, and no public decision may be created after T-10.

Knockout competitions remain quarantined from supervised 1X2 training unless
regulation-time semantics are independently verified. Match scores cannot label
player, card, corner, first-half, or qualification props.

## LLM boundary

The LLM analyst runs hourly and uses a real Amazon Bedrock US cross-Region
profile chain: Nova 2 Lite, Nova Lite, then Nova Micro. A quota or transient
failure on one model immediately tries the next independently metered model.
It receives only isolated soccer coverage summaries, immutable feature-schema
metadata, and non-audit model metadata.
It may propose a small bounded hyperparameter search and diagnostics.

Every LLM response is treated as untrusted input. Code enforces numeric bounds,
deduplicates trials, and strips unknown controls. The LLM cannot write a match
prediction, alter a label or feature lock, promote a model, change deployment
resources, or access MLB/tennis state. Deterministic chronological evaluation is
the sole authority for accepting an LLM-proposed experiment. Its dedicated IAM
role can read only soccer diagnostics, write only the `LLM_ANALYSIS` operations
partition, and invoke only the three exact inference profiles and their exact
underlying Nova foundation-model resources.

A fresh validated analysis is reused without another model call. Deployment
forces a new Converse call and succeeds only after a provenance-signed,
digest-validated `LATEST` analysis is stored with the actual selected model,
context digest, clean stop reason, and token usage. Exhaustion of every fallback
is a visible Lambda failure rather than a false-green deferral. The analyst is
genuine adaptive research authority for bounded trial proposals, but its prose
or temporary unavailability cannot override deterministic promotion gates.

## Non-interference controls

- Separate stack: `parlay-platform-soccer-auto`.
- Separate DynamoDB tables, versioned S3 buckets, SQS/DLQ, API, alarms, model
  registry, and read routes under `/v1/soccer-auto/*`.
- No imports from `hello_world` or `tennis_learning` and no use of any existing
  physical table names.
- No reserved Lambda concurrency; the soccer SQS worker alone is capped at
  six concurrent consumers, well below the provider's account-wide request
  ceiling. A queue-age alarm fires if the oldest collection
  job remains pending for more than ten minutes across two evaluation periods;
  a separate depth alarm detects sustained fan-out above 1,000 pending jobs.
- After the initial release, the main deployment workflow ignores every
  soccer-only source, template, test, documentation, and deployment-workflow
  path. It deliberately does not ignore its own path, so future
  `deploy.yml`-only MLB/legacy repairs retain their original deployment
  behavior.
- The initial release also changes `deploy.yml` and adds branch guards to two
  write-capable MLB pull-request workflows. Those files must be published in a
  single merge commit whose message contains GitHub's native `[skip ci]`
  directive. Trigger-level suppression is required: merely skipping the main
  deploy job would still awaken its MLB `workflow_run` consumers. After that
  no-CI merge, soccer deployment was dispatched through its isolated workflow;
  the temporary marker-only push authorization below remains confined to that
  same workflow.
- The existing single Odds API credential is stored in the soccer stack's own
  secret. Costly calls fail closed until provider quota headers are known. An
  atomic soccer-only admission ledger prevents concurrent workers from racing
  past the currently observed shared-subscription balance. Coverage-first mode
  is now the deployment default: the percentage reserve is 0%, while the
  independent 2,000-credit in-flight race buffer remains enabled. The percentage
  reserve remains explicitly configurable during manual deployment. A block at
  that 0% boundary is recorded explicitly as `RACE_BUFFER_REACHED`, distinct
  from a configured percentage-reserve block.
- Shared-key protections remain active in coverage-first mode: the soccer SQS
  worker is capped at six concurrent consumers, every paid call must pass the
  atomic admission ledger, and provider HTTP 429 responses honor `Retry-After`
  with bounded retries. Every 429 attempt also writes a 30-day, non-secret
  diagnostic containing only its provider path, attempt number, retry delay,
  and observation time. Status and coverage expose a bounded rolling 24-hour
  count plus the latest rows. In addition, every soccer provider request attempt --
  including free catalog/event calls, live markets, scores, outrights,
  historical endpoints, and retry attempts -- must acquire an atomic lease in
  `SoccerOpsTable` before network I/O. The default and maximum soccer rate is
  three calls per second with burst capacity one. A single globally smoothed
  `next_allowed_ms` pointer spaces leases by at least 334 milliseconds, avoiding
  fixed-window boundary bursts and leaving normal request-rate capacity for the
  separately deployed MLB and tennis systems. If the lease table is missing,
  unavailable, too contended, or cannot grant a slot within eight seconds, the
  request fails closed and records or logs a bounded diagnostic; it never calls
  the provider without a lease. Paid-call admission conservatively reserves the
  maximum four-attempt credit cost up front, while each actual retry must still
  acquire its own distributed request permit. The status and coverage APIs expose the
  effective 0% percentage reserve, 2,000-credit race buffer, three-RPS cap, and
  burst-one policy without exposing the credential. MLB and tennis remain
  outside this soccer-only stack and ledger; the Odds API subscription and
  credential are still shared.
- Historical raw backfill is enabled by default. Its bounded, resumable
  per-league calls use the same distributed three-RPS limiter and shared-credit
  admission controls, and terminal cursors never wrap or replay. Historical
  odds rows remain training-ineligible until joined to an authoritative final
  result with point-in-time T-45 materialization; prices are never used as
  labels.
- This repair uses one exact marker-path push trigger so the connected GitHub
  publisher can run the verified deployment once. The marker and trigger are
  removed together after live verification; ordinary soccer source pushes do
  not deploy the stack, and the workflow then remains manual-only.

## Deployment recovery

The deploy identity must be allowed to provision every isolated resource in
this stack. In particular, the initial account policy must include the full
CloudFormation lifecycle for the two soccer-prefixed SQS queues and the
soccer-prefixed SNS alarm topic, plus create/read/delete access to the
soccer-prefixed CloudWatch dashboard; omitting any of those lifecycle actions
causes stack creation or rollback to fail before runtime activation.

If an initial creation reaches `ROLLBACK_COMPLETE` or `ROLLBACK_FAILED`, the next soccer deployment
records the exact `DELETE_SKIPPED` physical IDs in its GitHub Actions summary,
deletes only the failed `parlay-platform-soccer-auto` stack record, and then
recreates it. Durable tables, buckets, and the copied Odds API secret use
`RetainExceptOnCreate`: empty resources are removed on a failed initial create,
while established production data remains retained on a later stack deletion
or replacement.

## Read API

- `GET /v1/soccer-auto/status`
- `GET /v1/soccer-auto/predictions`
- `GET /v1/soccer-auto/coverage`
- `GET /v1/soccer-auto/models`

Coverage output includes known competitions, books, markets, historical
cursors, and persisted daily window timing (`first_kickoff`,
`scheduled_open_at`, `actual_first_provider_call_at`, `drift_ms`, and
`sla_state`).
