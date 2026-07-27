#!/usr/bin/env python3
"""Run a bounded, immutable historical first-five market probe.

The probe selects records by chronology and provider-event-ID availability only,
never by outcomes. It reads the V9 immutable historical dataset, requests exactly
first-five H2H and first-five spread at each game's T-minus-45 timestamp, writes one
content-addressed V8 shadow artifact, and never mutates training or production state.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import boto3
import requests

import mlb_odds_market_expansion_v8 as v8

VERSION = "MLB-V8-HISTORICAL-FIRST-FIVE-PROBE-v1"
EXPECTED_DATASET = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"
EXPECTED_HANDLER = "mlb_historical_optimizer_v7_recovery_entrypoint.lambda_handler"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
MARKETS = ("h2h_1st_5_innings", "spreads_1st_5_innings")
REGIONS = ("us",)
CREDITS_PER_HISTORICAL_MARKET_REGION = 10
DEFAULT_LIMIT = 20
DEFAULT_MAX_CREDITS = 500
HTTP_RETRIES = 4
HTTP_TIMEOUT_SECONDS = 25


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _request(session: requests.Session, url: str) -> Tuple[Any, Dict[str, Any]]:
    last: Exception | None = None
    for attempt in range(HTTP_RETRIES):
        try:
            response = session.get(
                url,
                timeout=HTTP_TIMEOUT_SECONDS,
                headers={
                    "accept": "application/json",
                    "user-agent": VERSION,
                },
            )
            if response.status_code == 429 or 500 <= response.status_code <= 599:
                if attempt + 1 < HTTP_RETRIES:
                    time.sleep(min(10, 2**attempt))
                    continue
            response.raise_for_status()
            headers: Dict[str, Any] = {}
            for name in ("x-requests-last", "x-requests-used", "x-requests-remaining"):
                raw = response.headers.get(name)
                if raw is None:
                    continue
                try:
                    headers[name] = int(raw)
                except Exception:
                    headers[name] = str(raw)
            return response.json(), headers
        except Exception as exc:
            last = exc
            if attempt + 1 >= HTTP_RETRIES:
                break
            time.sleep(min(10, 2**attempt))
    raise RuntimeError(f"historical_first_five_http_failed:{type(last).__name__}:{last}")


def _load_records(state: Mapping[str, Any], s3: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for slate in state.get("completedSlates") or []:
        if not isinstance(slate, Mapping):
            continue
        pointer = slate.get("artifact") or {}
        bucket = str(pointer.get("bucket") or "")
        key = str(pointer.get("key") or "")
        if not bucket or not key:
            raise RuntimeError("completed_slate_artifact_pointer_incomplete")
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        expected = str(pointer.get("sha256") or "")
        if expected and hashlib.sha256(body).hexdigest() != expected:
            raise RuntimeError(f"completed_slate_checksum_mismatch:{key}")
        dataset = json.loads(body.decode("utf-8"))
        if dataset.get("featureDatasetVersion") != EXPECTED_DATASET:
            raise RuntimeError(f"completed_slate_wrong_dataset:{key}")
        if dataset.get("completeSlate") is not True:
            raise RuntimeError(f"completed_slate_not_complete:{key}")
        if dataset.get("postLockDataExcluded") is not True:
            raise RuntimeError(f"completed_slate_post_lock_proof_missing:{key}")
        for row in dataset.get("records") or []:
            if isinstance(row, Mapping):
                value = _plain(row)
                value["slateDateEt"] = str(value.get("slateDateEt") or dataset.get("slateDateEt") or "")
                rows.append(value)
    return rows


def _eligible(records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for row in records:
        event_id = str(row.get("providerEventId") or "").strip()
        lock_at = str(row.get("predictionLockAtUtc") or "").strip()
        if not event_id or not lock_at:
            continue
        values.append(copy.deepcopy(dict(row)))
    # Use the newest chronology-only cohort to maximize endpoint continuity. No
    # result, score, winner, confidence, or audit metric participates in selection.
    return sorted(
        values,
        key=lambda row: (
            str(row.get("slateDateEt") or ""),
            str(row.get("predictionLockAtUtc") or ""),
            str(row.get("officialGamePk") or ""),
        ),
        reverse=True,
    )


def _config(max_credits: int, limit: int) -> v8.V8Config:
    return v8.V8Config(
        enabled=True,
        featured_regions=REGIONS,
        event_regions=REGIONS,
        featured_markets=("h2h",),
        first_five_enabled=True,
        alternates_enabled=False,
        team_props_enabled=False,
        player_props_enabled=False,
        max_event_markets=len(MARKETS),
        max_events_per_cycle=limit,
        max_estimated_credits_per_cycle=max_credits,
    )


def _immutable_put(s3: Any, bucket: str, key: str, body: bytes) -> Dict[str, Any]:
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            Metadata={
                "sha256": hashlib.sha256(body).hexdigest(),
                "record-type": "mlb-v8-historical-first-five-probe",
            },
            IfNoneMatch="*",
        )
        return {
            "bucket": bucket,
            "key": key,
            "versionId": response.get("VersionId"),
            "etag": str(response.get("ETag") or "").strip('"'),
            "alreadyExisted": False,
        }
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
        status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        code = str((response.get("Error") or {}).get("Code") or "")
        if status == 412 or code in {"PreconditionFailed", "ConditionalRequestConflict"}:
            head = s3.head_object(Bucket=bucket, Key=key)
            return {
                "bucket": bucket,
                "key": key,
                "versionId": head.get("VersionId"),
                "etag": str(head.get("ETag") or "").strip('"'),
                "alreadyExisted": True,
            }
        raise


def run(
    *,
    region: str,
    historical_stack: str,
    v8_stack: str,
    table_name: str,
    limit: int,
    max_credits: int,
    output: Path,
) -> Dict[str, Any]:
    if limit < 1 or limit > 20:
        raise ValueError("limit must be between 1 and 20")
    if max_credits < 1 or max_credits > DEFAULT_MAX_CREDITS:
        raise ValueError(f"max credits must be between 1 and {DEFAULT_MAX_CREDITS}")
    estimated = limit * len(MARKETS) * len(REGIONS) * CREDITS_PER_HISTORICAL_MARKET_REGION
    if estimated > max_credits:
        raise RuntimeError(f"probe_cost_guard_blocked:{estimated}>{max_credits}")
    api_key = str(os.environ.get("ODDS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ODDS_API_KEY is not configured")

    cf = boto3.client("cloudformation", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    historical_outputs = _outputs(cf, historical_stack)
    v8_outputs = _outputs(cf, v8_stack)
    function_name = historical_outputs.get("HistoricalOptimizerFunctionName")
    shadow_bucket = v8_outputs.get("ShadowArtifactsBucketName")
    if not function_name or not shadow_bucket:
        raise RuntimeError("required CloudFormation output is missing")
    config = lam.get_function_configuration(FunctionName=function_name)
    if config.get("Handler") != EXPECTED_HANDLER:
        raise RuntimeError("historical handler identity mismatch")
    state_item = ddb.Table(table_name).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK}, ConsistentRead=True
    ).get("Item")
    if not state_item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(state_item.get("data") or {})
    state_checks = {
        "expectedDataset": state.get("featureDatasetVersion") == EXPECTED_DATASET,
        "rematerializationComplete": state.get("featureRematerializationComplete") is True,
        "rematerializationCountsMatch": int(state.get("featureRematerializedSlateCount") or 0)
        == int(state.get("featureRematerializationTotalSlateCount") or 0),
        "rematerializationErrorsEmpty": not (state.get("featureRematerializationErrors") or []),
        "lastErrorEmpty": not state.get("lastError"),
    }
    if not all(state_checks.values()):
        raise RuntimeError("historical V9 evidence is not ready:" + json.dumps(state_checks, sort_keys=True))

    records = _load_records(state, s3)
    candidates = _eligible(records)
    selected = candidates[:limit]
    if len(selected) != limit:
        raise RuntimeError(f"provider_event_id_coverage_insufficient:{len(selected)}<{limit}")
    selection_material = [
        {
            "slateDateEt": row.get("slateDateEt"),
            "officialGamePk": row.get("officialGamePk"),
            "providerEventId": row.get("providerEventId"),
            "predictionLockAtUtc": row.get("predictionLockAtUtc"),
        }
        for row in selected
    ]
    selection_fingerprint = _sha(selection_material)
    cfg = _config(max_credits, limit)
    session = requests.Session()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    provider_cost = 0
    provider_remaining = None

    for row in selected:
        event_id = str(row.get("providerEventId"))
        lock_at = str(row.get("predictionLockAtUtc"))
        game_result: Dict[str, Any] = {
            "slateDateEt": row.get("slateDateEt"),
            "officialGamePk": row.get("officialGamePk"),
            "providerEventId": event_id,
            "predictionLockAtUtc": lock_at,
            "homeTeam": row.get("homeTeam"),
            "awayTeam": row.get("awayTeam"),
            "markets": {},
        }
        for market in MARKETS:
            reserved = CREDITS_PER_HISTORICAL_MARKET_REGION * len(REGIONS)
            if provider_cost + reserved > max_credits:
                raise RuntimeError("runtime cost guard blocked the next historical market request")
            try:
                raw, headers = _request(
                    session,
                    v8.event_odds_url(
                        api_key,
                        event_id,
                        (market,),
                        historical_at=lock_at,
                        config=cfg,
                    ),
                )
                actual = int(headers.get("x-requests-last") or reserved)
                provider_cost += max(0, actual)
                provider_remaining = headers.get("x-requests-remaining", provider_remaining)
                payload = raw.get("data") if isinstance(raw, Mapping) and isinstance(raw.get("data"), Mapping) else raw
                if not isinstance(payload, Mapping):
                    raise RuntimeError("historical event market payload is not an object")
                normalized = v8.normalize_event(payload)
                features = v8.derive_team_level_features(normalized)
                game_result["markets"][market] = {
                    "ok": True,
                    "normalized": normalized,
                    "features": features,
                    "quota": headers,
                }
            except Exception as exc:
                marker = {
                    "officialGamePk": row.get("officialGamePk"),
                    "providerEventId": event_id,
                    "market": market,
                    "error": f"{type(exc).__name__}:{str(exc)[:400]}",
                }
                errors.append(marker)
                game_result["markets"][market] = {"ok": False, "error": marker["error"]}
        results.append(game_result)

    successful_markets = sum(
        bool(market.get("ok"))
        for game in results
        for market in (game.get("markets") or {}).values()
        if isinstance(market, Mapping)
    )
    expected_markets = limit * len(MARKETS)
    complete_games = sum(
        all(bool((game.get("markets") or {}).get(market, {}).get("ok")) for market in MARKETS)
        for game in results
    )
    artifact = {
        "recordType": "MLB_V8_HISTORICAL_FIRST_FIVE_PROBE_ARTIFACT",
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "selectionRule": "newest chronology-only records with verified providerEventId and T-minus-45 timestamp",
        "selectionUsedOutcomes": False,
        "selectionFingerprint": selection_fingerprint,
        "marketSet": list(MARKETS),
        "regions": list(REGIONS),
        "selectedGameCount": limit,
        "expectedMarketRequestCount": expected_markets,
        "successfulMarketRequestCount": successful_markets,
        "completeGameCount": complete_games,
        "coverage": round(successful_markets / expected_markets, 8) if expected_markets else 0.0,
        "providerCreditsConsumed": provider_cost,
        "maximumCredits": max_credits,
        "providerRemainingCredits": provider_remaining,
        "errors": errors,
        "results": results,
        "authority": "SHADOW_ONLY",
        "trainingEligible": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
    }
    digest = _sha(artifact)
    key = f"mlb/v8/historical-first-five-probes/{digest}.json"
    body = json.dumps(artifact, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    pointer = _immutable_put(s3, shadow_bucket, key, body)
    pointer["sha256"] = hashlib.sha256(body).hexdigest()

    report = {
        "proofType": "MLB_V8_HISTORICAL_FIRST_FIVE_PROBE",
        "version": VERSION,
        "createdAtUtc": datetime.now(timezone.utc).isoformat(),
        "sourceSha": os.environ.get("GITHUB_SHA"),
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runUrl": (
            f"https://github.com/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/"
            f"{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "stateChecks": state_checks,
        "selectionFingerprint": selection_fingerprint,
        "selectedGameCount": limit,
        "expectedMarketRequestCount": expected_markets,
        "successfulMarketRequestCount": successful_markets,
        "completeGameCount": complete_games,
        "coverage": artifact["coverage"],
        "providerCreditsConsumed": provider_cost,
        "maximumCredits": max_credits,
        "providerRemainingCredits": provider_remaining,
        "errorCount": len(errors),
        "errors": errors,
        "artifact": pointer,
        "authority": "SHADOW_ONLY",
        "trainingEligible": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "blockers": [],
        "ok": True,
    }
    if provider_cost > max_credits:
        report["blockers"].append("provider_cost_exceeded_hard_cap")
    if successful_markets == 0:
        report["blockers"].append("historical_first_five_endpoint_returned_zero_usable_markets")
    if complete_games == 0:
        report["blockers"].append("no_game_has_both_first_five_markets")
    report["ok"] = not report["blockers"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--historical-stack", default="parlay-platform-mlb-historical-optimizer")
    parser.add_argument("--v8-stack", default="parlay-platform-mlb-odds-v8-shadow")
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-credits", type=int, default=DEFAULT_MAX_CREDITS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        historical_stack=args.historical_stack,
        v8_stack=args.v8_stack,
        table_name=args.table_name,
        limit=args.limit,
        max_credits=args.max_credits,
        output=Path(args.output),
    )
    return 0 if value.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
