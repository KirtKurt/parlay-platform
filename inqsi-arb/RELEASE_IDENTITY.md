# Primary ARB Lambda release identity

## Scope

`GET /v1/arb/health` adds a `release` object that binds the primary ARB Lambda's
Python source files to a clean Git checkout revision and a specific workflow run
and attempt. This is a deployment-consistency check, not a signed attestation,
whole-environment SBOM, dependency verification, or proof of market coverage.
It does not grant the coding worker new permissions or resolve issue #813.

## Build and release

The existing release and bounded-repair workflows run
`python inqsi-arb/ops/release_proof.py stamp` before SAM build. The stamp rejects
modified tracked source, untracked Python source, symlinks, and a tracked release
manifest. It writes `src/arb_release_manifest.json` as a build-only artifact.
Never commit that generated manifest. The source checksum hashes sorted,
length-prefixed relative paths and bytes of `.py` files, excluding bytecode caches.

The primary Lambda uses `release_identity.lambda_handler`, which delegates all
application work to the existing handler and only adds health metadata. The
runtime recomputes the source hash before exposing a verified manifest; missing,
corrupt, or mismatched evidence is explicitly `unverified`. No credentials or
arbitrary manifest fields are reflected in health.

After the existing deploy command, the workflow runs
`python inqsi-arb/ops/release_proof.py verify --api-url "$API_URL"`.
The verifier requires healthy no-wager service, verified source digest, exact Git
SHA, file count, run ID, attempt and build timestamp. A different release cannot
pass merely because it reports the same `INQSI-ARB-v3` service version. A bounded
retry handles propagation; failure stays nonzero and produces an explicit failed
receipt. `arb-release-proof.json` is retained by the existing workflow artifact
mechanism. Existing ARB smoke/correctness checks remain necessary and unchanged.

## Verification and rollback

Run the complete ARB tests and SAM lint/build in PR CI before promotion. After
promotion inspect the release identity receipt and existing live smoke tests;
do not treat a green build as a deployed-version proof. Restamp any reviewed
rollback revision using that rollback run's identity before SAM build; do not
reuse an old run's receipt or manually insert an expected SHA. The bounded repair
workflow also stamps its actual checked-out main revision, not a possibly older
dispatch-event SHA. Repository/environment protections still apply.

This increment changes no Codex runner, credentials, permissions, agent schedule,
price authority, settlement gates, or unrelated prediction services. It does not
claim unattended draft publication, live executable opportunities, browser or
WebSocket journey completion, or the 24-hour observation window.
