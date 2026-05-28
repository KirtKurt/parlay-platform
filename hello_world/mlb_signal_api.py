import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key


SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")


dynamodb = boto3.resource("dynamodb")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None
outcomes_tbl = dynamodb.Table(OUTCOMES_TABLE) if OUTCOMES_TABLE else None

TARGET_SUCCESS_RATE = Decimal("75")
HOT_DELTA_THRESHOLD = 0.006
PUBLISH_DELTA_THRESHOLD = 0.018


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


def _params(event: Dict[str, Any]) -> Dict[str, str]:
    return event.get("queryStringParameters") or {}


def _american_to_prob(american: int) -> float:
    return abs(american) / (abs(american) + 100.0) if american < 0 else 100.0 / (american + 100.0)


def _vig_norm(home_american: int, away_american: int) -> Tuple[float, float]:
    home_raw = _american_to_prob(home_american)
    away_raw = _american_to_prob(away_american)
    total = home_raw + away_raw
    if total <= 0:
        return 0.5, 0.5
    return home_raw / total, away_raw / total


def _book_probs(game: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out = {}
    for book_key, payload in (game.get("books") or {}).items():
        ml = payload.get("ml") or {}
        if ml.get("home") is None or ml.get("away") is None:
            continue
        hp, ap = _vig_norm(int(ml["home"]), int(ml["away"]))
        out[book_key] = {"home": hp, "away": ap}
    return out


def _consensus_probs(game: Dict[str, Any]) -> Dict[str, Any]:
    probs = _book_probs(game)
    if not probs:
        return {"home": None, "away": None, "books": []}
    home_vals = [v["home"] for v in probs.values()]
    away_vals = [v["away"] for v in probs.values()]
    return {
        "home": sum(home_vals) / len(home_vals),
        "away": sum(away_vals) / len(away_vals),
        "books": sorted(probs.keys()),
    }


def _favorite_side(game: Dict[str, Any]) -> Dict[str, Any]:
    cp = _consensus_probs(game)
    home_team = game.get("home_team")
    away_team = game.get("away_team")
    if cp["home"] is None:
        return {"side": None, "team": None, "dog_side": None, "dog_team": None, "gap": None}
    if cp["home"] >= cp["away"]:
        return {"side": "home", "team": home_team, "dog_side": "away", "dog_team": away_team, "gap": cp["home"] - cp["away"]}
    return {"side": "away", "team": away_team, "dog_side": "home", "dog_team": home_team, "gap": cp["away"] - cp["home"]}


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g.get("game_key") or g.get("id"): g for g in snapshot.get("data", {}).get("games", []) or [] if g.get("game_key") or g.get("id")}


def _recent_snapshots(limit: int = 40, t: Optional[str] = None) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    slate_date = _slate_date_et()
    if t:
        key_condition = Key("PK").eq("SPORT#mlb") & Key("SK").begins_with(f"{t}#DATE#{slate_date}")
    else:
        key_condition = Key("PK").eq("SPORT#mlb")
    resp = snapshots_tbl.query(KeyConditionExpression=key_condition, ScanIndexForward=False, Limit=limit)
    items = resp.get("Items", [])
    return sorted(items, key=lambda x: x.get("asof") or "")


def _delta_for_game(prev: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, Any]:
    prev_cp = _consensus_probs(prev)
    latest_cp = _consensus_probs(latest)
    fav = _favorite_side(latest)
    if prev_cp["home"] is None or latest_cp["home"] is None:
        return {"ok": False, "reason": "missing_moneyline"}

    home_delta = latest_cp["home"] - prev_cp["home"]
    away_delta = latest_cp["away"] - prev_cp["away"]
    hot_side = "home" if home_delta >= away_delta else "away"
    hot_team = latest.get("home_team") if hot_side == "home" else latest.get("away_team")
    delta = home_delta if hot_side == "home" else away_delta

    favorite_not_separating = fav.get("side") != hot_side and abs(delta) >= HOT_DELTA_THRESHOLD
    dog_tightening = fav.get("dog_side") == hot_side and delta > 0
    book_agreement = _book_agreement(prev, latest, hot_side)
    spread_signal = _spread_signal(prev, latest, hot_side)
    total_signal = _total_signal(prev, latest)

    reason_codes = []
    if dog_tightening:
        reason_codes.append("dog_tightening")
    if favorite_not_separating:
        reason_codes.append("favorite_not_separating")
    if book_agreement["agreeing_books"] >= 2:
        reason_codes.append("multi_book_move")
    if spread_signal.get("direction") == "supports_hot_side":
        reason_codes.append("spread_supports_hot_side")
    elif spread_signal.get("direction") == "disagrees_with_hot_side":
        reason_codes.append("spread_disagreement")

    if abs(delta) >= PUBLISH_DELTA_THRESHOLD and len(reason_codes) >= 2:
        prediction_status = "PUBLISHED_MODERATE"
        confidence = "moderate"
    elif abs(delta) >= HOT_DELTA_THRESHOLD:
        prediction_status = "WATCHLIST"
        confidence = "volatile"
    else:
        prediction_status = "NO_EDGE"
        confidence = "no_edge"

    if dog_tightening and favorite_not_separating:
        prediction = f"{hot_team} upset/watchlist"
    elif hot_side == fav.get("side"):
        prediction = f"{hot_team} favorite pressure"
    else:
        prediction = f"{hot_team} pressure/watchlist"

    public = _public_language(
        prediction_status=prediction_status,
        hot_team=hot_team,
        prediction=prediction,
        reason_codes=reason_codes,
        book_agreement=book_agreement,
        spread_signal=spread_signal,
        total_signal=total_signal,
    )

    return {
        "ok": True,
        "game_key": latest.get("game_key"),
        "game_id": latest.get("id"),
        "home_team": latest.get("home_team"),
        "away_team": latest.get("away_team"),
        "commence_time": latest.get("commence_time"),
        "favorite": fav,
        "previous_asof": prev.get("_snapshot_asof"),
        "latest_asof": latest.get("_snapshot_asof"),
        "home_delta": round(home_delta, 6),
        "away_delta": round(away_delta, 6),
        "hot_side": hot_side,
        "hot_team": hot_team,
        "hot_delta": round(delta, 6),
        "hot_side_label": f"{hot_team} pressure",
        "prediction": prediction,
        "prediction_status": prediction_status,
        "confidence": confidence,
        "reason_codes": reason_codes,
        "public_market_language": public,
        "public_prediction": public["prediction_label"],
        "market_status": public["market_status"],
        "best_use": public["best_use"],
        "why": public["why"],
        "display_confidence_scores": False,
        "book_agreement": book_agreement,
        "spread_signal": spread_signal,
        "total_signal": total_signal,
        "latest_consensus": {"home": round(latest_cp["home"], 6), "away": round(latest_cp["away"], 6), "books": latest_cp["books"]},
        "previous_consensus": {"home": round(prev_cp["home"], 6), "away": round(prev_cp["away"], 6), "books": prev_cp["books"]},
    }


def _public_language(
    prediction_status: str,
    hot_team: Optional[str],
    prediction: str,
    reason_codes: List[str],
    book_agreement: Dict[str, Any],
    spread_signal: Dict[str, Any],
    total_signal: Dict[str, Any],
) -> Dict[str, Any]:
    tags = []
    if "multi_book_move" in reason_codes:
        tags.append("Cross-Book Confirmation")
    if "dog_tightening" in reason_codes:
        tags.append("Dog Tightening")
    if "favorite_not_separating" in reason_codes:
        tags.append("Favorite Not Separating")
    if "spread_supports_hot_side" in reason_codes:
        tags.append("Spread Support")
    if "spread_disagreement" in reason_codes:
        tags.append("Spread Disagreement")
    if total_signal.get("direction") in {"total_rising", "total_falling"}:
        tags.append("Total Movement")

    if prediction_status.startswith("PUBLISHED"):
        label = prediction
        status = "Published Edge"
        best_use = "Playable / Parlay Candidate"
        why = f"{hot_team} has enough market movement and confirmation to publish as an edge."
    elif prediction_status == "WATCHLIST":
        label = f"{hot_team} Watchlist"
        status = "Watchlist"
        best_use = "Track / Wait for Confirmation"
        why = f"{hot_team} is showing movement, but it has not met the full publish gate yet."
    else:
        label = "Pass / No Clean Edge"
        status = "No Clean Edge"
        best_use = "Avoid / No Bet"
        why = "No clean edge is published yet. The market is being tracked until movement becomes clearer."

    return {
        "language_version": "universal_market_language_v1",
        "prediction_label": label,
        "market_status": status,
        "best_use": best_use,
        "market_intelligence_tags": tags,
        "why": why,
        "books_agreeing": book_agreement.get("agreeing_books", 0),
        "books_disagreeing": book_agreement.get("disagreeing_books", 0),
        "spread_direction": spread_signal.get("direction"),
        "total_direction": total_signal.get("direction"),
    }


def _book_agreement(prev: Dict[str, Any], latest: Dict[str, Any], side: str) -> Dict[str, Any]:
    prev_probs = _book_probs(prev)
    latest_probs = _book_probs(latest)
    common = sorted(set(prev_probs.keys()) & set(latest_probs.keys()))
    agreeing = []
    disagreeing = []
    for book in common:
        delta = latest_probs[book][side] - prev_probs[book][side]
        if delta > 0:
            agreeing.append({"book": book, "delta": round(delta, 6)})
        elif delta < 0:
            disagreeing.append({"book": book, "delta": round(delta, 6)})
    return {"common_books": len(common), "agreeing_books": len(agreeing), "disagreeing_books": len(disagreeing), "agreeing": agreeing, "disagreeing": disagreeing}


def _avg_spread_point(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = []
    key = f"{side}_point"
    for payload in (game.get("books") or {}).values():
        spread = payload.get("spread") or {}
        if spread.get(key) is not None:
            vals.append(float(spread[key]))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _spread_signal(prev: Dict[str, Any], latest: Dict[str, Any], hot_side: str) -> Dict[str, Any]:
    prev_point = _avg_spread_point(prev, hot_side)
    latest_point = _avg_spread_point(latest, hot_side)
    if prev_point is None or latest_point is None:
        return {"direction": "missing", "delta": None}
    delta = latest_point - prev_point
    if abs(delta) < 0.01:
        direction = "flat"
    elif delta < 0:
        direction = "supports_hot_side"
    else:
        direction = "disagrees_with_hot_side"
    return {"direction": direction, "previous_point": round(prev_point, 3), "latest_point": round(latest_point, 3), "delta": round(delta, 3)}


def _avg_total_point(game: Dict[str, Any]) -> Optional[float]:
    vals = []
    for payload in (game.get("books") or {}).values():
        total = payload.get("total") or {}
        if total.get("over_point") is not None:
            vals.append(float(total["over_point"]))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _total_signal(prev: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, Any]:
    prev_total = _avg_total_point(prev)
    latest_total = _avg_total_point(latest)
    if prev_total is None or latest_total is None:
        return {"direction": "missing", "delta": None}
    delta = latest_total - prev_total
    if abs(delta) < 0.01:
        direction = "flat"
    elif delta > 0:
        direction = "total_rising"
    else:
        direction = "total_falling"
    return {"direction": direction, "previous_total": round(prev_total, 3), "latest_total": round(latest_total, 3), "delta": round(delta, 3)}


def movement_deltas(limit: int = 40) -> Dict[str, Any]:
    snapshots = _recent_snapshots(limit=limit)
    if len(snapshots) < 2:
        return {"ok": True, "sport": "mlb", "count": 0, "message": "Need at least two snapshots."}
    prev_snap = snapshots[-2]
    latest_snap = snapshots[-1]
    prev_games = _game_index(prev_snap)
    latest_games = _game_index(latest_snap)
    deltas = []
    for game_key, latest_game in latest_games.items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        prev_game = {**prev_game, "_snapshot_asof": prev_snap.get("asof")}
        latest_game = {**latest_game, "_snapshot_asof": latest_snap.get("asof")}
        row = _delta_for_game(prev_game, latest_game)
        if row.get("ok"):
            deltas.append(row)
    deltas.sort(key=lambda x: abs(float(x.get("hot_delta") or 0)), reverse=True)
    return {"ok": True, "sport": "mlb", "previous_asof": prev_snap.get("asof"), "latest_asof": latest_snap.get("asof"), "count": len(deltas), "deltas": deltas}


def hot_sides(limit: int = 40, store: bool = False, include_no_edge: bool = True) -> Dict[str, Any]:
    data = movement_deltas(limit=limit)
    rows = []
    actionable_rows = []
    now = _now_iso()
    slate_date = _slate_date_et()
    status_counts: Dict[str, int] = {}

    for row in data.get("deltas", []):
        status = row.get("prediction_status") or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1
        is_actionable = status != "NO_EDGE"
        if not include_no_edge and not is_actionable:
            continue

        item = None
        if is_actionable:
            item = {
                "PK": f"PRED#mlb#{slate_date}",
                "SK": f"HOT_SIDE#{row.get('latest_asof')}#{row.get('game_key')}",
                "sport": "mlb",
                "slate_date_et": slate_date,
                "created_at": now,
                "asof": row.get("latest_asof"),
                "previous_asof": row.get("previous_asof"),
                "game_key": row.get("game_key"),
                "game_id": row.get("game_id"),
                "home_team": row.get("home_team"),
                "away_team": row.get("away_team"),
                "market": "moneyline",
                "prediction_type": "HOT_SIDE_MOVEMENT",
                "prediction_status": status,
                "status": "OPEN",
                "predicted_team": row.get("hot_team") if status.startswith("PUBLISHED") else None,
                "hot_team": row.get("hot_team"),
                "hot_side": row.get("hot_side"),
                "confidence_label": row.get("confidence"),
                "confidence": Decimal(str(_confidence_score(row))),
                "target_success_rate": TARGET_SUCCESS_RATE,
                "reason_codes": row.get("reason_codes", []),
                "explanation": row.get("prediction"),
                "public_market_language": row.get("public_market_language", {}),
                "movement": {
                    "home_delta": Decimal(str(row.get("home_delta"))),
                    "away_delta": Decimal(str(row.get("away_delta"))),
                    "hot_delta": Decimal(str(row.get("hot_delta"))),
                },
                "evaluation": {},
            }
            actionable_rows.append(row)
            if store and predictions_tbl is not None:
                predictions_tbl.put_item(Item=item)

        rows.append({**row, "stored_prediction_key": item["SK"] if item and store else None})

    return {
        "ok": True,
        "sport": "mlb",
        "stored": store,
        "count": len(rows),
        "movement_count": data.get("count", 0),
        "actionable_count": len(actionable_rows),
        "status_counts": status_counts,
        "target_success_rate": 75,
        "display_confidence_scores": False,
        "message": "No clean edge is published yet; returning tracked market cards." if not actionable_rows else "Actionable market cards found.",
        "previous_asof": data.get("previous_asof"),
        "latest_asof": data.get("latest_asof"),
        "hot_sides": rows,
    }


def _confidence_score(row: Dict[str, Any]) -> int:
    score = 50 + min(35, int(abs(float(row.get("hot_delta") or 0)) * 1000))
    score += min(10, int(row.get("book_agreement", {}).get("agreeing_books", 0)))
    if row.get("spread_signal", {}).get("direction") == "supports_hot_side":
        score += 5
    return min(95, score)


def audit_snapshots(limit: int = 20) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    slate_date = _slate_date_et()
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"AUDIT#mlb#{slate_date}"), ScanIndexForward=False, Limit=limit)
    items = resp.get("Items", [])
    summaries = [i for i in items if i.get("entity_type") == "SNAPSHOT_AUDIT_SUMMARY"]
    game_rows = [i for i in items if i.get("entity_type") == "GAME_SNAPSHOT_AUDIT"]
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "count": len(items), "summary_count": len(summaries), "game_audit_count": len(game_rows), "items": items}


def audit_game(game_key: Optional[str], limit: int = 50) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        raise RuntimeError("SIGNAL_LEDGER_TABLE not configured")
    slate_date = _slate_date_et()
    resp = signal_ledger_tbl.query(KeyConditionExpression=Key("PK").eq(f"AUDIT#mlb#{slate_date}"), ScanIndexForward=False, Limit=limit)
    items = [i for i in resp.get("Items", []) if i.get("game_key") == game_key] if game_key else resp.get("Items", [])
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "game_key": game_key, "count": len(items), "items": items}


def results_status(slate_date: Optional[str] = None) -> Dict[str, Any]:
    if outcomes_tbl is None:
        raise RuntimeError("OUTCOMES_TABLE not configured")
    slate_date = slate_date or _slate_date_et()
    resp = outcomes_tbl.query(KeyConditionExpression=Key("PK").eq(f"OUTCOME#mlb#{slate_date}"))
    outcomes = resp.get("Items", [])
    if predictions_tbl is not None:
        pred_resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#mlb#{slate_date}"))
        predictions = pred_resp.get("Items", [])
    else:
        predictions = []
    graded = [p for p in predictions if p.get("status") in {"CORRECT", "WRONG"}]
    correct = [p for p in graded if p.get("status") == "CORRECT"]
    accuracy = round(len(correct) / len(graded) * 100, 2) if graded else None
    return {
        "ok": True,
        "sport": "mlb",
        "slate_date_et": slate_date,
        "outcomes_count": len(outcomes),
        "predictions_count": len(predictions),
        "graded_count": len(graded),
        "correct": len(correct),
        "wrong": len(graded) - len(correct),
        "accuracy_pct": accuracy,
        "target_success_rate": 75,
        "outcomes": outcomes,
    }


def source_status() -> Dict[str, Any]:
    return {
        "ok": True,
        "sport": "mlb",
        "items_1_to_5_status": {
            "1_audit_view_endpoints": "CONNECTED",
            "2_movement_delta_engine": "CONNECTED",
            "3_hot_side_prediction_endpoint": "CONNECTED",
            "4_results_evaluation_visibility": "CONNECTED",
            "5_source_status_visibility": "CONNECTED",
        },
        "external_data_sources": {
            "odds_ml_spread_total": "CONNECTED",
            "all_returned_books": "CONNECTED",
            "timestamps": "CONNECTED",
            "audit_ledger_schema": "CONNECTED",
            "results_scores": "CONNECTED_PENDING_COMPLETED_GAMES",
            "pitching": "NOT_CONNECTED_YET",
            "weather": "NOT_CONNECTED_YET",
            "injuries_lineups_news": "NOT_CONNECTED_YET",
            "public_betting_handle": "NOT_CONNECTED_YET",
        },
    }


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or ""
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        params = _params(event)
        if method == "GET" and path == "/v1/audit/mlb/snapshots":
            return _resp(200, audit_snapshots(min(int(params.get("limit") or 20), 100)))
        if method == "GET" and path == "/v1/audit/mlb/game":
            return _resp(200, audit_game(params.get("game_key"), min(int(params.get("limit") or 50), 200)))
        if method == "GET" and path == "/v1/signals/mlb/deltas":
            return _resp(200, movement_deltas(min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/predictions/mlb/hot-sides":
            include_no_edge = params.get("include_no_edge", "true").lower() != "false"
            return _resp(200, hot_sides(min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true", include_no_edge))
        if method == "GET" and path == "/v1/results/mlb/status":
            return _resp(200, results_status(params.get("slate_date_et")))
        if method == "GET" and path == "/v1/sources/mlb/status":
            return _resp(200, source_status())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "mlb", "error": str(exc)})
