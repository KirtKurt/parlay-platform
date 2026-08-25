#!/usr/bin/env python3
"""Prove one exact deployment identity across MLB training and inference paths.

The script performs no direct table writes.  It invokes the deployed trainer's
idempotent selection-capture and scheduled-training modes, then reads exact
persisted run evidence through status.  It also verifies that every live MLB
inference/lock/pull Lambda carries the same Git SHA and template SHA-256.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import boto3
from botocore.config import Config


VERSION = "MLB-ML-DEPLOYMENT-IDENTITY-SYNC-v1-training-selection-inference"
DEFAULT_LOGICAL_FUNCTIONS = (
    "MLBMLTrainingFunction",
    "MLBAuditedPullFunction",
    "MLBDailyPickLockFunction",
    "MLBV3ReadFunction",
)


class IdentitySyncError(RuntimeError):
    pass


def _json_object(value: Any, *, error: str) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception as exc:
            raise IdentitySyncError(error) from exc
        if isinstance(parsed, Mapping):
            return dict(parsed)
    raise IdentitySyncError(error)


def _read_payload(response: Mapping[str, Any]) -> bytes:
    payload = response.get("Payload")
    if hasattr(payload, "read"):
        return payload.read()
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    raise IdentitySyncError("lambda_payload_missing")


def invoke_json(lambda_client: Any, function_name: str, event: Dict[str, Any]) -> Dict[str, Any]:
    response = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(event, separators=(",", ":")).encode("utf-8"),
    )
    if int(response.get("StatusCode") or 0) != 200:
        raise IdentitySyncError("lambda_transport_status_not_200")
    if response.get("FunctionError"):
        raise IdentitySyncError("lambda_function_error")
    payload = _json_object(
        _read_payload(response).decode("utf-8"),
        error="lambda_response_not_json",
    )
    if "statusCode" in payload:
        status = int(payload.get("statusCode") or 0)
        body = _json_object(
            payload.get("body") or {},
            error="lambda_application_body_not_json",
        )
        if not 200 <= status < 300:
            raise IdentitySyncError(
                "lambda_application_status_not_success:"
                + json.dumps(
                    {
                        "statusCode": status,
                        "error": body.get("error"),
                        "status": body.get("status"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        return body
    return payload


def _physical_function(
    cloudformation: Any, stack_name: str, logical_id: str
) -> Optional[str]:
    try:
        response = cloudformation.describe_stack_resource(
            StackName=stack_name,
            LogicalResourceId=logical_id,
        )
    except Exception as exc:
        code = str(
            ((getattr(exc, "response", {}) or {}).get("Error") or {}).get(
                "Code"
            )
            or ""
        )
        if code in {"ValidationError", "ResourceNotFoundException"}:
            return None
        raise
    value = str(
        (response.get("StackResourceDetail") or {}).get("PhysicalResourceId")
        or ""
    )
    return value or None


def _identity_from_configuration(configuration: Mapping[str, Any]) -> Dict[str, str]:
    variables = ((configuration.get("Environment") or {}).get("Variables") or {})
    return {
        "gitSha": str(variables.get("INQSI_DEPLOY_GIT_SHA") or ""),
        "templateSha256": str(
            variables.get("INQSI_DEPLOY_TEMPLATE_SHA256") or ""
        ),
        "deployRunId": str(variables.get("INQSI_DEPLOY_RUN_ID") or ""),
        "v2InferenceEnabled": str(
            variables.get("INQSI_MLB_V2_INFERENCE_ENABLED") or ""
        ),
        "automaticPromotionEnabled": str(
            variables.get("INQSI_MLB_ML_AUTO_PROMOTE") or ""
        ),
    }


def _run_identity(payload: Mapping[str, Any]) -> Dict[str, str]:
    identity = payload.get("deploymentIdentity") or {}
    return {
        "gitSha": str(identity.get("gitSha") or ""),
        "templateSha256": str(identity.get("templateSha256") or ""),
    }


def synchronize(
    cloudformation: Any,
    lambda_client: Any,
    *,
    stack_name: str,
    expected_git_sha: str,
    expected_template_sha256: str,
) -> Dict[str, Any]:
    expected = {
        "gitSha": expected_git_sha,
        "templateSha256": expected_template_sha256,
    }
    if len(expected_git_sha) != 40 or len(expected_template_sha256) != 64:
        raise IdentitySyncError("expected_deployment_identity_length_invalid")
    functions: Dict[str, str] = {}
    configurations: Dict[str, Any] = {}
    errors: List[str] = []
    for logical_id in DEFAULT_LOGICAL_FUNCTIONS:
        physical = _physical_function(cloudformation, stack_name, logical_id)
        if not physical:
            if logical_id == "MLBMLTrainingFunction":
                errors.append("training_function_missing")
            continue
        functions[logical_id] = physical
        configuration = lambda_client.get_function_configuration(
            FunctionName=physical
        )
        identity = _identity_from_configuration(configuration)
        match = bool(
            identity["gitSha"] == expected_git_sha
            and identity["templateSha256"] == expected_template_sha256
        )
        v2_enabled = identity["v2InferenceEnabled"].lower() == "true"
        configurations[logical_id] = {
            "functionName": physical,
            "state": configuration.get("State"),
            "lastUpdateStatus": configuration.get("LastUpdateStatus"),
            "handler": configuration.get("Handler"),
            "identity": identity,
            "identityMatches": match,
            "v2InferenceEnabled": v2_enabled,
        }
        if configuration.get("State") != "Active":
            errors.append(f"{logical_id}:lambda_not_active")
        if configuration.get("LastUpdateStatus") not in {None, "Successful"}:
            errors.append(f"{logical_id}:lambda_update_not_successful")
        if not match:
            errors.append(f"{logical_id}:deployment_identity_mismatch")
        if logical_id in {
            "MLBAuditedPullFunction",
            "MLBDailyPickLockFunction",
            "MLBV3ReadFunction",
        } and not v2_enabled:
            errors.append(f"{logical_id}:v2_inference_not_enabled")

    trainer_name = functions.get("MLBMLTrainingFunction")
    if not trainer_name:
        raise IdentitySyncError("training_function_missing")
    selection = invoke_json(
        lambda_client,
        trainer_name,
        {"mode": "selection_capture", "run": "deployment_identity_sync_v1"},
    )
    training = invoke_json(
        lambda_client,
        trainer_name,
        {"mode": "scheduled", "run": "deployment_identity_sync_v1"},
    )
    status_event: Dict[str, Any] = {"mode": "status"}
    if training.get("runId"):
        status_event["trainingRunId"] = training["runId"]
    if selection.get("runId"):
        status_event["selectionCaptureRunId"] = selection["runId"]
    status = invoke_json(lambda_client, trainer_name, status_event)

    for name, payload in (
        ("training", training),
        ("selectionCapture", selection),
    ):
        if _run_identity(payload) != expected:
            errors.append(f"{name}:persisted_run_identity_mismatch")
    requested = status.get("requestedRunEvidence") or {}
    for name in ("training", "selectionCapture"):
        evidence = requested.get(name) or {}
        if evidence and evidence.get("ok") is not True:
            errors.append(f"{name}:requested_run_evidence_invalid")
    if status.get("automaticPromotionEnabled") is not True:
        errors.append("automatic_promotion_not_enabled")
    if status.get("firstPromotionRequiresManualReview") is not False:
        errors.append("first_promotion_still_requires_manual_review")
    if status.get("v2InferenceConsumerInstalled") is not True:
        errors.append("v2_inference_consumer_not_installed")
    if status.get("runtimeAuthorityActivationAvailable") is not True:
        errors.append("runtime_authority_activation_not_available")
    if status.get("learningContinuesBelowAspirationalAccuracy") is not True:
        errors.append("learning_still_bound_to_aspirational_accuracy")

    return {
        "ok": not errors,
        "version": VERSION,
        "stackName": stack_name,
        "expectedDeploymentIdentity": expected,
        "functions": configurations,
        "trainerRuns": {
            "selectionCapture": selection,
            "training": training,
            "status": status,
        },
        "errors": errors,
        "allRuntimeIdentitiesSynchronized": not any(
            "identity_mismatch" in error for error in errors
        ),
        "automaticPromotionEnabled": status.get("automaticPromotionEnabled"),
        "firstPromotionRequiresManualReview": status.get(
            "firstPromotionRequiresManualReview"
        ),
        "v2InferenceConsumerInstalled": status.get(
            "v2InferenceConsumerInstalled"
        ),
        "runtimeAuthorityActivationAvailable": status.get(
            "runtimeAuthorityActivationAvailable"
        ),
        "acceptedRowCount": training.get("acceptedRowCount"),
        "canonicalSlateContinuity": training.get("canonicalSlateContinuity"),
        "productionAuthorityChangedByVerifier": False,
        "automaticWagerAllowed": False,
    }


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stack-name", default="parlay-platform-dev")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--expected-template-sha256", required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runtime_reports/mlb_ml_deployment_identity_sync_latest.json"),
    )
    args = parser.parse_args()
    session = boto3.session.Session(region_name=args.region)
    cloudformation = session.client(
        "cloudformation",
        config=Config(retries={"total_max_attempts": 4, "mode": "standard"}),
    )
    lambda_client = session.client(
        "lambda",
        config=Config(
            connect_timeout=10,
            read_timeout=1020,
            retries={"total_max_attempts": 2, "mode": "standard"},
        ),
    )
    try:
        report = synchronize(
            cloudformation,
            lambda_client,
            stack_name=args.stack_name,
            expected_git_sha=args.expected_git_sha,
            expected_template_sha256=args.expected_template_sha256,
        )
    except Exception as exc:
        report = {
            "ok": False,
            "version": VERSION,
            "stackName": args.stack_name,
            "error": f"{type(exc).__name__}:{str(exc)[:500]}",
            "productionAuthorityChangedByVerifier": False,
            "automaticWagerAllowed": False,
        }
    _write(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
