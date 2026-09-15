# Inqsi Arb

Isolated production arbitrage service. It never places bets.

## Coverage model

Sports and sportsbooks are provider-discovered. `/v1/arb/catalog` reads the active sport catalogue from The Odds API. Production scans include every sportsbook returned across the configured worldwide region set; no hard-coded bookmaker allowlist is applied.

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

Production defaults to worldwide settlement scope (`*`) and the configured
global provider regions. The desk is book-first: users choose sportsbooks with
`books=`. State packs remain an internal house-rule/license footprint and are
not the product taxonomy. Unreviewed combinations remain visible but fail
closed. Explicit `regions` remains available for diagnostic scans. Scan history
persists bounded leg-level evidence for verified, held-back, rejected, and
exchange-pending candidates. Lay markets are never evaluated as ordinary
sportsbook back markets; they are reported separately until the commission,
liability, and liquidity-aware back/lay engine can evaluate them.
