# InQsi Build Progress Without Odds API Dependency

This tracks what was built without requiring live Odds API ingestion.

## Deferred because Odds API is required

1. Confirm Odds API ingestion is fully working for all target sports.
2. Normalize live multi-book market data across Fanatics, DraftKings, FanDuel, and other books.
3. Store scheduled T1/T2/T3/T4/T5 snapshots from live provider pulls.

These require the provider key, feed validation, and live multi-book responses. No fake market data is used.

## Built now without Odds API

4. Production signal-engine framework
   - Added backend scoring logic that can score caller-supplied verified market snapshots.
   - If no verified snapshots are present, it returns `MARKET_DATA_REQUIRED` instead of a fake grade.

5. Actual slip scanner endpoint
   - `POST /v1/scanner/scan`
   - `POST /v1/slips/{slipId}/scan`
   - `GET /v1/slips/{slipId}/scan-history`

6. Parlay builder endpoint
   - `POST /v1/parlays/build`
   - Refuses to build unless verified candidate legs are supplied.

7. Member saved slips connected to scan history
   - Scanner results are appended into `scan_history` on saved slips.
   - `last_scan` is stored for quick retrieval.

8. Subscription enforcement / paywall logic
   - Scanner and builder routes require member access.
   - Access is allowed for TRIAL, ACTIVE, ADMIN/OWNER, or ACTIVE/TRIALING subscription records.
   - `GET /v1/subscriptions/access` reports current access state.

9. Result grading and learning loops
   - Existing grading path was extended with `learning_feedback`.
   - Added `POST /v1/results/grade` owner/admin route.

10. Monitoring, error alerts, and daily data-quality checks
   - Added `GET /v1/monitoring/data-quality` owner/admin route.
   - Checks saved slip compliance, scan history gaps, incomplete/past-due subscriptions, and skips Odds API freshness checks until provider data is live.

## Frontend connection

- The AI Slip Scanner page now includes a backend-connected client component.
- It calls `/v1/scanner/scan` and saves the slip when configured with `NEXT_PUBLIC_INQSI_API_URL` and a member ID.

## Guardrails

- No fake scores.
- No default zeros.
- No odds-only build.
- If required live market data is missing, the API returns an explicit data-required status.
