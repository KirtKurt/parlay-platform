#!/usr/bin/env python3
"""Atomically promote a gate-passing V8 artifact with readback and rollback.

This pointer is owned by the V8 subsystem.  It does not enable wagering and does
not alter the existing MLB production authority unless a separately verified
runtime consumer is explicitly installed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

import boto3
from botocore.exceptions import ClientError

import mlb_v8_autonomy_v1 as autonomy

VERSION = "MLB-V8-AUTONOMOUS-CHAMPION-PROMOTION-v1-atomic-rollback"
POINTER_PK = "MLB_V8_AUTONOMOUS_CHAMPION#V1"
POINTER_SK = "ACTIVE"
RECORD_TYPE = "mlb_v8_autonomous_champion_pointer_v1"
DEFAULT_STACK = "parlay-platform-mlb-historical-optimizer"
DEFAULT_TABLE = "parlay_platform_snapshots"


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        + "\n"
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _validate_training(training: Mapping[str, Any]) -> Dict[str, Any]:
    errors = []
    expected_result_digest = str(training.get("resultDigest") or "")
    actual_result_digest = autonomy._sha(
        {key: item for key, item in training.items() if key != "resultDigest"}
    )
    if not expected_result_digest or expected_result_digest != actual_result_digest:
        errors.append("training_result_digest_invalid")
    learning = training.get("learningExecution") or {}
    if learning.get("learningExecuted") is not True:
        errors.append("learning_execution_unproven")
    if learning.get("learnedCandidateSelected") is not True:
        errors.append("learned_candidate_not_selected")
    if learning.get("marketBaselineRetainedByGuard") is True:
        errors.append("market_baseline_cannot_be_promoted")
    if (training.get("promotionGate") or {}).get("passed") is not True:
        errors.append("promotion_gate_not_passed")
    if training.get("freshProspectiveAuditRequired") is not False:
        errors.append("fresh_prospective_audit_not_complete")
    if training.get("productionPromotionEligible") is not True:
        errors.append("production_promotion_not_eligible")
    if training.get("automaticWagerAllowed") is not False:
        errors.append("automatic_wager_must_remain_disabled")

    model = training.get("model") or {}
    model_digest = str(model.get("modelDigest") or "")
    model_without_digest = {
        key: item for key, item in model.items() if key != "modelDigest"
    }
    if not model_digest or model_digest != _sha(model_without_digest).strip():
        # Historical model digests omit the trailing newline used by this module.
        canonical = hashlib.sha256(
            json.dumps(
                model_without_digest,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        if not model_digest or model_digest != canonical:
            errors.append("model_digest_invalid")
    if str(model.get("featureGroup") or "") in {"", autonomy.BASELINE_GROUP}:
        errors.append("learned_feature_group_required")
    if int(model.get("trainingSteps") or 0) <= 0:
        errors.append("selected_model_training_steps_missing")
    if not list(model.get("weights") or []):
        errors.append("selected_model_weights_missing")
    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "resultDigest": expected_result_digest,
        "modelDigest": model_digest,
    }


def _put_immutable(s3: Any, *, bucket: str, key: str, body: bytes, digest: str) -> Dict[str, Any]:
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={
                "sha256": digest,
                "record-type": "mlb-v8-autonomous-champion",
            },
        )
        return {"alreadyExisted": False, "versionId": response.get("VersionId")}
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = int(
            (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            or 0
        )
        if code not in {"PreconditionFailed", "ConditionalRequestConflict"} and status not in {409, 412}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing != digest:
            raise RuntimeError("V8 champion immutable artifact collision") from exc
        return {"alreadyExisted": True, "versionId": head.get("VersionId")}


def promote(
    training: Mapping[str, Any],
    *,
    bucket: str,
    table: Any,
    s3: Any,
    verify: bool = True,
) -> Dict[str, Any]:
    validation = _validate_training(training)
    created = datetime.now(timezone.utc).isoformat()
    if validation["ok"] is not True:
        return {
            "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_PROMOTION",
            "version": VERSION,
            "createdAtUtc": created,
            "ok": False,
            "promoted": False,
            "rolledBack": False,
            "validation": validation,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }

    artifact = {
        "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_ARTIFACT",
        "version": VERSION,
        "createdAtUtc": created,
        "trainingResultDigest": validation["resultDigest"],
        "modelDigest": validation["modelDigest"],
        "model": copy.deepcopy(training.get("model") or {}),
        "selection": copy.deepcopy(training.get("selection") or {}),
        "selectionObjective": copy.deepcopy(
            training.get("selectionObjective") or {}
        ),
        "metrics": copy.deepcopy(training.get("metrics") or {}),
        "partitions": copy.deepcopy(training.get("partitions") or {}),
        "learningExecution": copy.deepcopy(
            training.get("learningExecution") or {}
        ),
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    artifact_digest = _sha(artifact)
    key = (
        "mlb/v8/autonomous-champions/"
        f"{validation['modelDigest']}/{artifact_digest}.json"
    )
    body = _json_bytes(artifact)
    storage = _put_immutable(
        s3,
        bucket=bucket,
        key=key,
        body=body,
        digest=artifact_digest,
    )

    previous = table.get_item(
        Key={"PK": POINTER_PK, "SK": POINTER_SK},
        ConsistentRead=True,
    ).get("Item")
    previous_copy = copy.deepcopy(previous) if previous else None
    previous_revision = int((previous or {}).get("revision") or 0)
    revision = previous_revision + 1
    pointer = {
        "PK": POINTER_PK,
        "SK": POINTER_SK,
        "record_type": RECORD_TYPE,
        "revision": revision,
        "updated_at": created,
        "data": {
            "version": VERSION,
            "bucket": bucket,
            "key": key,
            "sha256": artifact_digest,
            "modelDigest": validation["modelDigest"],
            "trainingResultDigest": validation["resultDigest"],
            "stableChampion": True,
            "automaticPromotion": True,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        },
    }
    if previous_revision:
        table.put_item(
            Item=pointer,
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": previous_revision},
        )
    else:
        table.put_item(
            Item=pointer,
            ConditionExpression="attribute_not_exists(PK)",
        )

    rolled_back = False
    verification_errors = []
    try:
        if verify:
            readback = table.get_item(
                Key={"PK": POINTER_PK, "SK": POINTER_SK},
                ConsistentRead=True,
            ).get("Item") or {}
            data = readback.get("data") or {}
            if int(readback.get("revision") or 0) != revision:
                verification_errors.append("pointer_revision_mismatch")
            if data.get("sha256") != artifact_digest:
                verification_errors.append("pointer_artifact_digest_mismatch")
            head = s3.head_object(Bucket=bucket, Key=key)
            if str((head.get("Metadata") or {}).get("sha256") or "") != artifact_digest:
                verification_errors.append("artifact_head_digest_mismatch")
            if verification_errors:
                raise RuntimeError(";".join(verification_errors))
    except Exception:
        rolled_back = True
        if previous_copy is not None:
            table.put_item(
                Item=previous_copy,
                ConditionExpression="#revision = :expected",
                ExpressionAttributeNames={"#revision": "revision"},
                ExpressionAttributeValues={":expected": revision},
            )
        else:
            table.delete_item(
                Key={"PK": POINTER_PK, "SK": POINTER_SK},
                ConditionExpression="#revision = :expected",
                ExpressionAttributeNames={"#revision": "revision"},
                ExpressionAttributeValues={":expected": revision},
            )

    return {
        "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_PROMOTION",
        "version": VERSION,
        "createdAtUtc": created,
        "ok": not verification_errors and not rolled_back,
        "promoted": not verification_errors and not rolled_back,
        "rolledBack": rolled_back,
        "validation": validation,
        "artifact": {
            "bucket": bucket,
            "key": key,
            "sha256": artifact_digest,
            **storage,
        },
        "pointerRevision": revision,
        "previousPointerRevision": previous_revision,
        "verificationErrors": verification_errors,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--stack-name", default=DEFAULT_STACK)
    parser.add_argument("--table-name", default=DEFAULT_TABLE)
    parser.add_argument("--training-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    training = json.loads(Path(args.training_report).read_text(encoding="utf-8"))
    cf = boto3.client("cloudformation", region_name=args.region)
    outputs = _outputs(cf, args.stack_name)
    bucket = str(outputs.get("HistoricalArtifactsBucketName") or "").strip()
    if not bucket:
        raise RuntimeError("historical artifacts bucket output is missing")
    table = boto3.resource("dynamodb", region_name=args.region).Table(
        args.table_name
    )
    s3 = boto3.client("s3", region_name=args.region)
    result = promote(training, bucket=bucket, table=table, s3=s3)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
