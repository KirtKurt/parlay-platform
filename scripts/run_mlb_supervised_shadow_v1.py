#!/usr/bin/env python3
"""Train and evaluate the supervised MLB challenger from immutable AWS evidence.

The runner reads the canonical historical optimizer state and complete-slate S3
artifacts, trains a nested chronological shadow challenger, and writes only a
content-addressed shadow model artifact. It never writes DynamoDB authority,
predictions, locks, cutovers, or wagering state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

import boto3
from botocore.exceptions import ClientError

from mlb_supervised_shadow_v1 import train_and_evaluate

VERSION = "MLB-SUPERVISED-SHADOW-RUNNER-v1.0"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_state(ddb: Any, table_name: str) -> Dict[str, Any]:
    item = ddb.Table(table_name).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
    ).get("Item")
    if not item:
        raise RuntimeError("canonical historical optimizer state is missing")
    state = _plain(item.get("data") or {})
    if not isinstance(state, dict):
        raise RuntimeError("canonical historical optimizer state data is invalid")
    return state


def _read_pointer(s3: Any, pointer: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    if not bucket or not key:
        raise RuntimeError("completed-slate artifact pointer is incomplete")
    kwargs: Dict[str, Any] = {"Bucket": bucket, "Key": key}
    version_id = str(pointer.get("versionId") or "")
    if version_id and version_id != "unversioned":
        kwargs["VersionId"] = version_id
    response = s3.get_object(**kwargs)
    body = response["Body"].read()
    observed = _sha256(body)
    expected = str(pointer.get("sha256") or "")
    if expected and observed != expected:
        raise RuntimeError(f"historical artifact checksum mismatch:{key}")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"historical artifact is not an object:{key}")
    return value, {
        "bucket": bucket,
        "key": key,
        "versionId": response.get("VersionId") or version_id or "unversioned",
        "sha256": observed,
        "etag": str(response.get("ETag") or "").strip('"'),
    }


def _load_records(s3: Any, state: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    fingerprints: List[str] = []
    dates: List[str] = []
    v8_rows = 0
    completed = state.get("completedSlates") or []
    if not completed:
        raise RuntimeError("historical optimizer has no completed slates")
    for slate in completed:
        if not isinstance(slate, Mapping):
            raise RuntimeError("completed-slate ledger contains a non-object row")
        dataset, pointer = _read_pointer(s3, slate.get("artifact") or {})
        if dataset.get("completeSlate") is not True:
            raise RuntimeError(f"completed slate lost completeSlate proof:{slate.get('slateDateEt')}")
        if dataset.get("postLockDataExcluded") is not True:
            raise RuntimeError(f"completed slate lost post-lock exclusion proof:{slate.get('slateDateEt')}")
        if float(dataset.get("exactSlateCoverage") or 0.0) < 1.0 - 1e-12:
            raise RuntimeError(f"completed slate lost exact coverage:{slate.get('slateDateEt')}")
        day = str(dataset.get("slateDateEt") or slate.get("slateDateEt") or "")
        rows = dataset.get("records") or []
        if not day or not isinstance(rows, list):
            raise RuntimeError("completed slate dataset identity or records are invalid")
        for row in rows:
            if not isinstance(row, Mapping):
                raise RuntimeError(f"completed slate contains non-object record:{day}")
            value = _plain(row)
            if value.get("postLockDataExcluded") is not True:
                raise RuntimeError(f"game record lost post-lock exclusion proof:{day}")
            if value.get("gameSpecificLockClipping") is not True:
                raise RuntimeError(f"game record lost T-minus-45 clipping proof:{day}")
            expansion = (
                value.get("oddsMarketExpansionFeatures")
                or ((value.get("homeSignal") or {}).get("oddsMarketExpansionFeatures"))
                or ((value.get("awaySignal") or {}).get("oddsMarketExpansionFeatures"))
            )
            if isinstance(expansion, Mapping) and expansion:
                v8_rows += 1
            records.append(value)
        fingerprints.append(str(pointer.get("sha256") or ""))
        dates.append(day)
    material = {
        "completedSlateCount": len(completed),
        "recordCount": len(records),
        "firstDate": min(dates),
        "lastDate": max(dates),
        "artifactSha256": fingerprints,
    }
    return records, {
        **material,
        "datasetFingerprint": _sha256(_canonical_bytes(material)),
        "v8FeatureRecordCount": v8_rows,
        "v8FeatureCoverage": round(v8_rows / len(records), 8) if records else 0.0,
    }


def _put_shadow_model(s3: Any, bucket: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    body = _canonical_bytes(payload)
    digest = _sha256(body)
    key = f"mlb/supervised-shadow-v1/models/{digest}.json"
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
                "record-type": "mlb_supervised_shadow_model_v1",
                "authority": "shadow_only",
            },
        )
        created = True
        version_id = response.get("VersionId") or "unversioned"
        etag = str(response.get("ETag") or "").strip('"')
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        status = int((exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        if code not in {"PreconditionFailed", "412"} and status != 412:
            raise
        response = s3.get_object(Bucket=bucket, Key=key)
        existing = response["Body"].read()
        if _sha256(existing) != digest:
            raise RuntimeError("content-addressed supervised model collision") from exc
        created = False
        version_id = response.get("VersionId") or "unversioned"
        etag = str(response.get("ETag") or "").strip('"')
    return {
        "bucket": bucket,
        "key": key,
        "versionId": version_id,
        "etag": etag,
        "sha256": digest,
        "created": created,
        "immutable": True,
        "authority": "SHADOW_ONLY",
    }


def run(table_name: str, output: Path, region: str) -> Dict[str, Any]:
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    state = _load_state(ddb, table_name)
    if state.get("featureRematerializationComplete") is not True:
        raise RuntimeError("feature rematerialization is incomplete")
    if state.get("featureRematerializationErrors"):
        raise RuntimeError("feature rematerialization has unresolved errors")
    records, dataset = _load_records(s3, state)
    result = train_and_evaluate(records)
    model = result.get("modelArtifact") or {}
    if not isinstance(model, Mapping):
        raise RuntimeError("supervised shadow trainer omitted model artifact")
    bucket = str((state.get("completedSlates") or [{}])[0].get("artifact", {}).get("bucket") or "")
    if not bucket:
        raise RuntimeError("historical artifact bucket could not be resolved")
    pointer = _put_shadow_model(s3, bucket, model)
    latest = state.get("latestExperiment") or {}
    v7_gate = latest.get("promotionGate") or {}
    report = {
        "proofType": "MLB_SUPERVISED_SHADOW_V1_RUN",
        "version": VERSION,
        "createdAtUtc": _now_iso(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "authority": "SHADOW_ONLY",
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "canonicalHistoricalState": {
            "phase": state.get("phase"),
            "optimizationRound": state.get("optimizationRound"),
            "eligibleGameCount": state.get("eligibleGameCount"),
            "completeSlateCount": state.get("completeSlateCount"),
            "currentDate": state.get("currentDate"),
            "currentSlotIndex": state.get("currentSlotIndex"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "featureRematerializationComplete": state.get("featureRematerializationComplete"),
            "featureRematerializationErrors": state.get("featureRematerializationErrors") or [],
            "lastError": state.get("lastError"),
        },
        "dataset": dataset,
        "v7IncumbentCandidate": {
            "experimentId": latest.get("experimentId"),
            "status": latest.get("status"),
            "walkForwardMeanDailyAccuracy": v7_gate.get("walkForwardMeanDailyAccuracy"),
            "walkForwardMinimumDailyAccuracy": v7_gate.get("walkForwardMinimumDailyAccuracy"),
            "untouchedHoldoutMeanDailyAccuracy": v7_gate.get("untouchedHoldoutMeanDailyAccuracy"),
            "untouchedHoldoutMinimumDailyAccuracy": v7_gate.get("untouchedHoldoutMinimumDailyAccuracy"),
            "promotionPassed": v7_gate.get("passed") is True,
        },
        "supervisedShadow": result,
        "modelArtifact": pointer,
        "blockers": [],
        "ok": True,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(_plain(report), indent=2, sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", default="parlay_platform_snapshots")
    parser.add_argument("--region", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(args.table, Path(args.output), args.region)
    summary = {
        "ok": report.get("ok"),
        "authority": report.get("authority"),
        "dataset": report.get("dataset"),
        "status": (report.get("supervisedShadow") or {}).get("status"),
        "selectedFeatureGroup": (report.get("supervisedShadow") or {}).get("selectedFeatureGroup"),
        "walkForward": (report.get("supervisedShadow") or {}).get("walkForward"),
        "untouchedHoldout": (report.get("supervisedShadow") or {}).get("untouchedHoldout"),
        "promotionGate": (report.get("supervisedShadow") or {}).get("promotionGate"),
        "modelArtifact": report.get("modelArtifact"),
    }
    print(json.dumps(_plain(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
