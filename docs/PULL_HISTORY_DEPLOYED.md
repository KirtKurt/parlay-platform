# InQsi 15-Minute Pull-History Algorithm Deployment

This deployment adds the production starting layer for the new algorithm architecture. The algorithm no longer depends on fixed T1/T2/T3 snapshots as the primary model input. It calculates from many timestamped pull runs, designed for 15-minute odds pulls.

## Live Odds API status

Live Odds API ingestion remains intentionally guarded. This layer can operate now from manual or provider-shaped pull payloads. It does not pretend that Odds API ingestion is operational.

## Core endpoints

All routes are exposed under the existing InQsi namespace:

- `GET /v1/inqsi/algorithm/sports`
- `POST /v1/inqsi/markets/normalize-pull`
- `POST /v1/inqsi/pulls`
- `GET /v1/inqsi/pulls/latest?sport=<sport>`
- `GET /v1/inqsi/algorithm/signals?sport=<sport>`
- `POST /v1/inqsi/algorithm/readiness`
- `POST /v1/inqsi/parlays/build-pull-history`
- `POST /v1/inqsi/slips/scan-pull-history`
- `GET /v1/inqsi/monitoring/pull-data-quality`

## Supported starting sport keys

Professional: `nfl`, `mlb`, `nba`, `wnba`, `nhl`, `tennis`, `soccer`.

College: `cfb`, `college_football_men`, `college_football_women`, `college_baseball_men`, `college_baseball_women`, `college_softball_women`, `ncaam`, `ncaaw`.

Women’s college football/baseball-style keys are accepted by the algorithm now for manual/future-provider payloads. Live provider coverage must still be confirmed before enabling automated odds ingestion for those keys.

## Storage

Pull runs are stored in the existing `SNAPSHOTS_TABLE` using:

- `PK = PULLS#<sport>#<slate_date>`
- `SK = PULL#<pulled_at>#<pull_id>`

This avoids adding a new table during the first deployment and keeps the change low-risk.
