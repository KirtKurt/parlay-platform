import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key


dynamodb = boto3.resource("dynamodb")
TABLE_NAME = os.environ.get("SNAPSHOTS_TABLE", "")


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def out(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-member-id",
        },
        "body": json.dumps(clean(body)),
    }


def table():
    if not TABLE_NAME:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    return dynamodb.Table(TABLE_NAME)


def query_params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def days_back(days: int) -> List[str]:
    days = max(1, min(int(days or 7), 30))
    today = datetime.now(timezone.utc).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def load_events(days: int, limit_per_day: int = 750) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    for day in days_back(days):
        result = table().query(KeyConditionExpression=Key("PK").eq(f"ANALYTICS#{day}"), Limit=limit_per_day)
        events.extend(result.get("Items") or [])
    events.sort(key=lambda row: row.get("createdAt", ""), reverse=True)
    return events


def metadata(event: Dict[str, Any]) -> Dict[str, Any]:
    raw = event.get("metadata") or {}
    return raw if isinstance(raw, dict) else {}


def count_by(items: List[Dict[str, Any]], key_name: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        key = str(item.get(key_name) or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return counts


def risk_band(score: Any) -> str:
    try:
        value = float(score)
    except Exception:
        return "unknown"
    if value >= 75:
        return "high"
    if value >= 50:
        return "medium"
    if value >= 1:
        return "low"
    return "unknown"


def pct(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return Decimal(str(round((numerator / denominator) * 100, 2)))


def bool_from_meta(meta: Dict[str, Any], *names: str) -> Optional[bool]:
    for name in names:
        if name in meta:
            value = meta.get(name)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                if value.lower() in {"true", "yes", "hit", "won", "contained"}:
                    return True
                if value.lower() in {"false", "no", "miss", "lost", "not_contained"}:
                    return False
    return None


def handle_algorithm(event: Dict[str, Any]) -> Dict[str, Any]:
    q = query_params(event)
    try:
        days = max(1, min(int(q.get("days") or 7), 30))
    except Exception:
        days = 7
    events = load_events(days)

    scans = [e for e in events if e.get("eventType") == "slip_scanned"]
    builds = [e for e in events if e.get("eventType") == "parlay_built"]
    refusals = [e for e in events if e.get("eventType") == "parlay_refused"]

    risk_bands: Dict[str, int] = {}
    recommended_actions: Dict[str, int] = {}
    refusal_reasons: Dict[str, int] = {}
    outcome_rows: List[Dict[str, Any]] = []

    for scan in scans:
        meta = metadata(scan)
        band = risk_band(meta.get("riskScore") or meta.get("risk_score") or meta.get("score"))
        risk_bands[band] = risk_bands.get(band, 0) + 1
        action = str(meta.get("recommendedAction") or meta.get("recommended_action") or meta.get("action") or "unknown")
        recommended_actions[action] = recommended_actions.get(action, 0) + 1

    for item in refusals:
        meta = metadata(item)
        reason = str(meta.get("reason") or meta.get("refusalReason") or meta.get("refusal_reason") or "unknown")
        refusal_reasons[reason] = refusal_reasons.get(reason, 0) + 1

    for item in events:
        meta = metadata(item)
        model_hit = bool_from_meta(meta, "modelHit", "model_hit", "hit")
        top3 = bool_from_meta(meta, "top3Contained", "top_3_contained", "top3_contained")
        weakest_leg_hit = bool_from_meta(meta, "weakestLegHit", "weakest_leg_hit")
        if model_hit is None and top3 is None and weakest_leg_hit is None:
            continue
        outcome_rows.append({
            "eventId": item.get("eventId"),
            "eventType": item.get("eventType"),
            "createdAt": item.get("createdAt"),
            "sport": item.get("sport"),
            "modelHit": model_hit,
            "top3Contained": top3,
            "weakestLegHit": weakest_leg_hit,
            "metadata": meta,
        })

    hit_count = sum(1 for row in outcome_rows if row.get("modelHit") is True)
    miss_count = sum(1 for row in outcome_rows if row.get("modelHit") is False)
    top3_yes = sum(1 for row in outcome_rows if row.get("top3Contained") is True)
    top3_no = sum(1 for row in outcome_rows if row.get("top3Contained") is False)
    weakest_yes = sum(1 for row in outcome_rows if row.get("weakestLegHit") is True)
    weakest_no = sum(1 for row in outcome_rows if row.get("weakestLegHit") is False)

    return out(200, {
        "ok": True,
        "dashboard": "algorithm_performance",
        "windowDays": days,
        "totalAnalyticsEvents": len(events),
        "summary": {
            "slipsScanned": len(scans),
            "parlaysBuilt": len(builds),
            "parlaysRefused": len(refusals),
            "outcomesLogged": len(outcome_rows),
            "modelHits": hit_count,
            "modelMisses": miss_count,
            "modelHitPct": pct(hit_count, hit_count + miss_count),
            "top3Contained": top3_yes,
            "top3Missed": top3_no,
            "top3ContainmentPct": pct(top3_yes, top3_yes + top3_no),
            "weakestLegHits": weakest_yes,
            "weakestLegMisses": weakest_no,
            "weakestLegHitPct": pct(weakest_yes, weakest_yes + weakest_no),
        },
        "riskBands": risk_bands,
        "recommendedActions": recommended_actions,
        "refusalReasons": refusal_reasons,
        "recentOutcomes": outcome_rows[:50],
        "notes": [
            "No outcomes are inferred. Results appear only when outcome metadata is logged.",
            "Supported outcome metadata keys include modelHit, top3Contained, and weakestLegHit.",
        ],
    })


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/analytics") or path.startswith("/v1/admin/analytics")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/analytics/algorithm", "/v1/admin/analytics/algorithm"} and method == "GET":
        return handle_algorithm(event)
    return None
