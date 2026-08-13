"""AWS persistence boundary for the isolated soccer_auto stack."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from boto3.dynamodb.types import TypeSerializer

from .canonical import (
    SnapshotAttempt,
    attempt_rank,
    canonical_json,
    digest,
    floor_slot,
    iso_utc,
    parse_utc,
    scope_hash,
    stable_event_key,
)
from .config import PUBLISHED_SCORE_SUPPORT


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 12)))
    if isinstance(value, dict):
        return {str(key): ddb_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [ddb_safe(item) for item in value]
    return value


def plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [plain(item) for item in value]
    return value


def _is_conditional_failure(exc: ClientError) -> bool:
    return (exc.response.get("Error") or {}).get("Code") == "ConditionalCheckFailedException"


class SoccerStore:
    """Owns only resources whose names are passed through SOCCER_AUTO_* env vars."""

    def __init__(self, *, dynamodb: Any = None, s3: Any = None, sqs: Any = None) -> None:
        resource = dynamodb or boto3.resource("dynamodb")
        self.registry = resource.Table(os.environ["SOCCER_AUTO_REGISTRY_TABLE"])
        self.events = resource.Table(os.environ["SOCCER_AUTO_EVENTS_TABLE"])
        self.slots = resource.Table(os.environ["SOCCER_AUTO_SNAPSHOT_SLOTS_TABLE"])
        self.locks = resource.Table(os.environ["SOCCER_AUTO_LOCKS_TABLE"])
        self.settlements = resource.Table(os.environ["SOCCER_AUTO_SETTLEMENTS_TABLE"])
        self.predictions = resource.Table(os.environ["SOCCER_AUTO_PREDICTIONS_TABLE"])
        self.models = resource.Table(os.environ["SOCCER_AUTO_MODELS_TABLE"])
        self.ops = resource.Table(os.environ["SOCCER_AUTO_OPS_TABLE"])
        self.s3 = s3 or boto3.client("s3")
        self.sqs = sqs or boto3.client("sqs")
        self.raw_bucket = os.environ["SOCCER_AUTO_RAW_BUCKET"]
        self.artifact_bucket = os.environ["SOCCER_AUTO_ARTIFACT_BUCKET"]
        self.collection_queue_url = os.environ.get("SOCCER_AUTO_COLLECTION_QUEUE_URL", "")

    def archive_json(
        self,
        category: str,
        payload: Any,
        *,
        observed_at: str | datetime,
        identity: str,
        metadata: Mapping[str, str] | None = None,
    ) -> tuple[str, str]:
        observed = parse_utc(observed_at)
        body = canonical_json(payload).encode("utf-8")
        payload_hash = digest(payload)
        safe_identity = "".join(char if char.isalnum() or char in "-_." else "_" for char in identity)
        key = (
            f"raw/{category}/{observed:%Y/%m/%d/%H}/"
            f"{safe_identity}/{observed:%Y%m%dT%H%M%S.%fZ}-{payload_hash}.json"
        )
        self.s3.put_object(
            Bucket=self.raw_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            Metadata={str(k): str(v)[:1024] for k, v in (metadata or {}).items()},
            ServerSideEncryption="AES256",
        )
        return f"s3://{self.raw_bucket}/{key}", payload_hash

    def read_json(self, uri: str) -> Any:
        prefix = "s3://"
        if not uri.startswith(prefix):
            raise ValueError("only s3:// artifact URIs are supported")
        bucket, key = uri[len(prefix) :].split("/", 1)
        return json.loads(self.s3.get_object(Bucket=bucket, Key=key)["Body"].read())

    def put_competition(self, row: Mapping[str, Any], observed_at: str) -> None:
        key = str(row["key"])
        current = self.registry.get_item(Key={"PK": "COMPETITION", "SK": key}, ConsistentRead=True).get("Item")
        item = {
            "PK": "COMPETITION",
            "SK": key,
            "entity_type": "SOCCER_COMPETITION",
            "sport_key": key,
            "title": row.get("title") or key,
            "description": row.get("description"),
            "active": bool(row.get("active")),
            "has_outrights": bool(row.get("has_outrights")),
            "scores_supported": PUBLISHED_SCORE_SUPPORT.get(key, True),
            "first_seen_at": (current or {}).get("first_seen_at") or observed_at,
            "last_seen_at": observed_at,
            "source": "the_odds_api_sports_all",
        }
        self.registry.put_item(Item=ddb_safe(item))

    def list_competitions(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        response = self.registry.query(KeyConditionExpression=Key("PK").eq("COMPETITION"))
        rows = [plain(row) for row in response.get("Items", [])]
        return [row for row in rows if row.get("active")] if active_only else rows

    def put_event(self, event: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
        sport_key = str(event["sport_key"])
        event_id = str(event["id"])
        commence_time = iso_utc(str(event["commence_time"]))
        event_key = stable_event_key(sport_key, event_id)
        current = self.events.get_item(Key={"PK": event_key, "SK": "METADATA"}, ConsistentRead=True).get("Item")
        schedule_revision = int((current or {}).get("schedule_revision") or 1)
        if current and str(current.get("commence_time")) != commence_time:
            schedule_revision += 1
        item = {
            "PK": event_key,
            "SK": "METADATA",
            "entity_type": "SOCCER_EVENT",
            "event_key": event_key,
            "event_id": event_id,
            "sport_key": sport_key,
            "sport_title": event.get("sport_title"),
            "commence_time": commence_time,
            "schedule_revision": schedule_revision,
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
            "first_seen_at": (current or {}).get("first_seen_at") or observed_at,
            "last_seen_at": observed_at,
            "last_dispatched_at": (
                (current or {}).get("last_dispatched_at")
                if not current or str(current.get("commence_time")) == commence_time
                else None
            ),
            "completed": bool((current or {}).get("completed", False)),
            "GSI1PK": "COMPLETED" if bool((current or {}).get("completed", False)) else "ACTIVE",
            "GSI1SK": commence_time,
        }
        self.events.put_item(Item=ddb_safe(item))
        return plain(item)

    def record_collection_window_call(
        self,
        event: Mapping[str, Any],
        window: Mapping[str, Any],
        observed_at: str,
    ) -> None:
        """Persist the first actual gated market/odds call for one match-day."""
        opens_at = parse_utc(str(window["opens_at"]))
        observed = parse_utc(observed_at)
        if observed < opens_at:
            raise ValueError("cannot record a provider call before the collection window")
        key = {"PK": "COLLECTION_WINDOW", "SK": str(window["match_day"])}
        current = self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        first_call = str(current.get("actual_first_provider_call_at") or observed_at)
        first_call_dt = parse_utc(first_call)
        signed_drift_ms = int((first_call_dt - opens_at).total_seconds() * 1000)
        drift_ms = max(0, signed_drift_ms)
        sla_state = (
            "WINDOW_MOVED_LATER_AFTER_FIRST_CALL"
            if signed_drift_ms < 0
            else "OPENED_WITHIN_TWO_MINUTE_DRIFT"
            if drift_ms <= 120000
            else "LATE_DISCOVERY_OR_SCHEDULER_DRIFT"
        )
        item = {
            **key,
            "entity_type": "SOCCER_DAILY_COLLECTION_WINDOW",
            "timezone": window.get("timezone"),
            "match_day": window.get("match_day"),
            "first_kickoff": window.get("first_kickoff"),
            "scheduled_open_at": window.get("opens_at"),
            "initial_scheduled_open_at": window.get("opens_at"),
            "event_count": int(window.get("event_count") or 0),
            "actual_first_provider_call_at": first_call,
            "first_event_key": current.get("first_event_key") or event.get("event_key"),
            "drift_ms": drift_ms,
            "sla_state": sla_state,
            "last_provider_call_at": observed_at,
        }
        if current:
            self.ops.update_item(
                Key=key,
                UpdateExpression=(
                    "SET last_provider_call_at=:last, event_count=:count, "
                    "first_kickoff=:kickoff, scheduled_open_at=:opens, "
                    "drift_ms=:drift, sla_state=:sla, window_revised_after_first_call=:revised"
                ),
                ExpressionAttributeValues={
                    ":last": observed_at,
                    ":count": int(window.get("event_count") or 0),
                    ":kickoff": window.get("first_kickoff"),
                    ":opens": window.get("opens_at"),
                    ":drift": drift_ms,
                    ":sla": sla_state,
                    ":revised": str(current.get("scheduled_open_at") or "") != str(window.get("opens_at") or ""),
                },
            )
            return
        try:
            self.ops.put_item(
                Item=ddb_safe(item),
                ConditionExpression="attribute_not_exists(actual_first_provider_call_at)",
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
            self.ops.update_item(
                Key=key,
                UpdateExpression="SET last_provider_call_at=:last",
                ExpressionAttributeValues={":last": observed_at},
            )

    def get_collection_window(self, match_day: str) -> dict[str, Any] | None:
        row = self.ops.get_item(
            Key={"PK": "COLLECTION_WINDOW", "SK": str(match_day)},
            ConsistentRead=True,
        ).get("Item")
        return plain(row) if row else None

    def get_event(self, event_key: str) -> dict[str, Any] | None:
        row = self.events.get_item(Key={"PK": event_key, "SK": "METADATA"}, ConsistentRead=True).get("Item")
        return plain(row) if row else None

    def active_events_between(self, start: str, end: str) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "IndexName": "ByStateAndCommence",
            "KeyConditionExpression": Key("GSI1PK").eq("ACTIVE") & Key("GSI1SK").between(start, end),
        }
        rows: list[dict[str, Any]] = []
        while True:
            response = self.events.query(**kwargs)
            rows.extend(plain(row) for row in response.get("Items", []))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return rows

    def mark_dispatched(self, event_key: str, observed_at: str) -> None:
        self.events.update_item(
            Key={"PK": event_key, "SK": "METADATA"},
            UpdateExpression="SET last_dispatched_at=:value",
            ExpressionAttributeValues={":value": observed_at},
        )

    def mark_completed(self, event_key: str, observed_at: str) -> None:
        self.events.update_item(
            Key={"PK": event_key, "SK": "METADATA"},
            UpdateExpression="SET completed=:yes, GSI1PK=:state, completed_seen_at=:seen",
            ExpressionAttributeValues={":yes": True, ":state": "COMPLETED", ":seen": observed_at},
        )

    def claim_job(self, job_key: str, expires_at_epoch: int) -> bool:
        try:
            self.ops.put_item(
                Item={
                    "PK": "JOB_CLAIM",
                    "SK": job_key,
                    "entity_type": "SOCCER_AUTO_JOB_CLAIM",
                    "expires_at": int(expires_at_epoch),
                    "created_at": iso_utc(now_utc()),
                },
                ConditionExpression="attribute_not_exists(SK)",
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def release_job(self, job_key: str) -> None:
        self.ops.delete_item(Key={"PK": "JOB_CLAIM", "SK": job_key})

    def enqueue(self, payload: Mapping[str, Any]) -> str:
        if not self.collection_queue_url:
            raise RuntimeError("SOCCER_AUTO_COLLECTION_QUEUE_URL is not configured")
        response = self.sqs.send_message(
            QueueUrl=self.collection_queue_url,
            MessageBody=canonical_json(payload),
        )
        return str(response.get("MessageId") or "")

    def record_quota(self, response: Any, *, operation: str, observed_at: str) -> None:
        item = {
            "PK": "QUOTA",
            "SK": f"OBSERVED#{observed_at}#{digest({'operation': operation, 'url': response.request_url})[:12]}",
            "entity_type": "SOCCER_AUTO_QUOTA_OBSERVATION",
            "operation": operation,
            "remaining": response.quota_remaining,
            "used": response.quota_used,
            "last_cost": response.quota_last,
            "observed_at": observed_at,
            "expires_at": int((parse_utc(observed_at) + timedelta(days=90)).timestamp()),
        }
        self.ops.put_item(Item=ddb_safe(item))
        if response.quota_remaining is None or response.quota_used is None:
            return
        latest_key = {"PK": "QUOTA_STATE", "SK": "LATEST"}
        latest = {
            **latest_key,
            "entity_type": "SOCCER_AUTO_QUOTA_STATE",
            "operation": operation,
            "remaining": int(response.quota_remaining),
            "used": int(response.quota_used),
            "last_cost": response.quota_last,
            "observed_at": observed_at,
            "quota_snapshot": digest(
                {
                    "observed_at": observed_at,
                    "remaining": response.quota_remaining,
                    "used": response.quota_used,
                    "operation": operation,
                }
            ),
        }
        # Concurrent functions can return out of order. Advance the latest
        # pointer only with a compare-and-swap so an older response can never
        # replace a newer provider quota observation.
        for _ in range(5):
            current = self.ops.get_item(Key=latest_key, ConsistentRead=True).get("Item") or {}
            current_at = str(current.get("observed_at") or "")
            if current_at and current_at >= observed_at:
                break
            condition = "attribute_not_exists(observed_at)" if not current_at else "observed_at=:expected"
            kwargs: dict[str, Any] = {
                "Item": ddb_safe(latest),
                "ConditionExpression": condition,
            }
            if current_at:
                kwargs["ExpressionAttributeValues"] = {":expected": current_at}
            try:
                self.ops.put_item(**kwargs)
                break
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise

    def rate_limit_status(self) -> dict[str, Any] | None:
        row = self.ops.get_item(
            Key={"PK": "RATE_LIMIT", "SK": "ODDS_API_REQUESTS"},
            ConsistentRead=True,
        ).get("Item")
        return plain(row) if row else None

    def provider_429_status(
        self,
        *,
        observed_at: str | datetime | None = None,
        lookback_hours: int = 24,
        row_limit: int = 20,
        count_limit: int = 1000,
    ) -> dict[str, Any]:
        """Return bounded rolling provider-throttle evidence for health APIs."""
        observed = parse_utc(observed_at or now_utc())
        window_hours = max(1, int(lookback_hours))
        cap = max(1, int(count_limit))
        cutoff = observed - timedelta(hours=window_hours)
        response = self.ops.query(
            KeyConditionExpression=(
                Key("PK").eq("PROVIDER_429")
                & Key("SK").between(
                    f"OBSERVED#{iso_utc(cutoff)}",
                    f"OBSERVED#{iso_utc(observed)}\uffff",
                )
            ),
            ScanIndexForward=False,
            ConsistentRead=True,
            Limit=cap,
        )
        rows = [plain(row) for row in response.get("Items") or []]
        return {
            "lookback_hours": window_hours,
            "rolling_count": len(rows),
            "count_is_lower_bound": bool(response.get("LastEvaluatedKey")),
            "count_cap": cap,
            "latest_rows": rows[: max(1, int(row_limit))],
        }

    def provider_budget_available(
        self, operation: str, observed_at: str, estimated_cost: int = 1
    ) -> bool:
        """Atomically admit soccer spend inside its shared-subscription slice.

        Every soccer caller reserves its maximum estimated credit cost against
        one provider response snapshot. This closes the local check-then-call
        race across concurrent Lambdas. The separate race buffer protects
        against already-admitted calls that were still in flight when a newer
        response snapshot arrived. Other sports remain outside this soccer
        ledger, so the buffer is a bounded shared-key margin rather than an
        ownership claim on their activity.
        """
        latest = plain(
            self.ops.get_item(
                Key={"PK": "QUOTA_STATE", "SK": "LATEST"},
                ConsistentRead=True,
            ).get("Item")
            or {}
        )
        remaining = latest.get("remaining")
        used = latest.get("used")
        reserve_percent = max(
            0.0,
            min(100.0, float(os.getenv("SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT", "0"))),
        )
        configured_race_buffer = max(
            0,
            int(os.getenv("SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS", "2000")),
        )
        quota_known = remaining is not None and used is not None
        total = int(remaining) + int(used) if quota_known else 0
        reserve_credits = total * reserve_percent / 100.0 if quota_known else None
        # Use at most half of soccer's nominal allowance as the in-flight
        # buffer, so even small subscriptions can make a low-cost observation.
        soccer_allowance = max(0.0, total - float(reserve_credits or 0.0))
        race_buffer = (
            min(configured_race_buffer, int(soccer_allowance * 0.5))
            if quota_known
            else configured_race_buffer
        )
        cost = max(0, int(estimated_cost))
        spendable = max(0, int(float(remaining or 0) - float(reserve_credits or 0) - race_buffer))
        quota_snapshot = str(latest.get("quota_snapshot") or "")
        available = False
        reason = "QUOTA_OBSERVATION_UNAVAILABLE"
        if quota_known and total > 0 and quota_snapshot and cost <= spendable:
            admission_key = {"PK": "QUOTA_ADMISSION", "SK": "CURRENT"}
            for _ in range(8):
                admission = plain(
                    self.ops.get_item(Key=admission_key, ConsistentRead=True).get("Item")
                    or {}
                )
                admission_snapshot = str(admission.get("quota_snapshot") or "")
                admitted = int(admission.get("admitted_credits") or 0)
                if admission_snapshot != quota_snapshot:
                    if str(admission.get("quota_observed_at") or "") > str(latest.get("observed_at") or ""):
                        reason = "NEWER_QUOTA_SNAPSHOT_IN_FLIGHT"
                        break
                    condition = (
                        "attribute_not_exists(quota_snapshot)"
                        if not admission_snapshot
                        else "quota_snapshot=:previous"
                    )
                    kwargs = {
                        "Item": ddb_safe(
                            {
                                **admission_key,
                                "entity_type": "SOCCER_ATOMIC_QUOTA_ADMISSION",
                                "quota_snapshot": quota_snapshot,
                                "quota_observed_at": latest.get("observed_at"),
                                "remaining_at_snapshot": int(remaining),
                                "reserve_credits": reserve_credits,
                                "race_buffer_credits": race_buffer,
                                "spendable_credits": spendable,
                                "admitted_credits": cost,
                                "updated_at": observed_at,
                            }
                        ),
                        "ConditionExpression": condition,
                    }
                    if admission_snapshot:
                        kwargs["ExpressionAttributeValues"] = {":previous": admission_snapshot}
                    try:
                        self.ops.put_item(**kwargs)
                        available = True
                        break
                    except ClientError as exc:
                        if not _is_conditional_failure(exc):
                            raise
                        continue
                if admitted + cost > spendable:
                    reason = "ATOMIC_SOCCER_ALLOWANCE_EXHAUSTED"
                    break
                try:
                    self.ops.update_item(
                        Key=admission_key,
                        UpdateExpression="SET admitted_credits=:next, updated_at=:at",
                        ConditionExpression="quota_snapshot=:snapshot AND admitted_credits=:current",
                        ExpressionAttributeValues={
                            ":next": admitted + cost,
                            ":at": observed_at,
                            ":snapshot": quota_snapshot,
                            ":current": admitted,
                        },
                    )
                    available = True
                    break
                except ClientError as exc:
                    if not _is_conditional_failure(exc):
                        raise
            if not available and reason == "QUOTA_OBSERVATION_UNAVAILABLE":
                reason = "ATOMIC_ADMISSION_CONTENTION"
        elif quota_known:
            reason = (
                "RACE_BUFFER_REACHED"
                if reserve_percent == 0.0
                else "SHARED_SUBSCRIPTION_RESERVE_REACHED"
            )
        if not available:
            self.ops.put_item(
                Item=ddb_safe(
                    {
                        "PK": "QUOTA_GUARD",
                        "SK": f"BLOCKED#{observed_at}#{operation}",
                        "entity_type": "SOCCER_SHARED_PROVIDER_QUOTA_GUARD",
                        "operation": operation,
                        "remaining": remaining,
                        "used": used,
                        "reserve_percent": reserve_percent,
                        "reserve_credits": reserve_credits,
                        "race_buffer_credits": race_buffer,
                        "spendable_credits": spendable,
                        "estimated_cost": cost,
                        "reason": reason,
                        "observed_at": observed_at,
                        "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
                    }
                )
            )
        return available

    def put_market_inventory(self, event_key: str, payload: Mapping[str, Any], observed_at: str) -> None:
        for bookmaker, detail in payload.items():
            inventory_digest = digest(detail)
            observation = {
                "PK": event_key,
                "SK": f"MARKET_INVENTORY#{bookmaker}#{observed_at}#{inventory_digest[:16]}",
                "entity_type": "SOCCER_MARKET_INVENTORY",
                "bookmaker": bookmaker,
                "observed_at": observed_at,
                "inventory": {bookmaker: detail},
                "inventory_digest": inventory_digest,
                "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
            }
            self.ops.put_item(Item=ddb_safe(observation))

    def put_outright_manifest(
        self,
        *,
        sport_key: str,
        observed_at: str,
        raw_uri: str,
        payload_hash: str,
        event_count: int,
    ) -> None:
        """Index a tournament snapshot without admitting it as a match event."""
        self.ops.put_item(
            Item=ddb_safe(
                {
                    "PK": f"OUTRIGHT#{sport_key}",
                    "SK": f"SNAPSHOT#{observed_at}#{payload_hash}",
                    "entity_type": "SOCCER_OUTRIGHT_RAW_MANIFEST",
                    "sport_key": sport_key,
                    "observed_at": observed_at,
                    "raw_uri": raw_uri,
                    "payload_sha256": payload_hash,
                    "event_count": int(event_count),
                    "training_eligible": False,
                    "schedule_planner_eligible": False,
                    "expires_at": int((parse_utc(observed_at) + timedelta(days=365)).timestamp()),
                }
            )
        )

    def record_collection_failure(
        self,
        *,
        event_key: str,
        operation: str,
        observed_at: str,
        detail: str,
        scope: Mapping[str, Any],
        permanent: bool,
    ) -> None:
        self.ops.put_item(
            Item=ddb_safe(
                {
                    "PK": f"COLLECTION_FAILURE#{event_key}",
                    "SK": f"{observed_at}#{digest({'operation': operation, 'scope': scope})[:16]}",
                    "entity_type": "SOCCER_COLLECTION_FAILURE",
                    "event_key": event_key,
                    "operation": operation,
                    "detail": detail[:2000],
                    "scope": dict(scope),
                    "permanent": permanent,
                    "coverage_complete": False,
                    "observed_at": observed_at,
                    "expires_at": int((parse_utc(observed_at) + timedelta(days=90)).timestamp()),
                }
            )
        )

    def cumulative_market_inventory(
        self,
        event_key: str,
        *,
        observed_at: str | None = None,
        maximum_age_hours: int = 24,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": (
                Key("PK").eq(event_key) & Key("SK").begins_with("MARKET_INVENTORY#")
            ),
            "ConsistentRead": True,
        }
        cutoff = (
            parse_utc(observed_at) - timedelta(hours=max(1, maximum_age_hours))
            if observed_at
            else None
        )
        result: dict[str, Any] = {}
        while True:
            response = self.ops.query(**kwargs)
            for row in response.get("Items") or []:
                row = plain(row)
                if row.get("entity_type") != "SOCCER_MARKET_INVENTORY":
                    continue
                if cutoff and row.get("observed_at") and parse_utc(str(row["observed_at"])) < cutoff:
                    continue
                for bookmaker, detail in (row.get("inventory") or {}).items():
                    existing = result.setdefault(
                        str(bookmaker),
                        {
                            "title": detail.get("title") or bookmaker,
                            "regions": [],
                            "markets": [],
                        },
                    )
                    existing["title"] = detail.get("title") or existing.get("title") or bookmaker
                    existing["regions"] = sorted(
                        set(existing.get("regions") or ()) | set(detail.get("regions") or ())
                    )
                    existing["markets"] = sorted(
                        set(existing.get("markets") or ()) | set(detail.get("markets") or ())
                    )
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return result

    def put_coverage_plan(self, event_key: str, inventory: Mapping[str, Any], observed_at: str) -> None:
        expected_pairs = sorted(
            f"{bookmaker}|{market}"
            for bookmaker, detail in inventory.items()
            for market in detail.get("markets") or []
        )
        self.ops.put_item(
            Item=ddb_safe(
                {
                    "PK": f"COVERAGE#{event_key}",
                    "SK": f"PLAN#{observed_at}",
                    "entity_type": "SOCCER_EVENT_COVERAGE_PLAN",
                    "event_key": event_key,
                    "observed_at": observed_at,
                    "expected_pairs": expected_pairs,
                    "expected_pair_count": len(expected_pairs),
                    "expected_digest": digest(expected_pairs),
                    "coverage_complete": False,
                    "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
                }
            )
        )

    def put_coverage_fetch(
        self,
        event_key: str,
        payload: Mapping[str, Any],
        *,
        observed_at: str,
        requested_bookmakers: Sequence[str],
        requested_markets: Sequence[str],
        plan_observed_at: str | None = None,
    ) -> None:
        returned_pairs = sorted(
            f"{book.get('key')}|{market.get('key')}"
            for book in payload.get("bookmakers") or []
            if book.get("key")
            for market in book.get("markets") or []
            if market.get("key")
        )
        expected_request_pairs = sorted(
            f"{bookmaker}|{market}"
            for bookmaker in requested_bookmakers
            for market in requested_markets
        )
        missing = sorted(set(expected_request_pairs) - set(returned_pairs))
        identity = digest(
            {
                "observed_at": observed_at,
                "books": list(requested_bookmakers),
                "markets": list(requested_markets),
                "returned": returned_pairs,
            }
        )
        self.ops.put_item(
            Item=ddb_safe(
                {
                    "PK": f"COVERAGE#{event_key}",
                    "SK": f"FETCH#{observed_at}#{identity[:16]}",
                    "entity_type": "SOCCER_EVENT_COVERAGE_FETCH",
                    "event_key": event_key,
                    "observed_at": observed_at,
                    "plan_observed_at": plan_observed_at,
                    "requested_bookmakers": list(requested_bookmakers),
                    "requested_markets": list(requested_markets),
                    "returned_pairs": returned_pairs,
                    "missing_requested_pairs": missing,
                    "coverage_complete": bool(expected_request_pairs) and not missing,
                    "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
                }
            )
        )

    def put_snapshot_attempt(
        self,
        *,
        event: Mapping[str, Any],
        payload: Mapping[str, Any],
        observed_at: str,
        bookmakers: Sequence[str],
        markets: Sequence[str],
        request_metadata: Mapping[str, Any],
        slot_seconds: int = 60,
        grace_seconds: int = 20,
    ) -> dict[str, Any]:
        event_key = stable_event_key(str(event["sport_key"]), str(event["event_id"]))
        schedule_revision = int(event.get("schedule_revision") or 0)
        if schedule_revision <= 0:
            raise ValueError("a positive schedule_revision is required for a snapshot")
        slot = floor_slot(observed_at, slot_seconds)
        scope = scope_hash(bookmakers=bookmakers, markets=markets)
        raw_uri, payload_hash = self.archive_json(
            "event_odds",
            payload,
            observed_at=observed_at,
            identity=f"{event['sport_key']}-{event['event_id']}-{scope}",
            metadata={
                "event_key": event_key,
                "scope": scope,
                "schedule_revision": str(schedule_revision),
            },
        )
        normalized_books = payload.get("bookmakers") or []
        market_keys = sorted(
            {
                str(market.get("key"))
                for book in normalized_books
                for market in (book.get("markets") or [])
                if market.get("key")
            }
        )
        attempt_id = digest(
            {
                "event_key": event_key,
                "schedule_revision": schedule_revision,
                "observed_at": observed_at,
                "scope": scope,
                "payload": payload_hash,
            }
        )
        candidate = SnapshotAttempt(
            attempt_id=attempt_id,
            observed_at=observed_at,
            commence_time=str(event["commence_time"]),
            raw_uri=raw_uri,
            payload_sha256=payload_hash,
            bookmaker_count=len(normalized_books),
            market_count=len(market_keys),
            valid=(
                parse_utc(observed_at) < parse_utc(str(event["commence_time"]))
                and bool(normalized_books)
                and bool(market_keys)
            ),
        )
        attempt_item = {
            "PK": event_key,
            "SK": (
                f"ATTEMPT#{iso_utc(slot)}#REV#{schedule_revision}#"
                f"SCOPE#{scope}#{attempt_id}"
            ),
            "entity_type": "SOCCER_SNAPSHOT_ATTEMPT",
            **candidate.__dict__,
            "schedule_revision": schedule_revision,
            "slot_start": iso_utc(slot),
            "slot_seconds": slot_seconds,
            "scope_hash": scope,
            "bookmakers_requested": sorted(set(bookmakers)),
            "markets_requested": sorted(set(markets)),
            "market_keys_returned": market_keys,
            "request_metadata": dict(request_metadata),
            "phase": (
                "PREMATCH"
                if candidate.valid
                else "PREMATCH_EMPTY"
                if parse_utc(observed_at) < parse_utc(str(event["commence_time"]))
                else "IN_PLAY_OR_POST_START"
            ),
        }
        try:
            self.slots.put_item(Item=ddb_safe(attempt_item), ConditionExpression="attribute_not_exists(SK)")
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

        pointer_key = {
            "PK": event_key,
            "SK": f"SLOT#{iso_utc(slot)}#REV#{schedule_revision}#SCOPE#{scope}",
        }
        eligible = candidate.valid and candidate.observed <= slot + timedelta(seconds=slot_seconds + grace_seconds)
        promoted = False
        if eligible:
            for _ in range(5):
                current = self.slots.get_item(Key=pointer_key, ConsistentRead=True).get("Item")
                current_plain = plain(current) if current else None
                if current_plain:
                    current_attempt = SnapshotAttempt(
                        **{field: current_plain[field] for field in SnapshotAttempt.__dataclass_fields__}
                    )
                    if attempt_rank(candidate) <= attempt_rank(current_attempt):
                        break
                pointer = {
                    **pointer_key,
                    "entity_type": "SOCCER_CANONICAL_SNAPSHOT_SLOT",
                    **candidate.__dict__,
                    "schedule_revision": schedule_revision,
                    "slot_start": iso_utc(slot),
                    "slot_seconds": slot_seconds,
                    "grace_seconds": grace_seconds,
                    "scope_hash": scope,
                    "bookmakers_requested": sorted(set(bookmakers)),
                    "markets_requested": sorted(set(markets)),
                    "market_keys_returned": market_keys,
                    "canonical": True,
                    "training_eligible": True,
                }
                condition = "attribute_not_exists(SK)" if not current_plain else "payload_sha256=:expected"
                values = None if not current_plain else {":expected": current_plain["payload_sha256"]}
                try:
                    kwargs: dict[str, Any] = {"Item": ddb_safe(pointer), "ConditionExpression": condition}
                    if values:
                        kwargs["ExpressionAttributeValues"] = values
                    self.slots.put_item(**kwargs)
                    promoted = True
                    break
                except ClientError as exc:
                    if not _is_conditional_failure(exc):
                        raise
        return {
            "event_key": event_key,
            "attempt_id": attempt_id,
            "slot_start": iso_utc(slot),
            "scope_hash": scope,
            "raw_uri": raw_uri,
            "canonical_promoted": promoted,
            "training_eligible": eligible,
        }

    def canonical_slots_before(
        self,
        event_key: str,
        cutoff: str,
        *,
        schedule_revision: int | None = None,
    ) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(event_key) & Key("SK").begins_with("SLOT#"),
            "ConsistentRead": True,
        }
        cutoff_dt = parse_utc(cutoff)
        rows = []
        while True:
            response = self.slots.query(**kwargs)
            for item in response.get("Items", []):
                row = plain(item)
                if schedule_revision is not None and int(row.get("schedule_revision") or 0) != int(
                    schedule_revision
                ):
                    continue
                finalized_at = parse_utc(row["slot_start"]) + timedelta(
                    seconds=int(row.get("slot_seconds", 60)) + int(row.get("grace_seconds", 20))
                )
                if finalized_at <= cutoff_dt and parse_utc(row["observed_at"]) <= cutoff_dt:
                    rows.append(row)
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return sorted(rows, key=lambda row: (row["slot_start"], row["scope_hash"]))

    def put_lock(self, item: Mapping[str, Any]) -> bool:
        try:
            self.locks.put_item(Item=ddb_safe(dict(item)), ConditionExpression="attribute_not_exists(SK)")
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def get_lock(
        self,
        event_key: str,
        target: str = "result_1x2",
        *,
        schedule_revision: int | None = None,
    ) -> dict[str, Any] | None:
        lock_key = (
            f"LOCK#T45#REV#{int(schedule_revision)}#TARGET#{target}"
            if schedule_revision is not None
            else f"LOCK#T45#TARGET#{target}"
        )
        row = self.locks.get_item(
            Key={"PK": event_key, "SK": lock_key}, ConsistentRead=True
        ).get("Item")
        return plain(row) if row else None

    def put_settlement(self, item: Mapping[str, Any]) -> bool:
        try:
            self.settlements.put_item(Item=ddb_safe(dict(item)), ConditionExpression="attribute_not_exists(SK)")
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def put_prediction(self, item: Mapping[str, Any]) -> bool:
        try:
            self.predictions.put_item(Item=ddb_safe(dict(item)), ConditionExpression="attribute_not_exists(SK)")
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    @staticmethod
    def scan_all(table: Any, **kwargs: Any) -> Iterable[dict[str, Any]]:
        while True:
            response = table.scan(**kwargs)
            yield from (plain(item) for item in response.get("Items", []))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor

    def write_artifact(self, category: str, payload: Mapping[str, Any], artifact_digest: str) -> str:
        key = f"artifacts/{category}/{artifact_digest}.json"
        self.s3.put_object(
            Bucket=self.artifact_bucket,
            Key=key,
            Body=canonical_json(payload).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )
        return f"s3://{self.artifact_bucket}/{key}"

    def model_items(self, target: str = "result_1x2", scope: str = "global") -> list[dict[str, Any]]:
        response = self.models.query(
            KeyConditionExpression=Key("PK").eq(f"MODEL#{target}#{scope}"),
            ConsistentRead=True,
        )
        return [plain(item) for item in response.get("Items", [])]

    def put_model_version(self, item: Mapping[str, Any]) -> bool:
        try:
            self.models.put_item(Item=ddb_safe(dict(item)), ConditionExpression="attribute_not_exists(SK)")
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def promote_candidate(
        self,
        *,
        candidate: Mapping[str, Any],
        expected_champion_digest: str | None,
        promoted_at: str,
    ) -> None:
        """Atomically move one exact prospective candidate to champion."""
        serializer = TypeSerializer()

        def av(value: Any) -> dict[str, Any]:
            return serializer.serialize(ddb_safe(value))

        table_name = self.models.name
        champion = {
            "PK": candidate["PK"],
            "SK": "CHAMPION",
            "entity_type": "SOCCER_MODEL_CHAMPION_ALIAS",
            "target": candidate["target"],
            "scope": candidate["scope"],
            "model_digest": candidate["model_digest"],
            "artifact_uri": candidate["artifact_uri"],
            "feature_schema_version": candidate["feature_schema_version"],
            "promoted_at": promoted_at,
            "source_version_sk": candidate["SK"],
            "automatic_prediction_allowed": True,
        }
        condition = "attribute_not_exists(model_digest)" if expected_champion_digest is None else "model_digest = :expected"
        values = None if expected_champion_digest is None else {":expected": av(expected_champion_digest)}
        put: dict[str, Any] = {
            "TableName": table_name,
            "Item": {key: av(value) for key, value in champion.items()},
            "ConditionExpression": condition,
        }
        if values:
            put["ExpressionAttributeValues"] = values
        self.models.meta.client.transact_write_items(
            TransactItems=[
                {"Put": put},
                {
                    "Update": {
                        "TableName": table_name,
                        "Key": {"PK": av(candidate["PK"]), "SK": av(candidate["SK"])},
                        "UpdateExpression": "SET authority_state=:state, promoted_at=:at",
                        "ConditionExpression": "model_digest=:digest AND authority_state=:prospective",
                        "ExpressionAttributeValues": {
                            ":state": av("CHAMPION"),
                            ":at": av(promoted_at),
                            ":digest": av(candidate["model_digest"]),
                            ":prospective": av("PROSPECTIVE_SHADOW"),
                        },
                    }
                },
            ]
        )
