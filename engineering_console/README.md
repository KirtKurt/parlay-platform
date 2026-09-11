# InQsi Engineering Console service

Private server-side Codex job service. It uses the official `@openai/codex-sdk`; Codex credentials remain in the worker environment and are never accepted by an API or returned in job data.

## Security and operation

The service fails closed unless its identity, storage, origin, repository, and server-side scope policy are explicitly configured. Every route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind private authenticated ingress; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. The worker runs with workspace-write sandboxing and runtime network access disabled. Change collection is measured against the immutable starting revision so committed, staged, unstaged, renamed, deleted, and untracked files remain visible to scope enforcement. Deployment credentials must not be mounted in the coding worker; publication and release should use separate trusted automation.

Records, sanitized instructions/logs/diffs/test output, thread IDs, cancellation state, and continuation state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume. OpenAI and provider credentials must be supplied only through approved server-side secret management.

## Required service configuration

- `INQSI_ENGINEERING_OIDC_ISSUER`
- `INQSI_ENGINEERING_OIDC_AUDIENCE`
- `INQSI_ENGINEERING_JWKS_URI`
- `INQSI_ENGINEERING_ADMIN_CLAIM`
- `INQSI_ENGINEERING_REPOSITORY` — absolute path to the authorized checkout
- `INQSI_ENGINEERING_DATA_DIR` — absolute path on durable encrypted storage
- `INQSI_ENGINEERING_WORKSPACE_ROOT` — absolute path for isolated worktrees
- `INQSI_ENGINEERING_ORIGIN` — trusted HTTPS console origin
- `INQSI_ENGINEERING_ALLOWED_SCOPES` — comma-separated server-side repository path allowlist
- optional `INQSI_ENGINEERING_MAX_CONCURRENT_JOBS` — integer from 1 through 8; defaults to 1

## Required Next.js configuration

- `INQSI_ENGINEERING_API_URL` — private service base URL available only to the server runtime
- an HttpOnly, Secure, SameSite=Strict, Path=/ cookie named `__Host-inqsi_engineering_access`, issued only after the trusted authentication callback verifies the administrator session

The browser must never receive Codex, GitHub, AWS, or deployment credentials.

## Validation

Run:

```bash
npm install --ignore-scripts --no-audit --no-fund
npm test
```

The repository frontend workflow also runs Engineering Console syntax/tests for Console changes before building the Next.js frontend.

A green unit test or frontend build is readiness evidence only. Production operation is not established until the separate Console release path provisions private ingress, OIDC configuration, encrypted durable storage, server-side secret references, the worker runtime, and post-deployment verification of a controlled real Codex job.
