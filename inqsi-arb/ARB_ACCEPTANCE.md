# Inqsi ARB Acceptance Checklist

| Requirement | Evidence | Status | Remaining dependency |
|---|---|---|---|
| Primary sportsbook-price authority | `src/provider.py`, production health | PASS | none |
| All-sports discovery | provider `all=true`, `/v1/arb/catalog` | PASS | continuous observation |
| Exact sportsbook inventory audit | `sportsbook_catalog.py`, dedicated production stack | PASS | provider coverage limits remain external |
| Automatic event-specific market enumeration | `market_discovery.py`, `/v1/arb/markets` | PASS | continuous live evidence across more sports |
| Deterministic N-way math | `arb_engine.py`, tests | PASS | none for base N-way calculation |
| Explicit settlement-state P&L verifier | settlement-state engine + adverse-case tests | PASS | every book/market profile still requires review |
| Strict settlement fail-closed | `rules.py`, `validation.py`, `arb_engine.py` | PASS | rule breadth incomplete |
| Quote freshness fail-closed | `freshness.py`, validation tests | PASS | venue/phase-specific thresholds remain |
| Balance/limit constrained staking | `constraints.py`, API smoke tests | PASS | FX and additional venue constraints remain |
| Discrete stake minimum/increment/cap verification | `stake_rounding.py`, tests | PASS | expand book-specific stake metadata |
| Sportsbook-back/exchange-lay calculation | `exchange_engine.py`, calculator API | PASS | live exchange feed/entitlement not claimed |
| Persistent scan/position evidence | DynamoDB audit/state stores | PARTIAL | latest deploy proof had zero SCAN history rows; bounded observation proof added to accumulate genuine rows |
| Position completion assistant | `lifecycle.py`, position API smoke tests | PASS | richer reconciliation remains |
| Optional BBD adapter | `bbd_provider.py` | PARTIAL | runtime wiring + credential/entitlement proof |
| 20-minute production controller | controller code/workflow | PARTIAL | scheduled-run evidence was not yet visible; post-deploy liveness backstop added |
| Bounded same-version repair | repair workflow | PARTIAL | controlled repair exercise remains |
| Multidimensional coverage registry | `coverage_store.py`, `coverage_snapshot.py` | PASS | sustained production snapshots remain operational evidence |
| Every observed market family settlement adapter | catalog + rules registry | PARTIAL | many combinations intentionally UNKNOWN |
| Broad adverse-case zero-known-false suite | deterministic/property/regression tests | PARTIAL | continue replay expansion with each new rule family |
| p95 update-to-board <=2s | separate end-to-end path not yet measured | NOT YET | declared load + end-to-end harness |
| p95 cached API <=500ms | production latency proof: 300 requests, 20 persistent clients, p95 198.957 ms, 0 failures | PASS | continue post-deploy regression proof |
| Calculator endpoints production proof | `.github/workflows/inqsi-arb-calculator-proof.yml` | PARTIAL | first successful automatic post-deploy proof after workflow merge remains |
| Desktop/mobile journeys | embedded UI | PARTIAL | browser/accessibility proof |
| WebSocket push configuration | production health + deploy UI smoke | PASS | sustained delivery observation |
| Recovery and rollback | deploy + bounded repair | PARTIAL | explicit rollback exercise |
| Post-deploy controller check | controller `workflow_run` backstop | PARTIAL | first successful backstop run remains |
| Hourly genuine audit observation | `.github/workflows/inqsi-arb-observation-proof.yml` | PARTIAL | first scheduled production proof remains |
| 24-hour production observation | not complete | NOT YET | elapsed live observation |

Completion must not be declared while internal requirements are incomplete. Provider-unavailable sports/books/markets must be reported as source-unavailable rather than implemented coverage. Unknown settlement combinations remain fail-closed and cannot be promoted merely to satisfy a status check.
