# Isolated Console deployment

The dedicated workflow tests and packages one source revision, then loads that same artifact in the protected deployment job. It verifies the archive checksum, image ID and source label, pushes to an existing `eng-console-*` ECR repository in the authenticated account, and deploys by immutable digest. It accepts no arbitrary image input.

Only the `eng-console-runtime` stack is targeted. Existing IAM roles, VPC, subnets, security groups, hosted zone, certificate and secret references are parameters; the template does not create replacement roles or security groups.

## Protected GitHub environment

Configure `engineering-console-production` with existing approved references:

| Variable | Value |
| --- | --- |
| `AWS_REGION` | Approved AWS region |
| `ENG_CONSOLE_DEPLOY_ROLE_ARN` | Existing role trusted by GitHub OIDC for this repository/environment |
| `ENG_CONSOLE_ECR_REPOSITORY` | Existing repository with an `eng-console-` prefix |
| `ENG_CONSOLE_PARAMETERS_JSON` | JSON object of template parameter names and string values, excluding `ImageUri` and `SourceSha` |

Required names and patterns are authoritative in [template.yaml](template.yaml). Preflight prints missing parameter names, never secret values. Supply Controller/Broker/Publisher/Job/Probe execution roles, Controller/Broker/Publisher task roles, four security groups, VPC and two private subnets in different availability zones, domain/zone/certificate, OIDC issuer/JWKS/audience/client/admin claim, model and secret references.

The job group must have no ingress and only TCP 443 egress to approved groups or prefix lists. Destinations must include the private Console ALB, ECR API/Docker endpoints, CloudWatch Logs endpoint and regional S3 endpoint for image layers. No general internet egress is permitted. Trusted service groups require their approved access to GitHub, OpenAI, OIDC, EFS and applicable AWS APIs. The ALB group permits the approved administrator network and job group; trusted services accept ALB traffic on 8787/8790. EFS accepts 2049 only from trusted services. The private probe verifies actual connectivity.

Existing task roles must be distinct and narrowly scoped. The controller needs ECS describe/list/run/stop for the Console cluster/job definition and `iam:PassRole` for the existing job execution role only. Trusted roles need EFS mount/write for their designated access points. Only the publisher retrieves its GitHub App secret. Coding and probe tasks have no task role. Execution roles pull images/write logs; only relevant trusted service/probe execution roles retrieve injected secrets. No secrets are injected into coding tasks.

| Secret parameter | Stored value |
| --- | --- |
| `OpenAISecretArn` | Raw API key, injected only into broker |
| `GitHubAppSecretArn` | JSON: `appId`, `installationId`, `privateKey` |
| `BrowserOidcSecretArn` | JSON: `clientSecret`, `sessionKey` (at least 32 bytes) |
| `ProbeOidcSecretArn` | JSON: `clientId`, `clientSecret` for approved OIDC machine grant |

Register `https://<ConsoleDomainName>/auth/callback` for browser login. The issuer must issue signed access JWTs for `OidcAudience` with `AdminClaim`; the probe client must support `client_credentials` with that audience/admin claim. Browser access-token and ID-token subjects must match. No PIN or bypass token is accepted. GitHub App policy must permit proof PR creation; do not substitute a personal token to bypass policy.

## Verification and recovery

Preflight rejects reused task-role ARNs across the controller, broker and publisher. The approved deployment role also needs narrowly scoped ECS StopTask/DescribeTasks/ListTasks access for the Console cluster, because verification deliberately replaces its controller while a controlled coding job is active.

Run **Engineering Console Deploy** manually from reviewed `main`. It waits for one controller, broker and publisher using the expected image/task definitions. A probe inside the private VPC checks source identity and unauthenticated rejection, obtains an OIDC token, runs a real Codex job, waits for the dedicated proof check and publisher merge, then verifies the PR/head/merge SHA and generated file contents. ECS health alone cannot pass deployment. The `eng-console-deployment-<run-id>` artifact records old/new definitions, image, source, endpoint, task identities and proof receipt.

The controlled job first waits 90 seconds. Once its task identity, thread and event checkpoint are durable, the private probe emits a recovery request. Deployment verifies that coding is still active, stops only the observed Console controller, waits for its replacement with the same verified definition/image, and requires the replacement to recover the same execution and checkpoint through EFS. The probe must observe a new controller instance, unchanged coding task/session identity and subsequent publication completion. Deployment checks the ECS running/stopped inventory for duplicate executions and retains both controller identities and the recovery receipt. A clean job-to-merge probe without this recovery evidence cannot mark deployment verified. Local tests simulate AWS observations; only the protected deployment establishes live results.

Human browser sign-in remains an additional acceptance check before calling administrator access fully verified. Confirmed terminal coding tasks remove their input archive and raw prompt from transport storage; only checkpoint/result material needed for reconciliation or continuation remains.

The filesystem is encrypted, backed up and retained. Normal updates reuse it. After stack deletion, explicitly provide the retained `ExistingFileSystemId`; preflight refuses to silently create empty state if it finds retained Console data. Keep `ExistingMountTargetsPresent=false` when old targets were deleted, so the template recreates them. Use `true` only for an existing complete matching pair. The storage mode stays fixed for an existing stack. Services created with new targets explicitly depend on both targets; the mutually exclusive external-target service resources use the preflight-verified existing pair. Log groups have stack-generated names and retention policies, avoiding collisions on recreation.

For rollback, stop admission and drain or confirm STOPPED for coding jobs. Restore only recorded compatible Console task definitions/images; retain state. Do not run older JobStore writers alongside this version. Cancellation stays pending until task/PR closure is confirmed. A verified merge after accepted cancellation is a durable reconciliation conflict.
