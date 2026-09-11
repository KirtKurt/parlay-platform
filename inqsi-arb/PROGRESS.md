# Inqsi ARB Progress

## Production baseline
- INQSI-ARB-v3 deterministic N-way arbitrage engine.
- Provider-discovered sports and bookmakers; no short hard-coded sportsbook allowlist.
- Automatic market discovery, persistent audit state, position lifecycle, per-book constraints, WebSocket push, and embedded UI.
- Settlement validation remains fail-closed: unreviewed sportsbook/sport/market combinations are detected but cannot be promoted to verified arbitrage.

## Current increment: sportsbook inventory observability
- Added live sportsbook inventory auditor.
- Added `/v1/arb/sportsbooks` API handler.
- Deduplicates provider `bookmaker.key` and `bookmaker.title` values.
- Tracks sports on which each bookmaker was observed.
- Audit returns `sportsbook_count`, `sport_count`, `sports_scanned`, `sports_failed`, `regions`, `audited_at`, and `complete`.
- Any sport-scan failure makes the audit incomplete instead of presenting a partial count as exact.
- Added unit tests and isolated SAM deployment workflow with live post-deploy assertions.

## Coverage policy
The active provider remains The Odds API. Core production credentials are configured. Any future supplemental provider must be isolated behind an explicit config flag and may not weaken canonical market identity or settlement validation.

## Credential/config stubs
Supplemental feed adapters are not enabled until credentials and commercial rights are configured. Missing supplemental credentials are non-fatal: the primary provider remains operational and the missing source is reported as unavailable rather than blocking unrelated ARB functionality.

## Remaining quality work
- Expand reviewed settlement-rule coverage sportsbook-by-sportsbook and market-family-by-market-family.
- Benchmark supplemental licensed feeds for books/markets not available from the primary provider.
- Add cached deep sportsbook audits so exact inventory scans do not consume unnecessary upstream quota.
- Continue prospective measurement of stale-quote rate, opportunity survival, second-leg failure, and displayed-vs-reconciled profit.
- Preserve isolation from KS1, MLB, Tennis, Soccer prediction authority and never place wagers automatically.
