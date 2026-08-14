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
    schedule_identity,
    stable_event_key,
)
from .config import ALL_BOOKMAKER_REGIONS, PUBLISHED_SCORE_SUPPORT


COVERAGE_PLAN_VERSION = "soccer-auto-coverage-plan-v3"
COVERAGE_DISPATCH_MANIFEST_VERSION = (
    "soccer-auto-coverage-dispatch-manifest-v2"
)
EVENT_INVENTORY_AUTHORITY_VERSION = "soccer-auto-event-inventory-authority-v1"
EVENT_INVENTORY_AUTHORITY_MAX_AGE_SECONDS = 20 * 60
EVENT_INVENTORY_LEASE_SECONDS = 10 * 60
# DynamoDB rejects an item at 400 KiB.  Keep enough headroom for the wire
# representation and future schema fields, and fail closed before AWS does.
COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES = 350_000
COVERAGE_FETCH_LEASE_SECONDS = 300
COVERAGE_EXTERNAL_QUOTA_REASONS = frozenset(
    {
        "ATOMIC_SOCCER_ALLOWANCE_EXHAUSTED",
        "RACE_BUFFER_REACHED",
        "SHARED_SUBSCRIPTION_RESERVE_REACHED",
    }
)
COVERAGE_MARKETS_PER_REQUEST = int(
    os.getenv("SOCCER_AUTO_MARKETS_PER_REQUEST", "10")
)


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


def ddb_item_size_bytes(value: Mapping[str, Any]) -> int:
    """Return a conservative serialized DynamoDB item-size estimate."""
    serializer = TypeSerializer()
    encoded = {
        str(key): serializer.serialize(ddb_safe(item))
        for key, item in value.items()
    }
    return len(
        json.dumps(encoded, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    )


def coverage_plan_digest(
    *,
    event_key: str,
    observed_at: str,
    schedule_revision: int,
    schedule_identity_value: str,
    request_markets: Sequence[str],
    required_pairs: Sequence[str],
    probe_pairs: Sequence[str],
) -> str:
    """Bind every immutable request-plan dimension in one shared contract."""
    return digest(
        {
            "version": COVERAGE_PLAN_VERSION,
            "event_key": str(event_key),
            "observed_at": str(observed_at),
            "schedule_revision": int(schedule_revision),
            "schedule_identity": str(schedule_identity_value),
            "request_markets": sorted(
                {str(value) for value in request_markets if value}
            ),
            "required_pairs": sorted(
                {str(value) for value in required_pairs if value}
            ),
            "probe_pairs": sorted(
                {str(value) for value in probe_pairs if value}
            ),
        }
    )


def coverage_batch_digest(
    *,
    plan_digest: str,
    markets: Sequence[str],
    bookmakers: Sequence[str] = (),
    regions: Sequence[str] = (),
    planned_pairs: Sequence[str] = (),
    split_group_digest: str = "",
    split_expected_regions: Sequence[str] = (),
    split_leaf_id: str = "",
    split_expected_leaf_ids: Sequence[str] = (),
) -> str:
    return digest(
        {
            "plan_digest": str(plan_digest),
            "markets": list(markets),
            "bookmakers": list(bookmakers),
            "regions": list(regions),
            "planned_pairs": sorted(
                {str(pair) for pair in planned_pairs if pair}
            ),
            "split_group_digest": str(split_group_digest or ""),
            "split_expected_regions": sorted(
                {str(value) for value in split_expected_regions if value}
            ),
            "split_leaf_id": str(split_leaf_id or ""),
            "split_expected_leaf_ids": sorted(
                {str(value) for value in split_expected_leaf_ids if value}
            ),
        }
    )


def coverage_expected_batch_digests(
    *,
    plan_digest: str,
    request_markets: Sequence[str],
    expected_pairs: Sequence[str],
) -> list[str]:
    """Derive every top-level paid request, including zero-pair probes."""
    markets = [str(value) for value in request_markets if value]
    pairs = {str(value) for value in expected_pairs if "|" in str(value)}
    result: list[str] = []
    for offset in range(0, len(markets), COVERAGE_MARKETS_PER_REQUEST):
        batch = markets[offset : offset + COVERAGE_MARKETS_PER_REQUEST]
        batch_scope = {
            pair
            for pair in pairs
            if pair.rsplit("|", 1)[1] in set(batch)
        }
        result.append(
            coverage_batch_digest(
                plan_digest=plan_digest,
                markets=batch,
                bookmakers=(),
                regions=tuple(ALL_BOOKMAKER_REGIONS),
                planned_pairs=sorted(batch_scope),
            )
        )
    return result


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
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq("COMPETITION"),
            "ConsistentRead": True,
        }
        rows: list[dict[str, Any]] = []
        while True:
            response = self.registry.query(**kwargs)
            rows.extend(plain(row) for row in response.get("Items", []))
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return [row for row in rows if row.get("active")] if active_only else rows

    def put_event(self, event: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
        sport_key = str(event["sport_key"])
        event_id = str(event["id"])
        commence_time = iso_utc(str(event["commence_time"]))
        event_key = stable_event_key(sport_key, event_id)
        incoming = {
            "sport_key": sport_key,
            "event_id": event_id,
            "commence_time": commence_time,
            "home_team": event.get("home_team"),
            "away_team": event.get("away_team"),
        }
        incoming_identity = schedule_identity(incoming)
        observed_at = iso_utc(observed_at)
        key = {"PK": event_key, "SK": "METADATA"}
        for _ in range(8):
            current = self.events.get_item(Key=key, ConsistentRead=True).get("Item")
            current_plain = plain(current) if current else {}
            current_seen = str(current_plain.get("last_seen_at") or "")
            # Provider and Lambda responses can arrive out of order. Never let
            # an older observation repaint current schedule authority.
            if current_seen and current_seen >= observed_at:
                return current_plain
            current_identity = str(current_plain.get("schedule_identity") or "")
            if current_plain and not current_identity:
                current_identity = schedule_identity(current_plain)
            identity_changed = bool(current_plain and current_identity != incoming_identity)
            current_revision = int(current_plain.get("schedule_revision") or 0)
            current_metadata_revision = int(
                current_plain.get("metadata_revision") or 0
            )
            schedule_revision = max(1, current_revision + int(identity_changed))
            completed = bool(current_plain.get("completed", False))
            item = {
                **key,
                "entity_type": "SOCCER_EVENT",
                "event_key": event_key,
                "event_id": event_id,
                "sport_key": sport_key,
                "sport_title": event.get("sport_title") or current_plain.get("sport_title"),
                "commence_time": commence_time,
                "schedule_revision": schedule_revision,
                "metadata_revision": current_metadata_revision + 1,
                "schedule_identity": incoming_identity,
                "home_team": event.get("home_team"),
                "away_team": event.get("away_team"),
                "first_seen_at": current_plain.get("first_seen_at") or observed_at,
                "last_seen_at": observed_at,
                "last_inventory_success_at": observed_at,
                "inventory_omission_count": 0,
                "last_dispatched_at": (
                    current_plain.get("last_dispatched_at") if not identity_changed else None
                ),
                "completed": completed,
                "completed_seen_at": current_plain.get("completed_seen_at"),
                "GSI1PK": "COMPLETED" if completed else "ACTIVE",
                "GSI1SK": commence_time,
            }
            kwargs: dict[str, Any] = {
                "Item": ddb_safe(item),
                "ConditionExpression": (
                    "attribute_not_exists(PK)"
                    if not current_plain
                    else (
                        "last_seen_at=:seen AND schedule_revision=:revision AND "
                        "metadata_revision=:metadata_revision"
                        if current_metadata_revision > 0
                        else (
                            "last_seen_at=:seen AND schedule_revision=:revision AND "
                            "attribute_not_exists(metadata_revision)"
                        )
                    )
                ),
            }
            if current_plain:
                kwargs["ExpressionAttributeValues"] = {
                    ":seen": current_seen,
                    ":revision": current_revision,
                }
                if current_metadata_revision > 0:
                    kwargs["ExpressionAttributeValues"][":metadata_revision"] = (
                        current_metadata_revision
                    )
            try:
                self.events.put_item(**kwargs)
                return plain(item)
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("event schedule update contention did not converge")

    def record_event_inventory_omissions(
        self,
        sport_key: str,
        *,
        seen_event_keys: Iterable[str],
        observed_at: str,
    ) -> int:
        """Count omissions only after a successful scoped events response."""
        seen = {str(value) for value in seen_event_keys}
        observed = parse_utc(observed_at)
        candidates = self._indexed_active_events_between(
            iso_utc(observed),
            iso_utc(observed + timedelta(days=45)),
        )
        omitted = 0
        for row in candidates:
            if str(row.get("sport_key") or "") != str(sport_key):
                continue
            event_key = str(row.get("event_key") or "")
            if not event_key or event_key in seen:
                continue
            try:
                self.events.update_item(
                    Key={"PK": event_key, "SK": "METADATA"},
                    UpdateExpression=(
                        "SET last_inventory_success_at=:observed "
                        "ADD inventory_omission_count :one, metadata_revision :one"
                    ),
                    ConditionExpression=(
                        "schedule_revision=:revision AND "
                        "(attribute_not_exists(last_inventory_success_at) OR "
                        "last_inventory_success_at < :observed)"
                    ),
                    ExpressionAttributeValues={
                        ":observed": observed_at,
                        ":one": 1,
                        ":revision": int(row.get("schedule_revision") or 0),
                    },
                )
                omitted += 1
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        return omitted

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

    def _indexed_active_events_between(
        self, start: str, end: str
    ) -> list[dict[str, Any]]:
        """Use the eventually consistent GSI only for advisory bookkeeping."""
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

    def event_inventory_authority(self) -> dict[str, Any]:
        row = self.ops.get_item(
            Key={"PK": "EVENT_INVENTORY_AUTHORITY", "SK": "LATEST"},
            ConsistentRead=True,
        ).get("Item")
        return plain(row) if row else {}

    def begin_event_inventory_generation(
        self,
        *,
        generation_id: str,
        observed_at: str,
        lease_seconds: int = EVENT_INVENTORY_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Acquire the one global lease that fences every schedule mutation."""
        key = {"PK": "EVENT_INVENTORY_AUTHORITY", "SK": "LATEST"}
        observed_epoch = int(parse_utc(observed_at).timestamp())
        for _ in range(8):
            current = plain(
                self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
            )
            if (
                str(current.get("authority_state") or "") == "RUNNING"
                and int(current.get("lease_expires_at") or 0) > observed_epoch
            ):
                return {**current, "acquired": False}
            revision = int(current.get("authority_revision") or 0)
            item = {
                **key,
                "entity_type": "SOCCER_EVENT_INVENTORY_AUTHORITY",
                "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
                "authority_state": "RUNNING",
                "authority_revision": revision + 1,
                "generation_id": str(generation_id),
                "started_at": str(observed_at),
                "completed_at": "",
                "lease_expires_at": observed_epoch + max(60, int(lease_seconds)),
                "last_completed_generation_id": str(
                    current.get("generation_id")
                    if current.get("authority_state") == "COMPLETED"
                    else current.get("last_completed_generation_id") or ""
                ),
                "last_completed_at": str(
                    current.get("completed_at")
                    if current.get("authority_state") == "COMPLETED"
                    else current.get("last_completed_at") or ""
                ),
                "updated_at": str(observed_at),
            }
            kwargs: dict[str, Any] = {
                "Item": ddb_safe(item),
                "ConditionExpression": (
                    "attribute_not_exists(authority_revision)"
                    if "authority_revision" not in current
                    else "authority_revision=:revision"
                ),
            }
            if "authority_revision" in current:
                kwargs["ExpressionAttributeValues"] = ddb_safe(
                    {":revision": revision}
                )
            try:
                self.ops.put_item(**kwargs)
                return {**plain(item), "acquired": True}
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("event inventory authority lease contention")

    def finish_event_inventory_generation(
        self,
        *,
        generation_id: str,
        observed_at: str,
        success: bool,
        competitions_refreshed: int,
        failures: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """Publish a completed generation only after every scoped refresh succeeds."""
        key = {"PK": "EVENT_INVENTORY_AUTHORITY", "SK": "LATEST"}
        for _ in range(8):
            current = plain(
                self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
            )
            if (
                str(current.get("authority_state") or "") != "RUNNING"
                or str(current.get("generation_id") or "") != str(generation_id)
            ):
                return {**current, "updated": False}
            revision = int(current.get("authority_revision") or 0)
            state = "COMPLETED" if success else "FAILED"
            item = {
                **current,
                "authority_state": state,
                "authority_revision": revision + 1,
                "completed_at": str(observed_at),
                "lease_expires_at": 0,
                "competitions_refreshed": int(competitions_refreshed),
                "failure_count": len(failures),
                "failure_sample": [
                    {
                        str(key)[:100]: str(value)[:1000]
                        for key, value in row.items()
                    }
                    for row in failures[:20]
                ],
                "updated_at": str(observed_at),
            }
            if success:
                item["last_completed_generation_id"] = str(generation_id)
                item["last_completed_at"] = str(observed_at)
            try:
                self.ops.put_item(
                    Item=ddb_safe(item),
                    ConditionExpression=(
                        "authority_revision=:revision AND generation_id=:generation "
                        "AND authority_state=:running"
                    ),
                    ExpressionAttributeValues=ddb_safe(
                        {
                            ":revision": revision,
                            ":generation": str(generation_id),
                            ":running": "RUNNING",
                        }
                    ),
                )
                return {**plain(item), "updated": True}
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("event inventory authority completion contention")

    def active_events_between(self, start: str, end: str) -> list[dict[str, Any]]:
        """Return the authoritative active-event universe from the base table.

        DynamoDB GSIs cannot be read strongly consistently. Dispatch and the
        provider-call gate must not checksum an omitted or stale projection as
        authoritative, especially when the correct universe is empty. The
        Events table is deliberately small metadata, so scan its base rows with
        ``ConsistentRead`` and filter the requested window locally.
        """
        start_at = parse_utc(start)
        end_at = parse_utc(end)
        kwargs: dict[str, Any] = {"ConsistentRead": True}
        rows: list[dict[str, Any]] = []
        while True:
            response = self.events.scan(**kwargs)
            for stored in response.get("Items") or []:
                row = plain(stored)
                if (
                    row.get("entity_type") != "SOCCER_EVENT"
                    or str(row.get("SK") or "") != "METADATA"
                    or bool(row.get("completed"))
                    or not row.get("commence_time")
                ):
                    continue
                try:
                    commence = parse_utc(str(row["commence_time"]))
                except (TypeError, ValueError):
                    continue
                if start_at <= commence <= end_at:
                    rows.append(row)
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return sorted(
            rows,
            key=lambda row: (
                str(row.get("commence_time") or ""),
                str(row.get("event_key") or row.get("PK") or ""),
            ),
        )

    def authoritative_active_events_between(
        self,
        start: str,
        end: str,
        *,
        observed_at: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Fence a strong base-table scan to one completed inventory generation."""
        before = self.event_inventory_authority()
        rows = self.active_events_between(start, end)
        after = self.event_inventory_authority()
        same_generation = bool(
            before
            and after
            and str(before.get("generation_id") or "")
            == str(after.get("generation_id") or "")
            and int(before.get("authority_revision") or 0)
            == int(after.get("authority_revision") or 0)
            and str(before.get("completed_at") or "")
            == str(after.get("completed_at") or "")
        )
        completed = bool(
            same_generation
            and str(after.get("authority_version") or "")
            == EVENT_INVENTORY_AUTHORITY_VERSION
            and str(after.get("authority_state") or "") == "COMPLETED"
            and str(after.get("generation_id") or "")
            and str(after.get("completed_at") or "")
        )
        fresh = False
        if completed:
            try:
                age = (
                    parse_utc(observed_at) - parse_utc(str(after["completed_at"]))
                ).total_seconds()
                fresh = -5 <= age <= EVENT_INVENTORY_AUTHORITY_MAX_AGE_SECONDS
            except (KeyError, TypeError, ValueError):
                fresh = False
        valid = completed and fresh
        if not same_generation:
            reason = "INVENTORY_GENERATION_CHANGED_DURING_SCAN"
        elif not completed:
            reason = "INVENTORY_GENERATION_NOT_COMPLETED"
        elif not fresh:
            reason = "INVENTORY_GENERATION_STALE"
        else:
            reason = ""
        return rows, {
            "authority_version": EVENT_INVENTORY_AUTHORITY_VERSION,
            "generation_id": str(after.get("generation_id") or ""),
            "completed_at": str(after.get("completed_at") or ""),
            "authority_revision": int(after.get("authority_revision") or 0),
            "authority_state": str(after.get("authority_state") or "MISSING"),
            "valid": valid,
            "reason": reason,
        }

    def mark_dispatched(
        self,
        event_key: str,
        observed_at: str,
        *,
        schedule_revision: int,
        schedule_identity_value: str,
    ) -> bool:
        try:
            self.events.update_item(
                Key={"PK": event_key, "SK": "METADATA"},
                UpdateExpression=(
                    "SET last_dispatched_at=:value "
                    "ADD metadata_revision :one"
                ),
                ConditionExpression=(
                    "schedule_revision=:revision AND schedule_identity=:identity"
                ),
                ExpressionAttributeValues={
                    ":value": observed_at,
                    ":one": 1,
                    ":revision": int(schedule_revision),
                    ":identity": str(schedule_identity_value),
                },
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def mark_completed(self, event_key: str, observed_at: str) -> None:
        self.events.update_item(
            Key={"PK": event_key, "SK": "METADATA"},
            UpdateExpression=(
                "SET completed=:yes, GSI1PK=:state, completed_seen_at=:seen "
                "ADD metadata_revision :one"
            ),
            ExpressionAttributeValues={
                ":yes": True,
                ":state": "COMPLETED",
                ":seen": observed_at,
                ":one": 1,
            },
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

    def begin_coverage_fetch_execution(
        self,
        *,
        event_key: str,
        plan_digest: str,
        batch_digest: str,
        execution_token: str,
        observed_at: str,
        lease_seconds: int = COVERAGE_FETCH_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Acquire a reclaimable provider-call lease for one exact batch."""
        summary = self.latest_coverage_summary(event_key)
        terminal = (
            str(summary.get("plan_digest") or "") == str(plan_digest)
            and batch_digest
            in set(summary.get("terminal_fetch_batch_digests") or ())
        )
        if terminal:
            return {"acquired": False, "state": "COMPLETED"}

        now_epoch = int(parse_utc(observed_at).timestamp())
        key = {
            "PK": "COVERAGE_FETCH_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "plan_digest": plan_digest,
                    "batch_digest": batch_digest,
                }
            ),
        }
        item = {
            **key,
            "entity_type": "SOCCER_COVERAGE_FETCH_EXECUTION",
            "event_key": event_key,
            "plan_digest": plan_digest,
            "batch_digest": batch_digest,
            "execution_state": "IN_PROGRESS",
            "execution_token": execution_token,
            "lease_expires_at": now_epoch + max(30, int(lease_seconds)),
            "started_at": observed_at,
            "expires_at": now_epoch + 30 * 86400,
        }
        try:
            self.ops.put_item(
                Item=ddb_safe(item),
                ConditionExpression=(
                    "attribute_not_exists(SK) OR "
                    "(execution_state <> :completed AND lease_expires_at <= :now)"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {":completed": "COMPLETED", ":now": now_epoch}
                ),
            )
            return {
                "acquired": True,
                "state": "IN_PROGRESS",
                "execution_token": execution_token,
                "lease_expires_at": item["lease_expires_at"],
            }
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        current = plain(
            self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        )
        return {
            "acquired": False,
            "state": str(current.get("execution_state") or "IN_PROGRESS"),
            "lease_expires_at": int(current.get("lease_expires_at") or 0),
        }

    def complete_coverage_fetch_execution(
        self,
        *,
        event_key: str,
        plan_digest: str,
        batch_digest: str,
        execution_token: str,
        observed_at: str,
    ) -> bool:
        """Complete a lease only after exact terminal batch evidence exists."""
        summary = self.latest_coverage_summary(event_key)
        if not (
            str(summary.get("plan_digest") or "") == str(plan_digest)
            and batch_digest
            in set(summary.get("terminal_fetch_batch_digests") or ())
        ):
            return False
        key = {
            "PK": "COVERAGE_FETCH_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "plan_digest": plan_digest,
                    "batch_digest": batch_digest,
                }
            ),
        }
        try:
            self.ops.update_item(
                Key=key,
                UpdateExpression=(
                    "SET execution_state=:completed, completed_at=:completed_at, "
                    "expires_at=:expires REMOVE lease_expires_at"
                ),
                ConditionExpression=(
                    "execution_state=:running AND execution_token=:token"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {
                        ":completed": "COMPLETED",
                        ":completed_at": observed_at,
                        ":expires": int(
                            (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                        ),
                        ":running": "IN_PROGRESS",
                        ":token": execution_token,
                    }
                ),
            )
            return True
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        current = plain(
            self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        )
        return str(current.get("execution_state") or "") == "COMPLETED"

    def release_coverage_fetch_execution(
        self,
        *,
        event_key: str,
        plan_digest: str,
        batch_digest: str,
        execution_token: str,
    ) -> None:
        key = {
            "PK": "COVERAGE_FETCH_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "plan_digest": plan_digest,
                    "batch_digest": batch_digest,
                }
            ),
        }
        try:
            self.ops.delete_item(
                Key=key,
                ConditionExpression=(
                    "execution_state=:running AND execution_token=:token"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {":running": "IN_PROGRESS", ":token": execution_token}
                ),
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    def begin_coverage_discovery_execution(
        self,
        *,
        event_key: str,
        discovery_observed_at: str,
        schedule_revision: int,
        execution_token: str,
        observed_at: str,
        lease_seconds: int = COVERAGE_FETCH_LEASE_SECONDS,
    ) -> dict[str, Any]:
        """Single-flight one paid event-market discovery generation."""
        summary = self.latest_coverage_summary(event_key)
        terminal_statuses = {
            "HTTP_200",
            "REQUEST_REJECTED",
            "PLAN_SIZE_LIMIT",
            "SUMMARY_SIZE_LIMIT",
        }
        if (
            str(summary.get("discovery_observed_at") or "")
            == str(discovery_observed_at)
            and int(summary.get("schedule_revision") or 0)
            == int(schedule_revision)
            and str(summary.get("discovery_status") or "")
            in terminal_statuses
            and (
                str(summary.get("discovery_status") or "") != "HTTP_200"
                or str(summary.get("plan_version") or "")
                == COVERAGE_PLAN_VERSION
            )
        ):
            return {"acquired": False, "state": "COMPLETED"}
        now_epoch = int(parse_utc(observed_at).timestamp())
        key = {
            "PK": "COVERAGE_DISCOVERY_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "discovery_observed_at": discovery_observed_at,
                    "schedule_revision": int(schedule_revision),
                    "plan_version": COVERAGE_PLAN_VERSION,
                }
            ),
        }
        item = {
            **key,
            "entity_type": "SOCCER_COVERAGE_DISCOVERY_EXECUTION",
            "event_key": event_key,
            "discovery_observed_at": discovery_observed_at,
            "schedule_revision": int(schedule_revision),
            "plan_version": COVERAGE_PLAN_VERSION,
            "execution_state": "IN_PROGRESS",
            "execution_token": execution_token,
            "lease_expires_at": now_epoch + max(30, int(lease_seconds)),
            "started_at": observed_at,
            "expires_at": now_epoch + 30 * 86400,
        }
        try:
            self.ops.put_item(
                Item=ddb_safe(item),
                ConditionExpression=(
                    "attribute_not_exists(SK) OR "
                    "(execution_state <> :completed AND lease_expires_at <= :now)"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {":completed": "COMPLETED", ":now": now_epoch}
                ),
            )
            return {
                "acquired": True,
                "state": "IN_PROGRESS",
                "execution_token": execution_token,
                "lease_expires_at": item["lease_expires_at"],
            }
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        current = plain(
            self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        )
        return {
            "acquired": False,
            "state": str(current.get("execution_state") or "IN_PROGRESS"),
            "lease_expires_at": int(current.get("lease_expires_at") or 0),
        }

    def complete_coverage_discovery_execution(
        self,
        *,
        event_key: str,
        discovery_observed_at: str,
        schedule_revision: int,
        execution_token: str,
        observed_at: str,
    ) -> bool:
        summary = self.latest_coverage_summary(event_key)
        if not (
            str(summary.get("discovery_observed_at") or "")
            == str(discovery_observed_at)
            and int(summary.get("schedule_revision") or 0)
            == int(schedule_revision)
            and str(summary.get("discovery_status") or "")
            in {
                "HTTP_200",
                "REQUEST_REJECTED",
                "PLAN_SIZE_LIMIT",
                "SUMMARY_SIZE_LIMIT",
            }
            and (
                str(summary.get("discovery_status") or "") != "HTTP_200"
                or str(summary.get("plan_version") or "")
                == COVERAGE_PLAN_VERSION
            )
        ):
            return False
        key = {
            "PK": "COVERAGE_DISCOVERY_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "discovery_observed_at": discovery_observed_at,
                    "schedule_revision": int(schedule_revision),
                    "plan_version": COVERAGE_PLAN_VERSION,
                }
            ),
        }
        try:
            self.ops.update_item(
                Key=key,
                UpdateExpression=(
                    "SET execution_state=:completed, completed_at=:completed_at, "
                    "expires_at=:expires REMOVE lease_expires_at"
                ),
                ConditionExpression=(
                    "execution_state=:running AND execution_token=:token"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {
                        ":completed": "COMPLETED",
                        ":completed_at": observed_at,
                        ":expires": int(
                            (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                        ),
                        ":running": "IN_PROGRESS",
                        ":token": execution_token,
                    }
                ),
            )
            return True
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        current = plain(
            self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
        )
        return str(current.get("execution_state") or "") == "COMPLETED"

    def release_coverage_discovery_execution(
        self,
        *,
        event_key: str,
        discovery_observed_at: str,
        schedule_revision: int,
        execution_token: str,
    ) -> None:
        key = {
            "PK": "COVERAGE_DISCOVERY_EXECUTION",
            "SK": digest(
                {
                    "event_key": event_key,
                    "discovery_observed_at": discovery_observed_at,
                    "schedule_revision": int(schedule_revision),
                    "plan_version": COVERAGE_PLAN_VERSION,
                }
            ),
        }
        try:
            self.ops.delete_item(
                Key=key,
                ConditionExpression=(
                    "execution_state=:running AND execution_token=:token"
                ),
                ExpressionAttributeValues=ddb_safe(
                    {":running": "IN_PROGRESS", ":token": execution_token}
                ),
            )
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise

    def enqueue(self, payload: Mapping[str, Any], *, delay_seconds: int = 0) -> str:
        if not self.collection_queue_url:
            raise RuntimeError("SOCCER_AUTO_COLLECTION_QUEUE_URL is not configured")
        kwargs: dict[str, Any] = {
            "QueueUrl": self.collection_queue_url,
            "MessageBody": canonical_json(payload),
        }
        delay = max(0, min(900, int(delay_seconds)))
        if delay:
            kwargs["DelaySeconds"] = delay
        response = self.sqs.send_message(
            **kwargs,
        )
        return str(response.get("MessageId") or "")

    def defer_message(self, receipt_handle: str, *, visibility_seconds: int) -> None:
        """Shorten one controlled SQS retry without acknowledging its work."""
        if not self.collection_queue_url:
            raise RuntimeError("SOCCER_AUTO_COLLECTION_QUEUE_URL is not configured")
        self.sqs.change_message_visibility(
            QueueUrl=self.collection_queue_url,
            ReceiptHandle=str(receipt_handle),
            VisibilityTimeout=max(0, min(43200, int(visibility_seconds))),
        )

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

    def provider_budget_admission(
        self, operation: str, observed_at: str, estimated_cost: int = 1
    ) -> dict[str, Any]:
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
        try:
            remaining_value = int(remaining)
            used_value = int(used)
        except (TypeError, ValueError):
            remaining_value = -1
            used_value = -1
        reserve_percent = max(
            0.0,
            min(100.0, float(os.getenv("SOCCER_AUTO_SHARED_QUOTA_RESERVE_PERCENT", "0"))),
        )
        configured_race_buffer = max(
            0,
            int(os.getenv("SOCCER_AUTO_QUOTA_RACE_BUFFER_CREDITS", "2000")),
        )
        quota_known = remaining_value >= 0 and used_value >= 0
        total = remaining_value + used_value if quota_known else 0
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
        spendable = max(
            0,
            int(
                float(remaining_value if quota_known else 0)
                - float(reserve_credits or 0)
                - race_buffer
            ),
        )
        quota_snapshot = str(latest.get("quota_snapshot") or "")
        quota_observation_valid = bool(
            quota_known and total > 0 and quota_snapshot
        )
        available = False
        reason = "QUOTA_OBSERVATION_UNAVAILABLE"
        if quota_observation_valid and cost <= spendable:
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
                                "remaining_at_snapshot": remaining_value,
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
        elif quota_observation_valid:
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
        return {
            "available": available,
            "reason": "ADMITTED" if available else reason,
            "external_capacity": bool(
                not available and reason in COVERAGE_EXTERNAL_QUOTA_REASONS
            ),
            "quota_snapshot": quota_snapshot,
            "remaining": remaining,
            "used": used,
            "estimated_cost": cost,
        }

    def provider_budget_available(
        self, operation: str, observed_at: str, estimated_cost: int = 1
    ) -> bool:
        """Compatibility boolean for non-coverage provider callers."""
        return bool(
            self.provider_budget_admission(
                operation,
                observed_at,
                estimated_cost,
            )["available"]
        )

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
                if (
                    observed_at
                    and row.get("observed_at")
                    and parse_utc(str(row["observed_at"])) > parse_utc(observed_at)
                ):
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

    def put_coverage_plan(
        self,
        event_key: str,
        inventory: Mapping[str, Any],
        observed_at: str,
        *,
        required_inventory: Mapping[str, Any] | None = None,
        event: Mapping[str, Any] | None = None,
        discovery_observed_at: str | None = None,
        request_markets: Sequence[str] = (),
    ) -> dict[str, Any]:
        request_market_keys = sorted(
            {str(value) for value in request_markets if value}
        )
        schedule_revision_value = int((event or {}).get("schedule_revision") or 0)
        schedule_identity_value = (
            str((event or {}).get("schedule_identity") or "")
            or (schedule_identity(event or {}) if event else "")
        )
        expected_pairs = sorted(
            f"{bookmaker}|{market}"
            for bookmaker, detail in inventory.items()
            for market in detail.get("markets") or []
        )
        current_required_inventory = (
            required_inventory if required_inventory is not None else inventory
        )
        required_pairs = sorted(
            f"{bookmaker}|{market}"
            for bookmaker, detail in current_required_inventory.items()
            for market in detail.get("markets") or []
        )
        probe_pairs = sorted(set(expected_pairs) - set(required_pairs))
        expected_digest = digest(expected_pairs)
        plan_digest = coverage_plan_digest(
            event_key=event_key,
            observed_at=observed_at,
            schedule_revision=schedule_revision_value,
            schedule_identity_value=schedule_identity_value,
            request_markets=request_market_keys,
            required_pairs=required_pairs,
            probe_pairs=probe_pairs,
        )
        generation_at = str(discovery_observed_at or observed_at)
        detail_item = {
            "PK": f"COVERAGE#{event_key}",
            "SK": f"PLAN#{observed_at}",
            "entity_type": "SOCCER_EVENT_COVERAGE_PLAN",
            "event_key": event_key,
            "observed_at": observed_at,
            "plan_version": COVERAGE_PLAN_VERSION,
            "expected_pairs": expected_pairs,
            "expected_pair_count": len(expected_pairs),
            "required_pairs": required_pairs,
            "required_pair_count": len(required_pairs),
            "probe_pairs": probe_pairs,
            "probe_pair_count": len(probe_pairs),
            "expected_digest": expected_digest,
            "plan_digest": plan_digest,
            "commence_time": (event or {}).get("commence_time"),
            "schedule_revision": schedule_revision_value,
            "schedule_identity": schedule_identity_value or None,
            "coverage_complete": False,
            "request_markets": request_market_keys,
            "expires_at": int(
                (parse_utc(observed_at) + timedelta(days=30)).timestamp()
            ),
        }
        detail_size = ddb_item_size_bytes(detail_item)
        if detail_size > COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES:
            def reject_oversized_plan(
                current: Mapping[str, Any],
            ) -> dict[str, Any] | None:
                current_generation = str(
                    current.get("discovery_observed_at")
                    or current.get("plan_observed_at")
                    or ""
                )
                if discovery_observed_at and current_generation != generation_at:
                    return None
                if current and (
                    int(current.get("schedule_revision") or 0)
                    != schedule_revision_value
                    or str(current.get("schedule_identity") or "")
                    != schedule_identity_value
                ):
                    return None
                if current.get("plan_observed_at"):
                    return None
                return {
                    **current,
                    "PK": "COVERAGE_LATEST",
                    "SK": event_key,
                    "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                    "event_key": event_key,
                    "discovery_observed_at": generation_at,
                    "discovery_status": "PLAN_SIZE_LIMIT",
                    "discovery_status_observed_at": observed_at,
                    "coverage_error": "DDB_ITEM_SIZE_LIMIT",
                    "coverage_item_size_bytes": detail_size,
                    "plan_version": "",
                    "plan_observed_at": "",
                    "plan_digest": "",
                    "required_pairs": [],
                    "probe_pairs": [],
                    "expected_digest": digest([]),
                    "request_markets": [],
                    "commence_time": (event or {}).get("commence_time"),
                    "schedule_revision": schedule_revision_value,
                    "schedule_identity": schedule_identity_value,
                    "returned_pairs": [],
                    "provider_unavailable_pairs": [],
                    "normalization_rejected_pairs": [],
                    "attempted_incomplete_pairs": [],
                    "quota_deferred_pairs": [],
                    "failed_pairs": [],
                    "fanout_expected_batch_digests": [],
                    "fanout_enqueued_batch_digests": [],
                    "fanout_succeeded_batch_digests": [],
                    "fanout_failed_batch_digests": [],
                    "fanout_deferred_batch_digests": [],
                    "fanout_deferred_batch_reasons": {},
                    "terminal_fetch_batch_digests": [],
                    "updated_at": observed_at,
                    "expires_at": int(
                        (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                    ),
                }

            latest, updated = self._mutate_latest_coverage(
                event_key, reject_oversized_plan
            )
            return {
                **latest,
                "expected_pairs": [],
                "latest_summary_updated": updated,
            }

        self.ops.put_item(Item=ddb_safe(detail_item))

        def replace_plan(current: Mapping[str, Any]) -> dict[str, Any] | None:
            current_generation = str(
                current.get("discovery_observed_at")
                or current.get("plan_observed_at")
                or ""
            )
            # A worker may only publish the plan for the exact dispatch
            # generation that is still current. A late response from an older
            # queue message must not repaint a newer pending discovery.
            if discovery_observed_at:
                if current_generation and current_generation != generation_at:
                    return None
            elif current_generation > generation_at:
                return None
            if discovery_observed_at and not current:
                return None
            if (
                current
                and (
                    str(current.get("schedule_identity") or "")
                    != schedule_identity_value
                    or int(current.get("schedule_revision") or 0)
                    != schedule_revision_value
                )
            ):
                return None
            # A dispatch generation has one immutable discovery plan. Duplicate
            # workers that were already running may archive their response, but
            # cannot reset this summary or its fetch outcomes.
            if current.get("plan_observed_at"):
                return None
            return {
                **current,
                "PK": "COVERAGE_LATEST",
                "SK": event_key,
                "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                "event_key": event_key,
                "discovery_observed_at": generation_at,
                "discovery_status": (
                    "PLAN_READY" if discovery_observed_at else "HTTP_200"
                ),
                "discovery_status_observed_at": observed_at,
                "coverage_error": None,
                "coverage_item_size_bytes": 0,
                "budget_reason": "",
                "plan_version": COVERAGE_PLAN_VERSION,
                "plan_observed_at": observed_at,
                "required_pairs": required_pairs,
                "probe_pairs": probe_pairs,
                "request_markets": request_market_keys,
                "expected_digest": expected_digest,
                "plan_digest": plan_digest,
                "commence_time": (event or {}).get("commence_time"),
                "schedule_revision": schedule_revision_value,
                "schedule_identity": schedule_identity_value or None,
                "returned_pairs": [],
                "provider_unavailable_pairs": [],
                "normalization_rejected_pairs": [],
                "attempted_incomplete_pairs": [],
                "quota_deferred_pairs": [],
                "failed_pairs": [],
                "region_split_groups": {},
                "region_split_conflicts": 0,
                "split_batch_groups": {},
                "split_batch_conflicts": 0,
                "fanout_expected_batch_digests": [],
                "fanout_enqueued_batch_digests": [],
                "fanout_succeeded_batch_digests": [],
                "fanout_failed_batch_digests": [],
                "fanout_deferred_batch_digests": [],
                "fanout_deferred_batch_reasons": {},
                "terminal_fetch_batch_digests": [],
                "outcome_counts": {},
                "updated_at": observed_at,
                "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
            }

        latest, updated = self._mutate_latest_coverage(event_key, replace_plan)
        return {
            **latest,
            "expected_pairs": sorted(
                set(latest.get("required_pairs") or ())
                | set(latest.get("probe_pairs") or ())
            ),
            "latest_summary_updated": updated,
        }

    def _mutate_latest_coverage(
        self,
        event_key: str,
        mutator: Any,
    ) -> tuple[dict[str, Any], bool]:
        """Apply one full-item latest-summary mutation with a revision CAS."""
        key = {"PK": "COVERAGE_LATEST", "SK": event_key}
        for _ in range(16):
            current = plain(
                self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
            )
            replacement = mutator(current)
            if replacement is None:
                return current, False
            revision = int(current.get("summary_revision") or 0)
            replacement = {
                **replacement,
                **key,
                "summary_revision": revision + 1,
            }
            replacement_size = ddb_item_size_bytes(replacement)
            if replacement_size > COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES:
                # Never let a growing exact-summary item fail at DynamoDB's
                # hard 400 KiB boundary while an older green summary remains.
                # Replace it with a small, explicit, fail-closed generation.
                replacement = {
                    **key,
                    "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                    "event_key": event_key,
                    "discovery_observed_at": str(
                        replacement.get("discovery_observed_at") or ""
                    ),
                    "discovery_status": "SUMMARY_SIZE_LIMIT",
                    "discovery_status_observed_at": str(
                        replacement.get("updated_at")
                        or replacement.get("discovery_status_observed_at")
                        or iso_utc(now_utc())
                    ),
                    "coverage_error": "DDB_ITEM_SIZE_LIMIT",
                    "coverage_item_size_bytes": replacement_size,
                    "plan_version": "",
                    "plan_observed_at": "",
                    "plan_digest": "",
                    "commence_time": replacement.get("commence_time"),
                    "schedule_revision": replacement.get("schedule_revision"),
                    "schedule_identity": replacement.get("schedule_identity"),
                    "required_pairs": [],
                    "probe_pairs": [],
                    "expected_digest": digest([]),
                    "request_markets": [],
                    "returned_pairs": [],
                    "provider_unavailable_pairs": [],
                    "normalization_rejected_pairs": [],
                    "attempted_incomplete_pairs": [],
                    "quota_deferred_pairs": [],
                    "failed_pairs": [],
                    "fanout_expected_batch_digests": [],
                    "fanout_enqueued_batch_digests": [],
                    "fanout_succeeded_batch_digests": [],
                    "fanout_failed_batch_digests": [],
                    "fanout_deferred_batch_digests": [],
                    "fanout_deferred_batch_reasons": {},
                    "terminal_fetch_batch_digests": [],
                    "outcome_counts": {},
                    "summary_revision": revision + 1,
                    "updated_at": str(
                        replacement.get("updated_at") or iso_utc(now_utc())
                    ),
                    "expires_at": int(
                        (now_utc() + timedelta(days=30)).timestamp()
                    ),
                }
            try:
                self.ops.put_item(
                    Item=ddb_safe(replacement),
                    ConditionExpression=(
                        "attribute_not_exists(summary_revision) "
                        "OR summary_revision=:revision"
                    ),
                    ExpressionAttributeValues=ddb_safe({":revision": revision}),
                )
                return plain(replacement), True
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("soccer latest coverage summary update contention")

    def put_coverage_discovery_attempt(
        self,
        event: Mapping[str, Any],
        *,
        discovery_observed_at: str,
        status: str,
        observed_at: str,
        budget_reason: str | None = None,
    ) -> dict[str, Any]:
        """Materialize every dispatched event before any paid discovery call."""
        event_key = str(event["event_key"])
        generation_at = str(discovery_observed_at)

        def mutate(current: Mapping[str, Any]) -> dict[str, Any] | None:
            current_generation = str(
                current.get("discovery_observed_at")
                or current.get("plan_observed_at")
                or ""
            )
            if current_generation > generation_at:
                return None
            incoming_identity = str(
                event.get("schedule_identity") or schedule_identity(event)
            )
            current_identity = str(current.get("schedule_identity") or "")
            revision_advanced = bool(
                current_generation == generation_at
                and int(event.get("schedule_revision") or 0)
                > int(current.get("schedule_revision") or 0)
            )
            schema_advanced = bool(
                current_generation == generation_at
                and current.get("plan_observed_at")
                and str(current.get("plan_version") or "")
                != COVERAGE_PLAN_VERSION
            )
            if (
                current_generation == generation_at
                and current_identity != incoming_identity
                and not revision_advanced
            ):
                return None
            if (
                current_generation == generation_at
                and not revision_advanced
                and not schema_advanced
            ):
                if (
                    str(current.get("discovery_status") or "")
                    in {
                        "HTTP_200",
                        "REQUEST_REJECTED",
                        "PLAN_SIZE_LIMIT",
                        "SUMMARY_SIZE_LIMIT",
                    }
                    and str(status)
                    != str(current.get("discovery_status") or "")
                ):
                    return None
                current_status_at = str(
                    current.get("discovery_status_observed_at") or ""
                )
                if current_status_at > observed_at:
                    return None
                return {
                    **current,
                    "discovery_status": str(status),
                    "discovery_status_observed_at": observed_at,
                    "budget_reason": str(budget_reason or ""),
                    "updated_at": observed_at,
                }
            return {
                "PK": "COVERAGE_LATEST",
                "SK": event_key,
                "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                "event_key": event_key,
                "discovery_observed_at": generation_at,
                "discovery_status": str(status),
                "discovery_status_observed_at": observed_at,
                "budget_reason": str(budget_reason or ""),
                "commence_time": event.get("commence_time"),
                "schedule_revision": event.get("schedule_revision"),
                "schedule_identity": (
                    str(event.get("schedule_identity") or "")
                    or incoming_identity
                ),
                "required_pairs": [],
                "probe_pairs": [],
                "expected_digest": digest([]),
                "plan_version": "",
                "plan_digest": "",
                "returned_pairs": [],
                "provider_unavailable_pairs": [],
                "normalization_rejected_pairs": [],
                "attempted_incomplete_pairs": [],
                "quota_deferred_pairs": [],
                "failed_pairs": [],
                "fanout_expected_batch_digests": [],
                "fanout_enqueued_batch_digests": [],
                "fanout_succeeded_batch_digests": [],
                "fanout_failed_batch_digests": [],
                "fanout_deferred_batch_digests": [],
                "fanout_deferred_batch_reasons": {},
                "terminal_fetch_batch_digests": [],
                "outcome_counts": {},
                "updated_at": observed_at,
                "expires_at": int(
                    (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                ),
            }

        latest, updated = self._mutate_latest_coverage(event_key, mutate)
        return {**latest, "latest_summary_updated": updated}

    def put_coverage_dispatch_manifest(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        observed_at: str,
        inventory_authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Publish the exact open-window event universe for this dispatch run."""
        canonical_entries = sorted(
            (
                {
                    "event_key": str(row["event_key"]),
                    "commence_time": str(row["commence_time"]),
                    "schedule_revision": int(row.get("schedule_revision") or 0),
                    "schedule_identity": str(row["schedule_identity"]),
                    "required_discovery_observed_at": str(
                        row.get("required_discovery_observed_at") or ""
                    ),
                }
                for row in entries
            ),
            key=lambda row: (row["commence_time"], row["event_key"]),
        )
        manifest_version = COVERAGE_DISPATCH_MANIFEST_VERSION
        inventory_binding = {
            "authority_version": str(
                inventory_authority.get("authority_version") or ""
            ),
            "generation_id": str(inventory_authority.get("generation_id") or ""),
            "completed_at": str(inventory_authority.get("completed_at") or ""),
            "authority_revision": int(
                inventory_authority.get("authority_revision") or 0
            ),
        }
        inventory_valid = bool(
            inventory_authority.get("valid")
            and inventory_binding["authority_version"]
            == EVENT_INVENTORY_AUTHORITY_VERSION
            and inventory_binding["generation_id"]
            and inventory_binding["completed_at"]
            and inventory_binding["authority_revision"] > 0
        )
        manifest_error = (
            ""
            if inventory_valid
            else str(
                inventory_authority.get("reason")
                or "INVENTORY_AUTHORITY_UNAVAILABLE"
            )
        )
        manifest_digest = digest(
            {
                "version": manifest_version,
                "observed_at": observed_at,
                "inventory_authority": inventory_binding,
                "manifest_error": manifest_error,
                "events": canonical_entries,
            }
        )
        common = {
            "entity_type": "SOCCER_COVERAGE_DISPATCH_MANIFEST",
            "manifest_version": manifest_version,
            "manifest_digest": manifest_digest,
            "observed_at": observed_at,
            "events": canonical_entries,
            "event_count": len(canonical_entries),
            "inventory_authority": inventory_binding,
            "manifest_error": manifest_error,
            "expires_at": int(
                (parse_utc(observed_at) + timedelta(days=2)).timestamp()
            ),
        }
        latest = {
            **common,
            "PK": "COVERAGE_DISPATCH_MANIFEST",
            "SK": "LATEST",
        }
        manifest_size = ddb_item_size_bytes(latest)
        if manifest_size > COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES:
            error_common = {
                "entity_type": "SOCCER_COVERAGE_DISPATCH_MANIFEST",
                "manifest_version": manifest_version,
                "manifest_digest": manifest_digest,
                "observed_at": observed_at,
                "events": [],
                "event_count": len(canonical_entries),
                "inventory_authority": inventory_binding,
                "manifest_error": "DDB_ITEM_SIZE_LIMIT",
                "manifest_item_size_bytes": manifest_size,
                "expires_at": int(
                    (parse_utc(observed_at) + timedelta(days=2)).timestamp()
                ),
            }
            self.ops.put_item(
                Item=ddb_safe(
                    {
                        **error_common,
                        "PK": "COVERAGE_DISPATCH_MANIFEST",
                        "SK": f"ERROR#{observed_at}",
                    }
                )
            )
            try:
                self.ops.put_item(
                    Item=ddb_safe(
                        {
                            **error_common,
                            "PK": "COVERAGE_DISPATCH_MANIFEST",
                            "SK": "LATEST",
                        }
                    ),
                    ConditionExpression=(
                        "attribute_not_exists(observed_at) OR observed_at < :observed_at"
                    ),
                    ExpressionAttributeValues=ddb_safe(
                        {":observed_at": observed_at}
                    ),
                )
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
            raise RuntimeError(
                "Soccer coverage dispatch manifest exceeds the fail-closed "
                f"DynamoDB size budget: {manifest_size} bytes"
            )
        self.ops.put_item(
            Item=ddb_safe(
                {
                    **common,
                    "PK": "COVERAGE_DISPATCH_MANIFEST",
                    "SK": f"RUN#{observed_at}",
                }
            )
        )
        try:
            self.ops.put_item(
                Item=ddb_safe(latest),
                ConditionExpression=(
                    "attribute_not_exists(observed_at) OR observed_at < :observed_at"
                ),
                ExpressionAttributeValues=ddb_safe({":observed_at": observed_at}),
            )
            return {**plain(latest), "latest_manifest_updated": True}
        except ClientError as exc:
            if not _is_conditional_failure(exc):
                raise
        current = self.latest_coverage_dispatch_manifest()
        return {**current, "latest_manifest_updated": False}

    def latest_coverage_dispatch_manifest(self) -> dict[str, Any]:
        return plain(
            self.ops.get_item(
                Key={"PK": "COVERAGE_DISPATCH_MANIFEST", "SK": "LATEST"},
                ConsistentRead=True,
            ).get("Item")
            or {}
        )

    def coverage_plan_is_current(
        self,
        event_key: str,
        *,
        plan_observed_at: str,
        plan_digest: str,
    ) -> bool:
        current = plain(
            self.ops.get_item(
                Key={"PK": "COVERAGE_LATEST", "SK": str(event_key)},
                ConsistentRead=True,
            ).get("Item")
            or {}
        )
        return bool(
            current.get("entity_type") == "SOCCER_EVENT_COVERAGE_LATEST"
            and str(current.get("plan_version") or "") == COVERAGE_PLAN_VERSION
            and str(current.get("plan_observed_at") or "") == str(plan_observed_at)
            and str(current.get("plan_digest") or "") == str(plan_digest)
        )

    def put_coverage_fanout_expected(
        self,
        event_key: str,
        *,
        plan_observed_at: str,
        plan_digest: str,
        batch_digests: Sequence[str],
        observed_at: str,
    ) -> dict[str, Any]:
        expected = sorted({str(value) for value in batch_digests if value})

        def mutate(current: Mapping[str, Any]) -> dict[str, Any] | None:
            if (
                str(current.get("plan_observed_at") or "") != plan_observed_at
                or str(current.get("plan_digest") or "") != plan_digest
            ):
                return None
            # Batch execution follows request-market chunk order, while the
            # persisted fanout manifest is a canonical set.  Compare both in
            # the same order so a valid multi-batch plan cannot be rejected
            # merely because its SHA-256 digests are not lexicographically
            # ordered by their request chunks.
            exact_expected = sorted(
                coverage_expected_batch_digests(
                    plan_digest=plan_digest,
                    request_markets=tuple(current.get("request_markets") or ()),
                    expected_pairs=sorted(
                        set(current.get("required_pairs") or ())
                        | set(current.get("probe_pairs") or ())
                    ),
                )
            )
            if expected != exact_expected:
                return None
            stored = sorted(current.get("fanout_expected_batch_digests") or ())
            if stored and stored != expected:
                return None
            enqueued = sorted(
                current.get("fanout_enqueued_batch_digests") or ()
            )
            already_complete = bool(
                str(current.get("discovery_status") or "") == "HTTP_200"
                and stored == expected
                and enqueued == expected
            )
            return {
                **current,
                "fanout_expected_batch_digests": expected,
                "fanout_enqueued_batch_digests": enqueued,
                "discovery_status": (
                    "HTTP_200" if already_complete else "FANOUT_PENDING"
                ),
                "discovery_status_observed_at": (
                    current.get("discovery_status_observed_at")
                    if already_complete
                    else observed_at
                ),
                "updated_at": observed_at,
            }

        latest, updated = self._mutate_latest_coverage(event_key, mutate)
        return {**latest, "latest_summary_updated": updated}

    def mark_coverage_fanout_enqueued(
        self,
        event_key: str,
        *,
        plan_observed_at: str,
        plan_digest: str,
        batch_digest: str,
        observed_at: str,
    ) -> dict[str, Any]:
        def mutate(current: Mapping[str, Any]) -> dict[str, Any] | None:
            expected = set(current.get("fanout_expected_batch_digests") or ())
            if (
                str(current.get("plan_observed_at") or "") != plan_observed_at
                or str(current.get("plan_digest") or "") != plan_digest
                or batch_digest not in expected
            ):
                return None
            enqueued = set(current.get("fanout_enqueued_batch_digests") or ())
            enqueued.add(batch_digest)
            return {
                **current,
                "fanout_enqueued_batch_digests": sorted(enqueued),
                "updated_at": observed_at,
            }

        latest, updated = self._mutate_latest_coverage(event_key, mutate)
        return {**latest, "latest_summary_updated": updated}

    def complete_coverage_fanout(
        self,
        event_key: str,
        *,
        plan_observed_at: str,
        plan_digest: str,
        observed_at: str,
    ) -> dict[str, Any]:
        def mutate(current: Mapping[str, Any]) -> dict[str, Any] | None:
            expected = set(current.get("fanout_expected_batch_digests") or ())
            enqueued = set(current.get("fanout_enqueued_batch_digests") or ())
            if (
                str(current.get("plan_observed_at") or "") != plan_observed_at
                or str(current.get("plan_digest") or "") != plan_digest
                or not expected
                or expected != enqueued
            ):
                return None
            return {
                **current,
                "discovery_status": "HTTP_200",
                "discovery_status_observed_at": observed_at,
                "updated_at": observed_at,
            }

        latest, updated = self._mutate_latest_coverage(event_key, mutate)
        return {**latest, "latest_summary_updated": updated}

    def latest_coverage_summary(self, event_key: str) -> dict[str, Any]:
        return plain(
            self.ops.get_item(
                Key={"PK": "COVERAGE_LATEST", "SK": str(event_key)},
                ConsistentRead=True,
            ).get("Item")
            or {}
        )

    def latest_coverage_cycles(
        self,
        *,
        event_keys: Iterable[str] | None = None,
        active_after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Read every materialized latest cycle without sampling the Ops table."""
        allowed = {str(value) for value in event_keys} if event_keys is not None else None
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq("COVERAGE_LATEST"),
            "ConsistentRead": True,
        }
        rows: list[dict[str, Any]] = []
        while True:
            response = self.ops.query(**kwargs)
            for row in response.get("Items") or []:
                row = plain(row)
                if row.get("entity_type") != "SOCCER_EVENT_COVERAGE_LATEST":
                    continue
                event_key = str(row.get("event_key") or row.get("SK") or "")
                if allowed is not None and event_key not in allowed:
                    continue
                if (
                    active_after
                    and row.get("commence_time")
                    and str(row["commence_time"]) <= str(active_after)
                ):
                    continue
                rows.append(row)
            cursor = response.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        return rows

    def _merge_latest_coverage_attempt(
        self,
        *,
        event_key: str,
        plan_observed_at: str | None,
        plan_digest: str | None,
        observed_at: str,
        attempted_pairs: Sequence[str],
        returned_pairs: Sequence[str],
        raw_returned_pairs: Sequence[str],
        provider_unavailable_pairs: Sequence[str],
        normalization_rejected_pairs: Sequence[str],
        attempted_incomplete_pairs: Sequence[str],
        quota_deferred_pairs: Sequence[str],
        failed_pairs: Sequence[str],
        split_group_digest: str | None,
        attempted_regions: Sequence[str],
        split_expected_regions: Sequence[str],
        split_leaf_id: str | None,
        split_expected_leaf_ids: Sequence[str],
        split_child_leaf_ids: Sequence[str],
        batch_digest: str | None,
        outcome: str,
        budget_reason: str | None,
    ) -> bool:
        if not plan_observed_at or not plan_digest:
            return False
        key = {"PK": "COVERAGE_LATEST", "SK": event_key}
        for _ in range(16):
            current = plain(
                self.ops.get_item(Key=key, ConsistentRead=True).get("Item") or {}
            )
            if (
                current.get("entity_type") != "SOCCER_EVENT_COVERAGE_LATEST"
                or str(current.get("plan_version") or "")
                != COVERAGE_PLAN_VERSION
                or str(current.get("plan_observed_at") or "") != str(plan_observed_at)
                or str(current.get("plan_digest") or "") != str(plan_digest)
            ):
                return False
            revision = int(current.get("summary_revision") or 0)
            expected = (
                set(current.get("required_pairs") or ())
                | set(current.get("probe_pairs") or ())
            )
            returned = (
                set(current.get("returned_pairs") or ()) | set(returned_pairs)
            ) & expected
            rejected = (
                set(current.get("normalization_rejected_pairs") or ())
                | set(normalization_rejected_pairs)
            ) & expected - returned
            expected_batches = set(
                current.get("fanout_expected_batch_digests") or ()
            )
            region_split_groups = dict(current.get("region_split_groups") or {})
            region_split_conflicts = int(current.get("region_split_conflicts") or 0)
            completed_split_pairs: set[str] = set()
            if (
                split_group_digest
                and split_expected_regions
                and outcome == "HTTP_200"
            ):
                group_key = str(split_group_digest)
                existing_group = dict(region_split_groups.get(group_key) or {})
                expected_regions = sorted(
                    {str(value) for value in split_expected_regions if value}
                )
                planned = sorted(set(attempted_pairs) & expected)
                if existing_group and (
                    sorted(existing_group.get("expected_regions") or ()) != expected_regions
                    or sorted(existing_group.get("planned_pairs") or ()) != planned
                ):
                    region_split_conflicts += 1
                else:
                    completed_regions = sorted(
                        set(existing_group.get("completed_regions") or ())
                        | {str(value) for value in attempted_regions if value}
                    )
                    region_split_groups[group_key] = {
                        "expected_regions": expected_regions,
                        "completed_regions": completed_regions,
                        "planned_pairs": planned,
                    }
                    if expected_regions and set(completed_regions) >= set(expected_regions):
                        completed_split_pairs.update(planned)
            split_batch_groups = dict(current.get("split_batch_groups") or {})
            split_batch_conflicts = int(current.get("split_batch_conflicts") or 0)
            split_root_succeeded = ""
            split_root_failed = ""
            split_root_deferred = ""
            split_root_deferred_reasons: set[str] = set()
            if split_group_digest and str(split_group_digest) in expected_batches:
                root = str(split_group_digest)
                group = dict(split_batch_groups.get(root) or {})
                existing_expected_leaves = set(
                    group.get("expected_leaf_ids") or ()
                )
                incoming_expected_leaves = {
                    str(value) for value in split_expected_leaf_ids if value
                }
                completed_leaves = set(group.get("completed_leaf_ids") or ())
                failed_leaves = set(group.get("failed_leaf_ids") or ())
                deferred_leaf_reasons = {
                    str(key): str(value)
                    for key, value in dict(
                        group.get("deferred_leaf_reasons") or {}
                    ).items()
                    if key and value
                }
                planned_union = set(group.get("planned_pairs") or ()) | (
                    set(attempted_pairs) & expected
                )
                if outcome == "SPLIT_PENDING":
                    child_leaves = {
                        str(value) for value in split_child_leaf_ids if value
                    }
                    if not child_leaves:
                        split_batch_conflicts += 1
                    else:
                        base = existing_expected_leaves or incoming_expected_leaves
                        if split_leaf_id:
                            base.discard(str(split_leaf_id))
                            deferred_leaf_reasons.pop(str(split_leaf_id), None)
                        base.update(child_leaves)
                        existing_expected_leaves = base
                elif split_leaf_id:
                    leaf = str(split_leaf_id)
                    if not existing_expected_leaves:
                        existing_expected_leaves = incoming_expected_leaves
                    if leaf not in existing_expected_leaves:
                        # A recursive split may have sent this grandchild just
                        # before its parent advances the persisted leaf set.
                        # Do not ACK or poison the lineage; the short delayed
                        # retry will be admitted once that parent update lands.
                        return False
                    elif outcome == "HTTP_200":
                        completed_leaves.add(leaf)
                        failed_leaves.discard(leaf)
                        deferred_leaf_reasons.pop(leaf, None)
                    elif outcome in {
                        "REQUEST_REJECTED",
                        "RESPONSE_INVALID",
                        "EVIDENCE_SIZE_LIMIT",
                    }:
                        failed_leaves.add(leaf)
                        completed_leaves.discard(leaf)
                        deferred_leaf_reasons.pop(leaf, None)
                    elif outcome == "QUOTA_DEFERRED":
                        completed_leaves.discard(leaf)
                        # A quota observation must never erase an earlier
                        # non-quota execution failure for the same leaf.  The
                        # leaf remains retryable, and only a later successful
                        # response may clear that fail-closed evidence.
                        if leaf not in failed_leaves and budget_reason:
                            deferred_leaf_reasons[leaf] = str(budget_reason)
                    elif outcome == "RETRYABLE_ERROR":
                        completed_leaves.discard(leaf)
                        failed_leaves.add(leaf)
                        deferred_leaf_reasons.pop(leaf, None)
                deferred_leaf_reasons = {
                    leaf: reason
                    for leaf, reason in deferred_leaf_reasons.items()
                    if leaf in existing_expected_leaves
                    and leaf not in completed_leaves
                    and leaf not in failed_leaves
                }
                split_batch_groups[root] = {
                    "expected_leaf_ids": sorted(existing_expected_leaves),
                    "completed_leaf_ids": sorted(completed_leaves),
                    "failed_leaf_ids": sorted(failed_leaves),
                    "deferred_leaf_reasons": deferred_leaf_reasons,
                    "planned_pairs": sorted(planned_union),
                }
                terminal_leaves = completed_leaves | failed_leaves
                if (
                    existing_expected_leaves
                    and terminal_leaves >= existing_expected_leaves
                ):
                    if failed_leaves & existing_expected_leaves:
                        split_root_failed = root
                    else:
                        split_root_succeeded = root
                        completed_split_pairs.update(planned_union)
                else:
                    nonterminal_leaves = (
                        existing_expected_leaves
                        - completed_leaves
                        - failed_leaves
                    )
                    if (
                        nonterminal_leaves
                        and not failed_leaves
                        and nonterminal_leaves
                        <= set(deferred_leaf_reasons)
                    ):
                        split_root_deferred = root
                        split_root_deferred_reasons = {
                            deferred_leaf_reasons[leaf]
                            for leaf in nonterminal_leaves
                        }
            unavailable = (
                set(current.get("provider_unavailable_pairs") or ())
                | set(provider_unavailable_pairs)
                | completed_split_pairs
            ) & expected - returned - rejected
            attempted_incomplete = (
                set(current.get("attempted_incomplete_pairs") or ())
                | set(attempted_incomplete_pairs)
            ) & expected - returned - rejected - unavailable
            split_deferred_pairs: set[str] = set()
            if split_root_deferred:
                split_deferred_pairs = (
                    (planned_union & expected)
                    - returned
                    - rejected
                    - unavailable
                )
                attempted_incomplete -= split_deferred_pairs
            failed = (
                set(current.get("failed_pairs") or ()) | set(failed_pairs)
            ) & expected - returned - rejected - unavailable - attempted_incomplete
            if split_root_deferred:
                failed -= split_deferred_pairs
            current_deferred_pairs = set(
                current.get("quota_deferred_pairs") or ()
            )
            if split_group_digest:
                # Recompute this split root's classification from its current
                # leaf frontier so a later success/retry clears old quota state.
                current_deferred_pairs -= planned_union
            deferred = (
                current_deferred_pairs
                | set(quota_deferred_pairs)
                | split_deferred_pairs
            ) & expected - returned - rejected - unavailable - attempted_incomplete - failed
            succeeded_batches = set(
                current.get("fanout_succeeded_batch_digests") or ()
            )
            failed_batches = set(
                current.get("fanout_failed_batch_digests") or ()
            )
            deferred_batches = set(
                current.get("fanout_deferred_batch_digests") or ()
            )
            deferred_batch_reasons = {
                str(key): sorted({str(reason) for reason in value if reason})
                for key, value in dict(
                    current.get("fanout_deferred_batch_reasons") or {}
                ).items()
                if key and isinstance(value, (list, tuple, set))
            }
            terminal_fetch_batches = set(
                current.get("terminal_fetch_batch_digests") or ()
            )
            scoped_batch = (
                str(batch_digest)
                if batch_digest and str(batch_digest) in expected_batches
                else ""
            )
            if scoped_batch and outcome == "HTTP_200":
                succeeded_batches.add(scoped_batch)
                deferred_batch_reasons.pop(scoped_batch, None)
            elif scoped_batch and outcome in {
                "REQUEST_REJECTED",
                "RESPONSE_INVALID",
                "EVIDENCE_SIZE_LIMIT",
            }:
                failed_batches.add(scoped_batch)
                deferred_batch_reasons.pop(scoped_batch, None)
            elif scoped_batch and outcome == "QUOTA_DEFERRED":
                deferred_batches.add(scoped_batch)
                if budget_reason:
                    deferred_batch_reasons[scoped_batch] = [str(budget_reason)]
            elif scoped_batch and outcome == "RETRYABLE_ERROR":
                # Retryable transport/provider failures remain unresolved, but
                # they are not quota evidence. Keep them in the failed bucket
                # until an eventual successful retry clears the same digest so
                # a zero-pair proactive batch cannot masquerade as quota-only.
                failed_batches.add(scoped_batch)
                deferred_batch_reasons.pop(scoped_batch, None)
            if (
                completed_split_pairs
                and split_group_digest
                and str(split_group_digest) in expected_batches
            ):
                succeeded_batches.add(str(split_group_digest))
            if split_root_succeeded:
                succeeded_batches.add(split_root_succeeded)
                deferred_batch_reasons.pop(split_root_succeeded, None)
            if split_root_failed:
                failed_batches.add(split_root_failed)
                deferred_batch_reasons.pop(split_root_failed, None)
            if split_group_digest and str(split_group_digest) in expected_batches:
                deferred_batches.discard(str(split_group_digest))
            if split_root_deferred:
                deferred_batches.add(split_root_deferred)
                deferred_batch_reasons[split_root_deferred] = sorted(
                    split_root_deferred_reasons
                )
            if batch_digest and outcome in {
                "HTTP_200",
                "REQUEST_REJECTED",
                "RESPONSE_INVALID",
                "EVIDENCE_SIZE_LIMIT",
                "SPLIT_PENDING",
            }:
                terminal_fetch_batches.add(str(batch_digest))
            failed_batches -= succeeded_batches
            deferred_batches -= succeeded_batches | failed_batches
            deferred_batch_reasons = {
                key: value
                for key, value in deferred_batch_reasons.items()
                if key in deferred_batches
            }
            merged = {
                **current,
                "returned_pairs": sorted(returned),
                "provider_unavailable_pairs": sorted(unavailable),
                "normalization_rejected_pairs": sorted(rejected),
                "attempted_incomplete_pairs": sorted(attempted_incomplete),
                "quota_deferred_pairs": sorted(deferred),
                "failed_pairs": sorted(failed),
                "fanout_succeeded_batch_digests": sorted(succeeded_batches),
                "fanout_failed_batch_digests": sorted(failed_batches),
                "fanout_deferred_batch_digests": sorted(deferred_batches),
                "fanout_deferred_batch_reasons": deferred_batch_reasons,
                "terminal_fetch_batch_digests": sorted(
                    terminal_fetch_batches
                ),
                "region_split_groups": region_split_groups,
                "region_split_conflicts": region_split_conflicts,
                "split_batch_groups": split_batch_groups,
                "split_batch_conflicts": split_batch_conflicts,
                "outcome_counts": {
                    **dict(current.get("outcome_counts") or {}),
                    outcome: int((current.get("outcome_counts") or {}).get(outcome) or 0) + 1,
                },
                "summary_revision": revision + 1,
                "updated_at": observed_at,
            }
            merged_size = ddb_item_size_bytes(merged)
            if merged_size > COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES:
                merged = {
                    **key,
                    "entity_type": "SOCCER_EVENT_COVERAGE_LATEST",
                    "event_key": event_key,
                    "discovery_observed_at": str(
                        current.get("discovery_observed_at") or ""
                    ),
                    "discovery_status": "SUMMARY_SIZE_LIMIT",
                    "discovery_status_observed_at": observed_at,
                    "coverage_error": "DDB_ITEM_SIZE_LIMIT",
                    "coverage_item_size_bytes": merged_size,
                    "plan_version": "",
                    "plan_observed_at": "",
                    "plan_digest": "",
                    "commence_time": current.get("commence_time"),
                    "schedule_revision": current.get("schedule_revision"),
                    "schedule_identity": current.get("schedule_identity"),
                    "required_pairs": [],
                    "probe_pairs": [],
                    "expected_digest": digest([]),
                    "request_markets": [],
                    "returned_pairs": [],
                    "provider_unavailable_pairs": [],
                    "normalization_rejected_pairs": [],
                    "attempted_incomplete_pairs": [],
                    "quota_deferred_pairs": [],
                    "failed_pairs": [],
                    "fanout_expected_batch_digests": [],
                    "fanout_enqueued_batch_digests": [],
                    "fanout_succeeded_batch_digests": [],
                    "fanout_failed_batch_digests": [],
                    "fanout_deferred_batch_digests": [],
                    "fanout_deferred_batch_reasons": {},
                    "terminal_fetch_batch_digests": [],
                    "outcome_counts": {},
                    "summary_revision": revision + 1,
                    "updated_at": observed_at,
                    "expires_at": int(
                        (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                    ),
                }
            try:
                self.ops.put_item(
                    Item=ddb_safe(merged),
                    ConditionExpression=(
                        "plan_observed_at=:plan_at AND plan_digest=:plan_digest "
                        "AND summary_revision=:revision"
                    ),
                    ExpressionAttributeValues=ddb_safe(
                        {
                            ":plan_at": plan_observed_at,
                            ":plan_digest": plan_digest,
                            ":revision": revision,
                        }
                    ),
                )
                return True
            except ClientError as exc:
                if not _is_conditional_failure(exc):
                    raise
        raise RuntimeError("soccer latest coverage summary update contention")

    def put_coverage_fetch(
        self,
        event_key: str,
        payload: Mapping[str, Any],
        *,
        observed_at: str,
        requested_bookmakers: Sequence[str],
        requested_markets: Sequence[str],
        plan_observed_at: str | None = None,
        plan_digest: str | None = None,
        planned_pairs: Sequence[str] = (),
        raw_returned_pairs: Sequence[str] = (),
        outcome: str = "HTTP_200",
        absence_scope_complete: bool = True,
        split_group_digest: str | None = None,
        attempted_regions: Sequence[str] = (),
        split_expected_regions: Sequence[str] = (),
        split_leaf_id: str | None = None,
        split_expected_leaf_ids: Sequence[str] = (),
        split_child_leaf_ids: Sequence[str] = (),
        batch_digest: str | None = None,
        budget_reason: str | None = None,
    ) -> dict[str, Any]:
        budget_reason = str(budget_reason or "")
        if (
            outcome == "QUOTA_DEFERRED"
            and budget_reason not in COVERAGE_EXTERNAL_QUOTA_REASONS
        ):
            # Unknown observation/admission failures are operational defects,
            # not external capacity. Never let a malformed caller mint quota
            # evidence that the API or deployment proof could treat as safe.
            outcome = "RETRYABLE_ERROR"
        payload_returned_pairs = {
            f"{book.get('key')}|{market.get('key')}"
            for book in payload.get("bookmakers") or []
            if book.get("key")
            for market in book.get("markets") or []
            if market.get("key")
        }
        requested_books = {str(value) for value in requested_bookmakers if value}
        requested_market_keys = {str(value) for value in requested_markets if value}
        explicit_planned_pairs = {
                str(pair)
                for pair in planned_pairs
                if "|" in str(pair)
                and str(pair).rsplit("|", 1)[1] in requested_market_keys
                and (
                    not requested_books
                    or str(pair).rsplit("|", 1)[0] in requested_books
                )
            }
        legacy_cartesian_pairs = (
            {
                f"{bookmaker}|{market}"
                for bookmaker in requested_books
                for market in requested_market_keys
            }
            if not plan_digest
            else set()
        )
        expected_request_pairs = sorted(explicit_planned_pairs or legacy_cartesian_pairs)
        attempted = set(expected_request_pairs)
        returned = payload_returned_pairs & attempted
        raw_returned = {
            str(pair) for pair in raw_returned_pairs if pair
        } & attempted
        returned_pairs = sorted(returned)
        raw_pairs = sorted(raw_returned)
        unexpected_returned_pairs = sorted(payload_returned_pairs - attempted)
        unexpected_raw_returned_pairs = sorted(
            {str(pair) for pair in raw_returned_pairs if pair} - attempted
        )
        provider_unavailable = sorted(
            attempted - raw_returned
            if outcome == "HTTP_200" and absence_scope_complete
            else set()
        )
        normalization_rejected = sorted(
            attempted & raw_returned - returned
            if outcome == "HTTP_200"
            else set()
        )
        attempted_incomplete = sorted(
            attempted - returned - set(normalization_rejected)
            if outcome == "HTTP_200" and not absence_scope_complete
            else set()
        )
        quota_deferred = sorted(attempted if outcome == "QUOTA_DEFERRED" else set())
        failed = sorted(
            attempted
            if outcome in {"RETRYABLE_ERROR", "REQUEST_REJECTED", "RESPONSE_INVALID"}
            else set()
        )
        missing = sorted(attempted - returned)
        terminally_classified = set(provider_unavailable) | set(normalization_rejected)
        unresolved = sorted(attempted - returned - terminally_classified)
        identity = digest(
            {
                "observed_at": observed_at,
                "books": list(requested_bookmakers),
                "markets": list(requested_markets),
                "planned_pairs": expected_request_pairs,
                "returned": returned_pairs,
                "raw_returned": raw_pairs,
                "outcome": outcome,
                "budget_reason": budget_reason,
            }
        )
        item = {
            "PK": f"COVERAGE#{event_key}",
            "SK": f"FETCH#{observed_at}#{identity[:16]}",
            "entity_type": "SOCCER_EVENT_COVERAGE_FETCH",
            "event_key": event_key,
            "observed_at": observed_at,
            "plan_observed_at": plan_observed_at,
            "plan_digest": plan_digest,
            "outcome": outcome,
            "budget_reason": budget_reason,
            "absence_scope_complete": bool(absence_scope_complete),
            "split_group_digest": split_group_digest,
            "attempted_regions": list(attempted_regions),
            "split_expected_regions": list(split_expected_regions),
            "split_leaf_id": split_leaf_id,
            "split_expected_leaf_ids": list(split_expected_leaf_ids),
            "split_child_leaf_ids": list(split_child_leaf_ids),
            "batch_digest": batch_digest,
            "requested_bookmakers": list(requested_bookmakers),
            "requested_markets": list(requested_markets),
            "attempted_pairs": expected_request_pairs,
            "raw_returned_pairs": raw_pairs,
            "returned_pairs": returned_pairs,
            "unexpected_raw_returned_pairs": unexpected_raw_returned_pairs,
            "unexpected_returned_pairs": unexpected_returned_pairs,
            "provider_unavailable_pairs": provider_unavailable,
            "normalization_rejected_pairs": normalization_rejected,
            "attempted_incomplete_pairs": attempted_incomplete,
            "quota_deferred_pairs": quota_deferred,
            "failed_pairs": failed,
            "missing_requested_pairs": missing,
            "unresolved_requested_pairs": unresolved,
            "request_classification_complete": bool(expected_request_pairs) and not unresolved,
            "coverage_complete": bool(expected_request_pairs) and not missing,
            "expires_at": int((parse_utc(observed_at) + timedelta(days=30)).timestamp()),
        }
        merge_outcome = outcome
        item_size = ddb_item_size_bytes(item)
        if item_size > COVERAGE_DDB_ITEM_SOFT_LIMIT_BYTES:
            item = {
                "PK": f"COVERAGE#{event_key}",
                "SK": f"FETCH_SIZE_LIMIT#{observed_at}#{identity[:16]}",
                "entity_type": "SOCCER_EVENT_COVERAGE_FETCH_SIZE_LIMIT",
                "event_key": event_key,
                "observed_at": observed_at,
                "plan_observed_at": plan_observed_at,
                "plan_digest": plan_digest,
                "batch_digest": batch_digest,
                "outcome": "EVIDENCE_SIZE_LIMIT",
                "budget_reason": budget_reason,
                "coverage_error": "DDB_ITEM_SIZE_LIMIT",
                "coverage_item_size_bytes": item_size,
                "attempted_pair_count": len(expected_request_pairs),
                "attempted_pair_digest": digest(expected_request_pairs),
                "raw_returned_pair_count": len(raw_pairs),
                "raw_returned_pair_digest": digest(raw_pairs),
                "returned_pair_count": len(returned_pairs),
                "returned_pair_digest": digest(returned_pairs),
                "expires_at": int(
                    (parse_utc(observed_at) + timedelta(days=30)).timestamp()
                ),
            }
            provider_unavailable = []
            normalization_rejected = []
            attempted_incomplete = []
            quota_deferred = []
            failed = list(expected_request_pairs)
            merge_outcome = "EVIDENCE_SIZE_LIMIT"
        self.ops.put_item(Item=ddb_safe(item))
        item["latest_summary_updated"] = self._merge_latest_coverage_attempt(
            event_key=event_key,
            plan_observed_at=plan_observed_at,
            plan_digest=plan_digest,
            observed_at=observed_at,
            attempted_pairs=expected_request_pairs,
            returned_pairs=returned_pairs,
            raw_returned_pairs=raw_pairs,
            provider_unavailable_pairs=provider_unavailable,
            normalization_rejected_pairs=normalization_rejected,
            attempted_incomplete_pairs=attempted_incomplete,
            quota_deferred_pairs=quota_deferred,
            failed_pairs=failed,
            split_group_digest=split_group_digest,
            attempted_regions=attempted_regions,
            split_expected_regions=split_expected_regions,
            split_leaf_id=split_leaf_id,
            split_expected_leaf_ids=split_expected_leaf_ids,
            split_child_leaf_ids=split_child_leaf_ids,
            batch_digest=batch_digest,
            outcome=merge_outcome,
            budget_reason=budget_reason,
        )
        return plain(item)

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
        event_schedule_identity = str(event.get("schedule_identity") or schedule_identity(event))
        payload_schedule_identity = schedule_identity(payload)
        if payload_schedule_identity != event_schedule_identity:
            raise ValueError("provider event-odds response schedule identity mismatch")
        payload_commence = parse_utc(str(payload["commence_time"]))
        if parse_utc(observed_at) >= min(
            parse_utc(str(event["commence_time"])), payload_commence
        ):
            raise ValueError("provider event-odds response arrived at or after kickoff")
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
                "schedule_identity": event_schedule_identity,
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
            "schedule_identity": event_schedule_identity,
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
                    "schedule_identity": event_schedule_identity,
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
                condition = (
                    "attribute_not_exists(SK)"
                    if not current_plain
                    else "attempt_id=:expected"
                )
                values = (
                    None
                    if not current_plain
                    else {":expected": current_plain["attempt_id"]}
                )
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
        schedule_identity: str | None = None,
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
                if schedule_identity is not None and str(row.get("schedule_identity") or "") != str(
                    schedule_identity
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
