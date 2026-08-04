from __future__ import annotations

import re
from pathlib import Path


WORKFLOW = Path(".github/workflows/mlb-deploy-failure-record.yml")


def _job_condition(text: str) -> str:
    match = re.search(
        r"jobs:\n  record:\n(?:    #.*\n)*    if: >-\n(?P<condition>.*?)\n    runs-on:",
        text,
        flags=re.DOTALL,
    )
    assert match is not None
    return match.group("condition")


def test_cancelled_or_superseded_deploy_does_not_publish_failure_proof():
    text = WORKFLOW.read_text(encoding="utf-8")
    condition = _job_condition(text)

    assert "conclusion != 'success'" not in condition
    for conclusion in (
        "failure",
        "timed_out",
        "action_required",
        "startup_failure",
    ):
        assert f"conclusion == '{conclusion}'" in condition
    for conclusion in ("cancelled", "skipped", "neutral", "stale", "success"):
        assert f"conclusion == '{conclusion}'" not in condition


def test_failure_publisher_defends_terminal_conclusion_contract_at_runtime():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "terminal_conclusions = {" in text
    assert "if conclusion not in terminal_conclusions:" in text
    assert "nonTerminalCancellationIsFailure': False" in text
    assert "MLB-SCORING-FIX-POST-DEPLOY-v2-terminal-failures-only" in text
    assert "deploy_workflow_terminal_{conclusion}" in text
