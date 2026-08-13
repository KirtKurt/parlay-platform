"""Read-only operational API for the isolated soccer_auto service."""
from __future__ import annotations

import json
from typing import Any, Mapping

from boto3.dynamodb.conditions import Attr, Key

from .odds_api import provider_safety_config
from .storage import SoccerStore, plain


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
        }
    return {
        "ok": True,
        **plain(state),
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": provider_429_telemetry,
    }


def predictions(store: SoccerStore, limit: int = 100) -> dict[str, Any]:
    response = store.predictions.query(
        IndexName="ByPredictionTime",
        KeyConditionExpression=Key("GSI1PK").eq("SOCCER_PREDICTIONS"),
        ScanIndexForward=False,
        Limit=limit,
    )
    rows = [plain(row) for row in response.get("Items") or []]
    return {
        "ok": True,
        "system": "soccer_auto",
        "count": min(len(rows), limit),
        "predictions": rows[:limit],
    }


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
    inventories = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_MARKET_INVENTORY"),
        )
    ]
    books = set()
    markets = set()
    for row in inventories:
        for book, detail in (row.get("inventory") or {}).items():
            books.add(book)
            markets.update(detail.get("markets") or [])
    cursors = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("PK").eq("HISTORICAL_CURSOR"),
        )
    ]
    daily_windows = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("PK").eq("COLLECTION_WINDOW"),
        )
    ]
    daily_windows.sort(key=lambda row: row.get("match_day") or "", reverse=True)
    coverage_plans = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_EVENT_COVERAGE_PLAN"),
        )
    ]
    coverage_fetches = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_EVENT_COVERAGE_FETCH"),
        )
    ]
    collection_failures = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_COLLECTION_FAILURE"),
        )
    ]
    quota_blocks = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_SHARED_PROVIDER_QUOTA_GUARD"),
        )
    ]
    rate_limit_blocks = [
        row
        for row in store.scan_all(
            store.ops,
            FilterExpression=Attr("entity_type").eq("SOCCER_DISTRIBUTED_RATE_LIMIT_BLOCK"),
        )
    ]
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
            ),
        },
        "historical_cursors": cursors,
        "shared_provider_safety": provider_safety_config(),
        "distributed_rate_limit_state": store.rate_limit_status(),
        "provider_429_telemetry": store.provider_429_status(),
        "daily_collection_windows": daily_windows[:45],
        "collection_contract": {
            "match_day_timezone": "America/New_York",
            "opens": "10 hours before the first kickoff of each match-day",
            "no_early_market_or_odds_calls": True,
            "cadence": "15 minutes minimum, 5 minutes inside T-6h, 1 minute in-play",
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
