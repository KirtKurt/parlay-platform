#!/usr/bin/env python3
"""Run one authorized, fail-closed MLB first-five historical backfill batch.

Safety properties:
- No execution without a fingerprinted authorization manifest.
- Exactly one automatic provider attempt per planned market request.
- A DynamoDB ATTEMPTED marker is written before the provider call, so a crash can
  create an unresolved request but never an automatic duplicate paid call.
- A transactional reservation enforces the plan's total credit ceiling.
- Results and failures are immutable, exact-content-addressed S3 evidence.
- No production prediction, champion, cutover, training or wagering authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import boto3
import requests
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

import mlb_odds_market_expansion_v8 as v8

VERSION = "MLB-V8-HISTORICAL-FIRST-FIVE-BACKFILL-WORKER-v1"
AUTHORIZATION_VERSION = "MLB-V8-HISTORICAL-FIRST-FIVE-BACKFILL-AUTHORIZATION-v1"
PLAN_VERSION = "MLB-V8-HISTORICAL-FIRST-FIVE-BACKFILL-PLAN-v1"
EXPECTED_DATASET = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"
EXPECTED_MARKETS = ("h2h_1st_5_innings", "spreads_1st_5_innings")
EXPECTED_REGIONS = ("us",)
PLANNED_CREDITS_PER_REQUEST = 10
MAX_PLAN_CREDITS = 75_000
DEFAULT_BATCH_REQUESTS = 200
MAX_BATCH_REQUESTS = 400
HTTP_TIMEOUT_SECONDS = 25
REQUEST_STATUS_ATTEMPTED = "ATTEMPTED_NO_AUTO_RETRY"
REQUEST_STATUS_SUCCEEDED = "SUCCEEDED"
REQUEST_STATUS_FAILED = "FAILED_NO_AUTO_RETRY"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        return (json.dumps(value, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _redact(value: Any) -> str:
    text = str(value)
    secret = str(os.environ.get("ODDS_API_KEY") or "")
    if secret:
        text = text.replace(secret, "[REDACTED]")
    return re.sub(r"(?i)(apiKey=)[^&\s\"']+", r"\1[REDACTED]", text)


def _normalize_team(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


def _quota_headers(response: requests.Response) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in ("x-requests-last", "x-requests-used", "x-requests-remaining"):
        raw = response.headers.get(name)
        if raw is None:
            continue
        try:
            out[name] = int(raw)
        except Exception:
            out[name] = str(raw)
    return out


def _serialize_item(value: Mapping[str, Any]) -> Dict[str, Any]:
    serializer = TypeSerializer()
    return {str(key): serializer.serialize(item) for key, item in value.items()}


def _serialize_values(value: Mapping[str, Any]) -> Dict[str, Any]:
    serializer = TypeSerializer()
    return {str(key): serializer.serialize(item) for key, item in value.items()}


def _load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON document is not an object:{path}")
    return value


def _semantic_cost(plan: Mapping[str, Any]) -> Dict[str, int]:
    cost = plan.get("costModel") or {}
    keys = (
        "gameCount",
        "marketCountPerGame",
        "regionCount",
        "historicalRequestCount",
        "creditsPerHistoricalRequest",
        "estimatedCredits",
    )
    return {key: int(cost.get(key) or 0) for key in keys}


def _verify_plan_report(plan: Mapping[str, Any]) -> Dict[str, Any]:
    items = [dict(row) for row in plan.get("items") or [] if isinstance(row, Mapping)]
    markets = tuple(str(value) for value in plan.get("markets") or [])
    regions = tuple(str(value) for value in plan.get("regions") or [])
    cost = _semantic_cost(plan)
    checks = {
        "proofType": plan.get("proofType") == "MLB_V8_HISTORICAL_FIRST_FIVE_BACKFILL_PLAN",
        "version": plan.get("version") == PLAN_VERSION,
        "ok": plan.get("ok") is True,
        "planOnly": plan.get("authority") == "PLAN_ONLY",
        "providerCallsZero": int(plan.get("providerCallsMade") or 0) == 0,
        "outcomeFreeSelection": plan.get("selectionUsedOutcomes") is False,
        "productionAuthorityUnchanged": plan.get("productionAuthorityChanged") is False,
        "notTrainingEligible": plan.get("trainingEligible") is False,
        "expectedDataset": plan.get("featureDatasetVersion") == EXPECTED_DATASET,
        "expectedMarkets": markets == EXPECTED_MARKETS,
        "expectedRegions": regions == EXPECTED_REGIONS,
        "itemsPresent": bool(items),
        "gameCountMatches": cost["gameCount"] == len(items),
        "marketCountMatches": cost["marketCountPerGame"] == len(markets),
        "regionCountMatches": cost["regionCount"] == len(regions),
        "requestCountMatches": cost["historicalRequestCount"] == len(items) * len(markets),
        "creditsPerRequestMatches": cost["creditsPerHistoricalRequest"] == PLANNED_CREDITS_PER_REQUEST,
        "creditMathMatches": cost["estimatedCredits"]
        == cost["historicalRequestCount"] * PLANNED_CREDITS_PER_REQUEST,
        "withinHardMaximum": 0 < cost["estimatedCredits"] <= MAX_PLAN_CREDITS,
    }
    selection_material = [
        {
            "slateDateEt": row.get("slateDateEt"),
            "officialGamePk": row.get("officialGamePk"),
            "providerEventId": row.get("providerEventId"),
            "predictionLockAtUtc": row.get("predictionLockAtUtc"),
            "homeTeam": row.get("homeTeam"),
            "awayTeam": row.get("awayTeam"),
            "sourceDataset": row.get("sourceDataset"),
        }
        for row in items
    ]
    selection_fingerprint = _sha(selection_material)
    semantic_material = {
        "version": plan.get("version"),
        "featureDatasetVersion": plan.get("featureDatasetVersion"),
        "markets": list(markets),
        "regions": list(regions),
        "cost": cost,
        "maximumCredits": int((plan.get("costModel") or {}).get("maximumCredits") or 0),
        "selectionFingerprint": selection_fingerprint,
        "items": selection_material,
    }
    plan_fingerprint = _sha(semantic_material)
    checks.update(
        {
            "selectionFingerprintMatches": selection_fingerprint
            == str(plan.get("selectionFingerprint") or ""),
            "planFingerprintMatches": plan_fingerprint == str(plan.get("planFingerprint") or ""),
            "authorizationRequired": (plan.get("authorization") or {}).get("required") is True,
            "planDidNotAuthorizeItself": (plan.get("authorization") or {}).get("authorized") is False
            and (plan.get("authorization") or {}).get("executionAllowed") is False
            and (plan.get("authorization") or {}).get("paidCollectionStarted") is False,
        }
    )
    if not all(checks.values()):
        raise RuntimeError("backfill_plan_validation_failed:" + json.dumps(checks, sort_keys=True))
    return {
        "items": items,
        "markets": markets,
        "regions": regions,
        "cost": cost,
        "selectionFingerprint": selection_fingerprint,
        "planFingerprint": plan_fingerprint,
        "authorizationToken": str((plan.get("authorization") or {}).get("token") or ""),
        "artifact": dict(plan.get("artifact") or {}),
        "checks": checks,
    }


def _verify_authorization(
    manifest: Mapping[str, Any], verified_plan: Mapping[str, Any]
) -> Dict[str, Any]:
    token = str(verified_plan.get("authorizationToken") or "")
    checks = {
        "version": manifest.get("version") == AUTHORIZATION_VERSION,
        "authorized": manifest.get("authorized") is True,
        "planFingerprint": str(manifest.get("planFingerprint") or "")
        == str(verified_plan.get("planFingerprint") or ""),
        "selectionFingerprint": str(manifest.get("selectionFingerprint") or "")
        == str(verified_plan.get("selectionFingerprint") or ""),
        "authorizationTokenDigest": str(manifest.get("authorizationTokenSha256") or "")
        == _sha_text(token),
        "maximumCredits": int(manifest.get("maximumCredits") or 0)
        == int((verified_plan.get("cost") or {}).get("estimatedCredits") or 0),
        "paidCollectionAuthorized": manifest.get("paidCollectionAuthorized") is True,
        "productionAuthorityChanged": manifest.get("productionAuthorityChanged") is False,
    }
    if not all(checks.values()):
        raise RuntimeError("backfill_authorization_validation_failed:" + json.dumps(checks, sort_keys=True))
    return {
        "checks": checks,
        "manifestDigest": _sha(manifest),
        "authorizedAtUtc": manifest.get("authorizedAtUtc"),
    }


def _get_s3_json(s3: Any, pointer: Mapping[str, Any]) -> Dict[str, Any]:
    bucket = str(pointer.get("bucket") or "")
    key = str(pointer.get("key") or "")
    expected = str(pointer.get("sha256") or "")
    if not bucket or not key or not expected:
        raise RuntimeError("plan_artifact_pointer_incomplete")
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != expected:
        raise RuntimeError("plan_artifact_checksum_mismatch")
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("plan_artifact_is_not_an_object")
    return value


def _verify_plan_artifact(report: Mapping[str, Any], artifact: Mapping[str, Any]) -> None:
    keys = (
        "proofType",
        "version",
        "featureDatasetVersion",
        "selectionFingerprint",
        "planFingerprint",
        "markets",
        "regions",
        "costModel",
        "authorization",
        "items",
    )
    checks = {key: artifact.get(key) == report.get(key) for key in keys}
    if not all(checks.values()):
        raise RuntimeError("plan_report_artifact_mismatch:" + json.dumps(checks, sort_keys=True))


def _put_immutable(s3: Any, bucket: str, prefix: str, value: Mapping[str, Any]) -> Dict[str, Any]:
    body = _json_bytes(value, pretty=True)
    digest = hashlib.sha256(body).hexdigest()
    key = f"{prefix.rstrip('/')}/{digest}.json"
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={
                "record-type": "mlb-v8-first-five-backfill-request-evidence",
                "version": VERSION,
                "sha256": digest,
            },
        )
        already_existed = False
        version_id = response.get("VersionId")
        etag = str(response.get("ETag") or "").strip('"')
    except ClientError as exc:
        response = exc.response or {}
        status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        code = str((response.get("Error") or {}).get("Code") or "")
        if status != 412 and code not in {"PreconditionFailed", "ConditionalRequestConflict"}:
            raise
        head = s3.head_object(Bucket=bucket, Key=key)
        already_existed = True
        version_id = head.get("VersionId")
        etag = str(head.get("ETag") or "").strip('"')
    return {
        "bucket": bucket,
        "key": key,
        "sha256": digest,
        "versionId": version_id,
        "etag": etag,
        "alreadyExisted": already_existed,
    }


def _requests_for_plan(verified: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in verified.get("items") or []:
        for market in verified.get("markets") or []:
            index = len(out)
            request = {
                "index": index,
                "slateDateEt": item.get("slateDateEt"),
                "officialGamePk": item.get("officialGamePk"),
                "providerEventId": item.get("providerEventId"),
                "predictionLockAtUtc": item.get("predictionLockAtUtc"),
                "homeTeam": item.get("homeTeam"),
                "awayTeam": item.get("awayTeam"),
                "market": market,
                "regions": list(verified.get("regions") or []),
                "plannedCredits": PLANNED_CREDITS_PER_REQUEST,
                "sourceDataset": item.get("sourceDataset"),
            }
            request["requestDigest"] = _sha(request)
            out.append(request)
    return out


def _pk(plan_fingerprint: str) -> str:
    return f"MLB_V8_FIRST_FIVE_BACKFILL#{plan_fingerprint}"


def _request_sk(index: int) -> str:
    return f"REQUEST#{index:06d}"


def _initialize_meta(
    table: Any,
    plan: Mapping[str, Any],
    authorization: Mapping[str, Any],
    plan_artifact: Mapping[str, Any],
) -> Dict[str, Any]:
    plan_fingerprint = str(plan.get("planFingerprint") or "")
    key = {"PK": _pk(plan_fingerprint), "SK": "META"}
    existing = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if existing:
        existing = _plain(existing)
        checks = {
            "planFingerprint": existing.get("planFingerprint") == plan_fingerprint,
            "selectionFingerprint": existing.get("selectionFingerprint")
            == plan.get("selectionFingerprint"),
            "authorizationManifestDigest": existing.get("authorizationManifestDigest")
            == authorization.get("manifestDigest"),
            "planArtifactSha256": existing.get("planArtifactSha256")
            == plan_artifact.get("sha256"),
            "maximumCredits": int(existing.get("maximumCredits") or 0)
            == int((plan.get("cost") or {}).get("estimatedCredits") or 0),
            "authorized": existing.get("authorized") is True,
        }
        if not all(checks.values()):
            raise RuntimeError("existing_backfill_meta_mismatch:" + json.dumps(checks, sort_keys=True))
        return existing
    now = _now()
    item = {
        **key,
        "record_type": "mlb_v8_first_five_backfill_meta",
        "version": VERSION,
        "planFingerprint": plan_fingerprint,
        "selectionFingerprint": plan.get("selectionFingerprint"),
        "planArtifactSha256": plan_artifact.get("sha256"),
        "authorizationManifestDigest": authorization.get("manifestDigest"),
        "authorizedAtUtc": authorization.get("authorizedAtUtc"),
        "authorized": True,
        "completed": False,
        "manualResolutionRequired": False,
        "maximumCredits": int((plan.get("cost") or {}).get("estimatedCredits") or 0),
        "plannedRequestCount": int((plan.get("cost") or {}).get("historicalRequestCount") or 0),
        "reservedCredits": 0,
        "actualCredits": 0,
        "attemptedCount": 0,
        "succeededCount": 0,
        "failedCount": 0,
        "nextIndex": 0,
        "createdAtUtc": now,
        "updatedAtUtc": now,
        "productionAuthorityChanged": False,
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(PK)")
        return item
    except ClientError as exc:
        if str((exc.response.get("Error") or {}).get("Code") or "") != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not existing:
            raise RuntimeError("backfill_meta_conditional_collision_without_item")
        return _plain(existing)


def _claim_request(
    table: Any,
    request: Mapping[str, Any],
    plan_fingerprint: str,
    attempt_id: str,
) -> Tuple[bool, Dict[str, Any] | None]:
    key = {"PK": _pk(plan_fingerprint), "SK": _request_sk(int(request["index"]))}
    existing = table.get_item(Key=key, ConsistentRead=True).get("Item")
    if existing:
        return False, _plain(existing)
    now = _now()
    request_item = {
        **key,
        "record_type": "mlb_v8_first_five_backfill_request",
        "version": VERSION,
        "planFingerprint": plan_fingerprint,
        "index": int(request["index"]),
        "requestDigest": request.get("requestDigest"),
        "officialGamePk": request.get("officialGamePk"),
        "providerEventId": request.get("providerEventId"),
        "predictionLockAtUtc": request.get("predictionLockAtUtc"),
        "market": request.get("market"),
        "plannedCredits": PLANNED_CREDITS_PER_REQUEST,
        "status": REQUEST_STATUS_ATTEMPTED,
        "attemptId": attempt_id,
        "attemptedAtUtc": now,
        "automaticRetryAllowed": False,
        "productionAuthorityChanged": False,
    }
    meta_key = {"PK": _pk(plan_fingerprint), "SK": "META"}
    client = table.meta.client
    maximum_before = int(
        table.get_item(Key=meta_key, ConsistentRead=True).get("Item", {}).get("maximumCredits")
        or 0
    ) - PLANNED_CREDITS_PER_REQUEST
    try:
        client.transact_write_items(
            ClientRequestToken=("mlb-v8-f5-claim-" + _sha_text(f"{plan_fingerprint}:{request['index']}")[:20]),
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": _serialize_item(request_item),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                {
                    "Update": {
                        "TableName": table.name,
                        "Key": _serialize_item(meta_key),
                        "UpdateExpression": "SET updatedAtUtc = :now ADD reservedCredits :credits, attemptedCount :one",
                        "ConditionExpression": "authorized = :true AND completed = :false AND reservedCredits <= :maximumBefore",
                        "ExpressionAttributeValues": _serialize_values(
                            {
                                ":now": now,
                                ":credits": PLANNED_CREDITS_PER_REQUEST,
                                ":one": 1,
                                ":true": True,
                                ":false": False,
                                ":maximumBefore": maximum_before,
                            }
                        ),
                    }
                },
            ],
        )
        return True, request_item
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code") or "")
        if code not in {"TransactionCanceledException", "ConditionalCheckFailedException"}:
            raise
        existing = table.get_item(Key=key, ConsistentRead=True).get("Item")
        if existing:
            return False, _plain(existing)
        meta = table.get_item(Key=meta_key, ConsistentRead=True).get("Item") or {}
        if int(meta.get("reservedCredits") or 0) > maximum_before:
            raise RuntimeError("backfill_credit_reservation_ceiling_reached")
        raise RuntimeError("backfill_request_claim_transaction_failed")


def _advance_meta(table: Any, plan_fingerprint: str, next_index: int) -> None:
    key = {"PK": _pk(plan_fingerprint), "SK": "META"}
    try:
        table.update_item(
            Key=key,
            UpdateExpression="SET nextIndex = :nextIndex, updatedAtUtc = :now",
            ConditionExpression="attribute_not_exists(nextIndex) OR nextIndex <= :nextIndex",
            ExpressionAttributeValues={":nextIndex": int(next_index), ":now": _now()},
        )
    except ClientError as exc:
        if str((exc.response.get("Error") or {}).get("Code") or "") != "ConditionalCheckFailedException":
            raise


def _finalize_request(
    table: Any,
    request: Mapping[str, Any],
    plan_fingerprint: str,
    attempt_id: str,
    *,
    status: str,
    actual_credits: int,
    evidence: Mapping[str, Any],
    error: str | None = None,
) -> None:
    request_key = {"PK": _pk(plan_fingerprint), "SK": _request_sk(int(request["index"]))}
    expression = (
        "SET #status = :status, actualCredits = :actualCredits, evidence = :evidence, "
        "completedAtUtc = :now, automaticRetryAllowed = :false"
    )
    values: Dict[str, Any] = {
        ":status": status,
        ":actualCredits": int(actual_credits),
        ":evidence": dict(evidence),
        ":now": _now(),
        ":false": False,
        ":attemptId": attempt_id,
        ":attempted": REQUEST_STATUS_ATTEMPTED,
    }
    if error is not None:
        expression += ", error = :error"
        values[":error"] = _redact(error)[:1000]
    table.update_item(
        Key=request_key,
        UpdateExpression=expression,
        ConditionExpression="attemptId = :attemptId AND #status = :attempted",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues=values,
    )
    meta_key = {"PK": _pk(plan_fingerprint), "SK": "META"}
    counter_name = "succeededCount" if status == REQUEST_STATUS_SUCCEEDED else "failedCount"
    table.update_item(
        Key=meta_key,
        UpdateExpression=f"SET updatedAtUtc = :now ADD actualCredits :credits, {counter_name} :one",
        ExpressionAttributeValues={":now": _now(), ":credits": int(actual_credits), ":one": 1},
    )


def _status_counts(table: Any, plan_fingerprint: str) -> Dict[str, int]:
    counts = Counter()
    kwargs: Dict[str, Any] = {
        "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
        "ExpressionAttributeValues": {":pk": _pk(plan_fingerprint), ":prefix": "REQUEST#"},
        "ProjectionExpression": "#status",
        "ExpressionAttributeNames": {"#status": "status"},
    }
    while True:
        response = table.query(**kwargs)
        for item in response.get("Items") or []:
            counts[str(item.get("status") or "MISSING")] += 1
        key = response.get("LastEvaluatedKey")
        if not key:
            break
        kwargs["ExclusiveStartKey"] = key
    return dict(sorted(counts.items()))


def _mark_meta_completion(
    table: Any,
    plan_fingerprint: str,
    total_requests: int,
    status_counts: Mapping[str, int],
) -> Dict[str, Any]:
    succeeded = int(status_counts.get(REQUEST_STATUS_SUCCEEDED) or 0)
    failed = int(status_counts.get(REQUEST_STATUS_FAILED) or 0)
    attempted = int(status_counts.get(REQUEST_STATUS_ATTEMPTED) or 0)
    finished = succeeded + failed == total_requests and attempted == 0
    clean = finished and failed == 0
    table.update_item(
        Key={"PK": _pk(plan_fingerprint), "SK": "META"},
        UpdateExpression=(
            "SET completed = :completed, manualResolutionRequired = :manual, "
            "statusCounts = :counts, updatedAtUtc = :now, nextIndex = :nextIndex"
        ),
        ExpressionAttributeValues={
            ":completed": bool(clean),
            ":manual": bool(failed or attempted),
            ":counts": dict(status_counts),
            ":now": _now(),
            ":nextIndex": int(total_requests),
        },
    )
    item = table.get_item(
        Key={"PK": _pk(plan_fingerprint), "SK": "META"}, ConsistentRead=True
    ).get("Item")
    return _plain(item or {})


def _http_once(
    session: requests.Session,
    api_key: str,
    request: Mapping[str, Any],
) -> Tuple[bool, Dict[str, Any], Dict[str, Any], str | None]:
    config = v8.V8Config(
        enabled=True,
        featured_regions=EXPECTED_REGIONS,
        event_regions=EXPECTED_REGIONS,
        featured_markets=("h2h",),
        first_five_enabled=True,
        alternates_enabled=False,
        team_props_enabled=False,
        player_props_enabled=False,
        max_event_markets=1,
        max_events_per_cycle=1,
        max_estimated_credits_per_cycle=PLANNED_CREDITS_PER_REQUEST,
    )
    url = v8.event_odds_url(
        api_key,
        str(request.get("providerEventId") or ""),
        (str(request.get("market") or ""),),
        historical_at=str(request.get("predictionLockAtUtc") or ""),
        config=config,
    )
    try:
        response = session.get(
            url,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"accept": "application/json", "user-agent": VERSION},
        )
    except Exception as exc:
        return False, {}, {}, f"{type(exc).__name__}:{_redact(exc)}"
    quota = _quota_headers(response)
    actual_credits = int(quota.get("x-requests-last") or PLANNED_CREDITS_PER_REQUEST)
    metadata = {
        "httpStatus": int(response.status_code),
        "quota": quota,
        "actualCredits": actual_credits,
        "responseBodySha256": hashlib.sha256(response.content).hexdigest(),
    }
    if response.status_code < 200 or response.status_code >= 300:
        excerpt = _redact(response.text[:500])
        return False, metadata, {}, f"HTTP_{response.status_code}:{excerpt}"
    try:
        raw = response.json()
    except Exception as exc:
        return False, metadata, {}, f"JSON_DECODE:{type(exc).__name__}:{exc}"
    payload = raw.get("data") if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else raw
    if not isinstance(payload, Mapping):
        return False, metadata, {}, "provider_payload_not_object"
    event_id = str(payload.get("id") or "")
    if event_id != str(request.get("providerEventId") or ""):
        return False, metadata, {}, "provider_event_id_mismatch"
    home_ok = _normalize_team(payload.get("home_team")) == _normalize_team(request.get("homeTeam"))
    away_ok = _normalize_team(payload.get("away_team")) == _normalize_team(request.get("awayTeam"))
    if not home_ok or not away_ok:
        return False, metadata, {}, "provider_team_identity_mismatch"
    normalized = v8.normalize_event(payload)
    features = v8.derive_team_level_features(normalized)
    return True, metadata, {"normalized": normalized, "features": features}, None


def run(
    *,
    region: str,
    table_name: str,
    plan_path: Path,
    authorization_path: Path,
    batch_requests: int,
    output: Path,
) -> Dict[str, Any]:
    if batch_requests < 1 or batch_requests > MAX_BATCH_REQUESTS:
        raise ValueError(f"batch requests must be between 1 and {MAX_BATCH_REQUESTS}")
    api_key = str(os.environ.get("ODDS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")
    plan_report = _load_json(plan_path)
    verified = _verify_plan_report(plan_report)
    authorization_manifest = _load_json(authorization_path)
    authorization = _verify_authorization(authorization_manifest, verified)

    s3 = boto3.client("s3", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    table = ddb.Table(table_name)
    plan_artifact = _get_s3_json(s3, verified.get("artifact") or {})
    _verify_plan_artifact(plan_report, plan_artifact)
    meta = _initialize_meta(table, verified, authorization, verified.get("artifact") or {})
    requests_to_run = _requests_for_plan(verified)
    total_requests = len(requests_to_run)
    if total_requests != int((verified.get("cost") or {}).get("historicalRequestCount") or 0):
        raise RuntimeError("flattened_request_count_mismatch")
    start_index = max(0, min(total_requests, int(meta.get("nextIndex") or 0)))
    session = requests.Session()
    batch_provider_calls = 0
    batch_actual_credits = 0
    batch_succeeded = 0
    batch_failed = 0
    skipped_existing = 0
    last_index = start_index
    shadow_bucket = str((verified.get("artifact") or {}).get("bucket") or "")
    if not shadow_bucket:
        raise RuntimeError("shadow_bucket_missing_from_plan_artifact")

    for request in requests_to_run[start_index:]:
        if batch_provider_calls >= batch_requests:
            break
        index = int(request["index"])
        last_index = index + 1
        attempt_id = str(uuid.uuid4())
        claimed, existing = _claim_request(
            table,
            request,
            str(verified.get("planFingerprint") or ""),
            attempt_id,
        )
        if not claimed:
            skipped_existing += 1
            _advance_meta(table, str(verified.get("planFingerprint") or ""), index + 1)
            continue
        ok, metadata, provider_payload, error = _http_once(session, api_key, request)
        batch_provider_calls += 1
        actual_credits = int(metadata.get("actualCredits") or PLANNED_CREDITS_PER_REQUEST)
        batch_actual_credits += actual_credits
        evidence_body = {
            "recordType": "MLB_V8_HISTORICAL_FIRST_FIVE_BACKFILL_REQUEST_EVIDENCE",
            "version": VERSION,
            "createdAtUtc": _now(),
            "planFingerprint": verified.get("planFingerprint"),
            "selectionFingerprint": verified.get("selectionFingerprint"),
            "request": dict(request),
            "attemptId": attempt_id,
            "providerCallAttemptCount": 1,
            "automaticRetryAllowed": False,
            "success": bool(ok),
            "metadata": metadata,
            "providerPayload": provider_payload if ok else {},
            "error": _redact(error)[:1000] if error else None,
            "authority": "SHADOW_ONLY",
            "trainingEligible": False,
            "productionAuthorityChanged": False,
            "automaticWagerAllowed": False,
        }
        pointer = _put_immutable(
            s3,
            shadow_bucket,
            (
                f"mlb/v8/historical-first-five-backfill/results/"
                f"{verified.get('planFingerprint')}/{index:06d}"
            ),
            evidence_body,
        )
        if ok:
            batch_succeeded += 1
            _finalize_request(
                table,
                request,
                str(verified.get("planFingerprint") or ""),
                attempt_id,
                status=REQUEST_STATUS_SUCCEEDED,
                actual_credits=actual_credits,
                evidence=pointer,
            )
        else:
            batch_failed += 1
            _finalize_request(
                table,
                request,
                str(verified.get("planFingerprint") or ""),
                attempt_id,
                status=REQUEST_STATUS_FAILED,
                actual_credits=actual_credits,
                evidence=pointer,
                error=error or "unknown_provider_failure",
            )
        _advance_meta(table, str(verified.get("planFingerprint") or ""), index + 1)
        time.sleep(0.05)

    plan_fingerprint = str(verified.get("planFingerprint") or "")
    _advance_meta(table, plan_fingerprint, last_index)
    counts = _status_counts(table, plan_fingerprint)
    finished_count = int(counts.get(REQUEST_STATUS_SUCCEEDED) or 0) + int(
        counts.get(REQUEST_STATUS_FAILED) or 0
    )
    unresolved = int(counts.get(REQUEST_STATUS_ATTEMPTED) or 0)
    all_attempted = sum(counts.values()) >= total_requests and finished_count + unresolved >= total_requests
    if all_attempted:
        meta = _mark_meta_completion(table, plan_fingerprint, total_requests, counts)
    else:
        meta = _plain(
            table.get_item(
                Key={"PK": _pk(plan_fingerprint), "SK": "META"}, ConsistentRead=True
            ).get("Item")
            or {}
        )
    failed_total = int(counts.get(REQUEST_STATUS_FAILED) or 0)
    succeeded_total = int(counts.get(REQUEST_STATUS_SUCCEEDED) or 0)
    complete_clean = succeeded_total == total_requests and failed_total == 0 and unresolved == 0
    execution_can_continue = not all_attempted and unresolved == 0
    blockers: List[str] = []
    if int(meta.get("reservedCredits") or 0) > int(meta.get("maximumCredits") or 0):
        blockers.append("reserved_credit_ceiling_exceeded")
    if int(meta.get("actualCredits") or 0) > int(meta.get("maximumCredits") or 0):
        blockers.append("actual_credit_ceiling_exceeded")
    if all_attempted and failed_total:
        blockers.append("provider_requests_failed_without_automatic_retry")
    if all_attempted and unresolved:
        blockers.append("provider_requests_unresolved_after_pre_call_claim")
    if unresolved and not all_attempted:
        blockers.append("unresolved_attempted_request_requires_manual_review")
        execution_can_continue = False
    report = {
        "proofType": "MLB_V8_HISTORICAL_FIRST_FIVE_BACKFILL_BATCH",
        "version": VERSION,
        "createdAtUtc": _now(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "planFingerprint": plan_fingerprint,
        "selectionFingerprint": verified.get("selectionFingerprint"),
        "authorizationManifestDigest": authorization.get("manifestDigest"),
        "batch": {
            "startIndex": start_index,
            "endIndexExclusive": last_index,
            "maximumProviderCalls": batch_requests,
            "providerCallsMade": batch_provider_calls,
            "actualCredits": batch_actual_credits,
            "succeeded": batch_succeeded,
            "failed": batch_failed,
            "skippedExisting": skipped_existing,
        },
        "progress": {
            "plannedRequestCount": total_requests,
            "statusCounts": counts,
            "succeededCount": succeeded_total,
            "failedCount": failed_total,
            "unresolvedAttemptedCount": unresolved,
            "allRequestsAttempted": all_attempted,
            "completeClean": complete_clean,
            "executionCanContinue": execution_can_continue,
            "reservedCredits": int(meta.get("reservedCredits") or 0),
            "actualCredits": int(meta.get("actualCredits") or 0),
            "maximumCredits": int(meta.get("maximumCredits") or 0),
            "nextIndex": int(meta.get("nextIndex") or 0),
        },
        "authority": "SHADOW_ONLY",
        "trainingEligible": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "automaticProviderRetryAllowed": False,
        "blockers": blockers,
        "ok": not blockers,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--batch-requests", type=int, default=DEFAULT_BATCH_REQUESTS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        table_name=args.table_name,
        plan_path=Path(args.plan),
        authorization_path=Path(args.authorization),
        batch_requests=args.batch_requests,
        output=Path(args.output),
    )
    # Provider-level failures are durable evidence, not a reason to retry the same
    # request automatically. The workflow uses executionCanContinue to decide whether
    # another batch should be dispatched.
    return 0 if value.get("progress") else 1


if __name__ == "__main__":
    raise SystemExit(main())
