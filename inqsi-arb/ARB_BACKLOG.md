# Inqsi ARB Backlog

Priority order is dependency-driven. Do not lower correctness gates to make an item green.

1. **Provider/context integration**
   - Runtime-wire optional BBD identity/status context behind `ARB_BBD_ENABLED`.
   - Prove BBD authentication, schema, pagination, entitlement, IDs, status and timestamp behavior.
   - Keep The Odds API as sportsbook-price authority.
   - 2026-09-11 increment: BBD collection parsing now rejects unknown envelopes and non-object rows with `BBD_COLLECTION_SCHEMA_INVALID`; recognized empty collections remain valid. Offline regression coverage exercises health, sports and events, plus access-failure reasons.
   - 2026-09-11 authentication-transport increment: BBD requests reject HTTP 301/302/303/307/308 redirects with `BBD_REDIRECT_NOT_ALLOWED`, preventing bearer-token forwarding. Offline urllib transport tests cover same-origin, cross-origin, relative, HTTPS-to-HTTP and missing-Location responses across health, sports and events, plus the direct authenticated request contract. All 324 ARB tests pass; ARB SAM build succeeds. This is independent authentication safety work, not live authentication or entitlement proof.
   - 2026-09-11 destination-validation increment: BBD rejects non-HTTPS or malformed base URLs, embedded user credentials, query/fragment delimiters, whitespace/control characters and backslashes with `BBD_BASE_URL_INVALID` before creating an authenticated request. Offline tests cover health, sports and events and preserve valid HTTPS custom ports/base paths. All 380 ARB tests pass; ARB SAM build succeeds (global SAM metadata-cache write is blocked by the sandbox). Live credentials/entitlement fixtures remain unavailable; this increment proves only local destination safety and does not complete runtime wiring or live provider proof.
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
