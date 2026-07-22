# Inqis tennis system — schedule-disabled canary

This directory is an independent AWS SAM application. It deliberately does not
import from `hello_world`, change the root SAM template, share MLB DynamoDB
tables, or package tennis code with an MLB Lambda.

## What the collector does when explicitly enabled

- Runs one EventBridge invocation on every UTC quarter-hour.
- Discovers active, non-outright tennis tournament keys dynamically.
- Uses The Odds API `events` endpoint to find match times before the collection
  window. Schedule discovery does not call the paid odds endpoint.
- Groups matches by their `America/New_York` match date.
- Opens a slate on the first quarter-hour at or after `first match - 8 hours`,
  latches it open, and stops when no pre-match events remain. It never starts
  earlier than the requested eight-hour lead.
- Calls the odds endpoint only for active slates and only for scheduled event
  IDs.
- Stores one canonical row per provider event ID per 15-minute slot only when
  at least one fresh two-sided book is usable. Empty/stale responses remain
  retryable and never poison the completion marker.
- Writes a retained, versioned, conditional-create ZSTD Parquet archive for
  every tournament attempt before recording its completion checkpoint.
- Builds deterministic, pre-match, market-only feature vectors and signal
  scores in a separate signal table.
- Preserves EventBridge's scheduled timestamp across delayed retries, rejects
  odds returned at or after match start, and keeps two retry attempts at both
  the EventBridge delivery and Lambda asynchronous-invocation layers.
- Checkpoints successful tournaments independently, so retrying one failed
  tournament does not purchase successful tournament requests again.
- Uses a conditional DynamoDB slot lease with an expiring owner token, so
  concurrent delivery cannot duplicate paid requests. It does not reserve
  Lambda concurrency from the capacity-constrained MLB account pool.
- Rejects missing, invalid, stale, future-skewed, and at/post-start bookmaker
  update timestamps and emits compact coverage, quota, cutoff, archive, and
  retry metrics.

The schedule design follows the provider's official v4 guide: the sports and
events endpoints do not count against usage quota, while odds requests do.
See <https://the-odds-api.com/liveapi/guides/v4/>.

## Current signal layer

The v0 research layer carries forward the existing Inqis market features:

- two-way no-vig multi-book consensus;
- full-window movement, velocity, acceleration, and deceleration;
- reversal count, compression, and book divergence;
- `STEAM`, `RESISTANCE`, `MOMENTUM`, `REVERSAL`, `CHAOS`,
  `CERTAINTY_ANCHOR`, and the existing score guard;
- descriptive `FAVORITE_FLIP` and `MARKET_AGREEMENT` tags.

`PUBLIC_FADE_CANDIDATE` remains market-movement-only. There is no verified
public-betting split feed yet. There is also no implemented `TRAP` signal.

## Deliberate safety gates

- The runtime is locked to `RULE_BASED_SHADOW`.
- It never returns a trained-model probability or publishes a pick.
- Movement scoring requires at least two pre-match observations and two books
  common across the series.
- A research vector is not feature-ready until it has 12 observations, at
  least three latest books, and no `CHAOS` tag.
- Live or post-start observations are excluded.
- If schedule discovery and the odds response disagree on start time, the
  earlier timestamp is the hard pre-match cutoff.
- The earliest start ever observed is persisted per provider event ID. A later
  schedule change cannot reopen a match that already crossed its cutoff.
- Replay feature builds use an explicit as-of cutoff, so future snapshots
  cannot leak backward into a historical feature vector.
- Doubles are disabled by default so singles and doubles are not silently mixed.

## Deployment boundary

The stack defaults `TennisScheduleState` to `DISABLED`. The EventBridge rule is
an explicit tennis-only resource, and deployment is not accepted unless AWS
reports that rule as disabled. The pull Lambda, DynamoDB tables, Parquet bucket,
secrets, logs, and alarms all live in `parlay-platform-tennis-canary-dev`.

This canary intentionally creates no SQS resources and configures no reserved
Lambda concurrency, so its restricted deployment role does not need
`sqs:CreateQueue` or Lambda concurrency-management permission. Failed
EventBridge delivery, Lambda errors and throttles, aging asynchronous events,
and events dropped after retry or age exhaustion remain covered by CloudWatch
alarms. There is no durable dead-letter replay queue in this canary; slot
idempotency and the next scheduled collection window provide recovery while
the collector remains in shadow mode.
Retained resources use `RetainExceptOnCreate`, preserving established data
while allowing CloudFormation to clean up an unsuccessful first create.

The workflow does not delete or reuse resources from the earlier failed
`parlay-platform-tennis-dev` create. The fresh canary stack name avoids that
boundary entirely; legacy cleanup remains a separate, manually reviewed task.

Use only a dedicated `TENNIS_ODDS_API_KEY`; never substitute the MLB
`ODDS_API_KEY`. The key is placed in a tennis-owned Secrets Manager secret and
only the pull Lambda can read it. A disabled infrastructure canary may be
deployed without that credential, but the collector then fails closed if it is
invoked.

The root deployment workflow has a positive path allowlist. Tennis files and
the tennis canary workflow cannot enqueue the MLB stack deploy. The canary
also fingerprints the production MLB CloudFormation stack before and after
the tennis deployment and fails if its template, parameters, outputs, status,
or resources change.

## Results-provider boundary

`TennisResultsProvider` defines provider-neutral phases, completion types, and
training eligibility. A separate manual-only BBS probe checks `/v1/sports` for
the exact `tennis` slug before it may touch a tennis results route. It does not
infer support from a title or from `table_tennis`.

BBS currently does not advertise tennis, so the expected authenticated result
is `UNSUPPORTED`. Only `READY` can enable result retrieval, and only
`FINAL + NORMAL + verified winner` can become a normal training label.
Retirement, walkover, default, abandonment, suspension, postponement, and
cancellation remain distinct.

Actual ML training remains blocked until tennis settlement handles finals,
walkovers, retirements, abandonments, and voids and a chronological labeled
dataset exists.

## Local validation

From this directory:

```bash
python -m pytest -q tests
sam validate --template-file template.yaml
sam build --template-file template.yaml
```

The checked-in canary workflow deploys only `parlay-platform-tennis-canary-dev` and
hardcodes `TennisScheduleState=DISABLED`. Enabling the schedule is a later,
separately reviewed operation after the disabled canary proof is clean.
