from __future__ import annotations

from pathlib import Path

import pytest

from engineering_agent.outcome import build_no_action_receipt, validate_no_action_receipt


def _attempts(count: int = 5):
    return [
        {
            "attempt": index,
            "attemptBudget": count,
            "requestedAttemptBudget": 3,
            "title": f"Rejected task {index}",
            "domain": "calibration",
            "ok": True,
            "mode": "engineering_plan",
            "rejected": "ValueError: task duplicates engineering decision history: prior task (similarity=1.00)",
        }
        for index in range(1, count + 1)
    ]


def _log(count: int = 5) -> str:
    return (
        "Traceback (most recent call last):\n"
        f"RuntimeError: no acceptable planner task after {count} bounded attempts (requested=3)\n"
    )


def test_bounded_policy_exhaustion_becomes_explicit_read_only_no_action_receipt() -> None:
    attempts = _attempts()
    receipt = build_no_action_receipt(
        runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
    )
    assert receipt["status"] == "NO_ACTION"
    assert receipt["reason"] == "BOUNDED_PLANNER_EXHAUSTED"
    assert receipt["attemptCount"] == receipt["attemptBudget"] == 5
    assert receipt["requestedAttemptBudget"] == 3
    assert receipt["waitFor"] == "NEW_OPERATIONAL_EVIDENCE_OR_DISTINCT_VALID_TASK"
    assert receipt["readOnly"] is True
    for key in (
        "taskPublished",
        "productionAuthorityChanged",
        "directProductionDeployAllowed",
        "mainBranchWriteAllowed",
        "modelPromotionAllowed",
        "secretMutationAllowed",
        "otherSportChangeAllowed",
    ):
        assert receipt[key] is False
    assert validate_no_action_receipt(receipt, attempts) is receipt


def test_unrelated_runtime_failure_never_becomes_no_action() -> None:
    with pytest.raises(ValueError, match="did not fail by bounded task exhaustion"):
        build_no_action_receipt(
            runtime_log="RuntimeError: planner Lambda FunctionError",
            attempts=_attempts(),
            task_published=False,
            response_published=False,
        )


@pytest.mark.parametrize(
    ("title", "ok", "mode", "reason", "message"),
    [
        ("<lambda-failure>", False, "engineering_plan", "planner Lambda failed: provider unavailable", "planner/runtime failure"),
        ("<empty>", True, "engineering_plan", "planner Lambda returned empty task text", "planner/runtime failure"),
        ("<unparsed>", True, "engineering_plan", "JSONDecodeError: invalid JSON", "planner/runtime failure"),
        ("Candidate task", True, "engineering_plan", "ValueError: missing required safety receipts: no_secret_mutation", "non-policy planner rejection"),
        ("Candidate task", True, "engineering_plan", "ValueError: likelyFiles must be an array", "non-policy planner rejection"),
    ],
)
def test_bounded_planner_degradation_stays_a_hard_failure(title, ok, mode, reason, message) -> None:
    attempts = _attempts()
    attempts[2].update(title=title, ok=ok, mode=mode, rejected=reason)
    with pytest.raises(ValueError, match=message):
        build_no_action_receipt(
            runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
        )


def test_explicit_task_safety_rejection_is_eligible_for_no_action_receipt() -> None:
    attempts = _attempts()
    attempts[0]["rejected"] = (
        "ValueError: prospective holdout is underpowered; do not tune calibration "
        "against incomplete prospective evaluation"
    )
    receipt = build_no_action_receipt(
        runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
    )
    assert receipt["status"] == "NO_ACTION"


@pytest.mark.parametrize("published", ["task", "response"])
def test_exhaustion_with_published_authority_artifact_is_rejected(published: str) -> None:
    with pytest.raises(ValueError, match="must not publish"):
        build_no_action_receipt(
            runtime_log=_log(),
            attempts=_attempts(),
            task_published=published == "task",
            response_published=published == "response",
        )


def test_exhaustion_requires_sequential_uniformly_rejected_ledger() -> None:
    attempts = _attempts()
    attempts[2]["accepted"] = True
    with pytest.raises(ValueError, match="non-rejected"):
        build_no_action_receipt(
            runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
        )

    attempts = _attempts()
    attempts[3]["attempt"] = 5
    with pytest.raises(ValueError, match="not sequential"):
        build_no_action_receipt(
            runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
        )


def test_no_action_receipt_cannot_be_mutated_into_authority() -> None:
    attempts = _attempts()
    receipt = build_no_action_receipt(
        runtime_log=_log(), attempts=attempts, task_published=False, response_published=False
    )
    receipt["modelPromotionAllowed"] = True
    with pytest.raises(ValueError, match="grants forbidden authority"):
        validate_no_action_receipt(receipt, attempts)


def test_scheduled_workflow_preserves_hard_failures_and_uploads_no_action_proof() -> None:
    workflow = Path(".github/workflows/mlb-engineering-planner.yml").read_text(encoding="utf-8")
    assert "build_no_action_receipt" in workflow
    assert "validate_no_action_receipt" in workflow
    assert "runtime_reports/engineering_planner/no-action.json" in workflow
    assert "runtime_reports/engineering_planner/runtime.log" in workflow
    assert "planner failure missing attempts ledger" in workflow
    assert "planner produced neither an accepted task nor a fail-closed NO_ACTION receipt" in workflow
    assert "workflow_dispatch:" not in workflow
