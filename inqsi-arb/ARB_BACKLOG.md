# Inqsi ARB Backlog

Priority order is dependency-driven. Do not lower correctness gates to make an item green.

1. **Provider/context integration**
   - Runtime-wire optional BBD identity/status context behind `ARB_BBD_ENABLED`.
   - Prove BBD authentication, schema, pagination, entitlement, IDs, status and timestamp behavior.
   - Keep The Odds API as sportsbook-price authority.
   - 2026-09-11 increment: BBD collection parsing now rejects unknown envelopes and non-object rows with `BBD_COLLECTION_SCHEMA_INVALID`; recognized empty collections remain valid. Offline regression coverage exercises health, sports and events, plus access-failure reasons.
   - 2026-09-11 authentication-transport increment: BBD requests reject HTTP 301/302/303/307/308 redirects with `BBD_REDIRECT_NOT_ALLOWED`, preventing bearer-token forwarding. Offline urllib transport tests cover same-origin, cross-origin, relative, HTTPS-to-HTTP and missing-Location responses across health, sports and events, plus the direct authenticated request contract. All 324 ARB tests pass; ARB SAM build succeeds. This is independent authentication safety work, not live authentication or entitlement proof.
   - 2026-09-11 HTTPS-configuration increment (item 1 only): authenticated BBD requests now reject non-HTTPS or malformed base URLs, embedded user information, query/fragment components, backslashes, whitespace/control characters and invalid ports with `BBD_BASE_URL_INVALID` before opening transport. Offline tests cover health, sports and events and preserve valid HTTPS host, port and path-prefix configurations. Validation: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider inqsi-arb/tests` (390 passed); `SAM_CLI_TELEMETRY=0 sam build --no-cached --template-file template.yaml` from `inqsi-arb` (exit 0, build succeeded; global SAM metadata writes blocked by the read-only home filesystem). This independent safeguard does not establish live authentication or entitlement, qualify context, or enable runtime wiring. The Odds API remains the sportsbook-price authority.
   - 2026-09-11 response-decoding increment: malformed UTF-8 now returns `BBD_REQUEST_FAILED` instead of escaping adapter error handling. Offline transport regressions cover invalid UTF-8, truncated JSON and non-JSON bodies across health, sports and events, checking response closure and no returned context. All 339 ARB tests pass; ARB SAM build succeeds (global metadata writes are blocked by the read-only home filesystem).
   - 2026-09-12 JSON-ambiguity increment (item 1 only): BBD transport now rejects duplicate object keys (including nested objects and equivalent escaped keys) and nonstandard unquoted `NaN`/`Infinity`/`-Infinity` constants with `BBD_RESPONSE_JSON_INVALID`, returning no context. Offline health, sports and events regressions verify failure reasons and response closure; valid separate objects and quoted strings remain accepted. Validation: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider inqsi-arb/tests` (432 passed); `SAM_CLI_TELEMETRY=0 PYTHONDONTWRITEBYTECODE=1 sam build --no-cached --template-file template.yaml` from `inqsi-arb` (build succeeded; global SAM metadata writes blocked by the read-only home filesystem). This independent parsing safeguard does not establish live provider behavior or enable runtime wiring. The Odds API remains the sportsbook-price authority.
   - 2026-09-12 PR #831 reconciliation: preserve current-main HTTPS, response-decoding and JSON-ambiguity safeguards; reject malformed DNS/IP authorities and encoded hostnames before constructing authenticated requests. ASCII punycode, valid IPv4/IPv6, custom ports and path prefixes remain supported. All 495 ARB tests pass locally on Python 3.12; hosted SAM validation/build and fresh review remain required. This is offline transport validation, not live provider qualification.
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
