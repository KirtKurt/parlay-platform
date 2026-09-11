# Inqsi ARB Acceptance Checklist

| Requirement | Evidence | Status | Remaining dependency |
|---|---|---|---|
| Primary sportsbook-price authority | `src/provider.py`, production health | PASS | none |
| All-sports discovery | provider `all=true`, `/v1/arb/catalog` | PASS | continuous observation |
| Exact sportsbook inventory audit | `sportsbook_catalog.py`, dedicated production stack | PASS | provider coverage limits remain external |
| Deterministic N-way math | `arb_engine.py`, tests | PASS | complex settlement states separate |
| Strict settlement fail-closed | `rules.py`, `validation.py`, `arb_engine.py` | PASS | rule breadth incomplete |
| Quote freshness fail-closed | `freshness.py`, validation tests | PASS | venue/phase-specific thresholds remain |
| Balance/limit constrained staking | `constraints.py`, API smoke tests | PASS | exchange/FX/increment extensions remain |
| Persistent scan/position evidence | DynamoDB audit/state stores | PARTIAL | immutable raw provider bundles remain |
| Position completion assistant | `lifecycle.py`, position API smoke tests | PASS | richer reconciliation remains |
| Optional BBD adapter | `bbd_provider.py` | PARTIAL | runtime wiring + credential/entitlement proof |
| 20-minute production controller | controller code/workflow | IN REVIEW | merge + production workflow proof |
| Bounded same-version repair | repair workflow | IN REVIEW | merge + controlled repair proof |
| Multidimensional coverage registry | planned | NOT YET | implementation required |
| Every observed market family settlement adapter | catalog + rules registry | PARTIAL | many combinations intentionally UNKNOWN |
| Broad adverse-case zero-known-false suite | tests | PARTIAL | property/replay suite expansion required |
| p95 update-to-board <=2s | not measured | NOT YET | declared load + harness |
| p95 cached API <=500ms | not measured | NOT YET | declared load + harness |
| Desktop/mobile journeys | embedded UI | PARTIAL | browser/accessibility proof |
| Recovery and rollback | deploy + bounded repair | PARTIAL | explicit rollback exercise |
| 20-minute post-deploy check | scheduled controller | IN REVIEW | production merge/proof |
| 24-hour production observation | not complete | NOT YET | elapsed live observation |

Completion must not be declared while internal requirements are incomplete. Provider-unavailable sports/books/markets must be reported as source-unavailable rather than implemented coverage.
