from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo
from datetime import datetime

import boto3
from boto3.dynamodb.conditions import Key

from mlb_advanced_context import advanced_context_status, build_advanced_context, enrich_row_with_advanced_context
from mlb_signal_api import (
    _build_three_leg_parlay,
    _confidence_label,
    _confidence_score,
    _delta_for_game,
    _enrich_attempted_winner,
    _game_index,
    _prediction_item,
    _simple_no_edge_row,
    _to_ddb,
    results_status as _base_results_status,
)

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")

TARGET_SUCCESS_RATE = Decimal("75")
MLB_PULL_MODE = "ROLLING_15_MIN_ONLY"
MLB_PULL_T = "HOT"


dynamodb = boto3.resource("dynamodb")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.utcnow().replace(tzinfo=None).isoformat() + "+00:00"


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


def _params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def _game_date_from_params(params: Dict[str, str]) -> str:
    # Explicit game_date_et is preferred. slate_date_et is accepted for backward compatibility.
    return params.get("game_date_et") or params.get("slate_date_et") or _today_et()


def _recent_snapshots_for_date(game_date: str, limit: int = 40, t: Optional[str] = MLB_PULL_T) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    pk = f"SPORT#mlb#DATE#{game_date}"
    if t:
        key_condition = Key("PK").eq(pk) & Key("SK").begins_with(f"{t}#GAME_DATE#{game_date}")
    else:
        key_condition = Key("PK").eq(pk)
    resp = snapshots_tbl.query(KeyConditionExpression=key_condition, ScanIndexForward=False, Limit=limit)
    return sorted(resp.get("Items", []), key=lambda x: x.get("asof") or "")


def _latest_games_for_date(game_date: str, limit: int = 40) -> Dict[str, Dict[str, Any]]:
    snapshots = _recent_snapshots_for_date(game_date, limit=limit, t=MLB_PULL_T)
    latest_snapshot = snapshots[-1] if snapshots else {}
    return _game_index(latest_snapshot) if latest_snapshot else {}


def movement_deltas(game_date: str, limit: int = 40) -> Dict[str, Any]:
    # MLB-B1.0 now reads only 15-minute HOT pull history. Legacy T1/T2/T3/T4 snapshots are ignored.
    snapshots = _recent_snapshots_for_date(game_date, limit=limit, t=MLB_PULL_T)
    if len(snapshots) < 2:
        return {
            "ok": True,
            "sport": "mlb",
            "game_date_et": game_date,
            "date_isolated": True,
            "pull_mode": MLB_PULL_MODE,
            "snapshot_t_filter": MLB_PULL_T,
            "count": 0,
            "message": "Need at least two 15-minute HOT MLB snapshots for this game date.",
        }
    prev_snap = snapshots[-2]
    latest_snap = snapshots[-1]
    prev_games = _game_index(prev_snap)
    latest_games = _game_index(latest_snap)
    deltas: List[Dict[str, Any]] = []
    for game_key, latest_game in latest_games.items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        prev_game = {**prev_game, "_snapshot_asof": prev_snap.get("asof")}
        latest_game = {**latest_game, "_snapshot_asof": latest_snap.get("asof")}
        row = _delta_for_game(prev_game, latest_game)
        if row.get("ok"):
            row["game_date_et"] = game_date
            row["date_isolated"] = True
            row["pull_mode"] = MLB_PULL_MODE
            row["snapshot_t_filter"] = MLB_PULL_T
            deltas.append(row)
    deltas.sort(key=lambda x: abs(float(x.get("hot_delta") or 0)), reverse=True)
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "snapshot_partition": f"SPORT#mlb#DATE#{game_date}",
        "previous_asof": prev_snap.get("asof"),
        "latest_asof": latest_snap.get("asof"),
        "count": len(deltas),
        "deltas": deltas,
    }


def _advanced_counts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    eligible = [row for row in rows if row.get("advanced_eligible")]
    blockers: Dict[str, int] = {}
    for row in rows:
        for blocker in row.get("advanced_blockers") or []:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    return {
        "eligible_count": len(eligible),
        "blocked_count": len(rows) - len(eligible),
        "blockers": blockers,
        "policy": "Advanced eligibility requires every requested MLB context source. Missing context blocks ADVANCED_ELIGIBLE but does not hide market-only rows.",
    }


def _attach_parlay_advanced_context(parlay: Dict[str, Any], rows_by_key: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(parlay, dict) or not parlay.get("ok"):
        return parlay
    leg_contexts = []
    blockers = set()
    for leg in parlay.get("legs") or []:
        row = rows_by_key.get(leg.get("game_key")) or {}
        context = row.get("advanced_context") or {}
        leg_blockers = row.get("advanced_blockers") or []
        blockers.update(leg_blockers)
        leg_contexts.append({
            "game_key": leg.get("game_key"),
            "match": leg.get("match"),
            "advanced_eligible": bool(row.get("advanced_eligible")),
            "advanced_blockers": leg_blockers,
            "confirmed_probable_pitchers": context.get("confirmed_probable_pitchers"),
            "venue": context.get("venue"),
            "closing_line_value": context.get("closing_line_value"),
        })
    parlay["advanced_context"] = {
        "advanced_eligible": bool(leg_contexts) and all(item.get("advanced_eligible") for item in leg_contexts),
        "blockers": sorted(blockers),
        "leg_contexts": leg_contexts,
        "policy": "This parlay is not ADVANCED_ELIGIBLE until every leg has FIP/xFIP, wRC+, handedness splits, probable pitchers, bullpen, lineups, weather/roof, park factors, injuries/news, public handle, and CLV connected.",
    }
    return parlay


def hot_sides(game_date: str, limit: int = 40, store: bool = False, include_no_edge: bool = True) -> Dict[str, Any]:
    data = movement_deltas(game_date=game_date, limit=limit)
    snapshots = _recent_snapshots_for_date(game_date, limit=limit, t=MLB_PULL_T)
    latest_snapshot = snapshots[-1] if snapshots else {}
    latest_games = _game_index(latest_snapshot) if latest_snapshot else {}
    rows_by_key: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    actionable_rows: List[Dict[str, Any]] = []
    stored_count = 0
    storage_errors: List[str] = []
    now = _now_iso()
    status_counts: Dict[str, int] = {}

    for row in data.get("deltas", []):
        latest_game = latest_games.get(row.get("game_key"))
        status = row.get("prediction_status") or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1
        is_actionable = status != "NO_EDGE"
        if not include_no_edge and not is_actionable:
            continue
        row = {**row, "display_confidence_scores": True, "confidence_score": _confidence_score(row), "confidence_label": _confidence_label(row), "game_date_et": game_date, "date_isolated": True, "pull_mode": MLB_PULL_MODE, "snapshot_t_filter": MLB_PULL_T}
        row = _enrich_attempted_winner(row, latest_game)
        row = enrich_row_with_advanced_context(game_date, row, latest_game)
        rows_by_key[row.get("game_key")] = row

    # Guarantee one attempted game-winner row for every available MLB moneyline game in this actual game-date partition.
    for game_key, game in latest_games.items():
        if game_key not in rows_by_key:
            simple = _simple_no_edge_row(game, latest_snapshot.get("asof"))
            if simple:
                simple = {**simple, "game_date_et": game_date, "date_isolated": True, "pull_mode": MLB_PULL_MODE, "snapshot_t_filter": MLB_PULL_T}
                simple = _enrich_attempted_winner(simple, game)
                simple = enrich_row_with_advanced_context(game_date, simple, game)
                rows_by_key[game_key] = simple

    for row in rows_by_key.values():
        status = row.get("prediction_status") or "UNKNOWN"
        is_actionable = status != "NO_EDGE"
        if is_actionable:
            actionable_rows.append(row)
        item = _prediction_item(row, game_date, now)
        item["game_date_et"] = game_date
        item["date_isolated"] = True
        item["pull_mode"] = MLB_PULL_MODE
        item["snapshot_t_filter"] = MLB_PULL_T
        item["snapshot_partition"] = f"SPORT#mlb#DATE#{game_date}"
        item["advanced_context"] = row.get("advanced_context")
        item["advanced_eligible"] = row.get("advanced_eligible")
        item["advanced_blockers"] = row.get("advanced_blockers")
        stored_prediction_key = None
        if store:
            if predictions_tbl is None:
                storage_errors.append("PREDICTIONS_TABLE not configured")
            else:
                predictions_tbl.put_item(Item=_to_ddb(item))
                stored_count += 1
                stored_prediction_key = item["SK"]
        rows.append({**row, "stored_prediction_key": stored_prediction_key})

    rows.sort(key=lambda x: (x.get("prediction_status") != "NO_EDGE", x.get("advanced_eligible") is True, x.get("confidence_score") or 0, (x.get("favorite") or {}).get("gap") or 0), reverse=True)
    parlay = _build_three_leg_parlay(rows, latest_games)
    if isinstance(parlay, dict):
        parlay["game_date_et"] = game_date
        parlay["date_isolated"] = True
        parlay["pull_mode"] = MLB_PULL_MODE
        parlay["snapshot_t_filter"] = MLB_PULL_T
        parlay["snapshot_partition"] = f"SPORT#mlb#DATE#{game_date}"
        parlay = _attach_parlay_advanced_context(parlay, {row.get("game_key"): row for row in rows})

    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "snapshot_partition": f"SPORT#mlb#DATE#{game_date}",
        "stored": store,
        "stored_count": stored_count,
        "storage_status": "CONNECTED" if store and predictions_tbl is not None else ("NOT_REQUESTED" if not store else "NOT_CONFIGURED"),
        "storage_errors": storage_errors,
        "count": len(rows),
        "movement_count": data.get("count", 0),
        "individual_prediction_count": len(rows),
        "actionable_count": len(actionable_rows),
        "status_counts": status_counts,
        "advanced_context_status": advanced_context_status(),
        "advanced_context_counts": _advanced_counts(rows),
        "target_success_rate": 75,
        "display_confidence_scores": True,
        "message": "MLB game-winner attempts and 3-leg parlay are built only from date-isolated 15-minute HOT pull history. Advanced context is scored into eligibility and blocks ADVANCED_ELIGIBLE when required feeds are missing.",
        "previous_asof": data.get("previous_asof"),
        "latest_asof": latest_snapshot.get("asof") or data.get("latest_asof"),
        "game_predictions": rows,
        "three_leg_parlay": parlay,
        "hot_sides": rows,
    }


def audit_snapshots(game_date: str, limit: int = 20) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"AUDIT#mlb#{game_date}"), ScanIndexForward=False, Limit=limit)
    items = resp.get("Items", [])
    summaries = [i for i in items if i.get("entity_type") == "SNAPSHOT_AUDIT_SUMMARY"]
    game_rows = [i for i in items if i.get("entity_type") == "GAME_SNAPSHOT_AUDIT"]
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "snapshot_partition": f"SPORT#mlb#DATE#{game_date}",
        "count": len(items),
        "summary_count": len(summaries),
        "game_audit_count": len(game_rows),
        "items": items,
    }


def audit_game(game_date: str, game_key: Optional[str], limit: int = 50) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"AUDIT#mlb#{game_date}"), ScanIndexForward=False, Limit=limit)
    items = [i for i in resp.get("Items", []) if i.get("game_key") == game_key] if game_key else resp.get("Items", [])
    return {
        "ok": True,
        "sport": "mlb",
        "game_date_et": game_date,
        "date_isolated": True,
        "pull_mode": MLB_PULL_MODE,
        "snapshot_t_filter": MLB_PULL_T,
        "game_key": game_key,
        "count": len(items),
        "items": items,
    }


def game_context(game_date: str, game_key: str, limit: int = 40) -> Dict[str, Any]:
    games = _latest_games_for_date(game_date, limit=limit)
    game = games.get(game_key)
    if not game:
        return {"ok": False, "sport": "mlb", "game_date_et": game_date, "game_key": game_key, "error": "Game not found in latest 15-minute HOT snapshot."}
    return {"ok": True, "sport": "mlb", "game_date_et": game_date, "game_key": game_key, "advanced_context": build_advanced_context(game_date, game, None)}


def source_status() -> Dict[str, Any]:
    advanced = advanced_context_status()
    return {
        "ok": True,
        "sport": "mlb",
        "algorithm_silo": "MLB-B1.0-rolling-15min-only-plus-advanced-context",
        "pull_history_policy": {
            "status": "CONNECTED_15_MIN_ONLY",
            "allowed_snapshot_t": "HOT",
            "legacy_t1_t2_t3_t4": "IGNORED_BY_SIGNAL_READER",
            "rule": "MLB signals, individual game picks, and 3-leg parlay attempts are built from HOT 15-minute pull history only.",
        },
        "advanced_context_policy": advanced,
        "data_isolation": {
            "status": "CONNECTED",
            "rule": "MLB signals read SPORT#mlb#DATE#YYYY-MM-DD only with SK beginning HOT#GAME_DATE#YYYY-MM-DD. They do not compare T1/T2/T3/T4 or broad mixed-date snapshots.",
            "game_key_pattern": "mlb|YYYY-MM-DD|away|home",
            "prediction_pk_pattern": "PRED#mlb#YYYY-MM-DD",
            "audit_pk_pattern": "AUDIT#mlb#YYYY-MM-DD",
        },
        "items_1_to_5_status": {
            "1_audit_view_endpoints": "CONNECTED_DATE_ISOLATED",
            "2_movement_delta_engine": "CONNECTED_15_MIN_ONLY",
            "3_hot_side_prediction_endpoint": "CONNECTED_15_MIN_ONLY_PLUS_ADVANCED_CONTEXT",
            "4_results_evaluation_visibility": "CONNECTED",
            "5_source_status_visibility": "CONNECTED",
            "6_game_winner_attempts_all_games": "CONNECTED_15_MIN_ONLY_PLUS_ADVANCED_CONTEXT",
            "7_three_leg_parlay_attempt": "CONNECTED_15_MIN_ONLY_ADVANCED_ELIGIBILITY_SCORED",
        },
        "external_data_sources": advanced.get("source_status", {}),
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        params = _params(event)
        game_date = _game_date_from_params(params)
        if method == "GET" and path == "/v1/audit/mlb/snapshots":
            return _resp(200, audit_snapshots(game_date, min(int(params.get("limit") or 20), 100)))
        if method == "GET" and path == "/v1/audit/mlb/game":
            return _resp(200, audit_game(game_date, params.get("game_key"), min(int(params.get("limit") or 50), 200)))
        if method == "GET" and path == "/v1/signals/mlb/deltas":
            return _resp(200, movement_deltas(game_date, min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/predictions/mlb/hot-sides":
            include_no_edge = params.get("include_no_edge", "true").lower() != "false"
            return _resp(200, hot_sides(game_date, min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true", include_no_edge))
        if method == "GET" and path == "/v1/mlb/context/status":
            return _resp(200, advanced_context_status())
        if method == "GET" and path == "/v1/mlb/context/game":
            return _resp(200, game_context(game_date, params.get("game_key") or "", min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/results/mlb/status":
            return _resp(200, _base_results_status(params.get("slate_date_et") or game_date))
        if method == "GET" and path == "/v1/sources/mlb/status":
            return _resp(200, source_status())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
