# ARB Status

Updated 2026-09-11.

Repository: KirtKurt/parlay-platform.
Service: INQSI-ARB-v3.
Primary odds source: The Odds API.
Production stacks: inqsi-arb-prod and inqsi-arb-sportsbook-catalog-prod.

Current production capabilities:
- deterministic N-way arbitrage calculation
- settlement-rule fail-closed qualification
- stale quote suppression
- audit history and position lifecycle
- constrained stake optimization
- WebSocket delivery and embedded UI
- live sportsbook inventory audit

Observed production inventory proof: 86 sports or competition keys and 73 unique bookmaker keys in the current audited region set.

Autonomous operations controller:
- inqsi-arb/ops/controller.py
- .github/workflows/inqsi-arb-controller.yml
- .github/workflows/inqsi-arb-repair.yml

The controller checks production health, full sports discovery, settlement rules, sportsbook inventory, and BBD authentication/discovery when a BBD key is configured. A production ARB failure can dispatch one bounded repair of current main. Missing BBD credentials are reported as an external dependency and do not stop independent Odds API comparisons.

Highest priority remaining work:
1. Complete BBD event identity/status adapter behind a config flag.
2. Persist multidimensional coverage inventory.
3. Expand reviewed settlement-rule coverage.
4. Expand complex settlement-state solvers and adverse-case tests.
5. Produce measured load/latency and 24-hour production observation evidence.

Overall master acceptance status: IN PROGRESS.
