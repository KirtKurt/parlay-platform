# Engineering Console release repair checkpoint

This release reconciles current main with deployment PR [#826](https://github.com/KirtKurt/parlay-platform/pull/826), state repair [#833](https://github.com/KirtKurt/parlay-platform/pull/833) and publication recovery [#829](https://github.com/KirtKurt/parlay-platform/pull/829). Git history is preserved. Earlier comments described commits that GitHub could not resolve; those summaries were not shipped-code evidence.

Implemented repairs:

- Same-run tested-image artifact, immutable ECR digest and running task/source verification.
- Per-job Fargate filesystem/process boundary; no shared EFS, task role, GitHub credential or reusable OpenAI key in coding jobs.
- Scoped broker capabilities, fixed model endpoint and durable opaque session checkpoints.
- Shared controller/publisher/broker locks, atomic state writes, durable task identity, idempotent recovery and confirmed stop before continuation.
- Renewable GitHub App installation tokens; closed/externally-merged PR recovery, atomic cancellation/merge arbitration and truthful pending cancellation receipts.
- Standalone OIDC login with PKCE/state/nonce, administrator JWT and same-origin cookie write enforcement.
- Approved existing IAM/network references, isolated resources, retained EFS reattachment and collision-free retained log groups.
- Private-VPC real Codex-to-PR-to-check-to-merge proof required for deployment success.

Local verification covers real Git repositories, concurrent local processes, mocked ECS stop/recovery, signed OIDC login, token renewal, broker isolation, deployment error handling and CloudFormation validation. Hosted Docker and actual AWS/browser/EFS integration remain separate gates.

Status at preparation: **not deployed**. No live endpoint, production task identity or end-to-end receipt has been observed. The AWS STS identity attempt returned: “network approval was cancelled before a decision was returned.” No alternate AWS route was used. Required external references are described in [deployment configuration](README.md); do not invent them or equate local tests with deployment success.
