from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _load(filename: str) -> dict[str, Any]:
    document = yaml.load(
        (WORKFLOWS / filename).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(document, dict)
    return document


def test_scoring_guard_keeps_pr_validation_read_only_and_live_proof_trusted() -> None:
    workflow = _load("mlb-scoring-guard.yml")
    jobs = workflow["jobs"]
    validation = jobs["scoring-proof"]
    live = jobs["live-scoring-proof"]

    assert "pull_request" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in validation
    assert "AWS_ACCESS_KEY_ID" not in str(validation)
    assert "git push" not in str(validation)

    assert live["if"] == "github.event_name != 'pull_request'"
    assert live["needs"] == "scoring-proof"
    assert live["permissions"] == {"contents": "write"}
    assert "aws-actions/configure-aws-credentials@v4" in str(live)
    assert "git push origin HEAD:main" in str(live)


def test_obsolete_trainer_handler_mutator_is_manual_read_only_validation() -> None:
    workflow = _load("diagnose-mlb-trainer-function-error.yml")
    source = (WORKFLOWS / "diagnose-mlb-trainer-function-error.yml").read_text(
        encoding="utf-8"
    )

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"validate"}
    assert "github.event.pull_request" not in source
    assert "git push" not in source
    assert "git commit" not in source
    assert "Handler: mlb_ml_aws_training_v1_compat.lambda_handler" in source


def test_provider_identity_pr_job_is_read_only_and_writer_is_trusted() -> None:
    workflow = _load("mlb-provider-neutral-deploy-identity-migration.yml")
    jobs = workflow["jobs"]
    validation = jobs["migrate-and-verify"]
    writer = jobs["commit-migration"]

    assert "pull_request" in workflow["on"]
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in validation
    assert "git push" not in str(validation)
    assert "migrate_mlb_deploy_identity_provider_neutral.py" in str(validation)

    assert writer["if"] == "github.event_name != 'pull_request'"
    assert writer["needs"] == "migrate-and-verify"
    assert writer["permissions"] == {"contents": "write"}
    assert "git push origin" in str(writer)


def test_retired_bbd_writer_is_manual_only_with_job_scoped_write_authority() -> None:
    workflow = _load("mlb-remove-bbd-active-runtime-once.yml")
    writer = workflow["jobs"]["remove-and-verify"]

    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert writer["permissions"] == {"contents": "write"}
    assert "remove_mlb_bbd_active_runtime.py" in str(writer)
    assert "git push origin HEAD:agent/mlb-remove-bbd-and-fix-blockers-20260802" in str(
        writer
    )
