from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from scripts.publish_mlb_postdeploy_proof import selection_reason


def _proof(*, run_id: int, attempt: int = 1, ok: bool = True) -> dict:
    created = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    sam_completed = created - timedelta(minutes=5) + timedelta(seconds=run_id)
    sha = f"{run_id:040x}"[-40:]
    return {
        "ok": ok,
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": created.isoformat(),
        "deployedCommit": sha,
        "deploymentSucceeded": True,
        "sourceDeployRunId": run_id,
        "sourceDeployRunAttempt": attempt,
        "sourceDeployRunNumber": run_id,
        "sourceDeployWorkflowId": 17,
        "sourceDeployWorkflowName": "Deploy SAM to AWS",
        "sourceDeployWorkflowPath": ".github/workflows/deploy.yml",
        "sourceDeployHeadBranch": "main",
        "sourceDeployHeadSha": sha,
        "sourceDeployCreatedAtUtc": (sam_completed - timedelta(minutes=1)).isoformat(),
        "sourceDeploySamCompletedAtUtc": sam_completed.isoformat(),
        "sourceDeploySamStepName": "Deploy exact canonical source",
        "sourceDeploySamStepConclusion": "success",
    }


def test_newer_sam_completion_supersedes_older_deployment() -> None:
    selected, reason = selection_reason(_proof(run_id=2), _proof(run_id=1))
    assert selected is True
    assert reason == "newer_deployed_source"


def test_older_source_cannot_replace_newer_even_with_later_observation() -> None:
    candidate = _proof(run_id=1)
    candidate["createdAtUtc"] = "2026-08-29T12:00:00+00:00"
    selected, reason = selection_reason(candidate, _proof(run_id=2))
    assert selected is False
    assert reason == "deployed_source_regression"


def test_same_source_failed_retry_cannot_regress_success() -> None:
    current = _proof(run_id=3, ok=True)
    candidate = deepcopy(current)
    candidate["ok"] = False
    candidate["createdAtUtc"] = "2026-08-28T13:00:00+00:00"
    assert selection_reason(candidate, current) == (
        False,
        "same_source_success_cannot_regress",
    )


def test_same_source_successful_retry_repairs_failure() -> None:
    current = _proof(run_id=4, ok=False)
    candidate = deepcopy(current)
    candidate["ok"] = True
    candidate["createdAtUtc"] = "2026-08-28T13:00:00+00:00"
    assert selection_reason(candidate, current) == (
        True,
        "same_source_retry_repaired_evidence",
    )


def test_same_source_conflicting_sha_is_rejected() -> None:
    current = _proof(run_id=5)
    candidate = deepcopy(current)
    candidate["sourceDeployHeadSha"] = "f" * 40
    candidate["deployedCommit"] = "f" * 40
    with pytest.raises(ValueError, match="conflicting immutable identity"):
        selection_reason(candidate, current)


def test_newer_unhealthy_deployment_supersedes_legacy_success() -> None:
    legacy = {
        "ok": True,
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": "2026-08-28T11:00:00+00:00",
        "deployedCommit": "a" * 40,
    }
    candidate = _proof(run_id=6, ok=False)

    selected, reason = selection_reason(candidate, legacy)

    assert selected is True
    assert reason == "newer_source_supersedes_legacy_pointer"


def test_proof_created_before_sam_completion_is_rejected() -> None:
    candidate = _proof(run_id=7)
    candidate["createdAtUtc"] = "2026-08-28T11:00:00+00:00"

    with pytest.raises(ValueError, match="proof creation cannot precede SAM completion"):
        selection_reason(candidate, None)


def test_partial_provenance_is_not_misclassified_as_legacy() -> None:
    corrupt_existing = {
        "ok": True,
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": "2026-08-28T11:00:00+00:00",
        "deployedCommit": "a" * 40,
        # This marker was not in the original fixed marker tuple. Any
        # sourceDeploy* field means the report attempted provenance and must
        # therefore validate completely rather than taking the legacy path.
        "sourceDeployWorkflowName": "Deploy SAM to AWS",
    }

    with pytest.raises(ValueError, match="sourceDeployCreatedAtUtc"):
        selection_reason(_proof(run_id=8), corrupt_existing)
