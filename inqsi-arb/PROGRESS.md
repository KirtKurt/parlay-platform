# Inqsi ARB Progress Log

## Production baseline
- INQSI-ARB-v3 is deployed as an isolated AWS service.
- Deterministic N-way arbitrage math.
- Automatic sport, event, bookmaker, and market discovery from the configured primary odds provider.
- Persistent audit history and position lifecycle.
- Per-book balance/limit feasibility analysis.
- WebSocket push and embedded UI.
- No automatic wager placement.

## Verified production inventory
- Active provider sport/competition keys observed in the production catalog: 86.
- Exact live sportsbook audit: 73 unique provider bookmaker keys across the initial audit regions `us,us2,uk,eu,au`.
- The sportsbook inventory endpoint fails closed if any sport scan fails; a partial scan is never presented as an exact count.

## Correctness hardening
- Mathematical arbitrage and verified arbitrage are separate states.
- Only settlement status `compatible` can produce `arb=true`.
- `provider_identity_only`, missing rules, and unknown settlement states remain detected/unverified.
- Quote freshness is fail closed: stale or untimestamped quotes are excluded before verified arb qualification.
- Default maximum quote age is 180 seconds and is configurable with `ARB_MAX_QUOTE_AGE_SECONDS`.

## Settlement-rule coverage
- Reviewed production rules currently seed DraftKings and FanDuel MLB full-game winner, spread/run-line, and total families.
- All unreviewed sportsbook/sport/market combinations remain UNKNOWN and cannot be called verified arbitrage.
- Rule expansion must use current sportsbook house-rule sources and preserve differences such as overtime, postponement, participation, retirement, shortened-game, and push handling.

## Provider configuration
- Primary provider: The Odds API, configured in production.
- Supplemental data providers are not enabled because credentials/commercial configuration are not currently present in this service.
- Future supplemental adapters must be isolated behind explicit config flags; missing supplemental credentials must not break the primary provider or unrelated ARB functionality.

## Next quality increments
1. Expand reviewed settlement-rule coverage across high-volume sportsbooks, sports, and market families.
2. Add supplemental provider adapters behind disabled-by-default config flags to close primary-provider coverage gaps.
3. Expand sportsbook inventory audits to additional permitted provider regions/exchanges after live validation.
4. Add cached inventory snapshots and freshness/latency telemetry to reduce upstream quota use.
5. Measure prospective opportunity survival, stale-price rate, second-leg failure, accepted-price slippage, and displayed-vs-reconciled profit.
6. Continue UI refinement for evidence, freshness, settlement state, and open-position recovery.

The system must remain fail closed, auditable, and isolated from KS1/MLB/Tennis/Soccer prediction authority.
