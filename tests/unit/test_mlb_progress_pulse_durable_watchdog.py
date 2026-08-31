from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/mlb-progress-pulse-watchdog.yml"


def _trigger_block(workflow: str) -> str:
    return workflow.split('"on":\n', 1)[1].split("\n# Validation", 1)[0]


def test_durable_watchdog_has_a_non_cron_continuation_owner() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger = _trigger_block(workflow)

    assert "  workflow_dispatch:\n" in trigger
    assert "  push:\n" in trigger
    assert "  pull_request:\n" in trigger
    assert "  schedule:\n" not in trigger
    assert "  workflow_run:\n" not in trigger
    assert "group: mlb-progress-pulse-durable-watchdog" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "MLB_DURABLE_WATCHDOG_INTERVAL_SECONDS: '240'" in workflow
    assert "MLB_DURABLE_WATCHDOG_SEED_INTERVAL_SECONDS: '15'" in workflow
    assert "needs.validate-source.result == 'success'" in workflow
    assert "always()" in workflow


def test_durable_watchdog_dispatches_only_fixed_github_workflows() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    lowered = workflow.lower()

    assert 'MLB_PROGRESS_WORKFLOW: mlb-30m-progress-pulse.yml' in workflow
    assert 'MLB_DURABLE_WATCHDOG_WORKFLOW: mlb-progress-pulse-watchdog.yml' in workflow
    assert 'gh workflow run "$MLB_PROGRESS_WORKFLOW"' in workflow
    assert 'gh workflow run "$MLB_DURABLE_WATCHDOG_WORKFLOW"' in workflow
    assert "--ref main" in workflow
    assert "--field force=false" in workflow
    assert "test \"$GITHUB_REF_NAME\" = 'main'" in workflow

    for forbidden in (
        "aws-actions/configure-aws-credentials",
        "aws lambda invoke",
        "aws dynamodb",
        "aws cloudformation",
        "issues: write",
        "contents: write",
        "secrets.aws_",
        "put_item",
        "update_item",
        "delete_item",
        "promote_candidate",
    ):
        assert forbidden not in lowered


def test_durable_watchdog_prevents_duplicate_chains_and_proves_successor() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    for status in ("queued", "in_progress", "waiting", "pending", "requested"):
        assert f'"{status}"' in workflow
    assert '((.id | tostring) != $current)' in workflow
    assert '.event == "workflow_dispatch" and .id > $current' in workflow
    assert "durable_watchdog_successor_not_observable" in workflow
    assert "A queued/active durable successor already exists" in workflow
    assert "for attempt in $(seq 1 18)" in workflow
    assert "Schedule dependency: none" in workflow
    assert "External credential dependency: none" in workflow


def test_pull_request_execution_is_validation_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in workflow
    assert "python tests/unit/test_mlb_progress_pulse_durable_watchdog.py" in workflow
    assert "if: github.event_name != 'pull_request'" in workflow
    assert (
        "if: ${{ always() && github.event_name != 'pull_request' && "
        "needs.validate-source.result == 'success' }}"
    ) in workflow
    assert workflow.count("actions: write") == 2


def main() -> None:
    test_durable_watchdog_has_a_non_cron_continuation_owner()
    test_durable_watchdog_dispatches_only_fixed_github_workflows()
    test_durable_watchdog_prevents_duplicate_chains_and_proves_successor()
    test_pull_request_execution_is_validation_only()


if __name__ == "__main__":
    main()
