# Arb desk (in-repo app feature)

Pre-game cross-book surebet scanner. Lives in this repository. Does not place bets.

## Routes

| Method | Path | What |
| --- | --- | --- |
| GET | `/v1/arb/health` | liveness |
| GET | `/v1/arb/scan?sport=mlb&bankroll=1000` | Odds API + BBD overlay → scan |
| POST | `/v1/scan` | scan a posted `{bankroll, events}` payload |

Frontend: `/arb` (Next.js) proxies through `/api/arb` to `PARLAY_API_BASE`.

## Sources

- **The Odds API** (`ODDS_API_KEY`): US books, `h2h,spreads,totals`.
- **Big Balls Data** (`BBS_API_KEY` or `BBS_API_SECRET_ARN`): MLB match context (starters when present). Only added as a quote book if BBD actually returns prices.

## Contract

```
POST /v1/scan
{
  "bankroll": 1000,
  "events": [{
    "id": "nym-nyy-h2h",
    "event": "NYM @ NYY",
    "market": "h2h",
    "quotes": [
      {"outcome": "NYM", "book": "fanduel", "american": 118},
      {"outcome": "NYY", "book": "draftkings", "american": -105}
    ]
  }]
}
```

A hit is `sum(1/decimal) < 1`. Stakes are bankroll × implied / sum so every outcome pays the same.

## Env

Lambda already uses `ODDS_API_KEY`. BBD reuses the existing client env. Frontend needs `PARLAY_API_BASE` (or `NEXT_PUBLIC_PARLAY_API_BASE`) pointing at the API that serves `/v1/arb/scan`.
