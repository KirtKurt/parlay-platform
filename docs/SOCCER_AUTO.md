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
- After opening, every game is collected at least every 15 minutes, every five
  minutes inside six hours of kickoff, and every minute while live/recently due.
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

Historical featured and additional-market backfills are resumable and archive
provider timestamps. They are opt-in at initial activation because the one API
subscription is shared with existing sports. Historical odds remain
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

Knockout competitions remain quarantined from supervised 1X2 training unless
regulation-time semantics are independently verified. Match scores cannot label
player, card, corner, first-half, or qualification props.

## LLM boundary

The daily LLM analyst uses Amazon Bedrock Nova 2 Lite through the US
cross-Region inference profile (`us.amazon.nova-2-lite-v1:0`). It receives only isolated
soccer coverage summaries, immutable feature-schema metadata, and model reports.
It may propose a small bounded hyperparameter search and diagnostics.

Every LLM response is treated as untrusted input. Code enforces numeric bounds,
deduplicates trials, and strips unknown controls. The LLM cannot write a match
prediction, alter a label or feature lock, promote a model, change deployment
resources, or access MLB/tennis state. Deterministic chronological evaluation is
the sole authority for accepting an LLM-proposed experiment. Its dedicated IAM
role can read only soccer diagnostics, write only the `LLM_ANALYSIS` operations
partition, and invoke only that inference profile and its underlying Nova 2 Lite
foundation-model resources.

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
  no-CI merge, only `deploy-soccer-auto.yml` is dispatched manually.
- The existing single Odds API credential is stored in the soccer stack's own
  secret. Costly calls fail closed until provider quota headers are known. An
  atomic soccer-only admission ledger prevents concurrent workers from racing
  past soccer's allowance. The initial configuration leaves 80% of credits
  untouched plus a 600-credit in-flight buffer; the percentage reserve is
  explicitly configurable during manual deployment.
- The new deployment workflow is manual-only during verification. Nothing in
  this build deploys or changes production automatically.

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
