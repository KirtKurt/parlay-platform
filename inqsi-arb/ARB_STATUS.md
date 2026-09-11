# Inqsi ARB Status

Updated: 2026-09-11

Repository: `KirtKurt/parlay-platform`
Service: `INQSI-ARB-v3`
Primary odds authority: The Odds API
Supplemental context provider: Big Balls Data (optional, disabled/fail-closed when unavailable)
Production stacks: `inqsi-arb-prod`, `inqsi-arb-sportsbook-catalog-prod`

## Verified production baseline

- Deterministic N-way arbitrage calculation.
- Automatic sport/event/bookmaker/market discovery from the configured odds provider.
- Event-specific market enumeration that accepts only market keys actually returned by the provider.
- Strict separation of mathematical detection from settlement-verified arbitrage.
- Verified ARB requires compatible reviewed settlement rules.
- Stale or untimestamped quotes are excluded from verified ARBs.
- Persistent DynamoDB audit history and position lifecycle infrastructure.
- Persistent multidimensional coverage registry for provider support, access, current offering, ingestion, parser support, settlement verification and freshness.
- Per-book balance/limit feasibility and constrained stake optimization.
- Discrete stake minimum/increment/cap verification after continuous optimization.
- Calculation-only sportsbook-back/exchange-lay math with explicit commission, liquidity and liability constraints.
- WebSocket delivery and embedded ARB UI.
- Dedicated sportsbook inventory audit.
- No automatic wager placement.

Observed inventory evidence: 86 sport/competition keys and 73 unique provider bookmaker keys in the current audited region set (`us,us2,uk,eu,au`). These are provider-observed counts, not claims of every sportsbook worldwide.

The latest declared cached/read-only production latency proof measured 300 successful requests with 20 persistent concurrent clients and zero failures. Client-observed p95 was **198.957 ms**, passing the **500 ms** acceptance target without weakening the target.

## Settlement rules and state correctness

Reviewed production settlement coverage has expanded beyond the initial MLB seed into jurisdiction-scoped major-sport rules, including reviewed Fanatics New York full-game winner/spread/total profiles for supported major sports. Coverage remains intentionally fail-closed: any unreviewed sportsbook/sport/market/jurisdiction combination stays UNKNOWN and cannot be presented as a verified strict arbitrage.

The settlement-state verification layer now supports explicit exhaustive state universes, cent-rounded outcome P&L verification, refund/push states, dead-heat/partial-void representations, quarter-line split representation, and exchange commission/liability calculations. These capabilities do not imply that every sportsbook rule combination has been reviewed; rule breadth remains a separate qualification requirement.

## Operations controller and observation

- `inqsi-arb/ops/controller.py`
- `.github/workflows/inqsi-arb-controller.yml`
- `.github/workflows/inqsi-arb-repair.yml`
- `.github/workflows/inqsi-arb-observation-proof.yml`

The controller is bounded to Inqsi ARB and never writes application code, promotes prediction models, touches other sports systems or places wagers. It checks the live API, all-sports catalog, reviewed rules, audit-history contract, embedded UI capability, WebSocket/optimizer/completion health signals and exact sportsbook inventory. A failed core ARB health check may dispatch one bounded redeploy of the already-tested current `main`; correctness gates remain fail-closed.

The controller retains its scheduled cadence and also has a post-deploy liveness backstop after successful main-branch Inqsi ARB deployments. The observation proof is intentionally bounded: it discovers a real active event, proves event-specific market enumeration, performs a maximum-two-event read-only `h2h` scan, requires a genuine append-only SCAN audit event, and proves that same event is readable through `/v1/arb/history`.

BBD authentication/discovery is checked when credentials are available. Missing BBD credentials or entitlement degrades supplemental context only and must not break independent Odds API arbitrage scanning.

The 2026-09-11 bounded provider/context increment makes the optional BBD adapter reject malformed collection envelopes and mixed invalid rows instead of treating them as successful empty or partial results. Recognized empty collections remain valid, and access failures retain their existing reason. Validation: `python -m pytest -q inqsi-arb/tests` (129 passed) and `sam build --no-cached --template-file template.yaml` from `inqsi-arb` (succeeded; SAM global metadata writes were blocked by the read-only home filesystem). No live authenticated fixtures or entitlement proof were available for this increment. Runtime wiring, pagination and live identity/status/timestamp proof remain pending; this change does not qualify BBD context for settlement or price authority.

## Highest-priority remaining work

1. Wire the optional BBD context adapter into runtime endpoints only after credential and entitlement proof.
2. Continue reviewed settlement-rule expansion across observed books, jurisdictions, market families and periods.
3. Accumulate sustained live observation/audit evidence and add immutable raw-provider-bundle retention for stronger replay provenance.
4. Expand property/replay testing as new settlement profiles and market families are qualified.
5. Prove the separate end-to-end quote-update-to-board latency target under declared load; cached API latency is already qualified.
6. Complete browser/mobile/accessibility verification.
7. Exercise and document an explicit rollback/recovery drill without weakening fail-closed behavior.
8. Complete the required 24-hour production observation window.

Overall master acceptance status: **IN PROGRESS**. Completion is not claimed while internal requirements remain open or external coverage is unavailable.
