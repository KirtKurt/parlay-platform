# InQsi Engineering Console service

Private server-side Codex job service. It uses the official `@openai/codex-sdk`; Codex credentials remain in the worker environment and are never accepted by an API or returned in job data.

## Security and operation

The service fails closed unless its identity, storage, origin, repository, and server-side scope policy are explicitly configured. Every route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind private authenticated ingress; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. The worker runs with workspace-write sandboxing and runtime network access disabled. Change collection is measured against the immutable starting revision so committed, staged, unstaged, renamed, deleted, and untracked files remain visible to scope enforcement.

Before a new job resolves `HEAD`, the durable repository checkout fast-forwards from `origin/main`. A failure to refresh fails the request rather than silently starting from stale code.

Records, sanitized instructions/logs/diffs/test output, thread IDs, cancellation state, continuation state, and publication state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume.

## Two-role automated delivery

The production runtime deliberately separates coding from repository publication:

1. **Worker role** — runs `npm start`, invokes Codex, receives the OpenAI/Codex credential, has a writable isolated worktree, and **does not receive GitHub or AWS publishing credentials**. The Codex subprocess is launched with a minimal environment that excludes publisher/deployment variables.
2. **Trusted publisher role** — runs `npm run start:publisher`, shares only the durable job/publication volume, receives the repository publisher credential, and never invokes Codex. It independently validates the bundle checksum, credential scan, exact changed-file set, and server-authorized scope before creating a commit and pull request. When automatic merge is enabled, it requires at least one GitHub check run and refuses merge unless every observed check has completed successfully, neutrally, or as skipped.

The worker places a raw, credential-scanned patch plus an integrity manifest in `INQSI_ENGINEERING_DATA_DIR/publish-outbox`. The publisher applies that bundle to a fresh checkout of the immutable starting revision with Git hooks disabled, re-derives the changed files, publishes an `inqsi/job-<uuid>` branch, opens a pull request, waits for checks, and can merge the exact verified commit. Processed bundles move to `publish-processed`.

Deployment credentials are a separate trust boundary again; they must not be mounted in either the Codex subprocess or browser.

## Required worker configuration

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
- approved Codex/OpenAI authentication (`OPENAI_API_KEY` or the supported equivalent available to the installed SDK)

## Required trusted publisher configuration

- `INQSI_ENGINEERING_DATA_DIR` — same durable volume as the worker
- `INQSI_ENGINEERING_GITHUB_TOKEN` — server-side repository publication credential; never expose it to the worker or browser
- optional `INQSI_ENGINEERING_GITHUB_REPOSITORY` — defaults to `KirtKurt/parlay-platform`
- optional `INQSI_ENGINEERING_GITHUB_BASE_BRANCH` — defaults to `main`
- optional `INQSI_ENGINEERING_AUTO_MERGE` — defaults to `true`
- optional `INQSI_ENGINEERING_PUBLISH_POLL_MS`
- optional `INQSI_ENGINEERING_CHECK_TIMEOUT_MS`

In production, prefer a narrowly scoped GitHub App or other short-lived repository identity over a broad personal token. Store credentials in the approved server-side secret manager and inject each secret only into the role that needs it.

## Required Next.js configuration

- `INQSI_ENGINEERING_API_URL` — private service base URL available only to the server runtime
- an HttpOnly, Secure, SameSite=Strict, Path=/ cookie named `__Host-inqsi_engineering_access`, issued only after the trusted authentication callback verifies the administrator session

The browser must never receive Codex, GitHub, AWS, or deployment credentials.

## Container roles

`engineering_console/Dockerfile` provides the worker image. Its default command bootstraps/fast-forwards the durable repository checkout and starts the worker service. The same image can start the publisher with:

```bash
npm run start:publisher
```

Deploy the worker and publisher as separate containers/process trust boundaries. Mount the same encrypted durable data volume, but give only the worker the OpenAI credential and only the publisher the GitHub credential.

## Validation

Run:

```bash
npm install --ignore-scripts --no-audit --no-fund
npm test
```

The repository frontend workflow also runs Engineering Console syntax/tests for Console changes before building the Next.js frontend.

A green unit test or frontend build is readiness evidence only. Production operation is not established until the separate Console release path provisions authenticated private ingress, OIDC configuration, encrypted durable storage, server-side secret references, worker and publisher roles, and post-deployment verification of a controlled real Codex job through branch publication, CI, merge, and runtime recovery.
