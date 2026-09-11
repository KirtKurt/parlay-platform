# InQsi Engineering Console service

Private server-side Codex job service. It uses the official `@openai/codex-sdk`; Codex credentials remain in the worker environment and are never accepted by an API or returned in job data.

## Security and operation

The service fails closed unless its identity, storage, origin, repository, publication policy, and server-side scope policy are explicitly configured. Every route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind private authenticated ingress; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. New HEAD jobs refresh the durable repository to `origin/main` with fast-forward-only semantics before the workspace is created. Explicit starting revisions must be full commit SHAs already on trusted `main`. Existing deterministic workspaces/branches are recovered without resetting partial work after a process restart. The worker runs with workspace-write sandboxing, runtime network access disabled, and Codex web search disabled. Change collection is measured against the immutable starting revision so committed, staged/unstaged, renamed, deleted, and untracked files remain visible to scope enforcement.

Validated changes are not pushed by the coding worker. The worker writes an atomic publication request under `INQSI_ENGINEERING_DATA_DIR/publication-outbox` and enters `awaiting_publication`. The request contains the immutable starting revision, exact changed-file set, server-authorized scopes, required check names, a credential-scanned binary patch, and its SHA-256 digest. A separate trusted publisher process runs `npm run publish` with repository publication credentials. It independently reapplies and revalidates the patch, uses a deterministic `inqsi/publish-<job-id>` branch, reuses an identical existing branch/PR on retry, validates the complete branch-versus-main history, and will not merge until every configured required check is observed with an actual `success` conclusion and trusted workflow provenance. Missing checks remain pending rather than being treated as success.

The automated publication path is not allowed to modify its own control plane. The first production policy is intentionally narrow: only Markdown proof files under `engineering_console_publication_proof` are eligible for autonomous publication. Changes under `.github`, `engineering_console`, `frontend/app/api/engineering`, package/build hooks, sports code, ARB code, or any other path are outside this initial policy. Those files require the normal reviewed repository path rather than self-modification by the coding worker.

The publisher must be a separate process/runner. Do not provide GitHub or AWS credentials to the Console worker or Codex subprocess. The recommended production identity is a narrowly scoped, short-lived GitHub App installation token with repository contents and pull-request write permissions plus checks/actions read access. Deployment credentials are a separate trust boundary again.

Records, sanitized instructions/logs/diffs/test output, thread IDs, cancellation state, and publication state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume. Durable execution fields keep their full redacted value; public API strings are independently bounded so persisted instructions/diffs are not silently truncated. OpenAI and provider credentials must be supplied only through approved server-side secret management.

## Required service configuration

- `INQSI_ENGINEERING_OIDC_ISSUER`
- `INQSI_ENGINEERING_OIDC_AUDIENCE`
- `INQSI_ENGINEERING_JWKS_URI`
- `INQSI_ENGINEERING_ADMIN_CLAIM`
- `INQSI_ENGINEERING_REPOSITORY` — absolute path to the authorized checkout
- `INQSI_ENGINEERING_DATA_DIR` — absolute path on durable encrypted storage shared with the trusted publisher outbox/receipts
- `INQSI_ENGINEERING_WORKSPACE_ROOT` — absolute path for isolated worktrees
- `INQSI_ENGINEERING_ORIGIN` — trusted HTTPS console origin
- `INQSI_ENGINEERING_ALLOWED_SCOPES` — for the initial controlled release, `engineering_console_publication_proof` or a narrower path below it
- `INQSI_ENGINEERING_PUBLICATION_POLICY=proof-v1`
- `INQSI_ENGINEERING_REQUIRED_CHECKS=engineering-console-publication-proof` — explicit and mandatory; there is no implicit `build` fallback
- optional `INQSI_ENGINEERING_MAX_CONCURRENT_JOBS` — integer from 1 through 8; defaults to 1

The service refuses to start if the publication policy, scope, or required check does not match the reviewed initial proof policy. Broader autonomous scopes require a separate reviewed policy and a matching immutable secret-free check.

## Trusted publisher configuration

Run `npm run publish` only in the isolated publisher trust boundary with:

- `INQSI_ENGINEERING_DATA_DIR` — the same encrypted durable data directory used by the worker for publication requests/receipts
- `INQSI_ENGINEERING_ALLOWED_SCOPES=engineering_console_publication_proof` (or an explicitly reviewed narrower proof subpath)
- `INQSI_ENGINEERING_PUBLICATION_POLICY=proof-v1`
- `INQSI_ENGINEERING_REQUIRED_CHECKS=engineering-console-publication-proof`
- `INQSI_ENGINEERING_PUBLISHER_LOCK_DIR` — absolute path on a lock filesystem shared by all publisher instances but not mounted by the Codex worker; it must be owned by the publisher uid and not group/other writable
- `GITHUB_REPOSITORY=KirtKurt/parlay-platform`
- `GH_TOKEN` — a short-lived repository publication credential supplied to the publisher process only

The publisher is safe to run repeatedly. A kernel lock serializes both new and recovered claims; pre-existing lock directories/files fail closed unless owned by the publisher identity with non-writable group/other permissions. The publisher compares an existing deterministic branch by Git tree before reuse, validates exact PR identity and complete Git history, reuses an existing pull request, and records terminal/pending publication state back into the durable job record. A failed required check, branch collision, unverified external merge, or policy mismatch is fail-closed. Accepted cancellation and merge commitment use a durable first-writer decision fence so an acknowledged cancellation cannot race a later autonomous merge.

The dedicated publication-proof workflow is intentionally skipped on ordinary Console hardening PRs. It becomes authoritative only after the trusted validator and workflow are merged to `main`; the first autonomous `inqsi/publish-*` proof PR must use that base-pinned validator. Do not activate publisher automation before that bootstrap merge is complete.

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

A green unit test or frontend build is readiness evidence only. Production operation is not established until the separate Console release path provisions private ingress, OIDC configuration, encrypted durable storage, server-side secret references, the worker runtime, the isolated publisher runtime (including its private lock mount), and post-deployment verification of a controlled real Codex job through PR publication, required CI, merge, and durable state recovery.
