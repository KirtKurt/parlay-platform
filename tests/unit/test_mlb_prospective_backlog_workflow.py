from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-prospective-backlog-reconcile-once.yml"
SCRIPT = ROOT / "scripts" / "reconcile_mlb_prospective_backlog.py"
UNIFIED_RECOVERY = (
    ROOT / ".github" / "workflows" / "unified-mlb-learning-recovery-once.yml"
)
SOURCE_CONTRACT = (
    ROOT / ".github" / "workflows" / "mlb-production-source-contract.yml"
)
DEPLOY_WORKFLOW = ROOT / ".github" / "workflows" / "deploy.yml"


def test_workflow_runs_bounded_reconciliation_before_training():
    source = WORKFLOW.read_text(encoding="utf-8")

    reconcile_at = source.index("Reconcile release-cutoff prospective slate backlog")
    trainer_at = source.index("Invoke trainer and prove usable prospective rows")
    assert reconcile_at < trainer_at
    assert "--max-slate-days 14" in source
    assert "prospective_backlog_reconciled_training" in source
    assert "accepted <= 0" in source
    assert "still has zero accepted rows" in source


def test_workflow_preserves_shadow_and_production_authority():
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "liveInferenceAuthority" in source
    assert "productionAuthorityChanged" in source
    assert "unexpectedly activated live inference authority" in source
    assert "unexpectedly changed production authority" in source
    assert "INQSI_MLB_ML_AUTO_PROMOTE" not in source
    assert "update-function-configuration" not in source
    assert "sam deploy" not in source


def test_reconciliation_script_has_no_direct_storage_writer():
    source = SCRIPT.read_text(encoding="utf-8")

    for forbidden in (
        "put_item(",
        "update_item(",
        "delete_item(",
        "batch_write_item(",
        "transact_write_items(",
        ".client(\"dynamodb\"",
        ".client('dynamodb'",
        ".resource(\"dynamodb\"",
        ".resource('dynamodb'",
    ):
        assert forbidden not in source
    assert "MLBDailyPickLockFunction" in source
    assert "MLBResultsSchedulerFunction" in source
    assert '"force": True' in source
    assert "postStartPredictionCreationAllowed" in source
    assert "immutablePredictionRewriteAllowed" in source


def test_unified_recovery_is_manual_exact_gap_then_target_only():
    source = UNIFIED_RECOVERY.read_text(encoding="utf-8")
    trigger = source.split("permissions:", 1)[0]

    assert "  workflow_dispatch:\n" in trigger
    assert "  push:" not in trigger
    assert "  schedule:" not in trigger
    assert "  workflow_run:" not in trigger
    gap_at = source.index('--target-slate-date "$REPAIR_SLATE_DATE"')
    target_at = source.index('--target-slate-date "$TARGET_SLATE_DATE"')
    assert gap_at < target_at
    assert "gap_report.get('selectedSlateDates') == [repair]" in source
    assert "report.get('selectedSlateDates') == [target]" in source
    assert "gap_report.get('lastSlateDateEt') == repair" in source
    assert "last_slate == target" in source


def test_unified_recovery_source_rebind_review_requeue_is_explicit_and_bounded():
    source = UNIFIED_RECOVERY.read_text(encoding="utf-8")
    trigger = source.split("permissions:", 1)[0]
    readiness_at = source.index("Wait for a quiet deploy queue and stable AWS runtime")
    remediation_at = source.index("Requeue protected source-binding review once")
    reconcile_at = source.index("Reconcile protected prospective settlement backlog")
    remediation = source[remediation_at:reconcile_at]

    assert "requeue_source_pull_proof_review_after_rebind:" in trigger
    assert "        default: false\n" in trigger
    assert "        type: boolean\n" in trigger
    assert (
        "if: ${{ inputs.requeue_source_pull_proof_review_after_rebind }}"
        in remediation
    )
    assert readiness_at < remediation_at < reconcile_at
    assert "LOGICAL_RESOURCE_ID = 'MLBDailyPickLockFunction'" in remediation
    assert "describe_stack_resource(" in remediation
    assert "FunctionName=function_name" in remediation
    assert (
        "EXPECTED_HANDLER = 'mlb_daily_pick_lock_protected.lambda_handler'"
        in remediation
    )
    assert "'run': EXPECTED_RUN" in remediation
    assert "'slateDateEt': repair_slate" in remediation
    assert "INCIDENT_SLATE_DATE = '2026-08-04'" in remediation
    assert "repair_slate != INCIDENT_SLATE_DATE" in remediation
    assert "'force': True" in remediation
    assert "'requeueSourcePullProofReviewAfterRebind'" in remediation
    assert "'MLB-STATUS-SOURCE-PULL-REBIND-v1-strong-immutable-row'" in remediation
    assert "'QUEUED_FOR_EVENTBRIDGE_LOCK_OWNER'" in remediation
    assert "'CLAIMED': 'CLAIMED_BY_EVENTBRIDGE_LOCK_OWNER'" in remediation
    assert "'COMPLETED': 'COMPLETED_BY_EVENTBRIDGE_LOCK_OWNER'" in remediation
    assert "'ACKNOWLEDGED': 'ACKNOWLEDGED_COMPLETION'" in remediation
    assert "if not remediation_idempotent and replay_state != 'QUEUED':" in remediation
    assert "durable_history = application.get(" in remediation
    assert "nested_durable_history = replay.get(" in remediation
    assert "nested_durable_history is not None" in remediation
    assert "nested_durable_history is not True" in remediation
    assert (
        "durable_history is not None\n"
        "              and durable_history is not True"
        in remediation
    )
    assert "durable_history not in (None, True)" not in source
    assert "official-read-bound no-op path" in remediation
    assert "durably fences every duplicate queue" in remediation
    assert "expected_completed = replay_state in COMPLETED_STATES" in remediation
    assert "replay.get('slateDateEt') != event['slateDateEt']" in remediation
    assert "'rawLambdaPayloadPersisted': False" in remediation
    assert "'requestIdentifierExposed': False" in remediation
    assert "'checkpointMaterialExposed': False" in remediation
    assert "'automaticRequeueAllowed': False" in remediation
    assert "source-pull-rebind-review-remediation.json" in remediation
    for field in (
        "activeLeaseMutationAllowed",
        "postStartPredictionCreationAllowed",
        "immutablePredictionRewriteAllowed",
        "directWorkflowTableWrite",
        "productionAuthorityChanged",
    ):
        assert field in remediation
    for forbidden in (
        "boto3.client('dynamodb'",
        'boto3.client("dynamodb"',
        ".update_item(",
        ".put_item(",
        ".delete_item(",
        "transact_write_items(",
        "while ",
        "for attempt in",
    ):
        assert forbidden not in remediation


def test_unified_recovery_prelock_review_v2_is_internal_proof_bound():
    source = UNIFIED_RECOVERY.read_text(encoding="utf-8")
    workflow = yaml.safe_load(source)
    deploy_workflow = yaml.safe_load(
        DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    )
    assert workflow["concurrency"] == {
        "group": "unified-mlb-learning",
        "cancel-in-progress": False,
    }
    recovery_job = workflow["jobs"]["recover-and-verify"]
    assert recovery_job["concurrency"] == {
        "group": "parlay-platform-deploy",
        "cancel-in-progress": False,
    }
    assert recovery_job["concurrency"] == deploy_workflow["concurrency"]
    workflow_dispatch = workflow["on"]["workflow_dispatch"]
    input_config = workflow_dispatch["inputs"][
        "requeue_prelock_candidate_review_after_installed_runtime_proof_v2"
    ]
    assert {
        key: input_config.get(key)
        for key in ("required", "default", "type")
    } == {
        "required": False,
        "default": False,
        "type": "boolean",
    }

    steps = workflow["jobs"]["recover-and-verify"]["steps"]
    reject_ambiguous = next(
        step
        for step in steps
        if step.get("name") == "Reject ambiguous remediation flags"
    )
    assert reject_ambiguous["env"] == {
        "REQUEUE_SOURCE_PULL_V1": (
            "${{ inputs.requeue_source_pull_proof_review_after_rebind }}"
        ),
        "REQUEUE_PRELOCK_V2": (
            "${{ inputs."
            "requeue_prelock_candidate_review_after_installed_runtime_proof_v2 }}"
        ),
    }
    v2_step = next(
        step
        for step in steps
        if step.get("name")
        == "Requeue protected prelock review after installed-runtime proof v2"
    )
    assert v2_step["if"] == (
        "${{ inputs."
        "requeue_prelock_candidate_review_after_installed_runtime_proof_v2 }}"
    )
    assert v2_step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    run = v2_step["run"]
    embedded = run.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    compile(embedded, "<prelock-review-v2-workflow>", "exec")

    readiness_at = source.index(
        "Wait for a quiet deploy queue and stable AWS runtime"
    )
    v1_at = source.index("Requeue protected source-binding review once")
    v2_at = source.index(
        "Requeue protected prelock review after installed-runtime proof v2"
    )
    reconcile_at = source.index(
        "Reconcile protected prospective settlement backlog"
    )
    remediation = source[v2_at:reconcile_at]

    assert readiness_at < v1_at < v2_at < reconcile_at
    readiness = source[readiness_at:v1_at]
    assert "noncompleted_runs = [" in readiness
    assert "if noncompleted_runs:" in readiness
    assert (
        "A deploy run became queued or active while "
        in readiness
    )
    assert "recovery owns the shared deploy lock" in readiness
    assert "not active_runs" not in readiness
    assert "Reject ambiguous remediation flags" in source
    assert (
        'REQUEUE_SOURCE_PULL_V1" == "true" && '
        '"$REQUEUE_PRELOCK_V2" == "true"'
        in source
    )
    assert (
        'if [[ "$REQUEUE_SOURCE_PULL_V1" == "true" ]]; then'
        in source
    )
    assert (
        "The v1 source-pull remediation is durably consumed and disabled"
        in source
    )
    assert "LOGICAL_RESOURCE_ID = 'MLBDailyPickLockFunction'" in remediation
    assert (
        "EXPECTED_HANDLER = 'mlb_daily_pick_lock_protected.lambda_handler'"
        in remediation
    )
    assert "INCIDENT_SLATE_DATE = '2026-08-04'" in remediation
    assert (
        "'requeuePrelockCandidateReviewAfterInstalledRuntimeProofV2'"
        in remediation
    )
    assert "'force': True" in remediation
    assert "'installedRuntimePositiveProofBound': True" in remediation
    assert (
        "'priorSourcePullRebindRemediationValidated': True"
        in remediation
    )
    assert "'automaticRetryAllowed': False" in remediation
    assert "'rawLambdaPayloadPersisted': False" in remediation
    assert "'requestIdentifierExposed': False" in remediation
    assert "'checkpointMaterialExposed': False" in remediation
    assert "'positiveProofMaterialExposed': False" in remediation
    assert "'automaticRequeueAllowed': False" in remediation
    assert "prelock-candidate-review-remediation-v2.json" in remediation
    assert "github_ref != 'refs/heads/main'" in remediation
    assert "deploy.yml/runs?branch=main&per_page=100" in remediation
    assert "status=success" not in remediation
    assert "main_ref.get('object')" in remediation
    assert "main_object.get('sha') != commit_sha" in remediation
    assert "latest.get('head_sha') != commit_sha" in remediation
    assert "latest.get('status') != 'completed'" in remediation
    assert "latest.get('conclusion') != 'success'" in remediation
    assert "parameters.get('DeployGitSha') != commit_sha" in remediation
    assert (
        "parameters.get('DeployRunId')\n"
        "                  != expected_deploy_run_id"
        in remediation
    )
    assert "INQSI_DEPLOY_GIT_SHA" in remediation
    assert "INQSI_DEPLOY_RUN_ID" in remediation
    assert remediation.count("latest_exact_deploy()") >= 4
    assert remediation.count("require_exact_stack_deployment()") >= 4
    assert "def exact_deploy_identity(" in remediation
    assert "type(run_id) is not int" in remediation
    assert "type(run_attempt) is not int" in remediation
    assert "get('run_attempt') or 1" not in remediation
    assert "latest_before_invoke_identity" in remediation
    assert "latest_after_invoke_identity" in remediation
    assert "post_invocation_configuration" in remediation
    invoke_at = remediation.index("invocation = lambda_client.invoke(")
    pre_configuration_at = remediation.index(
        "invocation_configuration ="
    )
    pre_latest_at = remediation.index("latest_before_invoke =")
    pre_stack_at = remediation.rindex(
        "require_exact_stack_deployment()",
        0,
        invoke_at,
    )
    post_configuration_at = remediation.index(
        "post_invocation_configuration =",
        invoke_at,
    )
    post_latest_at = remediation.index(
        "latest_after_invoke =",
        invoke_at,
    )
    post_stack_at = remediation.index(
        "require_exact_stack_deployment()",
        post_latest_at,
    )
    assert (
        pre_configuration_at
        < pre_latest_at
        < pre_stack_at
        < invoke_at
        < post_configuration_at
        < post_latest_at
        < post_stack_at
    )
    assert "type(lambda_status) is not int" in remediation
    assert "type(application_status) is not int" in remediation
    assert "application_status = int(" not in remediation
    assert "sameCommitDeploySuccessRequired': True" in remediation
    assert "pinned_revision_id" in remediation
    assert "pinned_code_sha256" in remediation
    assert "'RevisionId': pinned_revision_id" in remediation
    assert "'CodeSha256': pinned_code_sha256" in remediation
    assert (
        "durable_history is not None\n"
        "                  and durable_history is not True"
        in remediation
    )
    assert "durable_history not in (None, True)" not in source
    assert remediation.count("lambda_client.invoke(") == 1
    assert "dynamodb" not in remediation.lower()
    for forbidden in (
        "candidateSnapshotFingerprint",
        "predictionSourcePullFingerprint",
        ".update_item(",
        ".put_item(",
        ".delete_item(",
        "transact_write_items(",
        "while ",
        "for attempt in",
    ):
        assert forbidden not in remediation

def test_unified_recovery_binds_numeric_proof_to_requested_run_evidence():
    source = UNIFIED_RECOVERY.read_text(encoding="utf-8")

    assert "MIN_ACCEPTED_ROWS: '39'" in source
    assert "Capture pre-recovery R7 numeric baseline" in source
    assert "r7-before.json" in source
    assert "'latest_status_stale'" in source
    assert "'latest_status_deployment_identity_mismatch'" in source
    assert "health_errors <= allowed_health_errors" in source
    assert "deployment_identity_contract_ok" in source
    assert "status.get('requestedRunEvidence')" in source
    assert "training_evidence.get('run')" in source
    assert "selection_evidence.get('run')" in source
    assert "latest.get('runId') == training_run_id" in source
    assert "selection_run.get('runId') == selection_run_id" in source
    assert "health.get('latestRun')" not in source
    assert "selection_health.get('latestRun')" not in source
    assert "accepted > before_accepted" in source
    assert "train_count > before_train_count" in source
    assert "repair in finalized" in source
    assert "repair in processed" in source
    assert "before.get('champion') == status.get('champion')" in source
    assert "status.get('champion') in (None, {})" in source
    assert "training_evidence.get('deploymentIdentityMatches') is True" in source
    assert "selection_evidence.get('deploymentIdentityMatches') is True" in source
    for field in (
        "productionAuthorityChanged",
        "immutablePredictionRewriteAllowed",
        "postStartPredictionCreationAllowed",
        "otherSportChanged",
    ):
        assert f"latest.get('{field}')" in source
        assert f"latest.get('{field}') is False" in source


def test_source_contract_triggers_for_non_mlb_prefixed_recovery_tests():
    source = SOURCE_CONTRACT.read_text(encoding="utf-8")
    pull_request_paths = source.split("  pull_request:", 1)[1].split(
        "  push:", 1
    )[0]
    push_paths = source.split("  push:", 1)[1].split(
        "  workflow_dispatch:", 1
    )[0]
    required = (
        "tests/unit/test_reconcile_mlb_prospective_backlog_v5_settlement_replay.py",
        "tests/unit/test_unified_mlb_learning_ownership.py",
    )
    for path in required:
        assert f"      - '{path}'" in pull_request_paths
        assert f"      - '{path}'" in push_paths


def test_unified_recovery_does_not_confuse_current_slate_maturity_with_integrity():
    source = UNIFIED_RECOVERY.read_text(encoding="utf-8")
    before_step = source.split(
        "- name: Prove current integrity and August 25 immutable coverage before settlement",
        1,
    )[1].split("- name: Reconcile protected prospective settlement backlog", 1)[0]
    after_step = source.split(
        "- name: Prove current-slate integrity did not regress", 1
    )[1].split("- name: Upload complete recovery evidence", 1)[0]

    before_commands = before_step.split(
        "python scripts/mlb_scoring_guard_status.py", 2
    )
    assert len(before_commands) == 3
    current_before_command = before_commands[1].split(
        "python scripts/verify_mlb_recovery_current_slate_guard.py", 1
    )[0]
    target_command = before_commands[2].split("python - <<'PY'", 1)[0]
    current_after_command = after_step.split(
        "python scripts/mlb_scoring_guard_status.py", 1
    )[1].split("python scripts/verify_mlb_recovery_current_slate_guard.py", 1)[0]

    assert "--enforce" not in current_before_command
    assert "--enforce" not in current_after_command
    assert "--enforce" in target_command
    assert "verify_mlb_recovery_current_slate_guard.py" in before_step
    assert "verify_mlb_recovery_current_slate_guard.py" in after_step
    assert "--before /tmp/unified-mlb-recovery/production-before.json" in before_step
    assert "--after /tmp/unified-mlb-recovery/production-after.json" in after_step
    assert '--target-slate-date "$REPAIR_SLATE_DATE"' in source
    assert '--target-slate-date "$TARGET_SLATE_DATE"' in source
    assert "accepted >= minimum" in source
    assert "train_count >= minimum" in source
    assert "productionAuthorityChanged') is False" in source
