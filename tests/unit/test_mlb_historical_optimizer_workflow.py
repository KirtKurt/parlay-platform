from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "mlb-historical-optimizer.yml"


def _workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_failed_create_stack_is_recovered_before_sam_deploy():
    text = _workflow_text()

    recovery = text.index("- name: Recover historical optimizer stack from failed create")
    deployment = text.index("- name: Deploy fail-closed historical optimizer")

    assert recovery < deployment
    assert "ROLLBACK_COMPLETE" in text[recovery:deployment]
    assert "aws cloudformation delete-stack" in text[recovery:deployment]
    assert "aws cloudformation wait stack-delete-complete" in text[recovery:deployment]
    assert "parlay-platform-mlb-historical-optimizer" in text[recovery:deployment]


def test_stack_recovery_is_fail_closed_for_unexpected_states():
    text = _workflow_text()
    recovery = text.index("- name: Recover historical optimizer stack from failed create")
    deployment = text.index("- name: Deploy fail-closed historical optimizer")
    block = text[recovery:deployment]

    assert "STACK_NOT_FOUND" in block
    assert "*_IN_PROGRESS" in block
    assert "Unexpected non-updateable historical optimizer stack status" in block
    assert "exit 1" in block


def test_recovery_does_not_authorize_paid_historical_calls():
    text = _workflow_text()

    assert "[authorize-mlb-historical-backfill]" in text
    assert "EXECUTE_PAID_BACKFILL" in text
    assert "if: env.EXECUTE_PAID_BACKFILL == 'true'" in text
    assert "AUTHORIZE_THE_ODDS_API_HISTORICAL_CREDITS" in text
