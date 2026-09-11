# InQsi Engineering Console service

Private server-side Codex job service. It uses the official `@openai/codex-sdk`; Codex credentials remain in the worker environment and are never accepted by an API or returned in job data.

## Security and operation

The service fails closed unless its identity, storage, origin, repository, and server-side scope policy are explicitly configured. Every route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind private authenticated ingress; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. New HEAD jobs refresh the durable repository to `origin/main` with fast-forward-only semantics before the workspace is created. Existing deterministic workspaces/branches are recovered without resetting partial work after a process restart. The worker runs with workspace-write sandboxing, runtime network access disabled, and Codex web search disabled. Change collection is measured against the immutable starting revision so committed, staged/unstaged, renamed, deleted, and untracked files remain visible to scope enforcement.

Validated changes are not pushed by the coding worker. The worker writes an atomic publication request under `INQSI_ENGINEERING_DATA_DIR/publication-outbox` and enters `awaiting_publication`. The request contains the immutable starting revision, exact changed-file set, server-authorized scopes, required check names, a credential-scanned binary patch, and its SHA-256 digest. A separate trusted publisher process runs `npm run publish` with repository publication credentials. It independently reapplies and revalidates the patch, uses a deterministic `inqsi/publish-<job-id>` branch, reuses an identical existing branch/PR on retry, and will not merge until every configured required check is observed and passing. Missing checks remain pending rather than being treated as success.

The automated publication path is not allowed to modify its own control plane. Changes under `.github`, `engineering_console`, or `frontend/app/api/engineering` are rejected by the publication boundary even if a job scope accidentally includes them. Those trust-boundary files require the normal human/reviewed repository path rather than self-modification by the coding worker.

The publisher must be a separate process/runner sharing only the encrypted durable job volume. Do not provide GitHub or AWS credentials to the Console worker or Codex subprocess. The recommended production identity is a narrowly scoped, short-lived GitHub App installation token with repository contents and pull-request write permissions plus checks read access. Deployment credentials are a separate trust boundary again.

Records, sanitized instructions/logs/diffs/test output, thread IDs, cancellation state, and publication state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume. Durable execution fields keep their full redacted value; public API strings are independently bounded so persisted instructions/diffs are not silently truncated. OpenAI and provider credentials must be supplied only through approved server-side secret management.

## Required service configuration

- `INQSI_ENGINEERING_OIDC_ISSUER`
- `INQSI_ENGINEERING_OIDC_AUDIENCE`
- `INQSI_ENGINEERING_JWKS_URI`
- `INQSI_ENGINEERING_ADMIN_CLAIM`
- `INQSI_ENGINEERING_REPOSITORY` — absolute path to the authorized checkout
- `INQSI_ENGINEERING_DATA_DIR` — absolute path on durable encrypted storage shared with the trusted publisher
- `INQSI_ENGINEERING_WORKSPACE_ROOT` — absolute path for isolated worktrees
- `INQSI_ENGINEERING_ORIGIN` — trusted HTTPS console origin
- `INQSI_ENGINEERING_ALLOWED_SCOPES` — comma-separated server-side repository path allowlist
- optional `INQSI_ENGINEERING_REQUIRED_CHECKS` — comma-separated exact GitHub check-run names; defaults to `build`
- optional `INQSI_ENGINEERING_MAX_CONCURRENT_JOBS` — integer from 1 through 8; defaults to 1

## Trusted publisher configuration

Run `npm run publish` only in the isolated publisher trust boundary with:

- `INQSI_ENGINEERING_DATA_DIR` — the same encrypted durable data directory used by the worker
- `GITHUB_REPOSITORY=KirtKurt/parlay-platform`
- `GH_TOKEN` — a short-lived repository publication credential supplied to the publisher process only

The publisher is safe to run repeatedly. It atomically claims durable requests, compares an existing deterministic branch by Git tree before reuse, reuses an existing pull request, and records terminal/pending publication state back into the durable job record. A failed required check is fail-closed. A publication branch collision is fail-closed. Configured checks are revalidated before a merged receipt is accepted, including when an external actor merged the PR first.

## Required Next.js configuration

- `INQSI_ENGINEERING_API_URL` — private service base URL available only to the server runtime
- an HttpOnly, Secure, SameSite=Strict, Path=/ cookie named `__Host-inqsi_engineering_access`, issued only after the trusted authentication callback verifies the administrator session

The browser must never receive Codex, GitHub, AWS, or deployment credentials.

## Validation

Run:

```bash
npm install --ignore-scripts --no-audit --no-fund
npm test
for file in src/*.js test/*.js scripts/*.mjs; do node --check "$file"; done
```

The repository frontend workflow also runs Engineering Console syntax/tests for Console changes before building the Next.js frontend.

A green unit test or frontend build is readiness evidence only. Production operation is not established until the separate Console release path provisions private ingress, OIDC configuration, encrypted durable storage, server-side secret references, the worker runtime, the isolated publisher runtime, and post-deployment verification of a controlled real Codex job through PR publication, required CI, merge, and durable state recovery.
