# Inqsi ARB Backlog

Priority order is dependency-driven. Do not lower correctness gates to make an item green.

1. **Provider/context integration**
   - Runtime-wire optional BBD identity/status context behind `ARB_BBD_ENABLED`.
   - Prove BBD authentication, schema, pagination, entitlement, IDs, status and timestamp behavior.
   - Keep The Odds API as sportsbook-price authority.

2. **Persistent coverage registry**
   - Record provider support, subscription access, current offering, ingestion health, parser support, settlement verification, freshness, first/last observation, last successful fetch, failure reason, and evidence.
   - Preserve unsupported/out-of-season/not-offered/access-denied/parser-missing/rules-pending as distinct states.

3. **Settlement-rule expansion**
   - Prioritize observed high-volume books, sports and markets.
   - Version by book, sport, market family and jurisdiction.
   - Cover overtime, draw, push, retirement, participation, pitcher, postponement, abandonment, dead heat and void behavior.

4. **Complex settlement-state engine**
   - Integer pushes/refunds.
   - Asian quarter lines and split stakes.
   - Exchange commission/liability/liquidity.
   - Dead heats and partial voids.
   - Stake increments, minimums, maximums, balances, currencies and known costs.

5. **Ingestion durability**
   - Immutable raw evidence snapshots.
   - Queue/checkpoint recovery where collection volume warrants it.
   - Idempotency, duplicate suppression and out-of-order protection.
   - Quota-aware adaptive scheduling and provider health telemetry.

6. **Product completion**
   - Saved filters, watchlists and favorites.
   - Price history and opportunity lifecycle views.
   - Alerts with expiry, deduplication, quiet hours and minimum actionable profit.
   - Coverage/health console for operators.
   - Desktop/mobile/browser/accessibility verification.

7. **Release proof**
   - Unit/property/integration/replay/browser/load/recovery tests.
   - Independent payout oracle for adverse cases.
   - p95 normalized-update-to-board and cached-API latency measurements.
   - Rollback/recovery exercise.
   - 20-minute post-deploy evidence.
   - 24-hour live production observation window.
