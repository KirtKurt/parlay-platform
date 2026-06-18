# Risk Check Syndicate Frontend v0.1

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

## Current data mode

The app currently uses mock data in:

```bash
lib/mockData.ts
```

When the AWS backend is ready, replace the mock data with API calls to:

```bash
GET /v1/slates/today?sport=nfl
GET /v1/games/{game_id}/snapshots
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
