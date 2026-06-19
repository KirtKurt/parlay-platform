# Silvers Syndicate Frontend v0.1

Sportsbook-style Next.js frontend shell for the parlay intelligence platform.

## What this is

This is the first consumer-facing UI scaffold. It is intentionally built to feel like a premium sportsbook product without copying FanDuel or Fanatics directly.

Included screens/components:

- Landing / hero area
- Sport-style top navigation
- Slate status cards
- Eligible game cards
- Moneyline / total display
- Signal badges: STEAM, RESISTANCE, COIN_FLIP, DAC, CHAOS, etc.
- Parlay slip panel
- Ranked 8-combo containment zone panel
- Mobile-first responsive layout

## Locked product requirement: line movement graph

The game-detail graph must not show only T1, T2, and T3.

The graph must include:

- T1 baseline snapshot
- Every 15-minute hot pull after T1
- T2 marker
- Every 15-minute hot pull after T2
- T3 marker
- Every 15-minute pull after T3 when available
- Later T4/T5 markers when those confirmation/safety captures exist

The chart should visually distinguish major checkpoints from regular hot pulls:

- Major checkpoints: T1, T2, T3, T4, T5 as labeled milestone markers
- 15-minute pulls: smaller plotted points between milestones
- Separate line series per book and/or per side when useful
- Hover/tap detail should show timestamp, book, team, moneyline, spread, total, and signal changes

This is a core product requirement because users need to see the full market path, not just three checkpoint dots.

## Current data mode

The app currently uses mock data in:

```bash
lib/mockData.ts
```

When the AWS backend is ready, replace the mock data with API calls to:

```bash
GET /v1/slates/today?sport=nfl
GET /v1/games/{game_id}/snapshots
GET /v1/games/{game_id}/line-movement?interval=15m
POST /v1/parlays/build
GET /v1/parlays/{build_id}
```

Set the backend URL in:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
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

## AWS Amplify deploy path

1. Push this folder to GitHub.
2. Open AWS Amplify Hosting.
3. Connect the GitHub repo.
4. Use the default Next.js build command:

```bash
npm run build
```

5. Add environment variable:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
```

6. Deploy.

## Product direction

Customer app:

- Next.js / React
- AWS Amplify Hosting
- API Gateway / FastAPI backend
- Stripe subscription gate
- Cognito or Clerk login

Internal/admin app:

- Streamlit or Retool
