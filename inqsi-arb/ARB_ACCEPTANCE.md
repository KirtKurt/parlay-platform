# ARB Acceptance Checklist

| Requirement | Evidence location | Status | Remaining dependency |
|---|---|---|---|
| Primary odds authority configured | inqsi-arb/src/provider.py, production health | PASS | none |
| All-sports discovery supported | provider list_sports(all=true), catalog endpoint | PASS | continuous refresh still required |
| Live sportsbook inventory audit | sportsbook_catalog.py and dedicated production stack | PASS | broaden region audit after validation |
| Deterministic N-way math | arb_engine.py and tests | PASS | complex settlement states pending |
| Strict settlement fail-closed | rules.py, validation.py, arb_engine.py | PASS | rule breadth incomplete |
| Quote freshness fail-closed | freshness.py and validation.py | PASS | venue/phase-specific thresholds pending |
| Stake constraints | constraints.py and API smoke | PASS | exchange liability/currency extensions pending |
| Persistent decision evidence | audit_store.py and history endpoint | PARTIAL | immutable raw provider bundles pending |
| Position lifecycle | lifecycle.py, position_store.py | PASS | richer accepted-price reconciliation pending |
| BBD authenticated context | autonomous controller probe | BLOCKED/IN PROGRESS | key/entitlement must be proven in CI |
| Coverage registry with support/access/offering/parser/rules/freshness dimensions | planned | NOT YET | implementation required |
| Every observed market family parser/settlement adapter | market catalog + rules | PARTIAL | many families remain unverified |
| Adverse-case zero false strict classifications | tests | PARTIAL | required broad cases pending |
| p95 update-to-board <=2s | not yet measured | NOT YET | load harness and live observation |
| p95 cached API <=500ms | not yet measured | NOT YET | load harness and declared load |
| Desktop/mobile journeys | embedded UI | PARTIAL | browser automation/accessibility pending |
| Recovery and rollback proof | deploy workflow + bounded repair | PARTIAL | explicit rollback exercise pending |
| 20-minute post-deploy durable check | planned controller follow-up | NOT YET | workflow scheduling implementation |
| 24-hour production observation | not yet complete | NOT YET | elapsed live observation required |

Completion cannot be declared until remaining internal requirements are implemented or explicitly documented as external blockers.
