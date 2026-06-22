# InQsi Backend Data Contracts

This document tracks the backend foundation needed to move InQsi from frontend scaffolds into real production data.

## Locked Rules

- No saved slip can exceed 3 legs.
- No fake/default market data.
- No silent fallback if a table, provider, or route is not wired.
- Public member score cards are opt-in.
- Connected social accounts are member-owned, revocable, and must never expose raw tokens in admin responses.

## Database Tables Needed

The production backend contract currently expects these storage areas:

1. `members`
   - member identity
   - role
   - status
   - plan
   - handle
   - public profile setting
   - public score setting
   - creator source

2. `social_accounts`
   - Facebook, Instagram, Reddit, X, TikTok, YouTube, Discord, LinkedIn, Twitch, Snapchat, and other provider connections
   - provider account ID
   - username/display name
   - OAuth scopes
   - token secret names only
   - connection/revocation status
   - public badge setting
   - social posting permission setting

3. `saved_slips`
   - member slips
   - 1 to 3 legs only
   - private/public visibility
   - scanner/builder/manual source
   - grading status
   - post-game review summary

4. `score_history`
   - 7-day score
   - 30-day score
   - lifetime score
   - public-visible score-card status

5. `creator_attribution`
   - first-touch source
   - last-touch source
   - creator/ref code
   - UTM parameters
   - anonymous visit to member signup conversion
   - paid conversion timing

6. `subscriptions`
   - Stripe/manual/none provider status
   - trial status
   - active/past-due/canceled status
   - provider customer and subscription IDs

7. `admin_audit_logs`
   - all owner/admin actions
   - member updates
   - slip grading
   - public score updates
   - social account connection/revocation
   - subscription updates
   - feature flag changes

8. `support_notes`
   - member support issues
   - owner/admin notes
   - open/watching/closed status

9. `feature_flags`
   - public score cards
   - social badges
   - social posting
   - challenges
   - admin controls

## API Contracts Needed

Core member routes:

- `GET /v1/members/me`
- `PATCH /v1/members/me`

Social account routes:

- `POST /v1/members/social/connect/start`
- `GET /v1/members/social/callback`
- `DELETE /v1/members/social/{connectionId}`

Saved slip routes:

- `POST /v1/slips`
- `GET /v1/slips`
- `GET /v1/slips/{slipId}`
- `POST /v1/slips/{slipId}/grade`

Score routes:

- `GET /v1/scores/me`
- `GET /v1/public/u/{handle}`

Creator/referral attribution routes:

- `POST /v1/attribution/visit`
- `POST /v1/attribution/convert`

Subscription routes:

- `POST /v1/subscriptions/checkout`
- `POST /v1/subscriptions/webhook`

Admin routes:

- `GET /v1/admin/dashboard`
- `GET /v1/admin/members`
- `GET /v1/admin/social-accounts`
- `GET /v1/admin/audit`
- `PATCH /v1/admin/feature-flags/{flagKey}`

## Social Connection Rules

Members may connect relevant social accounts for public profile verification, creator attribution, and later optional social publishing.

Initial supported providers:

- Facebook
- Instagram
- Reddit
- X
- TikTok
- YouTube
- Discord
- LinkedIn
- Twitch
- Snapchat
- Other

Rules:

- Store token secret names, not raw tokens, in the main app table.
- Tokens must live in AWS Secrets Manager or an equivalent secure secrets vault.
- Posting permission must be explicit and separate from basic profile connection.
- Members must be able to revoke a connection.
- Admin dashboard must show connection status without exposing tokens.
- Social posting should remain off by default until the product is ready.

## Backend Wiring Order

### Phase 1 — Storage

Create tables for:

1. members
2. social_accounts
3. saved_slips
4. score_history
5. creator_attribution
6. subscriptions
7. admin_audit_logs
8. support_notes
9. feature_flags

### Phase 2 — Member and Auth

- member creation
- session validation
- owner/admin role check
- profile update
- public score settings

### Phase 3 — Social Accounts

- provider OAuth start route
- callback route
- token secret storage
- revoke route
- admin overview route

### Phase 4 — Slips and Scores

- save slip
- enforce 3-leg max
- read saved slips
- grade slips
- calculate 7-day and 30-day scores
- publish opt-in public score card

### Phase 5 — Attribution and Subscriptions

- creator/ref visit tracking
- signup attribution
- paid conversion tracking
- Stripe checkout
- Stripe webhooks

### Phase 6 — Admin Wiring

- real admin dashboard aggregates
- member management
- social account connection overview
- audit log search
- feature flag controls

## Current Status

The contract layer is committed in `backend/src/inqsi_backend_contracts.py`.

The next step is to create the actual AWS resources and wire these contracts into live route handlers without using fake data or silent fallbacks.
