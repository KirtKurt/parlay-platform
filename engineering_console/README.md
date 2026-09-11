# InQsi Engineering Console service

Private server-side Codex job service. It uses the official `@openai/codex-sdk`; Codex credentials remain in the worker environment and are never accepted by an API or returned in job data.

## Security and operation

The service deliberately fails to start unless OIDC issuer, audience, administrator claim, and the absolute authorized repository path are configured. Every route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind the existing private ingress and identity provider; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. The runtime rejects changed paths outside `authorizedScope`. It has repository write access only: deployment credentials must not be mounted in this service. Records, logs, diffs, thread IDs, and continuation state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume. OpenAI and provider credentials must be supplied through the approved server secret manager. No credential has been created or requested by this change.

Required configuration:

- `INQSI_ENGINEERING_OIDC_ISSUER`, `INQSI_ENGINEERING_OIDC_AUDIENCE`, `INQSI_ENGINEERING_ADMIN_CLAIM`
- `INQSI_ENGINEERING_REPOSITORY`, `INQSI_ENGINEERING_DATA_DIR`, `INQSI_ENGINEERING_WORKSPACE_ROOT`
- `INQSI_ENGINEERING_API_URL` and an HttpOnly `inqsi_engineering_access` cookie in the Next.js server environment
- Codex authentication configured for the installed SDK via the approved secret workflow

Run `npm ci && npm test`, then `npm start`. Deployment is intentionally not automatic and must pass normal branch review, CI, identity, secret, encrypted-volume, and private-ingress approvals.
