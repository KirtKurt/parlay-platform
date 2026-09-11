# InQsi Engineering Console service

Private Engineering Console using the official `@openai/codex-sdk`. A trusted controller launches a separate credential-free Fargate task for each coding job. A scoped broker supplies model access; a separate publisher validates and publishes proof-only changes. The Console serves its own administrator sign-in and browser UI.

## Security and operation

The service fails closed unless its identity, storage, origin, repository, publication policy, and server-side scope policy are explicitly configured. Every job route—including listing, detail, event streams, continuation, and cancellation—verifies the signed OIDC access token and administrator claim. Jobs are owner-scoped. Put the service behind private authenticated ingress; do not expose port 8787 publicly.

Jobs get a dedicated git worktree and `inqsi/job-<uuid>` branch from an immutable starting commit. New HEAD jobs refresh the durable repository to `origin/main` with fast-forward-only semantics before the workspace is created. Explicit starting revisions must be full commit SHAs already on trusted `main`. Existing deterministic workspaces/branches are recovered without resetting partial work after a process restart. The worker runs with workspace-write sandboxing, runtime network access disabled, and Codex web search disabled. Change collection is measured against the immutable starting revision so committed, staged/unstaged, renamed, deleted, and untracked files remain visible to scope enforcement.

Validated changes are not pushed by the coding worker. The worker writes an atomic publication request under `INQSI_ENGINEERING_DATA_DIR/publication-outbox` and enters `awaiting_publication`. The request contains the immutable starting revision, exact changed-file set, server-authorized scopes, required check names, a credential-scanned binary patch, and its SHA-256 digest. A separate trusted publisher process runs `npm run publish` with repository publication credentials. It independently reapplies and revalidates the patch, uses a deterministic `inqsi/publish-<job-id>` branch, reuses an identical existing branch/PR on retry, validates the complete branch-versus-main history, and will not merge until every configured required check is observed with an actual `success` conclusion and trusted workflow provenance. Missing checks remain pending rather than being treated as success.

The automated publication path is not allowed to modify its own control plane. The first production policy is intentionally narrow: only Markdown proof files under `engineering_console_publication_proof` are eligible for autonomous publication. Changes under `.github`, `engineering_console`, `frontend/app/api/engineering`, package/build hooks, sports code, ARB code, or any other path are outside this initial policy. Those files require the normal reviewed repository path rather than self-modification by the coding worker.

The publisher runs in a separate task with its own lock mount. Coding tasks have no AWS task role, shared EFS mount, reusable API key or GitHub credential. The trusted controller holds only the existing role required to launch and observe isolated tasks. The recommended production identity is a narrowly scoped, short-lived GitHub App installation token with repository contents and pull-request write permissions plus checks/actions read access. Deployment credentials are a separate trust boundary again.

Records, sanitized instructions/logs/diffs/test output, thread IDs, cancellation state, and publication state are atomically persisted under `INQSI_ENGINEERING_DATA_DIR`, which must be a durable encrypted volume. Durable execution fields keep their full redacted value; public API strings are independently bounded so persisted instructions/diffs are not silently truncated. OpenAI and provider credentials must be supplied only through approved server-side secret management.

## Required service configuration

- `INQSI_ENGINEERING_OIDC_ISSUER`
- `INQSI_ENGINEERING_OIDC_AUDIENCE`
- `INQSI_ENGINEERING_JWKS_URI`
- `INQSI_ENGINEERING_ADMIN_CLAIM`
- `INQSI_ENGINEERING_REPOSITORY` — absolute path to the authorized checkout
- `INQSI_ENGINEERING_DATA_DIR` — absolute path on durable encrypted storage shared with the trusted publisher outbox/receipts
- `INQSI_ENGINEERING_WORKSPACE_ROOT` — absolute path for isolated worktrees
- `INQSI_ENGINEERING_WORKER_LOCK_DIR` — absolute durable lock directory shared by all trusted worker controllers; keep it outside coding-job mounts
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
- `INQSI_ENGINEERING_GITHUB_APP_SECRET_ARN` — publisher-only Secrets Manager reference to JSON containing `appId`, `installationId`, and `privateKey`; installation tokens are minted and renewed in memory

The publisher is safe to run repeatedly. A kernel lock serializes both new and recovered claims; pre-existing lock directories/files fail closed unless owned by the publisher identity with non-writable group/other permissions. The publisher compares an existing deterministic branch by Git tree before reuse, validates exact PR identity and complete Git history, reuses an existing pull request, and records terminal/pending publication state back into the durable job record. A failed required check, branch collision, unverified external merge, or policy mismatch is fail-closed. Accepted cancellation and merge commitment use a durable first-writer decision fence so an acknowledged cancellation cannot race a later autonomous merge.

The dedicated publication-proof workflow is intentionally skipped on ordinary Console hardening PRs. It becomes authoritative only after the trusted validator and workflow are merged to `main`; the first autonomous `inqsi/publish-*` proof PR must use that base-pinned validator. Do not activate publisher automation before that bootstrap merge is complete.

## Standalone browser and isolated deployment

The separate private Console serves its UI at `/`. `/auth/login` uses the configured OIDC authorization-code flow, PKCE and nonce. `/auth/callback` verifies the signed ID token and administrator access token before issuing the HttpOnly, Secure, SameSite=Strict access cookie. Cookie-authenticated writes require the exact Console origin. The production sports frontend does not need a configuration change.

See [deployment configuration](deploy/README.md) for the existing role, network, OIDC and secret references. The CloudFormation template wires the controller, broker, publisher, disposable coding task and live verification probe. All runtime resources belong to the isolated `eng-console-runtime` stack.

## Validation

Run:

```bash
npm ci --ignore-scripts --no-audit --no-fund
npm test
for file in src/*.js test/*.js scripts/*.mjs; do node --check "$file"; done
```

The repository frontend workflow also runs Engineering Console syntax/tests for Console changes before building the Next.js frontend.

A green unit test or frontend build is readiness evidence only. Production operation is not established until the separate Console release path provisions private ingress, OIDC configuration, encrypted durable storage, server-side secret references, the worker runtime, the isolated publisher runtime (including its private lock mount), and post-deployment verification of a controlled real Codex job through PR publication, required CI, merge, and durable state recovery.

## Shared-state restart requirements

`npm start` acquires a nonblocking shared worker lock before starting the server or recovering jobs. A concurrent controller exits with status 75; it never serves health or requeues the existing controller's jobs. Coding tasks have an independently recorded ECS task identity and RunTask idempotency token. A replacement controller observes that task instead of launching a duplicate. Cancellation expires the scoped model capability and waits for ECS STOPPED; an unconfirmed stop blocks continuation. A production restart test on the actual EFS mount is still required; local process-lock tests alone do not prove remote filesystem behavior or orphan cleanup.

JobStore writes hold a per-job shared kernel lock while comparing the caller's snapshot with the durable record. Disjoint field changes are preserved; conflicting transitions fail with `ESTALE` instead of replacing newer publication/cancellation state. Temporary files use UUIDs and exclusive creation, then fsync and atomic rename. Callers must update objects returned by that JobStore instance, and reread after a conflict. Do not retry by blindly replacing the current record. All trusted writers must run this version together; older writers do not honor these locks. Each coding task has its own filesystem and process boundary. Checkpoints contain a patch and opaque session archive; only a later disposable job task extracts the archive. The controller validates the patch paths and modes before applying it, and the publisher independently checks the resulting Git tree.

See [deployment checkpoint](deploy/repair-checkpoint-20260911.md) for the verified source and remaining release blockers.
