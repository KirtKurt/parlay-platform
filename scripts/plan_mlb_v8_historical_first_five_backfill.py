#!/usr/bin/env python3
"""Create an immutable, zero-provider-call MLB first-five backfill plan.

The planner reads only the reconciled V9 historical state and immutable completed-slate
artifacts. It selects every record with a verified provider event ID and T-minus-45
lock timestamp, computes an exact provider-credit ceiling, and writes a content-addressed
plan to the isolated V8 shadow bucket. It never calls The Odds API and never authorizes
or executes paid collection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import boto3

VERSION = "MLB-V8-HISTORICAL-FIRST-FIVE-BACKFILL-PLAN-v1"
EXPECTED_DATASET = "MLB-HISTORICAL-FEATURE-DATASET-v9-v8-event-id-trainable"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
MARKETS = ("h2h_1st_5_innings", "spreads_1st_5_innings")
REGIONS = ("us",)
CREDITS_PER_HISTORICAL_MARKET_REGION = 10
DEFAULT_MAX_CREDITS = 75_000
MAX_ALLOWED_CREDITS = 100_000
PROVIDER_QUOTA_RESERVE = 1_000


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


def _outputs(cf: Any, stack_name: str) -> Dict[str, str]:
    stack = (cf.describe_stacks(StackName=stack_name).get("Stacks") or [])[0]
    return {
        str(row.get("OutputKey")): str(row.get("OutputValue"))
        for row in stack.get("Outputs") or []
        if row.get("OutputKey") and row.get("OutputValue")
    }


def _state_checks(state: Mapping[str, Any]) -> Dict[str, bool]:
    completed = [row for row in state.get("completedSlates") or [] if isinstance(row, Mapping)]
    total = len(completed)
    versions = {str(row.get("featureDatasetVersion") or "") for row in completed}
    return {
        "expectedDataset": state.get("featureDatasetVersion") == EXPECTED_DATASET,
        "rematerializationComplete": state.get("featureRematerializationComplete") is True,
        "completedSlateCountPositive": total > 0,
        "stateCompleteSlateCountMatches": int(state.get("completeSlateCount") or 0) == total,
        "rematerializedCountMatches": int(state.get("featureRematerializedSlateCount") or 0) == total,
        "rematerializationTotalMatches": int(state.get("featureRematerializationTotalSlateCount") or 0) == total,
        "everyPointerVersionMatches": versions == {EXPECTED_DATASET},
        "rematerializationErrorsEmpty": not (state.get("featureRematerializationErrors") or []),
        "lastErrorEmpty": not state.get("lastError"),
        "paidRematerializationCallsZero": int(state.get("featureRematerializationPaidHistoricalCalls") or 0) == 0,
    }


def _load_items(state: Mapping[str, Any], s3: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen_games = set()
    event_to_game: Dict[str, Tuple[str, str]] = {}
    for slate in sorted(
        [row for row in state.get("completedSlates") or [] if isinstance(row, Mapping)],
        key=lambda row: str(row.get("slateDateEt") or ""),
    ):
        pointer = slate.get("artifact") or {}
        bucket = str(pointer.get("bucket") or "")
        key = str(pointer.get("key") or "")
        expected_sha = str(pointer.get("sha256") or "")
        if not bucket or not key or not expected_sha:
            raise RuntimeError("completed_slate_artifact_pointer_incomplete")
        response = s3.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        if hashlib.sha256(body).hexdigest() != expected_sha:
            raise RuntimeError(f"completed_slate_checksum_mismatch:{key}")
        dataset = json.loads(body.decode("utf-8"))
        if dataset.get("featureDatasetVersion") != EXPECTED_DATASET:
            raise RuntimeError(f"completed_slate_wrong_dataset:{key}")
        if dataset.get("completeSlate") is not True:
            raise RuntimeError(f"completed_slate_not_complete:{key}")
        if dataset.get("postLockDataExcluded") is not True:
            raise RuntimeError(f"completed_slate_post_lock_proof_missing:{key}")
        if dataset.get("gameSpecificLockClipping") is not True:
            raise RuntimeError(f"completed_slate_lock_clipping_proof_missing:{key}")
        records = [row for row in dataset.get("records") or [] if isinstance(row, Mapping)]
        if len(records) != int(dataset.get("officialGameCount") or 0):
            raise RuntimeError(f"completed_slate_record_count_mismatch:{key}")
        for row in records:
            day = str(row.get("slateDateEt") or dataset.get("slateDateEt") or "")
            game_pk = str(row.get("officialGamePk") or "").strip()
            event_id = str(row.get("providerEventId") or "").strip()
            lock_at = str(row.get("predictionLockAtUtc") or "").strip()
            home = str(row.get("homeTeam") or "").strip()
            away = str(row.get("awayTeam") or "").strip()
            if not day or not game_pk or not event_id or not lock_at or not home or not away:
                raise RuntimeError(f"backfill_identity_incomplete:{day}:{game_pk or 'missing_game_pk'}")
            identity = (day, game_pk)
            if identity in seen_games:
                raise RuntimeError(f"duplicate_official_game_identity:{day}:{game_pk}")
            seen_games.add(identity)
            prior = event_to_game.get(event_id)
            if prior and prior != identity:
                raise RuntimeError(
                    f"provider_event_id_collision:{event_id}:{prior[0]}:{prior[1]}:{day}:{game_pk}"
                )
            event_to_game[event_id] = identity
            items.append(
                {
                    "slateDateEt": day,
                    "officialGamePk": game_pk,
                    "providerEventId": event_id,
                    "predictionLockAtUtc": lock_at,
                    "homeTeam": home,
                    "awayTeam": away,
                    "sourceDataset": {
                        "bucket": bucket,
                        "key": key,
                        "sha256": expected_sha,
                        "versionId": pointer.get("versionId"),
                    },
                }
            )
    return sorted(
        items,
        key=lambda row: (
            row["slateDateEt"],
            row["predictionLockAtUtc"],
            row["officialGamePk"],
        ),
    )


def _cost(game_count: int) -> Dict[str, int]:
    request_count = game_count * len(MARKETS)
    credits_per_request = len(REGIONS) * CREDITS_PER_HISTORICAL_MARKET_REGION
    return {
        "gameCount": game_count,
        "marketCountPerGame": len(MARKETS),
        "regionCount": len(REGIONS),
        "historicalRequestCount": request_count,
        "creditsPerHistoricalRequest": credits_per_request,
        "estimatedCredits": request_count * credits_per_request,
    }


def _put_immutable(s3: Any, bucket: str, body: bytes) -> Dict[str, Any]:
    digest = hashlib.sha256(body).hexdigest()
    key = f"mlb/v8/historical-first-five-backfill-plans/{digest}.json"
    try:
        response = s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            IfNoneMatch="*",
            Metadata={
                "record-type": "mlb-v8-historical-first-five-backfill-plan",
                "version": VERSION,
                "sha256": digest,
            },
        )
        already_existed = False
        version_id = response.get("VersionId")
        etag = str(response.get("ETag") or "").strip('"')
    except Exception as exc:
        response = getattr(exc, "response", {}) or {}
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


def build_plan(
    *,
    state: Mapping[str, Any],
    items: Sequence[Mapping[str, Any]],
    maximum_credits: int,
) -> Dict[str, Any]:
    checks = _state_checks(state)
    if not all(checks.values()):
        raise RuntimeError("historical_state_not_ready:" + json.dumps(checks, sort_keys=True))
    if not items:
        raise RuntimeError("backfill_plan_contains_no_games")
    cost = _cost(len(items))
    estimated = cost["estimatedCredits"]
    configured_remaining = max(
        0,
        int(state.get("maximumCredits") or 0) - int(state.get("creditsConsumed") or 0),
    )
    quota = state.get("lastQuota") or {}
    provider_remaining = int(quota.get("x-requests-remaining") or 0)
    budget_checks = {
        "withinPlanMaximum": estimated <= maximum_credits,
        "withinHistoricalConfiguredRemaining": estimated <= configured_remaining,
        "providerQuotaReservePreserved": estimated + PROVIDER_QUOTA_RESERVE <= provider_remaining,
    }
    if not all(budget_checks.values()):
        raise RuntimeError("first_five_backfill_budget_blocked:" + json.dumps(budget_checks, sort_keys=True))
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
        "version": VERSION,
        "featureDatasetVersion": EXPECTED_DATASET,
        "markets": list(MARKETS),
        "regions": list(REGIONS),
        "cost": cost,
        "maximumCredits": maximum_credits,
        "selectionFingerprint": selection_fingerprint,
        "items": selection_material,
    }
    plan_fingerprint = _sha(semantic_material)
    authorization_token = "AUTHORIZE_MLB_FIRST_FIVE_BACKFILL_" + plan_fingerprint[:24].upper()
    return {
        "proofType": "MLB_V8_HISTORICAL_FIRST_FIVE_BACKFILL_PLAN",
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
        "authority": "PLAN_ONLY",
        "providerCallsMade": 0,
        "selectionUsedOutcomes": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "trainingEligible": False,
        "featureDatasetVersion": EXPECTED_DATASET,
        "stateSnapshot": {
            "revision": state.get("revision"),
            "phase": state.get("phase"),
            "currentDate": state.get("currentDate"),
            "completeSlateCount": state.get("completeSlateCount"),
            "eligibleGameCount": state.get("eligibleGameCount"),
            "featureDatasetVersion": state.get("featureDatasetVersion"),
            "featureRematerializedSlateCount": state.get("featureRematerializedSlateCount"),
            "featureRematerializationTotalSlateCount": state.get("featureRematerializationTotalSlateCount"),
            "creditsConsumed": state.get("creditsConsumed"),
            "maximumCredits": state.get("maximumCredits"),
            "configuredHistoricalCreditsRemaining": configured_remaining,
            "providerReportedCreditsRemaining": provider_remaining,
        },
        "stateChecks": checks,
        "budgetChecks": budget_checks,
        "markets": list(MARKETS),
        "regions": list(REGIONS),
        "costModel": {
            **cost,
            "maximumCredits": maximum_credits,
            "providerQuotaReserve": PROVIDER_QUOTA_RESERVE,
        },
        "selectionRule": "all reconciled V9 records with unique officialGamePk, providerEventId and immutable T-minus-45 timestamp",
        "selectionFingerprint": selection_fingerprint,
        "planFingerprint": plan_fingerprint,
        "authorization": {
            "required": True,
            "token": authorization_token,
            "authorized": False,
            "executionAllowed": False,
            "paidCollectionStarted": False,
        },
        "items": selection_material,
        "blockers": ["explicit_backfill_authorization_not_recorded"],
        "ok": True,
    }


def run(
    *,
    region: str,
    historical_stack: str,
    v8_stack: str,
    table_name: str,
    maximum_credits: int,
    output: Path,
) -> Dict[str, Any]:
    if maximum_credits < 1 or maximum_credits > MAX_ALLOWED_CREDITS:
        raise ValueError(f"maximum credits must be between 1 and {MAX_ALLOWED_CREDITS}")
    cf = boto3.client("cloudformation", region_name=region)
    ddb = boto3.resource("dynamodb", region_name=region)
    s3 = boto3.client("s3", region_name=region)
    historical_outputs = _outputs(cf, historical_stack)
    v8_outputs = _outputs(cf, v8_stack)
    if not historical_outputs.get("HistoricalOptimizerFunctionName"):
        raise RuntimeError("historical optimizer function output is missing")
    shadow_bucket = v8_outputs.get("ShadowArtifactsBucketName")
    if not shadow_bucket:
        raise RuntimeError("V8 shadow artifacts bucket output is missing")
    item = ddb.Table(table_name).get_item(
        Key={"PK": STATE_PK, "SK": STATE_SK},
        ConsistentRead=True,
    ).get("Item")
    if not item:
        raise RuntimeError("historical optimizer state is missing")
    state = _plain(item.get("data") or {})
    checks = _state_checks(state)
    if not all(checks.values()):
        raise RuntimeError("historical_state_not_ready:" + json.dumps(checks, sort_keys=True))
    items = _load_items(state, s3)
    plan = build_plan(state=state, items=items, maximum_credits=maximum_credits)
    body = _json_bytes(plan, pretty=True)
    plan["artifact"] = _put_immutable(s3, shadow_bucket, body)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": plan["ok"],
                "planFingerprint": plan["planFingerprint"],
                "gameCount": plan["costModel"]["gameCount"],
                "historicalRequestCount": plan["costModel"]["historicalRequestCount"],
                "estimatedCredits": plan["costModel"]["estimatedCredits"],
                "maximumCredits": plan["costModel"]["maximumCredits"],
                "authorization": plan["authorization"],
                "artifact": plan["artifact"],
                "blockers": plan["blockers"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", required=True)
    parser.add_argument("--historical-stack", default="parlay-platform-mlb-historical-optimizer")
    parser.add_argument("--v8-stack", default="parlay-platform-mlb-odds-v8-shadow")
    parser.add_argument("--table-name", default="parlay_platform_snapshots")
    parser.add_argument("--maximum-credits", type=int, default=DEFAULT_MAX_CREDITS)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    value = run(
        region=args.region,
        historical_stack=args.historical_stack,
        v8_stack=args.v8_stack,
        table_name=args.table_name,
        maximum_credits=args.maximum_credits,
        output=Path(args.output),
    )
    return 0 if value.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
