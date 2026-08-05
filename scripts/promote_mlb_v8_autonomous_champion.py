#!/usr/bin/env python3
"""Atomically promote a prospectively verified MLB V8 shadow champion.

The transaction stores an immutable runtime bundle, advances the V8 champion
pointer, marks the exact frozen prospective candidate as promoted, verifies both
pointers and the artifact, and restores both prior states on any failure.  It never
enables automatic wagering or changes the incumbent MLB production authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import boto3
from botocore.exceptions import ClientError

import mlb_v8_autonomy_v1 as autonomy
import mlb_v8_model_runtime as model_runtime

VERSION = "MLB-V8-AUTONOMOUS-CHAMPION-PROMOTION-v2-prospective-transaction"
POINTER_PK = "MLB_V8_AUTONOMOUS_CHAMPION#V1"
POINTER_SK = "ACTIVE"
RECORD_TYPE = "mlb_v8_autonomous_champion_pointer_v1"
PROSPECTIVE_PK = "MLB_V8_PROSPECTIVE_AUDIT#V1"
PROSPECTIVE_SK = "ACTIVE"
PROSPECTIVE_RECORD_TYPE = "mlb_v8_prospective_audit_pointer_v1"
DEFAULT_STACK = "parlay-platform-mlb-historical-optimizer"
DEFAULT_TABLE = "parlay_platform_snapshots"


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        + "\n"
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _canonical_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


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

    audit = training.get("prospectiveAudit") or {}
    if not isinstance(audit, Mapping):
        audit = {}
    if audit.get("prospectiveEvidenceComplete") is not True:
        errors.append("prospective_evidence_incomplete")
    if audit.get("prospectiveAuditPassed") is not True:
        errors.append("prospective_audit_not_passed")
    if audit.get("prospectiveAuditRejected") is True:
        errors.append("prospective_audit_rejected")
    if audit.get("modelRefitDuringProspectiveAudit") is not False:
        errors.append("prospective_model_refit_detected")
    if audit.get("selectionUsedProspectiveOutcomes") is not False:
        errors.append("prospective_outcome_selection_detected")
    candidate_digest = str(
        training.get("prospectiveCandidateDigest")
        or audit.get("candidateDigest")
        or ""
    )
    if not candidate_digest:
        errors.append("prospective_candidate_digest_missing")
    elif audit.get("candidateDigest") != candidate_digest:
        errors.append("prospective_candidate_digest_mismatch")

    model = training.get("model") or {}
    model_digest = str(model.get("modelDigest") or "")
    model_without_digest = {
        key: item for key, item in model.items() if key != "modelDigest"
    }
    if not model_digest or model_digest != _canonical_sha(model_without_digest):
        errors.append("model_digest_invalid")
    if str(model.get("featureGroup") or "") in {"", autonomy.BASELINE_GROUP}:
        errors.append("learned_feature_group_required")
    if int(model.get("trainingSteps") or 0) <= 0:
        errors.append("selected_model_training_steps_missing")
    if not list(model.get("weights") or []):
        errors.append("selected_model_weights_missing")

    bundle = None
    try:
        bundle = model_runtime.build_bundle(training)
        model_runtime.verify_bundle(bundle)
    except Exception as exc:
        errors.append(f"runtime_bundle_invalid:{type(exc).__name__}:{exc}")
    if bundle and audit.get("modelDigest") not in {
        bundle.get("modelDigest"),
        bundle.get("sourceModelDigest"),
    }:
        errors.append("prospective_audit_model_digest_mismatch")

    return {
        "ok": not errors,
        "errors": sorted(set(errors)),
        "resultDigest": expected_result_digest,
        "sourceModelDigest": model_digest,
        "modelDigest": (bundle or {}).get("modelDigest"),
        "candidateDigest": candidate_digest,
        "modelBundle": bundle,
        "prospectiveAuditDigest": audit.get("auditDigest"),
    }


def _put_immutable(
    s3: Any, *, bucket: str, key: str, body: bytes, digest: str
) -> Dict[str, Any]:
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
        return {
            "alreadyExisted": False,
            "versionId": response.get("VersionId"),
        }
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = int(
            (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            or 0
        )
        if code not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        } and status not in {409, 412}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing != digest:
            raise RuntimeError("V8 champion immutable artifact collision") from exc
        return {
            "alreadyExisted": True,
            "versionId": head.get("VersionId"),
        }


def _get_item(table: Any, pk: str, sk: str) -> Dict[str, Any] | None:
    value = table.get_item(
        Key={"PK": pk, "SK": sk}, ConsistentRead=True
    ).get("Item")
    return copy.deepcopy(value) if value else None


def _put_revision(
    table: Any,
    *,
    item: Mapping[str, Any],
    previous_revision: int,
) -> None:
    if previous_revision:
        table.put_item(
            Item=copy.deepcopy(dict(item)),
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": previous_revision},
        )
    else:
        table.put_item(
            Item=copy.deepcopy(dict(item)),
            ConditionExpression="attribute_not_exists(PK)",
        )


def _restore_item(
    table: Any,
    *,
    pk: str,
    sk: str,
    previous: Mapping[str, Any] | None,
    current_revision: int,
) -> None:
    if previous is not None:
        table.put_item(
            Item=copy.deepcopy(dict(previous)),
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": current_revision},
        )
    else:
        table.delete_item(
            Key={"PK": pk, "SK": sk},
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": current_revision},
        )


def _mark_prospective_promoted(
    table: Any,
    *,
    validation: Mapping[str, Any],
    champion: Mapping[str, Any],
    created_at: str,
) -> Tuple[Dict[str, Any], Dict[str, Any], int, bool]:
    previous = _get_item(table, PROSPECTIVE_PK, PROSPECTIVE_SK)
    if previous is None:
        raise RuntimeError("prospective audit pointer is missing")
    data = copy.deepcopy(dict(previous.get("data") or {}))
    status = str(data.get("status") or "").upper()
    if data.get("candidateDigest") != validation.get("candidateDigest"):
        raise RuntimeError("prospective pointer candidate mismatch")
    if data.get("modelDigest") not in {
        validation.get("modelDigest"),
        validation.get("sourceModelDigest"),
    }:
        raise RuntimeError("prospective pointer model mismatch")
    previous_revision = int(previous.get("revision") or 0)
    if status == "PROMOTED":
        return previous, previous, previous_revision, False
    if status != "PASSED":
        raise RuntimeError("prospective pointer is not passed")
    revision = previous_revision + 1
    data.update(
        {
            "status": "PROMOTED",
            "promotedAtUtc": created_at,
            "championArtifact": copy.deepcopy(dict(champion)),
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        }
    )
    current = {
        "PK": PROSPECTIVE_PK,
        "SK": PROSPECTIVE_SK,
        "record_type": PROSPECTIVE_RECORD_TYPE,
        "revision": revision,
        "updated_at": created_at,
        "data": data,
    }
    _put_revision(
        table, item=current, previous_revision=previous_revision
    )
    return previous, current, revision, True


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
            "alreadyActive": False,
            "rolledBack": False,
            "validation": validation,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }

    artifact_created = str(training.get("createdAtUtc") or created)
    artifact = {
        "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_ARTIFACT",
        "version": VERSION,
        "createdAtUtc": artifact_created,
        "trainingResultDigest": validation["resultDigest"],
        "modelDigest": validation["modelDigest"],
        "sourceModelDigest": validation["sourceModelDigest"],
        "prospectiveCandidateDigest": validation["candidateDigest"],
        "prospectiveAuditDigest": validation["prospectiveAuditDigest"],
        "modelBundle": copy.deepcopy(validation["modelBundle"]),
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
        "prospectiveAudit": copy.deepcopy(
            training.get("prospectiveAudit") or {}
        ),
        "authority": "V8_SHADOW_CHAMPION",
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
    champion_ref = {
        "bucket": bucket,
        "key": key,
        "sha256": artifact_digest,
        "modelDigest": validation["modelDigest"],
        "sourceModelDigest": validation["sourceModelDigest"],
        "prospectiveCandidateDigest": validation["candidateDigest"],
    }

    previous = _get_item(table, POINTER_PK, POINTER_SK)
    previous_revision = int((previous or {}).get("revision") or 0)
    previous_data = (previous or {}).get("data") or {}
    if (
        previous_data.get("modelDigest") == validation["modelDigest"]
        and previous_data.get("stableChampion") is True
    ):
        errors = []
        head = s3.head_object(
            Bucket=str(previous_data.get("bucket") or ""),
            Key=str(previous_data.get("key") or ""),
        )
        if str((head.get("Metadata") or {}).get("sha256") or "") != str(
            previous_data.get("sha256") or ""
        ):
            errors.append("active_champion_artifact_digest_mismatch")
        prospective_previous = None
        prospective_current = None
        prospective_revision = 0
        prospective_changed = False
        try:
            (
                prospective_previous,
                prospective_current,
                prospective_revision,
                prospective_changed,
            ) = _mark_prospective_promoted(
                table,
                validation=validation,
                champion=previous_data,
                created_at=created,
            )
        except Exception as exc:
            errors.append(f"prospective_mark_failed:{type(exc).__name__}:{exc}")
        return {
            "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_PROMOTION",
            "version": VERSION,
            "createdAtUtc": created,
            "ok": not errors,
            "promoted": not errors,
            "alreadyActive": True,
            "rolledBack": False,
            "validation": validation,
            "artifact": copy.deepcopy(dict(previous_data)),
            "pointerRevision": previous_revision,
            "previousPointerRevision": previous_revision,
            "prospectivePointerRevision": prospective_revision,
            "prospectivePointerChanged": prospective_changed,
            "verificationErrors": errors,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }

    revision = previous_revision + 1
    pointer = {
        "PK": POINTER_PK,
        "SK": POINTER_SK,
        "record_type": RECORD_TYPE,
        "revision": revision,
        "updated_at": created,
        "data": {
            "version": VERSION,
            **champion_ref,
            "trainingResultDigest": validation["resultDigest"],
            "prospectiveAuditDigest": validation["prospectiveAuditDigest"],
            "stableChampion": True,
            "automaticPromotion": True,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        },
    }
    _put_revision(table, item=pointer, previous_revision=previous_revision)

    prospective_previous = None
    prospective_current = None
    prospective_revision = 0
    prospective_changed = False
    rolled_back = False
    verification_errors: list[str] = []
    try:
        (
            prospective_previous,
            prospective_current,
            prospective_revision,
            prospective_changed,
        ) = _mark_prospective_promoted(
            table,
            validation=validation,
            champion=champion_ref,
            created_at=created,
        )
        if verify:
            readback = _get_item(table, POINTER_PK, POINTER_SK) or {}
            data = readback.get("data") or {}
            if int(readback.get("revision") or 0) != revision:
                verification_errors.append("pointer_revision_mismatch")
            if data.get("sha256") != artifact_digest:
                verification_errors.append("pointer_artifact_digest_mismatch")
            if data.get("modelDigest") != validation["modelDigest"]:
                verification_errors.append("pointer_model_digest_mismatch")
            head = s3.head_object(Bucket=bucket, Key=key)
            if str((head.get("Metadata") or {}).get("sha256") or "") != artifact_digest:
                verification_errors.append("artifact_head_digest_mismatch")
            prospective_readback = _get_item(
                table, PROSPECTIVE_PK, PROSPECTIVE_SK
            ) or {}
            prospective_data = prospective_readback.get("data") or {}
            if prospective_data.get("status") != "PROMOTED":
                verification_errors.append("prospective_pointer_not_promoted")
            if prospective_data.get("candidateDigest") != validation[
                "candidateDigest"
            ]:
                verification_errors.append(
                    "prospective_pointer_candidate_mismatch"
                )
            if verification_errors:
                raise RuntimeError(";".join(verification_errors))
    except Exception as exc:
        if not verification_errors:
            verification_errors.append(
                f"promotion_transaction_failed:{type(exc).__name__}:{exc}"
            )
        rolled_back = True
        if prospective_changed and prospective_previous is not None:
            _restore_item(
                table,
                pk=PROSPECTIVE_PK,
                sk=PROSPECTIVE_SK,
                previous=prospective_previous,
                current_revision=prospective_revision,
            )
        _restore_item(
            table,
            pk=POINTER_PK,
            sk=POINTER_SK,
            previous=previous,
            current_revision=revision,
        )

    return {
        "proofType": "MLB_V8_AUTONOMOUS_CHAMPION_PROMOTION",
        "version": VERSION,
        "createdAtUtc": created,
        "ok": not verification_errors and not rolled_back,
        "promoted": not verification_errors and not rolled_back,
        "alreadyActive": False,
        "rolledBack": rolled_back,
        "validation": validation,
        "artifact": {**champion_ref, **storage},
        "pointerRevision": revision,
        "previousPointerRevision": previous_revision,
        "prospectivePointerRevision": prospective_revision,
        "prospectivePointerChanged": prospective_changed,
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

    training = json.loads(
        Path(args.training_report).read_text(encoding="utf-8")
    )
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
