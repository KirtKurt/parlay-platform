from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from soccer_audit import soccer_results_status, soccer_source_status, soccer_three_way_probs


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None

HOT_DELTA_THRESHOLD = 0.006
PUBLISH_DELTA_THRESHOLD = 0.018
TARGET_SUCCESS_RATE = Decimal("75")
MODEL_VERSION = "SOC-B1.1-three-way-audit-v1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-headers": "content-type",
            "access-control-allow-methods": "GET,POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _recent_snapshots(limit: int = 40) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    resp = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq("SPORT#soccer"), ScanIndexForward=False, Limit=limit)
    return sorted(resp.get("Items", []), key=lambda x: x.get("asof") or "")


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g.get("game_key") or g.get("id"): g for g in snapshot.get("data", {}).get("games", []) or [] if g.get("game_key") or g.get("id")}


def _book_probs(game: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    consensus = soccer_three_way_probs(game)
    return consensus.get("book_probs") or {}


def _book_agreement(prev: Dict[str, Any], latest: Dict[str, Any], outcome: str) -> Dict[str, Any]:
    prev_probs = _book_probs(prev)
    latest_probs = _book_probs(latest)
    common = sorted(set(prev_probs.keys()) & set(latest_probs.keys()))
    agreeing, disagreeing = [], []
    for book in common:
        delta = latest_probs[book].get(outcome, 0) - prev_probs[book].get(outcome, 0)
        if delta > 0:
            agreeing.append({"book": book, "delta": round(delta, 6)})
        elif delta < 0:
            disagreeing.append({"book": book, "delta": round(delta, 6)})
    return {"common_books": len(common), "agreeing_books": len(agreeing), "disagreeing_books": len(disagreeing), "agreeing": agreeing, "disagreeing": disagreeing}


def _outcome_label(outcome: str, game: Dict[str, Any]) -> str:
    if outcome == "home":
        return game.get("home_team") or "Home"
    if outcome == "away":
        return game.get("away_team") or "Away"
    return "Draw"


def _delta_for_game(prev: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, Any]:
    prev_p = soccer_three_way_probs(prev)
    latest_p = soccer_three_way_probs(latest)
    if prev_p.get("home") is None or latest_p.get("home") is None:
        return {"ok": False, "reason": "missing_three_way_h2h"}

    deltas = {side: latest_p[side] - prev_p[side] for side in ["home", "draw", "away"]}
    hot_outcome = max(deltas, key=lambda side: deltas[side])
    hot_delta = deltas[hot_outcome]
    hot_label = _outcome_label(hot_outcome, latest)
    current_leader = latest_p.get("leader")
    leader_label = _outcome_label(current_leader, latest) if current_leader else None

    reason_codes = []
    if hot_outcome == "draw" and hot_delta > HOT_DELTA_THRESHOLD:
        reason_codes.append("draw_pressure")
    if hot_outcome != current_leader and hot_delta > HOT_DELTA_THRESHOLD:
        reason_codes.append("non_favorite_pressure")
    if hot_outcome == current_leader and hot_delta > HOT_DELTA_THRESHOLD:
        reason_codes.append("favorite_pressure")
    agreement = _book_agreement(prev, latest, hot_outcome)
    if agreement["agreeing_books"] >= 2:
        reason_codes.append("multi_book_move")
    if latest_p.get("leader_gap") is not None and latest_p["leader_gap"] < 0.05:
        reason_codes.append("compressed_three_way_market")

    if hot_delta >= PUBLISH_DELTA_THRESHOLD and len(reason_codes) >= 2:
        prediction_status = "PUBLISHED_MODERATE"
        confidence = "moderate"
    elif hot_delta >= HOT_DELTA_THRESHOLD:
        prediction_status = "WATCHLIST"
        confidence = "volatile"
    else:
        prediction_status = "NO_EDGE"
        confidence = "no_edge"

    if hot_outcome == "draw":
        prediction_text = "Draw pressure/watchlist"
    elif hot_outcome == current_leader:
        prediction_text = f"{hot_label} market pressure"
    else:
        prediction_text = f"{hot_label} upset/watchlist"

    return {
        "ok": True,
        "game_key": latest.get("game_key"),
        "game_id": latest.get("id"),
        "sport_key": latest.get("sport_key"),
        "home_team": latest.get("home_team"),
        "away_team": latest.get("away_team"),
        "commence_time": latest.get("commence_time"),
        "previous_asof": prev.get("_snapshot_asof"),
        "latest_asof": latest.get("_snapshot_asof"),
        "market_type": "THREE_WAY_HOME_DRAW_AWAY",
        "current_leader": current_leader,
        "current_leader_label": leader_label,
        "hot_outcome": hot_outcome,
        "hot_label": hot_label,
        "hot_delta": round(hot_delta, 6),
        "deltas": {side: round(deltas[side], 6) for side in ["home", "draw", "away"]},
        "hot_side_label": f"{hot_label} pressure",
        "prediction": prediction_text,
        "prediction_status": prediction_status,
        "confidence": confidence,
        "reason_codes": reason_codes,
        "book_agreement": agreement,
        "latest_consensus_three_way": {k: latest_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
        "previous_consensus_three_way": {k: prev_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
    }


def soccer_movement_deltas(limit: int = 40) -> Dict[str, Any]:
    snapshots = _recent_snapshots(limit=limit)
    if len(snapshots) < 2:
        return {"ok": True, "sport": "soccer", "count": 0, "message": "Need at least two soccer snapshots."}
    prev_snap = snapshots[-2]
    latest_snap = snapshots[-1]
    prev_games = _game_index(prev_snap)
    latest_games = _game_index(latest_snap)
    deltas = []
    for game_key, latest_game in latest_games.items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        row = _delta_for_game({**prev_game, "_snapshot_asof": prev_snap.get("asof")}, {**latest_game, "_snapshot_asof": latest_snap.get("asof")})
        if row.get("ok"):
            deltas.append(row)
    deltas.sort(key=lambda x: abs(float(x.get("hot_delta") or 0)), reverse=True)
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "previous_asof": prev_snap.get("asof"), "latest_asof": latest_snap.get("asof"), "count": len(deltas), "deltas": deltas}


def _confidence_score(row: Dict[str, Any]) -> int:
    score = 50 + min(35, int(abs(float(row.get("hot_delta") or 0)) * 1000))
    score += min(10, int(row.get("book_agreement", {}).get("agreeing_books", 0)))
    if "compressed_three_way_market" in row.get("reason_codes", []):
        score -= 5
    return max(1, min(95, score))


def soccer_hot_sides(limit: int = 40, store: bool = False) -> Dict[str, Any]:
    data = soccer_movement_deltas(limit=limit)
    rows = []
    now = _now_iso()
    slate_date = _slate_date_et()
    for row in data.get("deltas", []):
        if row.get("prediction_status") == "NO_EDGE":
            continue
        item = {
            "PK": f"PRED#soccer#{slate_date}",
            "SK": f"HOT_SIDE#{row.get('latest_asof')}#{row.get('game_key')}",
            "sport": "soccer",
            "sport_key": row.get("sport_key"),
            "slate_date_et": slate_date,
            "created_at": now,
            "asof": row.get("latest_asof"),
            "previous_asof": row.get("previous_asof"),
            "game_key": row.get("game_key"),
            "game_id": row.get("game_id"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "market": "h2h_three_way",
            "prediction_type": "SOCCER_HOT_SIDE_THREE_WAY_MOVEMENT",
            "prediction_status": row.get("prediction_status"),
            "status": "OPEN",
            "predicted_outcome": row.get("hot_outcome") if row.get("prediction_status", "").startswith("PUBLISHED") else None,
            "hot_outcome": row.get("hot_outcome"),
            "hot_label": row.get("hot_label"),
            "confidence_label": row.get("confidence"),
            "confidence": Decimal(str(_confidence_score(row))),
            "target_success_rate": TARGET_SUCCESS_RATE,
            "reason_codes": row.get("reason_codes", []),
            "explanation": row.get("prediction"),
            "movement": {"hot_delta": Decimal(str(row.get("hot_delta"))), "deltas": {k: Decimal(str(v)) for k, v in row.get("deltas", {}).items()}},
            "evaluation": {},
        }
        rows.append({**row, "stored_prediction_key": item["SK"] if store else None})
        if store and predictions_tbl is not None:
            predictions_tbl.put_item(Item=item)
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "stored": store, "count": len(rows), "target_success_rate": 75, "hot_sides": rows}


def soccer_audit_snapshots(limit: int = 20) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    slate_date = _slate_date_et()
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"AUDIT#soccer#{slate_date}"), ScanIndexForward=False, Limit=limit)
    items = resp.get("Items", [])
    summaries = [i for i in items if i.get("entity_type") == "SNAPSHOT_AUDIT_SUMMARY"]
    game_rows = [i for i in items if i.get("entity_type") == "GAME_SNAPSHOT_AUDIT"]
    return {"ok": True, "sport": "soccer", "slate_date_et": slate_date, "count": len(items), "summary_count": len(summaries), "game_audit_count": len(game_rows), "items": items}


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    params = event.get("queryStringParameters") or {}
    try:
        if method == "GET" and path == "/v1/audit/soccer/snapshots":
            return _resp(200, soccer_audit_snapshots(min(int(params.get("limit") or 20), 100)))
        if method == "GET" and path == "/v1/signals/soccer/deltas":
            return _resp(200, soccer_movement_deltas(min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/predictions/soccer/hot-sides":
            return _resp(200, soccer_hot_sides(min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true"))
        if method == "GET" and path == "/v1/results/soccer/status":
            return _resp(200, soccer_results_status(params.get("slate_date_et")))
        if method == "GET" and path == "/v1/sources/soccer/status":
            return _resp(200, soccer_source_status())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "soccer", "error": str(exc)})
