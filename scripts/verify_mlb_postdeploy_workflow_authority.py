#!/usr/bin/env python3
"""Static fail-closed contract for MLB post-deploy provenance and publication."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = ".github/workflows/deploy.yml"
POSTDEPLOY_WORKFLOW = ".github/workflows/mlb-post-deploy-fix-verification.yml"
PUBLISHER = "scripts/publish_mlb_postdeploy_proof.py"


def _job(source: str, job_id: str) -> str:
    matches = list(
        re.finditer(
            rf"(?ms)^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            source,
        )
    )
    return matches[0].group("body") if len(matches) == 1 else ""


def _step(source: str, name: str) -> str:
    matches = list(
        re.finditer(
            rf"(?ms)^      - name: {re.escape(name)}\s*$\n"
            r"(?P<body>.*?)(?=^      - (?:name: |uses:)|\Z)",
            source,
        )
    )
    return matches[0].group("body") if len(matches) == 1 else ""


def verify_repository(root: Path = ROOT) -> List[str]:
    errors: List[str] = []
    deploy_path = root / DEPLOY_WORKFLOW
    postdeploy_path = root / POSTDEPLOY_WORKFLOW
    publisher_path = root / PUBLISHER
    deploy = (
        deploy_path.read_text(encoding="utf-8") if deploy_path.is_file() else ""
    )
    postdeploy = (
        postdeploy_path.read_text(encoding="utf-8")
        if postdeploy_path.is_file()
        else ""
    )
    publisher = (
        publisher_path.read_text(encoding="utf-8")
        if publisher_path.is_file()
        else ""
    )

    if not deploy:
        errors.append("postdeploy_contract:deploy_workflow_missing")
    else:
        deploy_job = _job(deploy, "deploy")
        dispatch_job = _job(deploy, "dispatch-postdeploy")
        deploy_step = _step(deploy, "Deploy exact canonical source")
        for token in (
            "    name: deploy\n",
            "    outputs:\n",
            "      deployment_succeeded: ${{ steps.deploy.outputs.deployment_succeeded }}\n",
            "      deployed_sha: ${{ steps.deploy.outputs.deployed_sha }}\n",
        ):
            if token not in deploy_job:
                errors.append("postdeploy_contract:deploy_output_binding_missing:" + token.strip())
        if not deploy_step:
            errors.append("postdeploy_contract:sam_step_missing_or_duplicated")
        else:
            ordered = (
                deploy_step.find("sam deploy"),
                deploy_step.find('echo "deployment_succeeded=true" >> "$GITHUB_OUTPUT"'),
                deploy_step.find('echo "deployed_sha=$GITHUB_SHA" >> "$GITHUB_OUTPUT"'),
            )
            if not (ordered[0] >= 0 and ordered[0] < ordered[1] < ordered[2]):
                errors.append("postdeploy_contract:sam_outputs_not_after_successful_deploy")
            if "continue-on-error:" in deploy_step:
                errors.append("postdeploy_contract:sam_step_may_hide_failure")
        for token in (
            "    needs: deploy\n",
            "    if: ${{ always() && needs.deploy.outputs.deployment_succeeded == 'true' && needs.deploy.outputs.deployed_sha != '' }}\n",
            "      actions: write\n",
            "          DEPLOYED_SHA: ${{ needs.deploy.outputs.deployed_sha }}\n",
            '-f "target_deploy_sha=$DEPLOYED_SHA"',
            '-f "source_deploy_run_id=$GITHUB_RUN_ID"',
            '-f "source_deploy_run_attempt=$GITHUB_RUN_ATTEMPT"',
        ):
            if token not in dispatch_job:
                errors.append("postdeploy_contract:dispatcher_binding_missing:" + token.strip())
        if any(token in dispatch_job for token in ("actions/checkout", "aws ", "sam deploy")):
            errors.append("postdeploy_contract:dispatcher_not_isolated")
        if deploy.count("actions: write") != 1:
            errors.append("postdeploy_contract:deploy_actions_write_scope_invalid")

    if not postdeploy:
        errors.append("postdeploy_contract:verification_workflow_missing")
    else:
        trigger_match = re.search(
            r"(?ms)^on:\n(?P<body>.*?)(?=^permissions:\n)", postdeploy
        )
        trigger = trigger_match.group("body") if trigger_match else ""
        for token in (
            "  workflow_run:\n",
            '    workflows: ["Deploy SAM to AWS"]\n',
            "    types: [completed]\n",
            "  workflow_dispatch:\n",
            "      target_deploy_sha:\n",
            "      source_deploy_run_id:\n",
            "      source_deploy_run_attempt:\n",
        ):
            if token not in trigger:
                errors.append("postdeploy_contract:trigger_or_input_missing:" + token.strip())
        if trigger.count("        required: false\n") != 3:
            errors.append("postdeploy_contract:dispatch_inputs_must_be_optional")
        if re.search(r"(?m)^  (?:push|schedule|repository_dispatch):", trigger):
            errors.append("postdeploy_contract:unapproved_trigger")

        resolve_job = _job(postdeploy, "resolve")
        verify_job = _job(postdeploy, "verify")
        publish_job = _job(postdeploy, "publish")
        for token in (
            "      actions: read\n",
            'canonical_path=".github/workflows/deploy.yml"',
            'canonical_name="Deploy SAM to AWS"',
            'sam_step_name="Deploy exact canonical source"',
            "actions/workflows/deploy.yml",
            "actions/runs/$run_id/attempts/$run_attempt/jobs?per_page=100",
            ".workflow_id == $workflow_id",
            ".path == $path",
            ".head_repository.full_name == $repo",
            '.head_branch == "main"',
            '.conclusion == "success"',
            "source_deploy_sam_completed_at",
            "candidate_sam_completed_at",
            "best_sam_epoch",
            "find_latest_successful_sam_source",
            "explicit_count == 3",
            "source_superseded_by_newer_successful_sam_deployment",
            "canonical_direct_verifier_already_active_or_successful",
        ):
            if token not in resolve_job:
                errors.append("postdeploy_contract:source_attestation_missing:" + token)
        for token in (
            "    needs: resolve\n",
            "needs.resolve.outputs.should_verify == 'true'",
            "TARGET_DEPLOY_SHA: ${{ needs.resolve.outputs.target_deploy_sha }}",
            "SOURCE_DEPLOY_RUN_ATTEMPT: ${{ needs.resolve.outputs.source_deploy_run_attempt }}",
            'git merge-base --is-ancestor "$TARGET_DEPLOY_SHA" origin/main',
            "'deploymentSucceeded': True",
            "'sourceDeployRunId': os.environ['SOURCE_DEPLOY_RUN_ID']",
            "'sourceDeployRunAttempt': int(os.environ['SOURCE_DEPLOY_RUN_ATTEMPT'])",
            "'sourceDeployRunNumber': int(os.environ['SOURCE_DEPLOY_RUN_NUMBER'])",
            "'sourceDeployWorkflowPath': os.environ['SOURCE_DEPLOY_WORKFLOW_PATH']",
            "'sourceDeploySamCompletedAtUtc': os.environ['SOURCE_DEPLOY_SAM_COMPLETED_AT']",
            "'sourceDeploySamStepConclusion': 'success'",
            "Upload exact publication candidate",
        ):
            if token not in verify_job:
                errors.append("postdeploy_contract:verification_binding_missing:" + token)
        for token in (
            "    needs: [resolve, verify]\n",
            "      contents: write\n",
            "    concurrency:\n",
            "      group: mlb-postdeploy-proof-publication\n",
            "      cancel-in-progress: false\n",
            "actions/download-artifact@v4",
            "python scripts/publish_mlb_postdeploy_proof.py",
        ):
            if token not in publish_job:
                errors.append("postdeploy_contract:global_publication_missing:" + token.strip())
        if "queue:" in postdeploy:
            errors.append("postdeploy_contract:unsupported_concurrency_queue_key")
        if postdeploy.count("contents: write") != 1:
            errors.append("postdeploy_contract:publication_write_scope_invalid")
        if "actions: write" in postdeploy:
            errors.append("postdeploy_contract:verifier_actions_write_forbidden")
        if "github.event.workflow_run.head_sha || github.sha" in postdeploy:
            errors.append("postdeploy_contract:unattested_sha_fallback_present")

    if not publisher:
        errors.append("postdeploy_contract:source_monotonic_publisher_missing")
    else:
        for token in (
            "def validate_provenance(",
            "sourceDeployRunNumber",
            "sourceDeployRunAttempt",
            "sourceDeploySamCompletedAtUtc",
            "PROVENANCE_MARKERS",
            "same_source_retry_repaired_evidence",
            "same_source_success_cannot_regress",
            "deployed_source_regression",
            "newer_deployed_source",
            "same source has conflicting immutable identity",
        ):
            if token not in publisher:
                errors.append("postdeploy_contract:publisher_guard_missing:" + token)

    return sorted(set(errors))


def main() -> int:
    errors = verify_repository()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("MLB post-deploy provenance and publication authority verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
