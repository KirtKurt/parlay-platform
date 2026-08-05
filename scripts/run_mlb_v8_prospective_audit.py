#!/usr/bin/env python3
"""Persist and advance the frozen MLB V8 prospective-audit lifecycle."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple

import boto3
from botocore.exceptions import ClientError

import mlb_v8_autonomy_v1 as autonomy
import mlb_v8_prospective_audit_v1 as prospective
import run_mlb_supervised_shadow_v2 as historical

VERSION = "MLB-V8-PROSPECTIVE-AUDIT-RUNNER-v1-atomic-state"
POINTER_PK = "MLB_V8_PROSPECTIVE_AUDIT#V1"
POINTER_SK = "ACTIVE"
RECORD_TYPE = "mlb_v8_prospective_audit_pointer_v1"
TERMINAL = frozenset({"REJECTED", "PROMOTED"})


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        + "\n"
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _put_immutable(
    s3: Any,
    *,
    bucket: str,
    key: str,
    value: Mapping[str, Any],
    record_type: str,
) -> Dict[str, Any]:
    body = _json_bytes(value)
    digest = _sha_bytes(body)
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={"sha256": digest, "record-type": record_type},
        )
        return {
            "bucket": bucket,
            "key": key,
            "sha256": digest,
            "versionId": response.get("VersionId"),
            "alreadyExisted": False,
        }
    except ClientError as exc:
        error = exc.response.get("Error") or {}
        status = int(
            (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
            or 0
        )
        if str(error.get("Code") or "") not in {
            "PreconditionFailed",
            "ConditionalRequestConflict",
        } and status not in {409, 412}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        existing = str((head.get("Metadata") or {}).get("sha256") or "")
        if existing != digest:
            raise RuntimeError("V8 prospective immutable artifact collision") from exc
        return {
            "bucket": bucket,
            "key": key,
            "sha256": digest,
            "versionId": head.get("VersionId"),
            "alreadyExisted": True,
        }


def _load_json_pointer(s3: Any, pointer: Mapping[str, Any]) -> Dict[str, Any]:
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    digest = str(pointer.get("sha256") or "")
    if not bucket or not key or not digest:
        raise RuntimeError("V8 prospective artifact pointer is incomplete")
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response["Body"].read()
    if _sha_bytes(body) != digest:
        raise RuntimeError("V8 prospective artifact checksum mismatch")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("V8 prospective artifact is not an object")
    return value


def _write_pointer(
    table: Any,
    *,
    previous_revision: int,
    data: Mapping[str, Any],
    created_at: str,
) -> int:
    revision = previous_revision + 1
    item = {
        "PK": POINTER_PK,
        "SK": POINTER_SK,
        "record_type": RECORD_TYPE,
        "revision": revision,
        "updated_at": created_at,
        "data": copy.deepcopy(dict(data)),
    }
    if previous_revision:
        table.put_item(
            Item=item,
            ConditionExpression="#revision = :expected",
            ExpressionAttributeNames={"#revision": "revision"},
            ExpressionAttributeValues={":expected": previous_revision},
        )
    else:
        table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(PK)",
        )
    return revision


def _current_pointer(table: Any) -> Tuple[Dict[str, Any], int]:
    item = table.get_item(
        Key={"PK": POINTER_PK, "SK": POINTER_SK},
        ConsistentRead=True,
    ).get("Item") or {}
    return copy.deepcopy(dict(item.get("data") or {})), int(
        item.get("revision") or 0
    )


def _candidate_model_digest(training: Mapping[str, Any]) -> str | None:
    eligibility = prospective.candidate_eligibility(training)
    bundle = eligibility.get("modelBundle") or {}
    return str(bundle.get("modelDigest") or "") or None


def _effective_current(
    training: Mapping[str, Any], lifecycle: Mapping[str, Any]
) -> Dict[str, Any]:
    value = copy.deepcopy(dict(training))
    value["prospectiveAuditLifecycle"] = copy.deepcopy(dict(lifecycle))
    value["productionAuthorityChanged"] = False
    value["automaticWagerAllowed"] = False
    value["resultDigest"] = autonomy._sha(
        {key: item for key, item in value.items() if key != "resultDigest"}
    )
    return value


def _report(
    *,
    created_at: str,
    status: str,
    pointer_revision: int,
    candidate: Mapping[str, Any] | None,
    candidate_pointer: Mapping[str, Any] | None,
    audit: Mapping[str, Any] | None,
    audit_pointer: Mapping[str, Any] | None,
    action: str,
    blockers: Sequence[str] = (),
) -> Dict[str, Any]:
    result = {
        "proofType": "MLB_V8_PROSPECTIVE_AUDIT_LIFECYCLE",
        "version": VERSION,
        "createdAtUtc": created_at,
        "ok": True,
        "status": status,
        "action": action,
        "pointerRevision": pointer_revision,
        "candidateDigest": (candidate or {}).get("candidateDigest"),
        "modelDigest": (candidate or {}).get("modelDigest"),
        "frozenCorpusLastDate": (candidate or {}).get(
            "frozenCorpusLastDate"
        ),
        "candidateArtifact": copy.deepcopy(dict(candidate_pointer or {})),
        "auditArtifact": copy.deepcopy(dict(audit_pointer or {})),
        "audit": copy.deepcopy(dict(audit or {})),
        "prospectiveEvidenceComplete": (audit or {}).get(
            "prospectiveEvidenceComplete"
        ),
        "prospectiveAuditPassed": (audit or {}).get(
            "prospectiveAuditPassed"
        ),
        "prospectiveAuditRejected": (audit or {}).get(
            "prospectiveAuditRejected"
        ),
        "blockers": sorted(set(str(item) for item in blockers if item)),
        "modelRefitDuringProspectiveAudit": False,
        "selectionUsedProspectiveOutcomes": False,
        "automaticWagerAllowed": False,
        "productionAuthorityChanged": False,
    }
    result["lifecycleDigest"] = autonomy._sha(result)
    return result


def advance(
    *,
    training: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    table: Any,
    s3: Any,
    bucket: str,
    created_at: str | None = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    observed_at = created_at or datetime.now(timezone.utc).isoformat()
    pointer, revision = _current_pointer(table)
    status = str(pointer.get("status") or "").upper()
    candidate_pointer = pointer.get("candidateArtifact") or {}
    candidate = None
    if candidate_pointer:
        candidate = _load_json_pointer(s3, candidate_pointer)
        prospective.verify_candidate(candidate)
        if pointer.get("candidateDigest") != candidate.get("candidateDigest"):
            raise RuntimeError("V8 prospective pointer candidate mismatch")

    current_model_digest = _candidate_model_digest(training)
    if status in {"COLLECTING", "PASSED"} and candidate is not None:
        if status == "PASSED":
            audit_pointer = pointer.get("auditArtifact") or {}
            audit_value = _load_json_pointer(s3, audit_pointer)
            effective = prospective.augment_training_for_promotion(
                candidate, audit_value
            )
            lifecycle = _report(
                created_at=observed_at,
                status="PASSED",
                pointer_revision=revision,
                candidate=candidate,
                candidate_pointer=candidate_pointer,
                audit=audit_value,
                audit_pointer=audit_pointer,
                action="AUTO_PROMOTE_GUARDED_CHAMPION",
            )
            effective["prospectiveAuditLifecycle"] = lifecycle
            effective["resultDigest"] = autonomy._sha(
                {
                    key: item
                    for key, item in effective.items()
                    if key != "resultDigest"
                }
            )
            return lifecycle, effective

        audit_value = prospective.evaluate_candidate(candidate, records)
        audit_key = (
            "mlb/v8/prospective-audits/"
            f"{candidate['candidateDigest']}/{audit_value['auditDigest']}.json"
        )
        audit_pointer = _put_immutable(
            s3,
            bucket=bucket,
            key=audit_key,
            value=audit_value,
            record_type="mlb-v8-prospective-audit",
        )
        if audit_value.get("prospectiveAuditPassed") is True:
            new_status = "PASSED"
            action = "AUTO_PROMOTE_GUARDED_CHAMPION"
        elif audit_value.get("prospectiveAuditRejected") is True:
            new_status = "REJECTED"
            action = "CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH"
        else:
            new_status = "COLLECTING"
            action = "COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT"
        revision = _write_pointer(
            table,
            previous_revision=revision,
            created_at=observed_at,
            data={
                "version": VERSION,
                "status": new_status,
                "candidateDigest": candidate["candidateDigest"],
                "modelDigest": candidate["modelDigest"],
                "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
                "candidateArtifact": candidate_pointer,
                "auditArtifact": audit_pointer,
                "prospectiveGameCount": int(
                    (audit_value.get("modelMetrics") or {}).get("gameCount") or 0
                ),
                "prospectiveDayCount": int(
                    (audit_value.get("modelMetrics") or {}).get("dayCount") or 0
                ),
                "automaticWagerAllowed": False,
                "productionAuthorityChanged": False,
            },
        )
        lifecycle = _report(
            created_at=observed_at,
            status=new_status,
            pointer_revision=revision,
            candidate=candidate,
            candidate_pointer=candidate_pointer,
            audit=audit_value,
            audit_pointer=audit_pointer,
            action=action,
            blockers=audit_value.get("errors") or [],
        )
        if new_status == "PASSED":
            effective = prospective.augment_training_for_promotion(
                candidate, audit_value
            )
        else:
            effective = _effective_current(training, lifecycle)
        effective["prospectiveAuditLifecycle"] = lifecycle
        effective["resultDigest"] = autonomy._sha(
            {
                key: item
                for key, item in effective.items()
                if key != "resultDigest"
            }
        )
        return lifecycle, effective

    previous_digest = str(pointer.get("modelDigest") or "")
    if status in TERMINAL and current_model_digest == previous_digest:
        lifecycle = _report(
            created_at=observed_at,
            status=status,
            pointer_revision=revision,
            candidate=candidate,
            candidate_pointer=candidate_pointer,
            audit=None,
            audit_pointer=pointer.get("auditArtifact") or {},
            action="CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH",
            blockers=["awaiting_distinct_gate_passing_challenger"],
        )
        return lifecycle, _effective_current(training, lifecycle)

    eligibility = prospective.candidate_eligibility(training)
    if eligibility["ok"] is not True:
        lifecycle = _report(
            created_at=observed_at,
            status="WAITING_FOR_RETROSPECTIVE_GATE",
            pointer_revision=revision,
            candidate=None,
            candidate_pointer=None,
            audit=None,
            audit_pointer=None,
            action="CONTINUE_AUTONOMOUS_CANDIDATE_SEARCH",
            blockers=eligibility.get("errors") or [],
        )
        return lifecycle, _effective_current(training, lifecycle)

    candidate = prospective.build_candidate(training)
    candidate_key = (
        "mlb/v8/prospective-candidates/"
        f"{candidate['modelDigest']}/{candidate['candidateDigest']}.json"
    )
    candidate_pointer = _put_immutable(
        s3,
        bucket=bucket,
        key=candidate_key,
        value=candidate,
        record_type="mlb-v8-prospective-candidate",
    )
    revision = _write_pointer(
        table,
        previous_revision=revision,
        created_at=observed_at,
        data={
            "version": VERSION,
            "status": "COLLECTING",
            "candidateDigest": candidate["candidateDigest"],
            "modelDigest": candidate["modelDigest"],
            "frozenCorpusLastDate": candidate["frozenCorpusLastDate"],
            "candidateArtifact": candidate_pointer,
            "auditArtifact": {},
            "prospectiveGameCount": 0,
            "prospectiveDayCount": 0,
            "automaticWagerAllowed": False,
            "productionAuthorityChanged": False,
        },
    )
    audit_value = prospective.evaluate_candidate(candidate, records)
    lifecycle = _report(
        created_at=observed_at,
        status="COLLECTING",
        pointer_revision=revision,
        candidate=candidate,
        candidate_pointer=candidate_pointer,
        audit=audit_value,
        audit_pointer=None,
        action="COLLECT_AUTONOMOUS_PROSPECTIVE_AUDIT",
        blockers=audit_value.get("errors") or [],
    )
    return lifecycle, _effective_current(training, lifecycle)


def _load_runtime_records(
    *, region: str, stack_name: str, table_name: str
) -> Tuple[list[Dict[str, Any]], Any, Any, str, Dict[str, Any]]:
    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    table = ddb.Table(table_name)
    function_name, resolution = historical._resolve_function_name(
        cf, lam, stack_name
    )
    config = lam.get_function_configuration(FunctionName=function_name)
    environment = (config.get("Environment") or {}).get("Variables") or {}
    item = table.get_item(
        Key={"PK": historical.STATE_PK, "SK": historical.STATE_SK},
        ConsistentRead=True,
    ).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = historical._plain(item.get("data") or {})
    records = historical._load_records(state, s3)
    outputs = historical._outputs(cf, stack_name)
    bucket = str(
        outputs.get("HistoricalArtifactsBucketName")
        or environment.get("MLB_HISTORICAL_ARTIFACTS_BUCKET")
        or ""
    ).strip()
    if not bucket:
        raise RuntimeError("historical artifacts bucket could not be resolved")
    return records, table, s3, bucket, {
        "stackName": stack_name,
        "functionName": function_name,
        "functionResolution": resolution,
        "handler": config.get("Handler"),
        "stateCurrentDate": state.get("currentDate"),
        "stateEndDate": state.get("endDate"),
        "recordCount": len(records),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument(
        "--stack-name", default="parlay-platform-mlb-historical-optimizer"
    )
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--training-report", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--effective-training-output", required=True)
    args = parser.parse_args()

    training = json.loads(
        Path(args.training_report).read_text(encoding="utf-8")
    )
    records, table, s3, bucket, runtime_identity = _load_runtime_records(
        region=args.region,
        stack_name=args.stack_name,
        table_name=args.table_name,
    )
    lifecycle, effective = advance(
        training=training,
        records=records,
        table=table,
        s3=s3,
        bucket=bucket,
    )
    lifecycle["runtimeIdentity"] = runtime_identity
    lifecycle["recordCountLoaded"] = len(records)
    lifecycle["lifecycleDigest"] = autonomy._sha(
        {
            key: item
            for key, item in lifecycle.items()
            if key != "lifecycleDigest"
        }
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(lifecycle, indent=2, sort_keys=True) + "\n")
    effective_path = Path(args.effective_training_output)
    effective_path.parent.mkdir(parents=True, exist_ok=True)
    effective_path.write_text(
        json.dumps(effective, indent=2, sort_keys=True) + "\n"
    )
    print(
        json.dumps(
            {
                "ok": lifecycle.get("ok"),
                "status": lifecycle.get("status"),
                "action": lifecycle.get("action"),
                "modelDigest": lifecycle.get("modelDigest"),
                "recordCountLoaded": len(records),
                "prospectiveEvidenceComplete": lifecycle.get(
                    "prospectiveEvidenceComplete"
                ),
                "prospectiveAuditPassed": lifecycle.get(
                    "prospectiveAuditPassed"
                ),
                "blockers": lifecycle.get("blockers"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if lifecycle.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
