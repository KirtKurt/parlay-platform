# Engineering Console production v3 acceptance gates

This branch is stacked on the reviewed Console runtime repair branch. It is Console-only. It must not modify sports/ARB source, sports workflows, prediction authority, or AWS resources owned by those systems.

The v3 deployment is not releasable until every gate below is implemented and covered by validation. A stable ECS service or a successful CloudFormation update alone is not proof of a working Console.

## Immutable source-to-image binding

The production image must be built from the exact source revision that passed Console runtime and frontend validation, pushed only to the dedicated approved Console ECR repository/account, and deployed by verified digest. Deployment must reject arbitrary dispatcher-supplied repositories or digests and record the source SHA plus pushed ECR digest.

## Worker execution isolation

A coding job must not be able to read another owner's job records, worktree, durable instruction/log state, publisher outbox/receipts/locks, service configuration, or reusable service credentials. `workspace-write`, redaction, shared UID ownership, and a shared EFS root are not sufficient isolation boundaries.

The coding process must run behind an OS/process/filesystem boundary that exposes only its job worktree and job-specific state. Reusable OpenAI authentication must not be readable by generated shell commands through the job environment, inherited file descriptors, or `/proc/*/environ`.

## Controller and rollout safety

The shared worker-controller lock introduced by the runtime repair must be configured on durable trusted storage outside coding-job mounts. A replacement worker must not recover/requeue a job while the old worker or any of its coding subprocesses is still alive. Rollout/restart behavior must be serialized or drained and tested with an active job.

## Publisher isolation

Publisher locks and publication credentials must live on publisher-only storage/identity inaccessible to coding workers. The publisher must retain proof-v1 scope and exact required-check enforcement.

GitHub App publication credentials must be renewable at runtime. A one-time short-lived token injected only when the container starts is not an acceptable long-running credential design.

## Durable recovery

Codex session recovery must either persist the session material inside the job's permitted boundary or explicitly restart/fail closed when the session is unavailable. A retained EFS/log configuration must have a tested reattachment/import path for stack recreation; retained data must not be silently orphaned and retained named log groups must not block reprovisioning.

## Administrator authentication and ingress

Ingress remains private HTTPS. Browser administrator access must use the verified engineering OIDC/JWT path and must not fall back to a PIN or spoofable identity headers. Browser clients must never receive OpenAI, GitHub, AWS, or deployment credentials.

## Post-deploy proof

Deployment success requires all of the following evidence from the running revision:

- source SHA and exact ECR digest bound to the deployed task definitions;
- previous/new worker and publisher task-definition ARNs and compatible retained storage identifiers;
- authenticated health through the intended private ingress;
- a controlled authenticated Console job that exercises the real OpenAI/Codex path;
- bounded proof-v1 change publication through the trusted GitHub publisher;
- exact required CI success, merge of the verified commit, and a durable terminal receipt;
- restart/recovery verification without duplicate coding or publication execution.

If any proof step cannot run because protected environment variables, AWS/OIDC access, network reachability, or real credentials are unavailable, the workflow must report that limitation and must not claim production readiness.
