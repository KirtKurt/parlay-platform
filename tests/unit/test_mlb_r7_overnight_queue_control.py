from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUEUE_WORKFLOW = ROOT / ".github/workflows/mlb-r7-overnight-advance.yml"
BOOTSTRAP_WORKFLOW = ROOT / ".github/workflows/bootstrap-unified-mlb-r7-recovery-now.yml"


def _trigger_block(workflow: str) -> str:
    return workflow.split('"on":\n', 1)[1].split("\npermissions:\n", 1)[0]


def test_overnight_queue_has_dst_safe_schedule_and_dedicated_concurrency() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(workflow)

    assert "  schedule:\n" in trigger
    assert "17,47 4-11 * * *" in trigger
    assert "America/New_York" in workflow
    assert "nyHour >= 0 && nyHour < 7" in workflow
    assert "group: mlb-r7-overnight-queue-control" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "group: parlay-platform-deploy" not in workflow


def test_overnight_queue_dispatches_only_the_canonical_recovery() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert "const recoveryWorkflow = 'unified-mlb-learning-recovery-once.yml';" in workflow
    assert "workflow_id: recoveryWorkflow" in workflow
    assert "request_id: requestId" in workflow
    assert "context.runId" in workflow
    assert "toISOString()" in workflow
    assert "RECOVERY_EPOCH_UTC" in workflow
    assert "run.conclusion === 'success'" in workflow
    assert "automaticTrainerOwner: 'eventbridge_schedule'" in workflow

    for forbidden in (
        "aws lambda invoke",
        "aws-actions/configure-aws-credentials",
        "aws cloudformation",
        "aws dynamodb",
        "workflow_id: 'deploy.yml'",
    ):
        assert forbidden not in lowered


def test_overnight_queue_serializes_active_and_legacy_recovery_runs() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")

    assert "bootstrapDrainDeadline" in workflow
    assert "activeBootstrap" in workflow
    assert "activeRecovery" in workflow
    assert "canonical_recovery_dispatch_not_observable" in workflow
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        assert f"'{status}'" in workflow


def test_bootstrap_is_manual_only_and_cannot_deploy_or_invoke_aws() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(workflow)
    lowered = workflow.lower()

    assert "  workflow_dispatch:\n" in trigger
    assert "  workflow_run:" not in trigger
    assert "  schedule:" not in trigger
    assert "  push:" not in trigger
    assert "mlb-r7-overnight-advance.yml" in workflow
    assert "force: 'true'" in workflow
    assert "bootstrap-mlb-r7-20260826" not in workflow

    for forbidden in (
        "workflow_id: 'deploy.yml'",
        "aws lambda invoke",
        "aws-actions/configure-aws-credentials",
        "aws cloudformation",
        "aws dynamodb",
    ):
        assert forbidden not in lowered


def main() -> None:
    test_overnight_queue_has_dst_safe_schedule_and_dedicated_concurrency()
    test_overnight_queue_dispatches_only_the_canonical_recovery()
    test_overnight_queue_serializes_active_and_legacy_recovery_runs()
    test_bootstrap_is_manual_only_and_cannot_deploy_or_invoke_aws()


if __name__ == "__main__":
    main()
