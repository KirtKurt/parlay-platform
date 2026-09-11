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
- Strict separation of mathematical detection from settlement-verified arbitrage.
- Verified ARB requires compatible reviewed settlement rules.
- Stale or untimestamped quotes are excluded from verified ARBs.
- Persistent audit history and position lifecycle.
- Per-book balance/limit feasibility and constrained stake optimization.
- WebSocket delivery and embedded ARB UI.
- Dedicated sportsbook inventory audit.
- No automatic wager placement.

Observed inventory evidence: 86 sport/competition keys and 73 unique provider bookmaker keys in the current audited region set (`us,us2,uk,eu,au`). These are provider-observed counts, not claims of every sportsbook worldwide.

## Settlement rules

Reviewed production settlement coverage has expanded beyond the initial MLB seed into jurisdiction-scoped major-sport rules. Coverage remains intentionally fail-closed: any unreviewed sportsbook/sport/market/jurisdiction combination stays UNKNOWN and cannot be presented as a verified strict arbitrage.

## Runtime operations controller

- `inqsi-arb/ops/controller.py`
- `.github/workflows/inqsi-arb-controller.yml`
- `.github/workflows/inqsi-arb-repair.yml`

The runtime controller runs on a 20-minute cadence, checks the live ARB API, all-sports catalog, reviewed rules, exact sportsbook inventory, and records evidence. A failed core health check may dispatch one bounded redeploy of the tested current `main`.

## Autonomous engineering controller

- `inqsi-arb/ops/engineering_controller.py`
- `.github/workflows/inqsi-arb-engineering-controller.yml`
- `inqsi-arb/ARB_ENGINEERING_CONTROLLER.md`

`INQSI-ARB-AEC-v2` is the higher-level bounded engineering controller. It runs every 20 minutes, inspects live AWS/GitHub release evidence, prioritizes the next safe action, dispatches controlled repair/redeploy work for production failures, tracks failed acceptance gates, and retains an immutable controller report artifact.

The AEC has a one-action-per-run default budget, an operator pause control, an ARB-only path allowlist, and explicit isolation from MLB/Tennis/Soccer/KS1. Arbitrary code-authoring is available only through a separately authorized code-agent runner configured by credential/model/command; if that runner is absent, the controller records the blocker rather than pretending code generation is active.

BBD authentication/discovery is checked when credentials are available. Missing BBD credentials or entitlement degrades supplemental context only and must not break independent Odds API arbitrage scanning.

## Highest-priority remaining work

1. Activate/prove the authorized external code-agent runner for open-ended autonomous feature implementation if credentials are available.
2. Wire the optional BBD context adapter into runtime endpoints after credential/entitlement proof.
3. Continue reviewed settlement-rule expansion across observed books and market families.
4. Expand immutable raw evidence and quota-aware ingestion durability.
5. Complete browser/mobile/accessibility verification and 24-hour production observation evidence.

Overall master acceptance status: **IN PROGRESS**. Completion is not claimed while internal requirements remain open or external coverage is unavailable.
