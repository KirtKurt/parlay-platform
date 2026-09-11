# Inqsi ARB Backlog

Priority order is dependency-driven. Do not lower correctness gates to make an item green.

1. **Provider/context integration**
   - Runtime-wire optional BBD identity/status context behind `ARB_BBD_ENABLED`.
   - Prove BBD authentication, schema, pagination, entitlement, IDs, status and timestamp behavior.
   - Keep The Odds API as sportsbook-price authority.
   - 2026-09-11 increment: BBD collection parsing now rejects unknown envelopes and non-object rows with `BBD_COLLECTION_SCHEMA_INVALID`; recognized empty collections remain valid. Offline regression coverage exercises health, sports and events, plus access-failure reasons.
   - 2026-09-11 identity increment: BBD event collections now fail closed on missing, malformed or conflicting identity aliases (`BBD_EVENT_IDENTITY_INVALID`) and repeated normalized IDs (`BBD_EVENT_IDENTITY_DUPLICATE`), returning no partial context. Opaque string IDs and integer IDs (including zero) are preserved; null aliases may fall back to a valid alias. Offline fixtures validate this local contract only. Validation: all 269 ARB tests passed (`PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider inqsi-arb/tests`); ARB `sam build --no-cached --template-file template.yaml` succeeded, with global metadata writes blocked by the read-only home filesystem.
   - Blocker: authenticated live fixtures and entitlement proof are not available for this increment. Runtime wiring remains pending; local schema tests do not prove live authentication, pagination, event identity, status or timestamps.

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
