import pytest

from engineering_agent.planner import validate


HISTORY = {
    "decisions": [
        {
            "status": "COMPLETED",
            "title": "Bind successor health to MLB-specific deployment identity",
        }
    ]
}


def test_duplicate_history_rejection_precedes_malformed_schema_diagnostic():
    task = {
        "title": "Bind successor health to MLB-specific deployment identity",
        "objective": "repeat completed work",
        "implementation": "not-an-array",
    }

    with pytest.raises(ValueError, match="duplicates engineering decision history"):
        validate(task, decision_history=HISTORY)


def test_distinct_task_still_fails_closed_on_malformed_schema():
    task = {
        "title": "Diagnose current MLB champion qualification blockers",
        "objective": "inspect current qualification evidence without changing authority",
        "implementation": "not-an-array",
    }

    with pytest.raises(ValueError, match="implementation must be a non-empty array"):
        validate(task, decision_history=HISTORY)
