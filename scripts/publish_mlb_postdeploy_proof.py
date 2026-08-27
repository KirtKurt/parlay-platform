#!/usr/bin/env python3
"""Publish MLB post-deploy proof without deployed-source rollback.

The generic evidence publisher orders by observation time. Post-deploy proofs
must instead be ordered first by the canonical Deploy SAM workflow run and
attempt that actually changed the stack. A retry for the same source may repair
failed evidence, but it may never replace successful same-source evidence with
a failure.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROOF_TYPE = "MLB_SCORING_FIX_POST_DEPLOY_ACCEPTANCE"
WORKFLOW_NAME = "Deploy SAM to AWS"
WORKFLOW_PATH = ".github/workflows/deploy.yml"
SAM_STEP_NAME = "Deploy exact canonical source"


def _read(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size <= 0:
        raise ValueError(f"empty or missing JSON evidence: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _time(value: Any, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _sha(value: Any, field: str) -> str:
    parsed = str(value or "")
    if len(parsed) != 40 or any(character not in "0123456789abcdef" for character in parsed):
        raise ValueError(f"{field} must be a full lowercase Git SHA")
    return parsed


def validate_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("proofType") != PROOF_TYPE:
        raise ValueError("unexpected post-deploy proof type")
    created_at = _time(value.get("createdAtUtc"), "createdAtUtc")
    source_created_at = _time(
        value.get("sourceDeployCreatedAtUtc"), "sourceDeployCreatedAtUtc"
    )
    run_id = _positive_int(value.get("sourceDeployRunId"), "sourceDeployRunId")
    run_attempt = _positive_int(
        value.get("sourceDeployRunAttempt"), "sourceDeployRunAttempt"
    )
    run_number = _positive_int(
        value.get("sourceDeployRunNumber"), "sourceDeployRunNumber"
    )
    workflow_id = _positive_int(
        value.get("sourceDeployWorkflowId"), "sourceDeployWorkflowId"
    )
    deployed_commit = _sha(value.get("deployedCommit"), "deployedCommit")
    head_sha = _sha(value.get("sourceDeployHeadSha"), "sourceDeployHeadSha")
    if deployed_commit != head_sha:
        raise ValueError("deployedCommit must equal sourceDeployHeadSha")
    if value.get("deploymentSucceeded") is not True:
        raise ValueError("deploymentSucceeded must be true")
    if value.get("sourceDeployWorkflowName") != WORKFLOW_NAME:
        raise ValueError("sourceDeployWorkflowName is not canonical")
    if value.get("sourceDeployWorkflowPath") != WORKFLOW_PATH:
        raise ValueError("sourceDeployWorkflowPath is not canonical")
    if value.get("sourceDeployHeadBranch") != "main":
        raise ValueError("sourceDeployHeadBranch must be main")
    if value.get("sourceDeploySamStepName") != SAM_STEP_NAME:
        raise ValueError("sourceDeploySamStepName is not canonical")
    if value.get("sourceDeploySamStepConclusion") != "success":
        raise ValueError("sourceDeploySamStepConclusion must be success")
    return {
        "created_at": created_at,
        "source_created_at": source_created_at,
        "source_key": (run_number, run_attempt),
        "identity": (
            run_id,
            run_attempt,
            run_number,
            workflow_id,
            WORKFLOW_PATH,
            "main",
            head_sha,
        ),
        "deployed_commit": deployed_commit,
        "ok": value.get("ok") is True,
    }


def _legacy_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("proofType") != PROOF_TYPE:
        raise ValueError("existing evidence has an unexpected proof type")
    return {
        "created_at": _time(value.get("createdAtUtc"), "existing.createdAtUtc"),
        "deployed_commit": _sha(
            value.get("deployedCommit"), "existing.deployedCommit"
        ),
        "ok": value.get("ok") is True,
    }


def selection_reason(
    candidate: Mapping[str, Any], current: Mapping[str, Any] | None
) -> tuple[bool, str]:
    candidate_meta = validate_provenance(candidate)
    if not current:
        return True, "first_provenance_bound_proof"

    try:
        current_meta = validate_provenance(current)
    except ValueError:
        legacy = _legacy_metadata(current)
        same_commit = (
            candidate_meta["deployed_commit"] == legacy["deployed_commit"]
        )
        source_is_definitively_newer = (
            candidate_meta["source_created_at"] > legacy["created_at"]
        )
        if not same_commit and not source_is_definitively_newer:
            return False, "legacy_pointer_cannot_be_replaced_by_older_source"
        if legacy["ok"] and not candidate_meta["ok"]:
            return False, "same_source_success_cannot_regress"
        if candidate_meta["created_at"] <= legacy["created_at"]:
            return False, "candidate_observation_not_newer"
        return True, (
            "adopt_provenance_for_same_deployed_commit"
            if same_commit
            else "newer_source_supersedes_legacy_pointer"
        )

    if candidate_meta["source_key"] > current_meta["source_key"]:
        return True, "newer_deployed_source"
    if candidate_meta["source_key"] < current_meta["source_key"]:
        return False, "deployed_source_regression"

    if candidate_meta["identity"] != current_meta["identity"]:
        raise ValueError("same source order has conflicting immutable identity")
    if current_meta["ok"] and not candidate_meta["ok"]:
        return False, "same_source_success_cannot_regress"
    if not current_meta["ok"] and candidate_meta["ok"]:
        return True, "same_source_retry_repaired_evidence"
    if candidate_meta["created_at"] > current_meta["created_at"]:
        return True, "same_source_newer_observation"
    return False, "same_source_observation_not_newer"


def _atomic_write(value: Mapping[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(value, temporary, indent=2, sort_keys=True, default=str)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        _read(temporary_path)
        os.replace(temporary_path, destination)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def publish(
    candidate_path: Path, existing_path: Path, output_path: Path
) -> dict[str, Any]:
    candidate = _read(candidate_path)
    current = (
        _read(existing_path)
        if existing_path.exists() and existing_path.stat().st_size > 0
        else None
    )
    selected, reason = selection_reason(candidate, current)
    if selected:
        _atomic_write(candidate, output_path)
    elif (
        current is not None
        and output_path.resolve() != existing_path.resolve()
    ):
        _atomic_write(current, output_path)
    return {
        "published": selected,
        "reason": reason,
        "candidateSourceRunId": candidate.get("sourceDeployRunId"),
        "candidateSourceRunAttempt": candidate.get("sourceDeployRunAttempt"),
        "candidateSourceRunNumber": candidate.get("sourceDeployRunNumber"),
        "existingSourceRunId": (
            current.get("sourceDeployRunId") if current is not None else None
        ),
        "existingSourceRunAttempt": (
            current.get("sourceDeployRunAttempt") if current is not None else None
        ),
        "existingSourceRunNumber": (
            current.get("sourceDeployRunNumber") if current is not None else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--existing", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = publish(args.candidate, args.existing, args.output)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
