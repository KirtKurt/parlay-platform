# Inqis Frontend

Mobile-first Next.js frontend for the Inqis sports market intelligence platform.

## Current UI direction

The app should follow the Inqis mockup system:

- Dark mobile-first glass UI
- Green and blue accent states
- Bottom app navigation
- Live market cards on public and member pages
- Clear moneyline, spread, and over/under fields
- No random failed state in public navigation
- No sport-specific membership selection
- One membership includes all supported sports

## Supported public/member surfaces

- `/` home dashboard
- `/sports` all-sports market board
- `/sports/[sport]` sport market board
- `/parlays` official hourly parlay cards
- `/parlay-scanner` slip scanner
- `/login` real login form
- `/register` real registration form
- `/account` member profile/workspace
- `/account/slips` my slips
- `/game/[gameId]` market detail

## Data route

The frontend reads board data from:

```bash
GET /v1/inqsi/markets/board
```

When backend environment variables are set, this route can proxy to the AWS API. If the backend URL is not configured yet, the site route returns visible board data so the UI does not render empty.

Preferred environment variable:

```bash
INQSI_API_URL=https://your-api-gateway-url/Prod
```

Also supported:

```bash
NEXT_PUBLIC_INQSI_API_URL=https://your-api-gateway-url/Prod
NEXT_PUBLIC_API_BASE_URL=https://your-api-gateway-url/Prod
API_URL=https://your-api-gateway-url/Prod
```

## Local run

```bash
npm install
npm run dev
```

Open:

```bash
http://localhost:3000
```

## Build

```bash
npm run build
```

## Hosting

The repo includes a frontend build workflow. Production hosting still needs the host provider to deploy the latest build from the GitHub repo and set the backend API URL where applicable.

## ARB V2 prototype

- `/arbitrage-v2` — opportunity-first ARB workspace matching the approved 16:9 desktop/mobile direction.
- `/arbitrage-v2/calculator` — standalone American-odds arbitrage calculator with an unrestricted positive-dollar stake field.
- `/v1/inqsi/arbitrage/history` — server-side, read-only proxy to the existing ARB history endpoint. Configure `INQSI_ARB_API_URL` (preferred) or one of the existing API base variables. The proxy fails closed and times out after 8 seconds.
- If live ARB history is unavailable, the UI enters an explicit **DESIGN PREVIEW / SAMPLE OPPORTUNITIES / NOT LIVE SPORTSBOOK DATA** state. Sample rows must never be represented as live opportunities.
- These routes are additive and do not replace the current production home route.

### Vercel deployment

Import the GitHub repository into Vercel with **Root Directory = `frontend`**. The included `frontend/vercel.json` uses the existing Next.js build and install commands. Configure `INQSI_ARB_API_URL` to the deployed AWS ARB API base (preferred); existing API base variables remain supported as fallback. Preview deployments can run without that variable, but they will display the explicit non-live sample-data banner instead of implying live sportsbook access.
