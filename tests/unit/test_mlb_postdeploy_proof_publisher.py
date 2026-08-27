from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.publish_mlb_postdeploy_proof import publish, selection_reason


def proof(
    *,
    run_id: int,
    run_attempt: int,
    run_number: int,
    deployed_sha: str,
    created_at: str,
    source_created_at: str,
    ok: bool,
    **overrides,
) -> dict:
    value = {
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": created_at,
        "deployedCommit": deployed_sha,
        "deploymentSucceeded": True,
        "sourceDeployRunId": str(run_id),
        "sourceDeployRunAttempt": run_attempt,
        "sourceDeployRunNumber": run_number,
        "sourceDeployWorkflowId": 12345,
        "sourceDeployWorkflowName": "Deploy SAM to AWS",
        "sourceDeployWorkflowPath": ".github/workflows/deploy.yml",
        "sourceDeployHeadBranch": "main",
        "sourceDeployHeadSha": deployed_sha,
        "sourceDeployCreatedAtUtc": source_created_at,
        "sourceDeploySamCompletedAtUtc": source_created_at,
        "sourceDeployRunUrl": f"https://github.com/example/repo/actions/runs/{run_id}",
        "sourceDeploySamStepName": "Deploy exact canonical source",
        "sourceDeploySamStepConclusion": "success",
        "ok": ok,
        "deploymentHealthy": ok,
    }
    value.update(overrides)
    return value


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def test_newer_deployed_source_replaces_older_success_even_when_unhealthy(tmp_path):
    older = proof(
        run_id=100,
        run_attempt=1,
        run_number=10,
        deployed_sha="a" * 40,
        source_created_at="2026-08-27T20:00:00Z",
        created_at="2026-08-27T20:15:00Z",
        ok=True,
    )
    newer = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=False,
    )

    selected, reason = selection_reason(newer, older)

    assert selected is True
    assert reason == "newer_deployed_source"


def test_late_older_observation_cannot_roll_back_newer_deployed_source(tmp_path):
    newer = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=False,
    )
    late_older = proof(
        run_id=100,
        run_attempt=1,
        run_number=10,
        deployed_sha="a" * 40,
        source_created_at="2026-08-27T20:00:00Z",
        created_at="2026-08-27T22:00:00Z",
        ok=True,
    )

    selected, reason = selection_reason(late_older, newer)

    assert selected is False
    assert reason == "deployed_source_regression"


def test_same_source_retry_can_replace_failed_proof_with_success():
    failed = proof(
        run_id=101,
        run_attempt=2,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=False,
    )
    repaired = proof(
        run_id=101,
        run_attempt=2,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:30:00Z",
        ok=True,
    )

    assert selection_reason(repaired, failed) == (
        True,
        "same_source_retry_repaired_evidence",
    )


def test_same_source_failure_cannot_replace_success():
    successful = proof(
        run_id=101,
        run_attempt=2,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=True,
    )
    later_failure = proof(
        run_id=101,
        run_attempt=2,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:30:00Z",
        ok=False,
    )

    assert selection_reason(later_failure, successful) == (
        False,
        "same_source_success_cannot_regress",
    )


def test_same_source_order_with_different_identity_fails_closed():
    current = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=False,
    )
    conflict = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="c" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:30:00Z",
        ok=True,
    )

    with pytest.raises(ValueError, match="conflicting immutable identity"):
        selection_reason(conflict, current)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("deploymentSucceeded", False),
        ("sourceDeployWorkflowPath", ".github/workflows/other.yml"),
        ("sourceDeployHeadBranch", "feature"),
        ("sourceDeploySamStepConclusion", "failure"),
        ("sourceDeployHeadSha", "c" * 40),
    ],
)
def test_invalid_source_provenance_fails_closed(field, value):
    candidate = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=True,
        **{field: value},
    )

    with pytest.raises(ValueError):
        selection_reason(candidate, None)


def test_existing_legacy_pointer_can_adopt_provenance_for_same_commit():
    legacy = {
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": "2026-08-27T21:15:00Z",
        "deployedCommit": "b" * 40,
        "ok": True,
    }
    candidate = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:30:00Z",
        ok=True,
    )

    assert selection_reason(candidate, legacy) == (
        True,
        "adopt_provenance_for_same_deployed_commit",
    )


def test_legacy_pointer_rejects_unproved_older_different_source():
    legacy = {
        "proofType": "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE",
        "createdAtUtc": "2026-08-27T21:15:00Z",
        "deployedCommit": "b" * 40,
        "ok": True,
    }
    candidate = proof(
        run_id=100,
        run_attempt=1,
        run_number=10,
        deployed_sha="a" * 40,
        source_created_at="2026-08-27T20:00:00Z",
        created_at="2026-08-27T22:00:00Z",
        ok=True,
    )

    assert selection_reason(candidate, legacy) == (
        False,
        "legacy_pointer_cannot_be_replaced_by_older_source",
    )


def test_publish_replaces_atomically_and_preserves_rejected_current(tmp_path):
    candidate_path = tmp_path / "candidate.json"
    current_path = tmp_path / "latest.json"
    current = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=True,
    )
    stale = proof(
        run_id=100,
        run_attempt=1,
        run_number=10,
        deployed_sha="a" * 40,
        source_created_at="2026-08-27T20:00:00Z",
        created_at="2026-08-27T22:00:00Z",
        ok=True,
    )
    write(current_path, current)
    write(candidate_path, stale)

    result = publish(candidate_path, current_path, current_path)

    assert result["published"] is False
    assert json.loads(current_path.read_text()) == current
    assert not list(tmp_path.glob(".latest.json.*.tmp"))


def test_partially_provenance_bound_existing_pointer_cannot_be_downgraded_to_legacy():
    corrupt_current = proof(
        run_id=101,
        run_attempt=1,
        run_number=11,
        deployed_sha="b" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        created_at="2026-08-27T21:15:00Z",
        ok=True,
        sourceDeployWorkflowPath=".github/workflows/corrupt.yml",
    )
    candidate = proof(
        run_id=102,
        run_attempt=1,
        run_number=12,
        deployed_sha="c" * 40,
        source_created_at="2026-08-27T22:00:00Z",
        created_at="2026-08-27T22:15:00Z",
        ok=True,
    )

    with pytest.raises(ValueError, match="sourceDeployWorkflowPath"):
        selection_reason(candidate, corrupt_current)


def test_later_rerun_of_older_run_number_is_the_newer_deployment_source():
    current = proof(
        run_id=200,
        run_attempt=1,
        run_number=20,
        deployed_sha="d" * 40,
        source_created_at="2026-08-27T21:00:00Z",
        sourceDeploySamCompletedAtUtc="2026-08-27T22:00:00Z",
        created_at="2026-08-27T22:15:00Z",
        ok=True,
    )
    rerun_older_workflow = proof(
        run_id=100,
        run_attempt=2,
        run_number=10,
        deployed_sha="e" * 40,
        source_created_at="2026-08-27T20:00:00Z",
        sourceDeploySamCompletedAtUtc="2026-08-27T23:00:00Z",
        created_at="2026-08-27T23:15:00Z",
        ok=False,
    )

    assert selection_reason(rerun_older_workflow, current) == (
        True,
        "newer_deployed_source",
    )
