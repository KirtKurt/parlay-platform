# ARB Backlog

Priority order:

1. BBD provider adapter
   - authenticate against user/me
   - discover sports and matches
   - preserve BBD IDs, source metadata, timestamps, status, participants
   - never replace The Odds API as odds authority unless separately configured
   - continue safely when BBD credentials are absent

2. Coverage registry
   - provider support
   - subscription access
   - current offering
   - ingestion health
   - parser support
   - settlement verification
   - freshness
   - first/last observation and failure reason

3. Settlement-rule expansion
   - high-volume books first
   - sport and market-family specific rule versions
   - overtime, draw, push, retirement, participation, pitcher, postponement, abandonment, dead-heat and void handling

4. Complex contract solver
   - pushes/refunds
   - quarter lines
   - exchange commission and lay liability
   - dead heats and partial voids
   - integer stake increments and caps

5. Ingestion architecture
   - durable raw snapshots
   - queue/checkpoint recovery
   - idempotency and out-of-order protection
   - quota-aware adaptive scheduling

6. Product completion
   - saved filters and watchlists
   - price history
   - alerts with expiry/deduplication
   - operator coverage/health console
   - mobile/desktop accessibility verification

7. Release proof
   - property/integration/replay/load/recovery coverage
   - p95 latency measurements
   - rollback/recovery proof
   - 20-minute post-deploy durable check
   - 24-hour production observation window
