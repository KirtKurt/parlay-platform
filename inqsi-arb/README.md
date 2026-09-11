# Inqsi Arb

Isolated production arbitrage service. It never places bets.

## Coverage model

Sports and sportsbooks are provider-discovered. `/v1/arb/catalog` reads the active sport catalogue from The Odds API; scans omit a hard-coded sportsbook allowlist so every provider-returned operator in the requested permitted regions can participate.

The deterministic engine supports N-way markets and arbitrary provider market keys. Featured `h2h`, `spreads`, `totals`, and `outrights` use the sport odds endpoint. Other exact market keys use event-specific odds endpoints. Market availability is sport/book dependent and is never fabricated.

## API

- `GET /v1/arb/health`
- `GET /v1/arb/catalog`
- `GET /v1/arb/scan?sport=baseball_mlb&markets=h2h,spreads,totals&bankroll=1000`
- `GET /v1/arb/scan?sport=all&markets=h2h&bankroll=1000`
- `POST /v1/arb/scan` deterministic posted-payload evaluation

Extended market example:

`/v1/arb/scan?sport=baseball_mlb&markets=player_strikeouts,alternate_totals`

The service rejects incomplete outcome universes and never labels unknown/incompatible rule identities as an arb. Quote/book links and provider timestamps are retained where the provider supplies them.
