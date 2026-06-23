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
