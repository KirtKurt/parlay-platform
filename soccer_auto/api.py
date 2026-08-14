"""Read-only operational API for the isolated soccer_auto service."""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

from boto3.dynamodb.conditions import Key

from .canonical import schedule_identity
from .odds_api import provider_safety_config
from .storage import SoccerStore, plain


PUBLIC_BINDING_VERSION = "soccer-auto-public-prediction-binding-v1"


def _response(status: int, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(plain(dict(body)), sort_keys=True, default=str),
    }


def status(store: SoccerStore) -> dict[str, Any]:
    state = store.ops.get_item(Key={"PK": "AUTONOMY", "SK": "STATE"}, ConsistentRead=True).get("Item")
    provider_429_telemetry = store.provider_429_status()
    if not state:
        return {
            "ok": True,
            "system": "soccer_auto",
            "authority": "BOOTSTRAPPING",
            "automatic_prediction_allowed": False,
            "promotion_blocked": True,
            "reason": "AUTONOMOUS_CONTROLLER_HAS_NOT_COMPLETED_FIRST_CYCLE",
            "shared_provider_safety": provider_safety_config(),
            "distributed_rate_limit_state": store.rate_limit_status(),
            "provider_429_telemetry": provider_429_telemetry,
            "historical_backfill": _historical_status(store),
        }
    return {
        "ok": True,
        **plain(state),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": provider_429_telemetry,
        "historical_backfill": _historical_status(store),
    }


def predictions(store: SoccerStore, limit: int = 100) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    query: dict[str, Any] = {
        "IndexName": "ByPredictionTime",
        "KeyConditionExpression": Key("GSI1PK").eq("SOCCER_PREDICTIONS"),
        "ScanIndexForward": False,
        "Limit": 500,
    }
    raw_cap = min(2000, max(500, limit * 4))
    for _ in range(4):
        response = store.predictions.query(**query)
        rows.extend(plain(row) for row in response.get("Items") or [])
        cursor = response.get("LastEvaluatedKey")
        if not cursor or len(rows) >= raw_cap:
            break
        query["ExclusiveStartKey"] = cursor
    rows = rows[:raw_cap]
    current_events: dict[str, Mapping[str, Any] | None] = {}
    public_bindings: dict[tuple[str, int, str, str], Mapping[str, Any] | None] = {}
    public_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    suppressed = 0
    for row in rows:
        if (
            row.get("prediction_status") not in {"PUBLISHED", "NO_PICK"}
            or row.get("model_authority") != "CHAMPION"
            or row.get("immutable") is not True
        ):
            suppressed += 1
            continue
        event_key = str(row.get("event_key") or "")
        if event_key not in current_events:
            current_events[event_key] = store.get_event(event_key)
        current = current_events[event_key] or {}
        try:
            revision = int(row.get("schedule_revision") or 0)
            horizon = str(row.get("horizon") or "")
            target = str(row.get("target") or "")
            row_identity = str(row.get("schedule_identity") or "")
            current_identity = str(current.get("schedule_identity") or "")
            same_schedule = bool(
                event_key
                and current
                and revision > 0
                and revision == int(current.get("schedule_revision") or 0)
                and row_identity
                and current_identity
                and row_identity == current_identity
                and row_identity == schedule_identity(row)
                and current_identity == schedule_identity(current)
                and str(row.get("commence_time") or "")
                == str(current.get("commence_time") or "")
                and horizon == "T45"
                and target == "result_1x2"
            )
        except (KeyError, TypeError, ValueError):
            same_schedule = False
        if not same_schedule:
            suppressed += 1
            continue
        binding_key = (event_key, revision, horizon, target)
        if binding_key not in public_bindings:
            binding = store.ops.get_item(
                Key={
                    "PK": f"PUBLIC_PREDICTION_BINDING#{event_key}",
                    "SK": f"REV#{revision}#HORIZON#{horizon}#TARGET#{target}",
                },
                ConsistentRead=True,
            ).get("Item")
            public_bindings[binding_key] = plain(binding) if binding else None
        binding = public_bindings[binding_key] or {}
        try:
            binding_matches = bool(
                binding
                and binding.get("entity_type") == "SOCCER_PUBLIC_PREDICTION_BINDING"
                and binding.get("binding_version") == PUBLIC_BINDING_VERSION
                and binding.get("immutable") is True
                and str(binding.get("event_key") or "") == event_key
                and str(binding.get("event_id") or "") == str(row.get("event_id") or "")
                and str(binding.get("sport_key") or "") == str(row.get("sport_key") or "")
                and str(binding.get("commence_time") or "")
                == str(row.get("commence_time") or "")
                and int(binding.get("schedule_revision") or 0) == revision
                and str(binding.get("schedule_identity") or "") == row_identity
                and str(binding.get("horizon") or "") == horizon
                and str(binding.get("target") or "") == target
                and str(binding.get("lock_sk") or "")
                == f"LOCK#{horizon}#REV#{revision}#TARGET#{target}"
                and bool(str(row.get("feature_hash") or ""))
                and str(binding.get("feature_hash") or "")
                == str(row.get("feature_hash") or "")
                and bool(str(row.get("model_digest") or ""))
                and str(binding.get("model_digest") or "")
                == str(row.get("model_digest") or "")
            )
        except (TypeError, ValueError):
            binding_matches = False
        if not binding_matches:
            suppressed += 1
            continue
        identity = (event_key, horizon, target)
        existing = public_rows.get(identity)
        if existing is None or str(row.get("created_at") or "") < str(
            existing.get("created_at") or ""
        ):
            public_rows[identity] = row
        else:
            suppressed += 1
    visible = sorted(
        public_rows.values(),
        key=lambda row: (str(row.get("commence_time") or ""), str(row.get("event_key") or "")),
        reverse=True,
    )[:limit]
    return {
        "ok": True,
        "system": "soccer_auto",
        "count": len(visible),
        "predictions": visible,
        "audit_rows_suppressed": suppressed,
        "public_contract": "one immutable current-schedule T45 public decision per event",
    }


def _query_partition(table: Any, pk: str, *, limit: int = 1000) -> tuple[list[dict[str, Any]], bool]:
    response = table.query(
        KeyConditionExpression=Key("PK").eq(pk),
        ConsistentRead=True,
        Limit=max(1, int(limit)),
    )
    return [plain(row) for row in response.get("Items") or []], bool(response.get("LastEvaluatedKey"))


def _historical_status(store: SoccerStore) -> dict[str, Any]:
    cursors, truncated = _query_partition(store.ops, "HISTORICAL_CURSOR", limit=1000)
    detail = [row for row in cursors if not str(row.get("SK") or "").endswith("#SUMMARY")]
    completed = [row for row in detail if row.get("status") == "COMPLETE"]
    progressing = [
        row
        for row in detail
        if row.get("status") in {"RUNNING", "PENDING", "QUOTA_DEFERRED"}
    ]
    latest_progress = max(
        (str(row.get("last_progress_at") or row.get("updated_at") or "") for row in detail),
        default="",
    )
    return {
        "enabled": os.getenv("SOCCER_AUTO_HISTORICAL_BACKFILL_ENABLED", "true").lower()
        == "true",
        "mode": "RAW_ARCHIVE_ONLY",
        "state": "COMPLETE" if detail and len(completed) == len(detail) else "RUNNING" if progressing else "PENDING",
        "cursor_rows": len(detail),
        "completed_cursor_rows": len(completed),
        "calls_completed": sum(int(row.get("calls_completed") or 0) for row in detail),
        "latest_progress_at": latest_progress or None,
        "cursors_truncated": truncated,
        "historical_training_rows": 0,
        "training_note": "Raw historical odds remain ineligible until joined to authoritative final results with point-in-time T45 materialization.",
    }


def _bounded_ops_diagnostics(
    store: SoccerStore,
    *,
    page_limit: int = 4,
    row_limit: int = 2000,
) -> tuple[list[dict[str, Any]], bool]:
    """Bound API work so the coverage endpoint cannot full-scan itself into a 504."""
    entity_types = {
        "SOCCER_MARKET_INVENTORY",
        "SOCCER_EVENT_COVERAGE_PLAN",
        "SOCCER_EVENT_COVERAGE_FETCH",
        "SOCCER_COLLECTION_FAILURE",
    }
    rows: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {"Limit": 500}
    cursor = None
    for _ in range(max(1, page_limit)):
        response = store.ops.scan(**kwargs)
        for item in response.get("Items") or []:
            row = plain(item)
            if row.get("entity_type") in entity_types:
                rows.append(row)
                if len(rows) >= max(1, row_limit):
                    return rows, True
        cursor = response.get("LastEvaluatedKey")
        if not cursor:
            return rows, False
        kwargs["ExclusiveStartKey"] = cursor
    return rows, bool(cursor)


def _latest_cycle_coverage(
    coverage_plans: list[Mapping[str, Any]],
    coverage_fetches: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Reconcile only each event's latest discovery/fetch cycle."""
    latest_by_event: dict[str, Mapping[str, Any]] = {}
    for plan in coverage_plans:
        event_key = str(plan.get("event_key") or "")
        observed_at = str(plan.get("observed_at") or "")
        if event_key and (
            event_key not in latest_by_event
            or observed_at > str(latest_by_event[event_key].get("observed_at") or "")
        ):
            latest_by_event[event_key] = plan
    returned_by_cycle: dict[tuple[str, str], set[str]] = {}
    for fetch in coverage_fetches:
        cycle = (str(fetch.get("event_key") or ""), str(fetch.get("plan_observed_at") or ""))
        if all(cycle):
            returned_by_cycle.setdefault(cycle, set()).update(
                str(pair) for pair in fetch.get("returned_pairs") or []
            )
    expected_pairs: set[str] = set()
    returned_pairs: set[str] = set()
    cycles = []
    for event_key, plan in latest_by_event.items():
        plan_at = str(plan.get("observed_at") or "")
        expected = {str(pair) for pair in plan.get("expected_pairs") or []}
        returned = returned_by_cycle.get((event_key, plan_at), set())
        missing = expected - returned
        expected_pairs.update(f"{event_key}|{pair}" for pair in expected)
        returned_pairs.update(f"{event_key}|{pair}" for pair in returned & expected)
        cycles.append(
            {
                "event_key": event_key,
                "plan_observed_at": plan_at,
                "expected": len(expected),
                "fetched": len(returned & expected),
                "missing": len(missing),
                "complete": bool(expected) and not missing,
            }
        )
    cycles.sort(key=lambda row: (row["plan_observed_at"], row["event_key"]), reverse=True)
    return {
        "expected_pairs": expected_pairs,
        "returned_pairs": returned_pairs,
        "missing_pairs": expected_pairs - returned_pairs,
        "cycles": cycles,
    }


def coverage(store: SoccerStore) -> dict[str, Any]:
    competitions = store.list_competitions()
    diagnostic_rows, diagnostics_truncated = _bounded_ops_diagnostics(store)
    inventories = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_MARKET_INVENTORY"]
    books = set()
    markets = set()
    for row in inventories:
        for book, detail in (row.get("inventory") or {}).items():
            books.add(book)
            markets.update(detail.get("markets") or [])
    cursors, cursors_truncated = _query_partition(store.ops, "HISTORICAL_CURSOR")
    daily_windows, windows_truncated = _query_partition(store.ops, "COLLECTION_WINDOW")
    daily_windows.sort(key=lambda row: row.get("match_day") or "", reverse=True)
    coverage_plans = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_EVENT_COVERAGE_PLAN"]
    coverage_fetches = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_EVENT_COVERAGE_FETCH"]
    collection_failures = [row for row in diagnostic_rows if row.get("entity_type") == "SOCCER_COLLECTION_FAILURE"]
    quota_blocks, quota_blocks_truncated = _query_partition(store.ops, "QUOTA_GUARD")
    rate_limit_blocks, rate_blocks_truncated = _query_partition(store.ops, "RATE_LIMIT_GUARD")
    cycle_coverage = _latest_cycle_coverage(coverage_plans, coverage_fetches)
    expected_pairs = cycle_coverage["expected_pairs"]
    returned_pairs = cycle_coverage["returned_pairs"]
    missing_pairs = sorted(cycle_coverage["missing_pairs"])
    return {
        "ok": True,
        "system": "soccer_auto",
        "competitions": {
            "known": len(competitions),
            "active": sum(bool(row.get("active")) for row in competitions),
            "rows": competitions,
        },
        "live_inventory": {
            "event_inventory_observations": len(inventories),
            "unique_bookmakers_seen": len(books),
            "bookmakers_seen": sorted(books),
            "unique_markets_seen": len(markets),
            "markets_seen": sorted(markets),
            "expected_event_bookmaker_market_pairs": len(expected_pairs),
            "fetched_event_bookmaker_market_pairs": len(returned_pairs),
            "missing_event_bookmaker_market_pairs": len(missing_pairs),
            "missing_pair_sample": missing_pairs[:500],
            "latest_event_cycles": cycle_coverage["cycles"][:500],
            "incomplete_latest_event_cycles": sum(
                not row["complete"] for row in cycle_coverage["cycles"]
            ),
            "collection_failures": len(collection_failures),
            "permanent_collection_failures": sum(bool(row.get("permanent")) for row in collection_failures),
            "quota_guard_blocks": len(quota_blocks),
            "distributed_rate_limit_blocks": len(rate_limit_blocks),
            "coverage_complete": (
                bool(expected_pairs)
                and not missing_pairs
                and not any(row.get("permanent") for row in collection_failures)
                and not diagnostics_truncated
            ),
            "diagnostics_truncated": diagnostics_truncated,
        },
        "historical_cursors": cursors,
        "historical_backfill": _historical_status(store),
        "response_truncated": any(
            (
                diagnostics_truncated,
                cursors_truncated,
                windows_truncated,
                quota_blocks_truncated,
                rate_blocks_truncated,
            )
        ),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": store.provider_429_status(),
        "daily_collection_windows": daily_windows[:45],
        "collection_contract": {
            "match_day_timezone": "America/New_York",
            "opens": "10 hours before the first kickoff of each match-day",
            "no_early_market_or_odds_calls": True,
            "cadence": "15 minutes after the window opens and 5 minutes inside T-6h; pre-match only",
        },
        "historical_label_limit": "The Odds API historical odds do not include final results; unlabeled historical snapshots remain training-ineligible.",
    }


def models(store: SoccerStore) -> dict[str, Any]:
    rows = store.model_items()
    return {
        "ok": True,
        "system": "soccer_auto",
        "count": len(rows),
        "models": rows,
    }


def api_handler(event: Mapping[str, Any], context: Any) -> dict[str, Any]:
    try:
        store = SoccerStore()
        path = str(event.get("rawPath") or event.get("path") or "")
        params = event.get("queryStringParameters") or {}
        if path == "/v1/soccer-auto/status":
            return _response(200, status(store))
        if path == "/v1/soccer-auto/predictions":
            return _response(200, predictions(store, min(500, max(1, int(params.get("limit") or 100)))))
        if path == "/v1/soccer-auto/coverage":
            return _response(200, coverage(store))
        if path == "/v1/soccer-auto/models":
            return _response(200, models(store))
        return _response(404, {"ok": False, "system": "soccer_auto", "error": "route_not_found"})
    except Exception as exc:
        return _response(500, {"ok": False, "system": "soccer_auto", "error": str(exc)})
