# Silvers Syndicate Auth, Billing, and Master Access Plan

## Rule

Do not hard-code a master password, payment credential, or provider secret in the frontend.

The browser can inspect frontend code. Any master unlock or subscription unlock must be issued by the backend after a verified identity and role check.

## Access levels

| Role | Subscription status | Teaser view | Premium view | Parlay build | Admin tools |
| --- | --- | --- | --- | --- | --- |
| VISITOR | NONE | Yes | No | No | No |
| REGISTERED | PENDING | Yes | No | No | No |
| SUBSCRIBER | ACTIVE | Yes | Yes | Yes | No |
| MASTER | MASTER_BYPASS | Yes | Yes | Yes | Yes |

## Provider-neutral billing flow

1. Customer registers.
2. Customer selects monthly plan.
3. Frontend sends customer to the selected payment provider checkout flow.
4. Payment provider confirms monthly billing status to backend.
5. Backend updates customer subscription status.
6. Frontend calls backend for current access state.
7. Premium pages unlock only when backend returns `SUBSCRIBER / ACTIVE` or `MASTER / MASTER_BYPASS`.

## Master login flow

1. Master user signs in through the same login path.
2. Backend verifies the user in the identity provider.
3. Backend checks that the user belongs to the master/admin group.
4. Backend returns a signed session claim with `role = MASTER`.
5. Frontend unlocks admin tools and premium views based on that claim.

## Data storage target

Customer profile table:

```text
customer_id
email
first_name
last_name
phone
state
birthdate
primary_sport
use_case
role
subscription_status
billing_provider
billing_customer_id
billing_plan_id
created_at
updated_at
```

## Important implementation notes

- The frontend may show teaser/blurred views without authentication.
- Premium data should not merely be hidden by CSS. The backend should refuse premium data unless access is verified.
- Payment credentials are not stored in GitHub.
- Payment credentials should be placed in AWS Secrets Manager or environment variables at deploy time.
- Master credentials are not stored in GitHub.
- The master role should be assigned in the identity provider or customer table by an administrator.
