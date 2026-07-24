"""AWS orchestrator for the MLB historical whole-slate optimizer.

The function is resumable and idempotent.  It first builds a no-paid-history
request/credit plan, then requires an explicit plan-fingerprint authorization
before any The Odds API historical call.  Every paid response is written to a
versioned S3 object before the DynamoDB cursor advances, so retries cannot
silently spend twice for an already archived timestamp.  Promotion requires
1,000 training games, 200 walk-forward games, 200 untouched-audit games, and
at least 80 percent on every complete validation/audit slate day.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import boto3

import mlb_canonical_final_labels_v1 as final_labels
import mlb_historical_daily_optimizer_v1 as optimizer
import mlb_historical_policy_v1 as policy_runtime


VERSION = "MLB-HISTORICAL-OPTIMIZER-AWS-v1.6-complete-ledger-fresh-audit-append-only-authority"
STATE_PK = "MLB_HISTORICAL_OPTIMIZER#V1"
STATE_SK = "STATE"
LEASE_SK = "LEASE"
EXPERIMENT_SK_PREFIX = "EXPERIMENT#"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST = 10
AUTHORIZATION_CONFIRMATION = "AUTHORIZE_THE_ODDS_API_HISTORICAL_CREDITS"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except Exception:
        return default


TARGET_TABLE = os.environ.get("TARGET_SNAPSHOTS_TABLE") or os.environ.get("SNAPSHOTS_TABLE", "")
ARTIFACTS_BUCKET = os.environ.get("MLB_HISTORICAL_ARTIFACTS_BUCKET", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
START_DATE = os.environ.get("MLB_HISTORICAL_START_DATE", "2025-04-01")
END_DATE = os.environ.get("MLB_HISTORICAL_END_DATE", "2025-08-31")
TARGET_GAMES = max(
    policy_runtime.MIN_TOTAL_SETTLED_GAMES,
    _env_int("MLB_HISTORICAL_TARGET_GAMES", 1600),
)
MAX_NETWORK_REQUESTS = max(1, min(100, _env_int("MLB_HISTORICAL_REQUESTS_PER_RUN", 40)))
MAX_CREDITS = max(1000, _env_int("MLB_HISTORICAL_MAX_CREDITS", 120000))
QUOTA_RESERVE = max(0, _env_int("MLB_HISTORICAL_QUOTA_RESERVE", 100))
MAX_CANDIDATES = max(100, _env_int("MLB_HISTORICAL_MAX_CANDIDATES", 25000))
MAX_OPTIMIZATION_ROUNDS = max(1, _env_int("MLB_HISTORICAL_MAX_OPTIMIZATION_ROUNDS", 6))
FRESH_AUDIT_INCREMENT_GAMES = max(
    policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
    _env_int("MLB_HISTORICAL_FRESH_AUDIT_INCREMENT_GAMES", 250),
)
LEASE_SECONDS = max(60, _env_int("MLB_HISTORICAL_LEASE_SECONDS", 840))
HTTP_TIMEOUT_SECONDS = max(5, _env_int("MLB_HISTORICAL_HTTP_TIMEOUT_SECONDS", 30))

_DDB = boto3.resource("dynamodb")
_S3 = boto3.client("s3")


class OrchestrationError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("DynamoDB cannot store non-finite floats")
        return Decimal(str(value))
    if isinstance(value, Mapping):
        return {str(key): _ddb_safe(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_ddb_safe(item) for item in value]
    return value


def _table():
    if not TARGET_TABLE:
        raise OrchestrationError("TARGET_SNAPSHOTS_TABLE is not configured")
    return _DDB.Table(TARGET_TABLE)


def _require_configuration() -> None:
    if not ARTIFACTS_BUCKET:
        raise OrchestrationError("MLB_HISTORICAL_ARTIFACTS_BUCKET is not configured")
    if not ODDS_API_KEY:
        raise OrchestrationError("ODDS_API_KEY is not configured")
    start = date.fromisoformat(START_DATE)
    end = date.fromisoformat(END_DATE)
    if end < start:
        raise OrchestrationError("MLB_HISTORICAL_END_DATE precedes start date")


def _get_item(sk: str, *, pk: str = STATE_PK) -> Optional[Dict[str, Any]]:
    item = _table().get_item(Key={"PK": pk, "SK": sk}, ConsistentRead=True).get("Item")
    return _plain(item) if item else None


def _save_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    value = _migrate_state(state)
    value["version"] = VERSION
    value["updatedAtUtc"] = _now_iso()
    value["revision"] = int(value.get("revision") or 0) + 1
    _table().put_item(
        Item=_ddb_safe(
            {
                "PK": STATE_PK,
                "SK": STATE_SK,
                "record_type": "mlb_historical_optimizer_state_v1",
                "updated_at": value["updatedAtUtc"],
                "revision": value["revision"],
                "data": value,
            }
        )
    )
    return value


def _load_state() -> Optional[Dict[str, Any]]:
    item = _get_item(STATE_SK)
    return copy.deepcopy(item.get("data") or {}) if item else None


def _migrate_state(state: Mapping[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(dict(state or {}))
    value["targetSettledGames"] = max(
        int(value.get("targetSettledGames") or 0), TARGET_GAMES
    )
    value["minimumTrainingGames"] = policy_runtime.MIN_TRAINING_GAMES
    value["minimumWalkForwardGames"] = policy_runtime.MIN_WALK_FORWARD_GAMES
    value["minimumUntouchedAuditGames"] = policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES
    value["minimumTotalEvidenceGames"] = policy_runtime.MIN_TOTAL_SETTLED_GAMES
    value.setdefault("paidBackfillAuthorized", False)
    value.setdefault("optimizationRound", 0)
    value.setdefault("plan", None)
    value.setdefault("freshAuditExpansionRequired", False)
    value.setdefault("freshAuditStartDate", None)
    value.setdefault("evaluatedAuditWindows", [])
    eligible = int(value.get("eligibleGameCount") or 0)
    if eligible < int(value["targetSettledGames"]) and value.get("phase") in {
        "OPTIMIZING",
        "CANDIDATE_REJECTED",
        "PROMOTED",
    }:
        # An old 1,000-total state cannot retain authority under the stricter
        # 1,000-train + 200 + 200 contract.  It must be replanned/recollected.
        value["phase"] = (
            "BACKFILLING" if value.get("paidBackfillAuthorized") is True else "PLANNING"
        )
    return value


def _acquire_lease(owner: str) -> bool:
    now_epoch = int(time.time())
    try:
        _table().put_item(
            Item={
                "PK": STATE_PK,
                "SK": LEASE_SK,
                "record_type": "mlb_historical_optimizer_lease_v1",
                "owner": owner,
                "expiresEpoch": now_epoch + LEASE_SECONDS,
                "createdAtUtc": _now_iso(),
            },
            ConditionExpression="attribute_not_exists(PK) OR expiresEpoch < :now",
            ExpressionAttributeValues={":now": now_epoch},
        )
        return True
    except Exception as exc:
        code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
        if code == "ConditionalCheckFailedException":
            return False
        raise


def _release_lease(owner: str) -> None:
    try:
        _table().delete_item(
            Key={"PK": STATE_PK, "SK": LEASE_SK},
            ConditionExpression="#owner = :owner",
            ExpressionAttributeNames={"#owner": "owner"},
            ExpressionAttributeValues={":owner": owner},
        )
    except Exception:
        pass


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _get_s3_json(key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    response = _S3.get_object(Bucket=ARTIFACTS_BUCKET, Key=key)
    value = json.loads(response["Body"].read().decode("utf-8"))
    return value, {
        "bucket": ARTIFACTS_BUCKET,
        "key": key,
        "versionId": response.get("VersionId") or "unversioned",
        "etag": str(response.get("ETag") or "").strip('"'),
        "sha256": str((response.get("Metadata") or {}).get("sha256") or ""),
    }


def _s3_exists(key: str) -> Optional[Dict[str, Any]]:
    try:
        response = _S3.head_object(Bucket=ARTIFACTS_BUCKET, Key=key)
        return {
            "bucket": ARTIFACTS_BUCKET,
            "key": key,
            "versionId": response.get("VersionId") or "unversioned",
            "etag": str(response.get("ETag") or "").strip('"'),
            "sha256": str((response.get("Metadata") or {}).get("sha256") or ""),
        }
    except Exception as exc:
        code = str(((getattr(exc, "response", {}) or {}).get("Error") or {}).get("Code") or "")
        status = int(((getattr(exc, "response", {}) or {}).get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        if code in {"404", "NoSuchKey", "NotFound"} or status == 404:
            return None
        raise


def _put_immutable_json(key: str, value: Any, *, record_type: str) -> Dict[str, Any]:
    body = _json_bytes(value)
    checksum = _sha256(body)
    existing = _s3_exists(key)
    if existing:
        if existing.get("sha256") and existing["sha256"] != checksum:
            raise OrchestrationError(f"immutable S3 artifact collision at {key}")
        if not existing.get("sha256"):
            current, pointer = _get_s3_json(key)
            if _sha256(_json_bytes(current)) != checksum:
                raise OrchestrationError(f"immutable S3 artifact content changed at {key}")
            pointer["sha256"] = checksum
            return pointer
        return existing
    response = _S3.put_object(
        Bucket=ARTIFACTS_BUCKET,
        Key=key,
        Body=body,
        ContentType="application/json",
        Metadata={"sha256": checksum, "record-type": record_type},
        ServerSideEncryption="AES256",
    )
    return {
        "bucket": ARTIFACTS_BUCKET,
        "key": key,
        "versionId": response.get("VersionId") or "unversioned",
        "etag": str(response.get("ETag") or "").strip('"'),
        "sha256": checksum,
    }


def _safe_headers(headers: Mapping[str, Any]) -> Dict[str, Any]:
    out = {}
    for key in ("x-requests-remaining", "x-requests-used", "x-requests-last"):
        value = headers.get(key) or headers.get(key.title())
        if value is None:
            continue
        try:
            out[key] = int(value)
        except Exception:
            out[key] = str(value)
    return out


def _http_json(url: str, *, timeout: int = HTTP_TIMEOUT_SECONDS) -> Tuple[Any, Dict[str, Any]]:
    request = urllib.request.Request(
        url,
        headers={
            "accept": "application/json",
            "user-agent": "inqsi-mlb-historical-daily-optimizer/1.0",
        },
    )
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload, _safe_headers(response.headers)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 and not 500 <= exc.code <= 599:
                body = ""
                try:
                    body = exc.read().decode("utf-8")[:300]
                except Exception:
                    pass
                raise OrchestrationError(f"upstream HTTP {exc.code}: {body}") from exc
            if attempt == 5:
                raise OrchestrationError(f"upstream retryable HTTP {exc.code} exhausted") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == 5:
                raise OrchestrationError("upstream network retries exhausted") from exc
        time.sleep(min(20, 2**attempt))
    raise OrchestrationError("unreachable HTTP retry state")


def _quota_status() -> Dict[str, Any]:
    query = urllib.parse.urlencode({"apiKey": ODDS_API_KEY})
    _, headers = _http_json(f"{ODDS_API_BASE}/sports?{query}")
    return headers


def _historical_url(requested_at_utc: str) -> str:
    query = urllib.parse.urlencode(
        {
            "apiKey": ODDS_API_KEY,
            "regions": "us",
            "markets": "h2h",
            "oddsFormat": "american",
            "dateFormat": "iso",
            "date": requested_at_utc,
        }
    )
    return f"{ODDS_API_BASE}/historical/sports/{optimizer.SPORT_KEY}/odds?{query}"


def _fetch_historical(requested_at_utc: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload, headers = _http_json(_historical_url(requested_at_utc))
    if not isinstance(payload, Mapping):
        raise OrchestrationError("historical response is not a JSON object")
    # Validate time direction and basic schema before spending another credit.
    optimizer.normalize_historical_snapshot(payload, requested_at_utc)
    return dict(payload), headers


def _date_key(day: str) -> str:
    return f"mlb/historical-daily-v1/official-finals/{day}.json"


def _raw_key(day: str, requested_at_utc: str) -> str:
    stamp = requested_at_utc.replace(":", "").replace("-", "").replace("+", "").replace(".", "")
    return f"mlb/historical-daily-v1/raw/{day}/{stamp}.json"


def _slate_key(day: str) -> str:
    return f"mlb/historical-daily-v1/datasets/{day}.json"


def _load_or_fetch_finals(day: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    key = _date_key(day)
    if _s3_exists(key):
        return _get_s3_json(key)
    payload = final_labels.fetch_official_schedule(day, timeout=HTTP_TIMEOUT_SECONDS)
    if payload.get("officialFinalCount") != payload.get("officialGameCount"):
        raise OrchestrationError(f"official slate {day} is not fully final")
    pointer = _put_immutable_json(key, payload, record_type="mlb_official_finals")
    return payload, pointer


def _initial_state() -> Dict[str, Any]:
    _require_configuration()
    return {
        "version": VERSION,
        "phase": "PLANNING",
        "startDate": START_DATE,
        "endDate": END_DATE,
        "currentDate": START_DATE,
        "currentSlotIndex": 0,
        "targetSettledGames": TARGET_GAMES,
        "minimumTrainingGames": policy_runtime.MIN_TRAINING_GAMES,
        "minimumWalkForwardGames": policy_runtime.MIN_WALK_FORWARD_GAMES,
        "minimumUntouchedAuditGames": policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        "minimumTotalEvidenceGames": policy_runtime.MIN_TOTAL_SETTLED_GAMES,
        "eligibleGameCount": 0,
        "completeSlateCount": 0,
        "completedSlates": [],
        "rejectedSlates": [],
        "networkRequestCount": 0,
        "creditsConsumed": 0,
        "maximumCredits": MAX_CREDITS,
        "quotaReserve": QUOTA_RESERVE,
        "snapshotStartAtEt": optimizer.PULL_START_ET,
        "snapshotIntervalMinutes": optimizer.PULL_INTERVAL_MINUTES,
        "perGameLockMinutesBeforeCommence": optimizer.FULL_SLATE_LOCK_MINUTES,
        "snapshotGridEndsAt": "last_game_t_minus_45",
        "dailyAccuracyRequirement": policy_runtime.MIN_DAILY_ACCURACY,
        "dailyAccuracyTargetHigh": policy_runtime.TARGET_DAILY_ACCURACY_HIGH,
        "paidBackfillAuthorized": False,
        "authorizationContract": AUTHORIZATION_CONFIRMATION,
        "optimizationRound": 0,
        "freshAuditExpansionRequired": False,
        "freshAuditStartDate": None,
        "evaluatedAuditWindows": [],
        "plan": None,
        "lastQuota": {},
        "lastError": None,
        "initializedAtUtc": _now_iso(),
        "revision": 0,
    }


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    material = {
        key: plan.get(key)
        for key in (
            "startDate",
            "endDate",
            "plannedThroughDate",
            "targetSettledGames",
            "plannedOfficialGames",
            "plannedCompleteSlateDays",
            "historicalRequestCount",
            "estimatedCredits",
            "maximumCredits",
            "snapshotStartAtEt",
            "snapshotIntervalMinutes",
            "snapshotGridEndsAt",
            "perGameFeatureCutoff",
            "maximumAuthorizedOfficialGames",
            "maximumOptimizationRounds",
            "freshAuditIncrementGames",
            "slateLedgerDigest",
            "completeDateRangeLedger",
            "planningErrorCount",
        )
    }
    return hashlib.sha256(_json_bytes(material)).hexdigest()


def _plan(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build the full request/credit plan without historical Odds API calls."""

    state = _migrate_state(state)
    if (
        state.get("paidBackfillAuthorized") is True
        or int(state.get("networkRequestCount") or 0) > 0
        or bool(state.get("completedSlates"))
    ):
        raise OrchestrationError(
            "cannot replace a fingerprinted plan after paid historical collection started"
        )
    day = date.fromisoformat(str(state.get("startDate") or START_DATE))
    end = date.fromisoformat(str(state.get("endDate") or END_DATE))
    target = int(state.get("targetSettledGames") or TARGET_GAMES)
    planned_games = 0
    request_count = 0
    slates: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    # The fingerprinted authorization covers the complete configured date
    # range, not merely the first optimization target.  This lets rejected
    # candidates collect strictly later audit slates without making any paid
    # request outside the originally reviewed credit ledger.
    while day <= end:
        day_text = day.isoformat()
        try:
            finals, _ = _load_or_fetch_finals(day_text)
            official_count = int(finals.get("officialGameCount") or 0)
            if official_count:
                starts = [optimizer._parse_dt(row.get("gameDate")) for row in finals.get("games") or []]
                starts = [value for value in starts if value is not None]
                if len(starts) != official_count:
                    raise OrchestrationError("official_start_time_missing")
                grid = optimizer.build_snapshot_grid(day_text, starts)
                daily_requests = len(grid.timestamps_utc)
                request_count += daily_requests
                planned_games += official_count
                slates.append(
                    {
                        "slateDateEt": day_text,
                        "officialGameCount": official_count,
                        "historicalRequestCount": daily_requests,
                        "estimatedCredits": daily_requests
                        * ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST,
                        "firstGameStartUtc": grid.first_game_start_utc,
                        "lastGameStartUtc": grid.last_game_start_utc,
                        "firstRequestUtc": grid.timestamps_utc[0],
                        "lastRequestUtc": grid.timestamps_utc[-1],
                    }
                )
        except Exception as exc:
            rejected.append(
                {
                    "slateDateEt": day_text,
                    "reason": "planning_official_schedule_rejected",
                    "details": f"{type(exc).__name__}:{str(exc)[:200]}",
                }
            )
        day += timedelta(days=1)
    estimated_credits = request_count * ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST
    slate_ledger_digest = _sha256(_json_bytes(slates))
    quota = _quota_status()
    remaining = quota.get("x-requests-remaining")
    enough_range = planned_games >= target
    within_cap = estimated_credits <= int(state.get("maximumCredits") or MAX_CREDITS)
    quota_sufficient = not isinstance(remaining, int) or remaining >= estimated_credits + QUOTA_RESERVE
    plan = {
        "version": "MLB-HISTORICAL-CREDIT-PLAN-v1.1-complete-date-range-ledger",
        "createdAtUtc": _now_iso(),
        "startDate": str(state.get("startDate") or START_DATE),
        "endDate": str(state.get("endDate") or END_DATE),
        "plannedThroughDate": slates[-1]["slateDateEt"] if slates else None,
        "targetSettledGames": target,
        "maximumAuthorizedOfficialGames": planned_games,
        "plannedOfficialGames": planned_games,
        "plannedCompleteSlateDays": len(slates),
        "historicalRequestCount": request_count,
        "estimatedCreditsPerRequest": ESTIMATED_CREDITS_PER_HISTORICAL_REQUEST,
        "estimatedCredits": estimated_credits,
        "maximumCredits": int(state.get("maximumCredits") or MAX_CREDITS),
        "quotaReserve": QUOTA_RESERVE,
        "providerReportedRemainingCredits": remaining,
        "enoughDateRange": enough_range,
        "withinConfiguredCreditCap": within_cap,
        "providerQuotaAppearsSufficient": quota_sufficient,
        "snapshotStartAtEt": optimizer.PULL_START_ET,
        "snapshotIntervalMinutes": optimizer.PULL_INTERVAL_MINUTES,
        "snapshotGridEndsAt": "last_game_t_minus_45",
        "perGameFeatureCutoff": "each_game_t_minus_45",
        "maximumOptimizationRounds": MAX_OPTIMIZATION_ROUNDS,
        "freshAuditIncrementGames": FRESH_AUDIT_INCREMENT_GAMES,
        "slateLedgerDigest": slate_ledger_digest,
        "partitions": {
            "minimumTrainingGames": policy_runtime.MIN_TRAINING_GAMES,
            "minimumWalkForwardGames": policy_runtime.MIN_WALK_FORWARD_GAMES,
            "minimumUntouchedAuditGames": policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        },
        "slates": slates,
        "rejectedDates": rejected,
        "planningErrorCount": len(rejected),
        "completeDateRangeLedger": not rejected,
        "paidHistoricalCallsMadeByPlan": 0,
    }
    plan["fingerprint"] = _plan_fingerprint(plan)
    state["plan"] = plan
    state["lastQuota"] = quota
    state["paidBackfillAuthorized"] = False
    state["currentDate"] = str(state.get("startDate") or START_DATE)
    state["currentSlotIndex"] = 0
    if rejected:
        state["phase"] = "PLAN_BLOCKED_INCOMPLETE_LEDGER"
        state["lastError"] = (
            "one or more configured dates could not be proven from the official schedule; "
            "paid authorization is blocked until the complete date-range ledger is rebuilt"
        )
    elif not enough_range:
        state["phase"] = "PLAN_BLOCKED_DATE_RANGE"
        state["lastError"] = "configured date range cannot reach the evidence target"
    elif not within_cap:
        state["phase"] = "PLAN_BLOCKED_CREDIT_CAP"
        state["lastError"] = "estimated historical credits exceed the configured hard cap"
    elif not quota_sufficient:
        state["phase"] = "PLAN_BLOCKED_PROVIDER_QUOTA"
        state["lastError"] = "provider-reported remaining credits are below the planned requirement"
    else:
        state["phase"] = "READY_FOR_AUTHORIZATION"
        state["lastError"] = None
    return state


def _authorize_backfill(state: Dict[str, Any], payload: Mapping[str, Any]) -> Dict[str, Any]:
    plan = state.get("plan") or {}
    if state.get("phase") != "READY_FOR_AUTHORIZATION" or not isinstance(plan, Mapping):
        raise OrchestrationError("a passing credit plan is required before authorization")
    if str(payload.get("confirm") or "") != AUTHORIZATION_CONFIRMATION:
        raise OrchestrationError("historical credit authorization confirmation is invalid")
    if str(payload.get("planFingerprint") or "") != str(plan.get("fingerprint") or ""):
        raise OrchestrationError("historical credit plan fingerprint mismatch")
    if str(plan.get("fingerprint") or "") != _plan_fingerprint(plan):
        raise OrchestrationError("historical credit plan content does not match its fingerprint")
    if str(plan.get("slateLedgerDigest") or "") != _sha256(
        _json_bytes(plan.get("slates") or [])
    ):
        raise OrchestrationError("historical credit slate ledger digest mismatch")
    if plan.get("completeDateRangeLedger") is not True or int(plan.get("planningErrorCount") or 0) != 0:
        raise OrchestrationError("historical credit plan does not prove every configured date")
    if plan.get("rejectedDates"):
        raise OrchestrationError("historical credit plan contains unresolved schedule dates")
    if plan.get("withinConfiguredCreditCap") is not True or plan.get("providerQuotaAppearsSufficient") is not True:
        raise OrchestrationError("historical credit plan no longer authorizes paid requests")
    state["paidBackfillAuthorized"] = True
    state["authorizedPlanFingerprint"] = plan.get("fingerprint")
    state["paidBackfillAuthorizedAtUtc"] = _now_iso()
    state["phase"] = "BACKFILLING"
    state["lastError"] = None
    return state


def _increment_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _advance_after_rejection(state: Dict[str, Any], day: str, reason: str, details: Any = None) -> None:
    state.setdefault("rejectedSlates", []).append(
        {"slateDateEt": day, "reason": reason, "details": details, "recordedAtUtc": _now_iso()}
    )
    state["currentDate"] = _increment_day(day)
    state["currentSlotIndex"] = 0


def _quota_allows_call(state: Mapping[str, Any], quota: Mapping[str, Any]) -> bool:
    consumed = int(state.get("creditsConsumed") or 0)
    if consumed + 10 > int(state.get("maximumCredits") or MAX_CREDITS):
        return False
    remaining = quota.get("x-requests-remaining")
    if isinstance(remaining, int) and remaining < QUOTA_RESERVE + 10:
        return False
    return True


def _authorized_plan_slate(
    state: Mapping[str, Any], day: str, grid: optimizer.SnapshotGrid
) -> Mapping[str, Any]:
    """Bind every paid request to the exact fingerprinted planning ledger."""

    plan = state.get("plan") or {}
    if state.get("paidBackfillAuthorized") is not True:
        raise OrchestrationError("paid historical backfill has not been authorized")
    if str(state.get("authorizedPlanFingerprint") or "") != str(plan.get("fingerprint") or ""):
        raise OrchestrationError("authorized historical credit-plan fingerprint changed")
    if str(plan.get("fingerprint") or "") != _plan_fingerprint(plan):
        raise OrchestrationError("authorized historical credit-plan content changed")
    if str(plan.get("slateLedgerDigest") or "") != _sha256(
        _json_bytes(plan.get("slates") or [])
    ):
        raise OrchestrationError("authorized historical slate ledger changed")
    if plan.get("completeDateRangeLedger") is not True or int(plan.get("planningErrorCount") or 0) != 0:
        raise OrchestrationError("authorized plan lost complete date-range schedule proof")
    if plan.get("rejectedDates"):
        raise OrchestrationError("authorized plan contains unresolved schedule dates")
    planned = next(
        (
            row
            for row in plan.get("slates") or []
            if isinstance(row, Mapping) and str(row.get("slateDateEt") or "") == day
        ),
        None,
    )
    if not isinstance(planned, Mapping):
        raise OrchestrationError(f"slate {day} is outside the authorized paid-request ledger")
    actual = list(grid.timestamps_utc)
    if (
        int(planned.get("historicalRequestCount") or -1) != len(actual)
        or str(planned.get("firstRequestUtc") or "") != str(actual[0] if actual else "")
        or str(planned.get("lastRequestUtc") or "") != str(actual[-1] if actual else "")
    ):
        raise OrchestrationError(f"authorized historical request grid changed for {day}")
    return planned


def _completed_slate_summary(state: Mapping[str, Any]) -> Dict[str, int]:
    return {
        str(row.get("slateDateEt") or ""): int(row.get("eligibleGameCount") or 0)
        for row in state.get("completedSlates") or []
        if isinstance(row, Mapping) and row.get("slateDateEt")
    }


def _fresh_audit_window(state: Mapping[str, Any]) -> Tuple[List[str], int]:
    start = str(state.get("freshAuditStartDate") or "")
    if not start:
        return [], 0
    counts = _completed_slate_summary(state)
    dates = sorted(day for day in counts if day >= start)
    return dates, sum(counts[day] for day in dates)


def _record_evaluated_audit_window(
    state: Dict[str, Any], result: Mapping[str, Any], artifact: Mapping[str, Any]
) -> None:
    definition = result.get("holdoutDefinition") or {}
    dates = [str(value) for value in definition.get("dates") or [] if str(value)]
    if not dates:
        return
    prior_dates = {
        str(value)
        for window in state.get("evaluatedAuditWindows") or []
        if isinstance(window, Mapping)
        for value in window.get("dates") or []
    }
    overlap = sorted(prior_dates & set(dates))
    if overlap:
        raise OrchestrationError(
            "untouched audit dates were reused after label evaluation: " + ",".join(overlap)
        )
    gate = result.get("promotionGate") or {}
    state.setdefault("evaluatedAuditWindows", []).append(
        {
            "optimizationRound": int(state.get("optimizationRound") or 0),
            "dates": dates,
            "firstDate": min(dates),
            "lastDate": max(dates),
            "gameCount": int(gate.get("untouchedHoldoutGameCount") or 0),
            "minimumDailyAccuracy": gate.get("untouchedHoldoutMinimumDailyAccuracy"),
            "meanDailyAccuracy": gate.get("untouchedHoldoutMeanDailyAccuracy"),
            "passed": gate.get("passed") is True,
            "evaluatedAtUtc": _now_iso(),
            "experimentArtifact": copy.deepcopy(dict(artifact)),
        }
    )


def _complete_slate(
    state: Dict[str, Any], day: str, finals: Mapping[str, Any], grid: optimizer.SnapshotGrid
) -> None:
    historical = []
    for requested in grid.timestamps_utc:
        raw, _ = _get_s3_json(_raw_key(day, requested))
        historical.append(
            {
                "requestedAtUtc": requested,
                "payload": raw.get("payload") if isinstance(raw, Mapping) and "payload" in raw else raw,
            }
        )
    dataset = optimizer.build_slate_dataset(
        day,
        finals.get("games") or [],
        historical,
        grid,
    )
    pointer = _put_immutable_json(_slate_key(day), dataset, record_type="mlb_historical_complete_slate")
    if dataset.get("completeSlate") is not True or float(dataset.get("exactSlateCoverage") or 0.0) < 1.0:
        _advance_after_rejection(
            state,
            day,
            "incomplete_full_slate_dataset",
            {
                "officialGameCount": dataset.get("officialGameCount"),
                "eligibleGameCount": dataset.get("eligibleGameCount"),
                "exactSlateCoverage": dataset.get("exactSlateCoverage"),
                "artifact": pointer,
            },
        )
        return
    state.setdefault("completedSlates", []).append(
        {
            "slateDateEt": day,
            "officialGameCount": dataset["officialGameCount"],
            "eligibleGameCount": dataset["eligibleGameCount"],
            "fingerprint": dataset["fingerprint"],
            "artifact": pointer,
        }
    )
    state["eligibleGameCount"] = int(state.get("eligibleGameCount") or 0) + int(
        dataset["eligibleGameCount"]
    )
    state["completeSlateCount"] = int(state.get("completeSlateCount") or 0) + 1
    state["currentDate"] = _increment_day(day)
    state["currentSlotIndex"] = 0
    if int(state["eligibleGameCount"]) >= int(state.get("targetSettledGames") or TARGET_GAMES):
        state["phase"] = "OPTIMIZING"
        state["dataCollectionCompletedAtUtc"] = _now_iso()


def _backfill(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("phase") not in {"BACKFILLING", "PAUSED_QUOTA"}:
        return state
    if state.get("paidBackfillAuthorized") is not True:
        state["phase"] = "READY_FOR_AUTHORIZATION"
        state["lastError"] = "paid historical backfill has not been explicitly authorized"
        return state
    state["phase"] = "BACKFILLING"
    quota = _quota_status()
    state["lastQuota"] = quota
    if not _quota_allows_call(state, quota):
        state["phase"] = "PAUSED_QUOTA"
        state["lastError"] = "historical quota/cost guard stopped paid requests"
        return state

    network_used = 0
    while network_used < MAX_NETWORK_REQUESTS and state.get("phase") == "BACKFILLING":
        day = str(state.get("currentDate") or START_DATE)
        if date.fromisoformat(day) > date.fromisoformat(str(state.get("endDate") or END_DATE)):
            state["phase"] = "DATA_RANGE_EXHAUSTED"
            state["lastError"] = "configured historical range ended before the 1,000-train plus validation/audit evidence floor"
            break
        try:
            finals, finals_pointer = _load_or_fetch_finals(day)
        except Exception as exc:
            _advance_after_rejection(state, day, "official_final_fetch_or_validation_failed", type(exc).__name__)
            continue
        official_count = int(finals.get("officialGameCount") or 0)
        if official_count == 0:
            _advance_after_rejection(state, day, "official_off_day")
            continue
        games = finals.get("games") or []
        starts = [optimizer._parse_dt(row.get("gameDate")) for row in games]
        starts = [value for value in starts if value is not None]
        if len(starts) != official_count:
            _advance_after_rejection(state, day, "official_start_time_missing")
            continue
        grid = optimizer.build_snapshot_grid(day, starts)
        _authorized_plan_slate(state, day, grid)
        slot_index = int(state.get("currentSlotIndex") or 0)
        while slot_index < len(grid.timestamps_utc) and network_used < MAX_NETWORK_REQUESTS:
            requested = grid.timestamps_utc[slot_index]
            key = _raw_key(day, requested)
            if not _s3_exists(key):
                if not _quota_allows_call(state, state.get("lastQuota") or {}):
                    state["phase"] = "PAUSED_QUOTA"
                    state["lastError"] = "historical quota/cost guard stopped paid requests"
                    break
                payload, headers = _fetch_historical(requested)
                artifact = {
                    "version": VERSION,
                    "recordType": "mlb_historical_odds_snapshot_v1",
                    "slateDateEt": day,
                    "requestedAtUtc": requested,
                    "fetchedAtUtc": _now_iso(),
                    "provider": "The Odds API",
                    "providerEndpoint": "/v4/historical/sports/baseball_mlb/odds",
                    "regions": "us",
                    "markets": "h2h",
                    "payload": payload,
                    "quota": headers,
                }
                _put_immutable_json(key, artifact, record_type="mlb_historical_odds_snapshot")
                network_used += 1
                state["networkRequestCount"] = int(state.get("networkRequestCount") or 0) + 1
                last_cost = int(headers.get("x-requests-last") or 10)
                state["creditsConsumed"] = int(state.get("creditsConsumed") or 0) + max(0, last_cost)
                state["lastQuota"] = headers
            slot_index += 1
            state["currentSlotIndex"] = slot_index
        if state.get("phase") == "PAUSED_QUOTA":
            break
        if slot_index >= len(grid.timestamps_utc):
            _complete_slate(state, day, finals, grid)
            state["lastCompletedFinalsArtifact"] = finals_pointer
    return state


def _load_training_records(state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for slate in state.get("completedSlates") or []:
        artifact = slate.get("artifact") or {}
        key = artifact.get("key")
        if not key:
            raise OrchestrationError("completed slate artifact pointer is missing")
        dataset, pointer = _get_s3_json(str(key))
        if pointer.get("sha256") and artifact.get("sha256") and pointer["sha256"] != artifact["sha256"]:
            raise OrchestrationError("completed slate artifact checksum changed")
        if dataset.get("completeSlate") is not True or dataset.get("postLockDataExcluded") is not True:
            raise OrchestrationError("completed slate lost its integrity proof")
        records.extend(copy.deepcopy(dataset.get("records") or []))
    return records


def _existing_champion() -> Optional[Dict[str, Any]]:
    item = _get_item(policy_runtime.CHAMPION_SK, pk=policy_runtime.CHAMPION_PK)
    if not item:
        return None
    validation = policy_runtime.validate_champion(item)
    if not validation.ok:
        raise OrchestrationError(
            "existing historical champion is invalid: "
            + ",".join(validation.errors)
        )
    return copy.deepcopy(item.get("data") or item)


def _existing_cutover() -> Optional[Dict[str, Any]]:
    item = _get_item(policy_runtime.CUTOVER_SK, pk=policy_runtime.CUTOVER_PK)
    if not item:
        return None
    validation = policy_runtime.validate_cutover(item)
    if not validation.ok:
        raise OrchestrationError(
            "existing production cutover is invalid: "
            + ",".join(validation.errors)
        )
    return copy.deepcopy(item.get("data") or item)

def _candidate_is_non_regressive(candidate: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
    """Reject a replacement that weakens any load-bearing evidence dimension."""

    new_gate = candidate.get("promotionGate") or {}
    old_gate = existing.get("promotionGate") or {}
    numeric_non_regression = (
        "settledGameCount",
        "trainingGameCount",
        "walkForwardGameCount",
        "untouchedHoldoutGameCount",
        "walkForwardDayCount",
        "untouchedHoldoutDayCount",
        "walkForwardMinimumDailyAccuracy",
        "walkForwardMeanDailyAccuracy",
        "untouchedHoldoutMinimumDailyAccuracy",
        "untouchedHoldoutMeanDailyAccuracy",
        "walkForwardSlateCoverage",
        "untouchedHoldoutSlateCoverage",
    )
    for name in numeric_non_regression:
        if float(new_gate.get(name) or 0.0) + 1e-12 < float(old_gate.get(name) or 0.0):
            return False
    required_truths = (
        "passed",
        "holdoutWasUntouchedDuringSearch",
        "chronologicalWholeSlateSplits",
        "postLockDataExcluded",
        "gameSpecificLockClipping",
        "overfitChecksPassed",
    )
    return all(new_gate.get(name) is True for name in required_truths)



def _serialize_transaction_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    return {
        str(key): serializer.serialize(value)
        for key, value in _ddb_safe(item).items()
    }


def _write_champion(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Activate the champion and historical-only cutover safely.

    The first successful promotion creates both records in one DynamoDB
    transaction. Later non-regressive historical champions may replace the
    champion pointer, but the write-once cutover remains and V15.10 never
    becomes an automatic production fallback again.
    """

    validation = policy_runtime.validate_champion(
        {"record_type": policy_runtime.CHAMPION_RECORD_TYPE, "data": payload}
    )
    if not validation.ok:
        raise OrchestrationError("runtime rejected champion: " + ",".join(validation.errors))
    existing = _existing_champion()
    if existing and not _candidate_is_non_regressive(payload, existing):
        raise OrchestrationError("candidate would regress the active historical champion")

    champion_item = {
        "PK": policy_runtime.CHAMPION_PK,
        "SK": policy_runtime.CHAMPION_SK,
        "record_type": policy_runtime.CHAMPION_RECORD_TYPE,
        "artifactDigest": payload.get("policyDigest"),
        "activated_at": payload.get("activatedAtUtc"),
        "data": payload,
    }
    cutover = policy_runtime.build_cutover_payload(payload)
    cutover_validation = policy_runtime.validate_cutover(
        {"record_type": policy_runtime.CUTOVER_RECORD_TYPE, "data": cutover}
    )
    if not cutover_validation.ok:
        raise OrchestrationError(
            "runtime rejected production cutover: "
            + ",".join(cutover_validation.errors)
        )
    cutover_item = {
        "PK": policy_runtime.CUTOVER_PK,
        "SK": policy_runtime.CUTOVER_SK,
        "record_type": policy_runtime.CUTOVER_RECORD_TYPE,
        "historicalOnly": True,
        "legacyFallbackAllowed": False,
        "activated_at": cutover.get("activatedAtUtc"),
        "data": cutover,
    }

    existing_cutover = _existing_cutover()
    client = getattr(getattr(_DDB, "meta", None), "client", None)

    if existing_cutover:
        # The production authority has already crossed the write-once boundary.
        # Replace only the champion, with an optimistic condition that prevents
        # races from overwriting a different reviewed champion. A missing
        # champion after cutover may be repaired, but it never restores V15.10.
        condition = "attribute_not_exists(PK)"
        values = None
        if existing:
            condition = "artifactDigest = :expectedArtifactDigest"
            values = {
                ":expectedArtifactDigest": existing.get("policyDigest"),
            }
        kwargs: Dict[str, Any] = {
            "Item": _ddb_safe(champion_item),
            "ConditionExpression": condition,
        }
        if values is not None:
            kwargs["ExpressionAttributeValues"] = _ddb_safe(values)
        _table().put_item(**kwargs)
        return copy.deepcopy(existing_cutover)

    # First activation must be all-or-nothing. A local two-Put approximation is
    # deliberately prohibited because it could leave an ambiguous authority
    # state during a crash or retry.
    if not TARGET_TABLE:
        raise OrchestrationError("TARGET_SNAPSHOTS_TABLE is not configured")
    if client is None or not callable(getattr(client, "transact_write_items", None)):
        raise OrchestrationError(
            "atomic DynamoDB transaction client is unavailable; production cutover blocked"
        )

    champion_put: Dict[str, Any] = {
        "TableName": TARGET_TABLE,
        "Item": _serialize_transaction_item(champion_item),
        "ConditionExpression": "attribute_not_exists(PK)",
    }
    client.transact_write_items(
        ClientRequestToken="mlb-historical-cutover-" + str(payload.get("policyDigest") or "")[:24],
        TransactItems=[
            {"Put": champion_put},
            {
                "Put": {
                    "TableName": TARGET_TABLE,
                    "Item": _serialize_transaction_item(cutover_item),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            },
        ],
    )
    return copy.deepcopy(cutover)

def _optimize(state: Dict[str, Any]) -> Dict[str, Any]:
    if state.get("phase") != "OPTIMIZING":
        return state
    records = _load_training_records(state)
    round_number = int(state.get("optimizationRound") or 0)
    fresh_dates: Optional[List[str]] = None
    if state.get("freshAuditExpansionRequired") is True:
        fresh_dates, fresh_game_count = _fresh_audit_window(state)
        state["freshAuditCollectedDayCount"] = len(fresh_dates)
        state["freshAuditCollectedGameCount"] = fresh_game_count
        if (
            len(fresh_dates) < policy_runtime.MIN_UNTOUCHED_HOLDOUT_DAYS
            or fresh_game_count < policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES
        ):
            state["targetSettledGames"] = max(
                int(state.get("targetSettledGames") or TARGET_GAMES)
                + FRESH_AUDIT_INCREMENT_GAMES,
                int(state.get("eligibleGameCount") or 0)
                + max(
                    FRESH_AUDIT_INCREMENT_GAMES,
                    policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES - fresh_game_count,
                ),
            )
            state["phase"] = "BACKFILLING"
            state["lastError"] = (
                "strictly later untouched audit window is still accumulating; "
                f"have {len(fresh_dates)} days/{fresh_game_count} games"
            )
            return state
    search_config = optimizer.SearchConfig(
        minimum_training_games=policy_runtime.MIN_TRAINING_GAMES,
        minimum_walk_forward_games=policy_runtime.MIN_WALK_FORWARD_GAMES,
        minimum_untouched_holdout_games=policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
        minimum_settled_games=policy_runtime.MIN_TOTAL_SETTLED_GAMES,
        maximum_candidates=MAX_CANDIDATES,
        random_seed=1541 + round_number * 7919,
    )
    result = optimizer.search(
        records,
        search_config,
        untouched_holdout_dates=fresh_dates,
    )
    experiment_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
    key = f"mlb/historical-daily-v1/experiments/{experiment_id}.json"
    # First write the immutable search result, then bind its versioned pointer
    # into the candidate/champion contract.
    pointer = _put_immutable_json(key, result, record_type="mlb_historical_optimizer_experiment")
    experiment_record = {
        "PK": STATE_PK,
        "SK": f"{EXPERIMENT_SK_PREFIX}{experiment_id}",
        "record_type": "mlb_historical_optimizer_experiment_v1",
        "created_at": _now_iso(),
        "data": {
            "experimentId": experiment_id,
            "artifact": pointer,
            "status": result.get("status"),
            "promotionGate": result.get("promotionGate"),
            "candidatePolicyDigest": (result.get("candidate") or {}).get("policyDigest"),
        },
    }
    _table().put_item(Item=_ddb_safe(experiment_record))
    state["latestExperiment"] = experiment_record["data"]
    state["optimizationCompletedAtUtc"] = _now_iso()
    if result.get("ok") is True:
        _record_evaluated_audit_window(state, result, pointer)
    if result.get("ok") is not True:
        state["lastError"] = str(result.get("partitionReason") or result.get("status") or "optimizer did not return a candidate")
        if str(result.get("status") or "") == "ACCUMULATING_HISTORICAL_GAMES":
            state["targetSettledGames"] = max(
                int(state.get("targetSettledGames") or TARGET_GAMES) + FRESH_AUDIT_INCREMENT_GAMES,
                int(state.get("eligibleGameCount") or 0) + FRESH_AUDIT_INCREMENT_GAMES,
            )
            state["phase"] = "BACKFILLING"
        else:
            state["phase"] = str(result.get("status") or "OPTIMIZATION_BLOCKED")
        return state
    if (result.get("promotionGate") or {}).get("passed") is not True:
        next_round = round_number + 1
        state["optimizationRound"] = next_round
        state["lastError"] = (
            "80% every-day walk-forward/untouched-audit gate was not achieved; "
            "the candidate is rejected without authority"
        )
        if next_round < MAX_OPTIMIZATION_ROUNDS and date.fromisoformat(str(state.get("currentDate") or END_DATE)) <= date.fromisoformat(str(state.get("endDate") or END_DATE)):
            # Add genuinely later completed slates so the next round receives a
            # fresh untouched audit instead of tuning against the failed audit.
            state["targetSettledGames"] = max(
                int(state.get("targetSettledGames") or TARGET_GAMES) + FRESH_AUDIT_INCREMENT_GAMES,
                int(state.get("eligibleGameCount") or 0) + FRESH_AUDIT_INCREMENT_GAMES,
            )
            state["phase"] = "BACKFILLING"
            state["freshAuditExpansionRequired"] = True
            state["freshAuditStartDate"] = str(state.get("currentDate") or "")
            state["freshAuditCollectedDayCount"] = 0
            state["freshAuditCollectedGameCount"] = 0
        else:
            state["phase"] = "CANDIDATE_REJECTED"
        return state
    champion = optimizer.champion_payload(result, pointer, _now_iso())
    cutover = _write_champion(champion)
    state["phase"] = "PROMOTED"
    state["freshAuditExpansionRequired"] = False
    state["champion"] = {
        "policyDigest": champion["policyDigest"],
        "artifact": champion["artifact"],
        "promotionGate": champion["promotionGate"],
        "activatedAtUtc": champion["activatedAtUtc"],
    }
    state["productionCutover"] = cutover
    state["automaticLegacyFallbackAllowed"] = False
    state["incumbentProductionAuthorityDestroyed"] = True
    state["lastError"] = None
    return state


def _status() -> Dict[str, Any]:
    state = _load_state()
    champion_item = _get_item(
        policy_runtime.CHAMPION_SK, pk=policy_runtime.CHAMPION_PK
    )
    validation = policy_runtime.validate_champion(champion_item) if champion_item else None
    cutover_item = _get_item(
        policy_runtime.CUTOVER_SK, pk=policy_runtime.CUTOVER_PK
    )
    cutover_validation = policy_runtime.validate_cutover(cutover_item) if cutover_item else None
    return {
        "ok": True,
        "version": VERSION,
        "state": state,
        "champion": (champion_item or {}).get("data") if champion_item else None,
        "championValidation": {
            "ok": validation.ok,
            "errors": list(validation.errors),
        }
        if validation
        else {"ok": False, "errors": ["no_active_champion"]},
        "productionCutover": (cutover_item or {}).get("data") if cutover_item else None,
        "cutoverValidation": {
            "ok": cutover_validation.ok,
            "errors": list(cutover_validation.errors),
        }
        if cutover_validation
        else {"ok": False, "errors": ["not_cut_over_before_first_promotion"]},
        "productionCutoverValidation": (
            {"ok": cutover_validation.ok, "errors": list(cutover_validation.errors)}
            if cutover_validation
            else {"ok": False, "errors": ["not_cut_over_before_first_promotion"]}
        ),
        "historicalOnlyCutover": {
            "active": bool(cutover_validation and cutover_validation.ok),
            "historicalOnly": bool(
                cutover_validation
                and cutover_validation.ok
                and (cutover_validation.cutover or {}).get("historicalOnly") is True
            ),
            "legacyFallbackAllowed": (
                (cutover_validation.cutover or {}).get("legacyFallbackAllowed")
                if cutover_validation and cutover_validation.ok
                else None
            ),
            "productionAuthorityMode": (
                (cutover_validation.cutover or {}).get("productionAuthorityMode")
                if cutover_validation and cutover_validation.ok
                else None
            ),
        },
        "productionAuthority": {
            "historicalChampionOnly": bool(
                validation and validation.ok and cutover_validation and cutover_validation.ok
            ),
            "incumbentProductionAuthorityDestroyed": bool(
                cutover_validation
                and cutover_validation.ok
                and (cutover_validation.cutover or {}).get(
                    "incumbentProductionAuthorityDestroyed"
                ) is True
            ),
            "automaticLegacyFallbackAllowed": False,
        },
        "automaticLegacyFallbackAllowed": False,
        "objective": {
            "minimumTrainingGames": policy_runtime.MIN_TRAINING_GAMES,
            "minimumWalkForwardGames": policy_runtime.MIN_WALK_FORWARD_GAMES,
            "minimumUntouchedAuditGames": policy_runtime.MIN_UNTOUCHED_AUDIT_GAMES,
            "minimumTotalEvidenceGames": policy_runtime.MIN_TOTAL_SETTLED_GAMES,
            "configuredCollectionTargetGames": TARGET_GAMES,
            "pullStartAtEt": "01:00",
            "pullIntervalMinutes": 15,
            "snapshotGridEndsAt": "last_game_t_minus_45",
            "perGameFeatureCutoff": "each_game_t_minus_45",
            "minimumEveryDayAccuracy": 0.80,
            "targetHighAccuracy": 0.90,
            "metricScope": "all_official_games_on_each_complete_slate_day",
        },
    }


def _response(status: int, body: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "access-control-allow-origin": "*"},
        "body": json.dumps(_plain(body), default=str),
    }


def _payload(event: Any) -> Dict[str, Any]:
    if not isinstance(event, Mapping):
        return {}
    value = dict(event)
    body = value.get("body")
    if body:
        try:
            parsed = json.loads(body) if isinstance(body, str) else body
            if isinstance(parsed, Mapping):
                value.update(parsed)
        except Exception:
            pass
    query = value.get("queryStringParameters") or {}
    if isinstance(query, Mapping):
        value.update(query)
    return value


def lambda_handler(event, context):
    payload = _payload(event)
    method = str(payload.get("httpMethod") or (payload.get("requestContext") or {}).get("http", {}).get("method") or "").upper()
    if method == "OPTIONS":
        return _response(200, {"ok": True})
    mode = str(payload.get("mode") or ("status" if method == "GET" else "orchestrate")).lower()
    if mode == "status":
        result = _status()
        return _response(200, result) if method else result

    owner = f"{getattr(context, 'aws_request_id', None) or uuid.uuid4().hex}:{mode}"
    if not _acquire_lease(owner):
        result = {"ok": True, "version": VERSION, "status": "LEASE_HELD", "state": _load_state()}
        return _response(200, result) if method else result
    try:
        state = _load_state()
        if state is None:
            state = _save_state(_initial_state())
        else:
            state = _migrate_state(state)
        if mode == "initialize":
            state = _save_state(state)
            result = {"ok": True, "version": VERSION, "status": state.get("phase"), "state": state}
        elif mode == "plan":
            state = _save_state(_plan(state))
            result = {"ok": True, "version": VERSION, "status": state.get("phase"), "state": state, "plan": state.get("plan")}
        elif mode == "authorize":
            state = _save_state(_authorize_backfill(state, payload))
            result = {"ok": True, "version": VERSION, "status": state.get("phase"), "state": state}
        elif mode in {"orchestrate", "backfill"}:
            if state.get("phase") in {"BACKFILLING", "PAUSED_QUOTA"}:
                state = _backfill(state)
            if state.get("phase") == "OPTIMIZING" and mode == "orchestrate":
                state = _optimize(state)
            state = _save_state(state)
            result = {"ok": True, "version": VERSION, "status": state.get("phase"), "state": state}
        elif mode == "optimize":
            if int(state.get("eligibleGameCount") or 0) < policy_runtime.MIN_TOTAL_SETTLED_GAMES:
                raise OrchestrationError("cannot optimize before the 1,000-train plus validation/audit evidence floor")
            state["phase"] = "OPTIMIZING"
            state = _save_state(_optimize(state))
            result = {"ok": True, "version": VERSION, "status": state.get("phase"), "state": state}
        else:
            raise OrchestrationError("unsupported mode")
        return _response(200, result) if method else result
    except Exception as exc:
        state = _load_state() or _initial_state()
        state["lastError"] = f"{type(exc).__name__}:{str(exc)[:500]}"
        state["lastErrorAtUtc"] = _now_iso()
        # Preserve the resumable phase for transient HTTP/S3 errors; hard schema
        # and quota failures are surfaced in lastError and retried idempotently.
        state = _save_state(state)
        result = {"ok": False, "version": VERSION, "status": state.get("phase"), "error": state["lastError"], "state": state}
        return _response(500, result) if method else result
    finally:
        _release_lease(owner)
