# Engineering Console deployment repair checkpoint

Source inspected: `KirtKurt/parlay-platform` main `e22d8f21a97f101a0b803056ae4443db9d7b404f`.
Publication base refreshed to current main `788f41afbfa13e9c2f99f2939603f2621aa4d0a7`; all Console source blobs matched the inspected revision.
Deployment PR: [#826](https://github.com/KirtKurt/parlay-platform/pull/826), head `f019a390104dd50907d446e1685f045abcf0c026`.

## Verified delivery issue

PR #826's validation and frontend build passed, but deployment was skipped in run `34645332850`. Its comments claimed repairs at `e3c8f4abf76aa082854433b83f307602ce2bcfea` and `4ea05f1`; GitHub's commit API could not resolve either on inspection. Those summaries cannot be treated as shipped code. This repair branch starts from the verified main revision above and does not include unresolvable code.

## Changes in this repair

- Node 24.21.0 image pinned to Docker Hub's verified manifest digest; committed npm lockfile and lifecycle-script-disabled `npm ci`.
- Per-job kernel locking, exclusive UUID temporary writes, fsync/rename, and snapshot conflict detection prevent container-PID collisions and stale record replacement. Disjoint updates remain intact.
- Worker startup takes a shared kernel lock before recovery or health service starts. Concurrent startup returns 75.
- Queue/runner conflict handling preserves newer durable publication outcomes.
- Dedicated secret-free runtime/image validation from the exact source revision. Existing sports/ARB source and deployment workflows are unchanged.

## Remaining blockers; do not infer release approval from these repairs

1. #826 still needs to consume the exact validated image rather than an arbitrary dispatch digest, restrict ECR account/repository, and produce an authenticated end-to-end publication proof.
2. Coding processes still need a reviewed filesystem/process boundary that hides other owners' records, shared repository metadata, publisher locks and reusable OpenAI credentials (including `/proc` access). `workspace-write` and redaction do not provide that boundary.
3. Configure the shared controller lock, kill the whole old task on server failure, and verify rollout/crash recovery on EFS with real active jobs. Deploy all trusted JobStore writers together; old writers do not honor the new protocol.
4. Put publisher lock storage on a publisher-only mount/identity. Persist each job's Codex session separately inside its permitted boundary.
5. Refresh or mint a short-lived publisher token during operation. Respect repository policy on automated PR creation; a long-lived credential is not a workaround.
6. Provide retained EFS reattachment and log-group restoration/import for stack recreation.
7. Use only isolated `eng-console-` resource names. #826 currently uses conflicting `inqsi-engineering-console` names and creates new roles/network resources; reconcile with the requested existing approved AWS identities/network.
8. Finish separate admin OIDC browser sign-in. The existing PIN cookie cannot substitute for a verified engineering JWT.
9. Reconcile reviewed publication-completion fixes in #829 before the live merge/receipt proof; this branch does not supersede that PR.
10. AWS execution credentials, protected environment references, private endpoint access, and a workflow-dispatch capability were unavailable in this session. No AWS integration was returned by plugin discovery.

## Deployment record

Status: **NOT DEPLOYED**. No production image digest, task revision, endpoint, authenticated health result, or successful Codex-to-PR-to-merge receipt is available. The pinned base-image manifest is not a production deployment digest.

Before activation, the reviewed release must record source SHA, tested/pushed ECR image digest, previous/new task-definition ARNs, retained filesystem/access-point IDs, private endpoint, authenticated end-to-end job/PR/check/merge/receipt evidence, and health results. Stop admission and drain jobs before rollback. Roll back only the isolated Console services to recorded compatible task revisions and image digest; retain state. Do not downgrade JobStore writers while any newer writer is active, and do not touch sports production resources.

## Validation evidence

Local `npm ci`, syntax checks and all **101 tests passed** on Node 24.19.0, including actual concurrent processes and real Git repositories. Docker Hub confirmed the Node 24.21.0 base-image manifest digest. Hosted image build is required because Docker is unavailable locally. Live EFS locking, real credentials, browser authentication, and AWS health are not covered by these local tests.
