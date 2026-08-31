from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
QUEUE_WORKFLOW = ROOT / ".github/workflows/mlb-r7-overnight-advance.yml"
BOOTSTRAP_WORKFLOW = ROOT / ".github/workflows/bootstrap-unified-mlb-r7-recovery-now.yml"


def _trigger_block(workflow: str) -> str:
    return workflow.split('"on":\n', 1)[1].split("\npermissions:\n", 1)[0]


def test_overnight_queue_targets_r8_and_restores_scheduled_wakeups() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(workflow)

    assert "  workflow_dispatch:\n" in trigger
    assert "  schedule:\n" in trigger
    assert "cron: '7,37 * * * *'" in trigger
    assert "  push:\n" in trigger
    assert "  pull_request:\n" in trigger
    assert "America/New_York" in workflow
    assert "nyHour >= 0 && nyHour < 7" in workflow
    assert "group: mlb-r8-overnight-evidence-queue" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "mlb-v2-2026-08-31-historical-live-r8" in workflow
    assert "group: parlay-platform-deploy" not in workflow


def test_overnight_queue_seeds_durable_evidence_not_retired_r7_recovery() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert "MLB_DURABLE_WATCHDOG_WORKFLOW: mlb-progress-pulse-watchdog.yml" in workflow
    assert "MLB_PROGRESS_WORKFLOW: mlb-30m-progress-pulse.yml" in workflow
    assert "workflow_id: watchdogWorkflow" in workflow
    assert "workflow_id: progressWorkflow" in workflow
    assert "force: 'false'" in workflow
    assert "automaticTrainerOwner: 'eventbridge_schedule'" in workflow
    assert "legacyR7RecoveryDispatched: false" in workflow
    assert "durable_r8_watchdog_seed_not_observable" in workflow
    assert "unified-mlb-learning-recovery-once.yml" not in workflow
    assert "RECOVERY_TARGET_SLATE_DATE" not in workflow
    assert "RECOVERY_MIN_ACCEPTED_ROWS" not in workflow

    for forbidden in (
        "aws lambda invoke",
        "aws-actions/configure-aws-credentials",
        "aws cloudformation",
        "aws dynamodb",
        "workflow_id: 'deploy.yml'",
        "issues: write",
        "contents: write",
    ):
        assert forbidden not in lowered


def test_overnight_queue_deduplicates_active_and_recent_watchdogs() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")

    assert "MLB_DURABLE_WATCHDOG_RECENT_MINUTES: '12'" in workflow
    assert "recentSuccess" in workflow
    assert "recentCutoff" in workflow
    assert "baselineIds" in workflow
    assert "already_active:" in workflow
    assert "recent_success:" in workflow
    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        assert f"'{status}'" in workflow


def test_pull_request_execution_is_validation_only() -> None:
    workflow = QUEUE_WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert "python tests/unit/test_mlb_r7_overnight_queue_control.py" in workflow
    assert workflow.count("actions: write") == 1


def test_bootstrap_alias_remains_manual_and_cannot_invoke_aws() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(workflow)
    lowered = workflow.lower()

    assert "  workflow_dispatch:\n" in trigger
    assert "  workflow_run:" not in trigger
    assert "  schedule:" not in trigger
    assert "  push:" not in trigger
    assert "mlb-r7-overnight-advance.yml" in workflow
    assert "force: 'true'" in workflow
    assert "eventbridge remains the only automatic" in workflow.lower()

    for forbidden in (
        "workflow_id: 'deploy.yml'",
        "aws lambda invoke",
        "aws-actions/configure-aws-credentials",
        "aws cloudformation",
        "aws dynamodb",
    ):
        assert forbidden not in lowered


def main() -> None:
    test_overnight_queue_targets_r8_and_restores_scheduled_wakeups()
    test_overnight_queue_seeds_durable_evidence_not_retired_r7_recovery()
    test_overnight_queue_deduplicates_active_and_recent_watchdogs()
    test_pull_request_execution_is_validation_only()
    test_bootstrap_alias_remains_manual_and_cannot_invoke_aws()


if __name__ == "__main__":
    main()
