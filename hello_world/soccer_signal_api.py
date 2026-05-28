from __future__ import annotations

import itertools
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from soccer_audit import soccer_results_status, soccer_source_status, soccer_three_way_probs
from universal_market_language import build_public_market_language, market_language_status


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
FEATURE_VERSION = "soccer_individual_match_and_27_combo_parlay_v2_market_language"
SOCCER_OUTCOMES = ("home", "draw", "away")


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


def _has_games(snapshot: Dict[str, Any]) -> bool:
    games = snapshot.get("data", {}).get("games", []) or []
    count = snapshot.get("data", {}).get("count")
    return bool(games) or float(count or 0) > 0


def _recent_snapshots(limit: int = 40, populated_only: bool = False) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    resp = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq("SPORT#soccer"), ScanIndexForward=False, Limit=limit)
    rows = sorted(resp.get("Items", []), key=lambda x: x.get("asof") or "")
    if populated_only:
        rows = [row for row in rows if _has_games(row)]
    return rows


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g.get("game_key") or g.get("id"): g for g in snapshot.get("data", {}).get("games", []) or [] if g.get("game_key") or g.get("id")}


def _book_probs(game: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    consensus = soccer_three_way_probs(game)
    return consensus.get("book_probs") or {}


def _american_to_decimal(price: float) -> float:
    price = float(price)
    if price == 0:
        raise ValueError("American odds cannot be 0")
    return round(1 + (100 / abs(price)) if price < 0 else 1 + (price / 100), 6)


def _decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    if decimal_odds >= 2:
        return int(round((decimal_odds - 1) * 100))
    return int(round(-100 / (decimal_odds - 1)))


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


def _price_for_outcome(game: Dict[str, Any], outcome: str) -> Optional[float]:
    prices: List[float] = []
    home = game.get("home_team")
    away = game.get("away_team")
    for markets in (game.get("books") or {}).values():
        h2h = markets.get("h2h") or markets.get("ml") or []
        if isinstance(h2h, list):
            for row in h2h:
                name = row.get("name")
                if ((outcome == "home" and name == home) or (outcome == "away" and name == away) or (outcome == "draw" and str(name).lower() in {"draw", "tie", "x"})) and row.get("price") is not None:
                    prices.append(float(row["price"]))
        elif isinstance(h2h, dict) and h2h.get(outcome) is not None:
            prices.append(float(h2h[outcome]))
    if not prices:
        return None
    return round(sum(prices) / len(prices), 2)


def _spread_summary(game: Dict[str, Any]) -> Dict[str, Any]:
    points: List[float] = []
    for markets in (game.get("books") or {}).values():
        spread = markets.get("spreads") or markets.get("spread")
        if isinstance(spread, list):
            for row in spread:
                if row.get("point") is not None:
                    points.append(float(row["point"]))
        elif isinstance(spread, dict):
            for key in ("home_point", "away_point"):
                if spread.get(key) is not None:
                    points.append(float(spread[key]))
    return {"available": bool(points), "sample_points": points[:6]}


def _total_summary(game: Dict[str, Any]) -> Dict[str, Any]:
    points: List[float] = []
    for markets in (game.get("books") or {}).values():
        total = markets.get("totals") or markets.get("total")
        if isinstance(total, list):
            for row in total:
                if row.get("point") is not None:
                    points.append(float(row["point"]))
        elif isinstance(total, dict):
            for key in ("over_point", "under_point"):
                if total.get(key) is not None:
                    points.append(float(total[key]))
    return {"available": bool(points), "sample_points": points[:6]}


def _point_delta(prev: Dict[str, Any], latest: Dict[str, Any], market: str) -> Dict[str, Any]:
    prev_summary = _spread_summary(prev) if market == "spread" else _total_summary(prev)
    latest_summary = _spread_summary(latest) if market == "spread" else _total_summary(latest)
    if not prev_summary["sample_points"] or not latest_summary["sample_points"]:
        return {"direction": "unavailable", "previous_point": None, "latest_point": None, "delta": None}
    prev_avg = sum(prev_summary["sample_points"]) / len(prev_summary["sample_points"])
    latest_avg = sum(latest_summary["sample_points"]) / len(latest_summary["sample_points"])
    delta = round(latest_avg - prev_avg, 4)
    direction = "flat" if abs(delta) < 0.001 else "up" if delta > 0 else "down"
    return {"direction": direction, "previous_point": round(prev_avg, 4), "latest_point": round(latest_avg, 4), "delta": delta}


def _public_language_for_row(row: Dict[str, Any], *, is_parlay: bool = False) -> Dict[str, Any]:
    return build_public_market_language(
        sport="soccer",
        prediction_status=row.get("prediction_status"),
        reason_codes=row.get("reason_codes", []),
        prediction=row.get("prediction"),
        is_parlay=is_parlay,
        soccer_context={
            "hot_outcome": row.get("hot_outcome"),
            "current_leader": row.get("current_leader"),
            "home_team": row.get("home_team"),
            "away_team": row.get("away_team"),
            "hot_label": row.get("hot_label"),
        },
    )


def _delta_for_game(prev: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, Any]:
    prev_p = soccer_three_way_probs(prev)
    latest_p = soccer_three_way_probs(latest)
    if prev_p.get("home") is None or latest_p.get("home") is None:
        return {"ok": False, "reason": "missing_three_way_h2h"}

    deltas = {side: latest_p[side] - prev_p[side] for side in SOCCER_OUTCOMES}
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

    row = {
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
        "deltas": {side: round(deltas[side], 6) for side in SOCCER_OUTCOMES},
        "hot_side_label": f"{hot_label} pressure",
        "prediction": prediction_text,
        "prediction_status": prediction_status,
        "confidence": confidence,
        "reason_codes": reason_codes,
        "book_agreement": agreement,
        "spread_signal": _point_delta(prev, latest, "spread"),
        "total_signal": _point_delta(prev, latest, "total"),
        "latest_consensus_three_way": {k: latest_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
        "previous_consensus_three_way": {k: prev_p.get(k) for k in ["home", "draw", "away", "leader", "leader_gap", "books", "book_count"]},
    }
    row["public_market_language"] = _public_language_for_row(row)
    return row


def soccer_movement_deltas(limit: int = 40) -> Dict[str, Any]:
    snapshots = _recent_snapshots(limit=limit, populated_only=True)
    if len(snapshots) < 2:
        latest = snapshots[-1] if snapshots else None
        return {
            "ok": True,
            "sport": "soccer",
            "model": MODEL_VERSION,
            "market_language": market_language_status(),
            "count": 0,
            "deltas": [],
            "status": "WAITING_FOR_SECOND_POPULATED_SNAPSHOT",
            "latest_populated_asof": latest.get("asof") if latest else None,
            "message": "Need at least two populated soccer snapshots. Empty HOT snapshots are ignored.",
        }
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
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "feature_version": FEATURE_VERSION, "market_language": market_language_status(), "previous_asof": prev_snap.get("asof"), "latest_asof": latest_snap.get("asof"), "count": len(deltas), "deltas": deltas}


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
        public_language = row.get("public_market_language") or _public_language_for_row(row)
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
            "confidence_label": public_language.get("market_status"),
            "confidence": None,
            "target_success_rate": TARGET_SUCCESS_RATE,
            "reason_codes": row.get("reason_codes", []),
            "explanation": public_language.get("public_explanation"),
            "public_market_language": public_language,
            "movement": {"hot_delta": Decimal(str(row.get("hot_delta"))), "deltas": {k: Decimal(str(v)) for k, v in row.get("deltas", {}).items()}},
            "evaluation": {},
        }
        rows.append({**row, "public_market_language": public_language, "stored_prediction_key": item["SK"] if store else None})
        if store and predictions_tbl is not None:
            predictions_tbl.put_item(Item=item)
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "feature_version": FEATURE_VERSION, "market_language": market_language_status(), "stored": store, "count": len(rows), "target_success_rate": 75, "hot_sides": rows}


def soccer_match_signals(limit: int = 40) -> Dict[str, Any]:
    snapshots = _recent_snapshots(limit=limit, populated_only=True)
    if not snapshots:
        return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "market_language": market_language_status(), "count": 0, "matches": [], "status": "NO_POPULATED_SOCCER_SNAPSHOT"}
    latest = snapshots[-1]
    movement_by_key = {row.get("game_key"): row for row in soccer_movement_deltas(limit=limit).get("deltas", [])}
    has_movement = len(snapshots) >= 2
    matches = []
    for game in latest.get("data", {}).get("games", []) or []:
        consensus = soccer_three_way_probs(game)
        if consensus.get("home") is None:
            continue
        key = game.get("game_key") or game.get("id")
        movement = movement_by_key.get(key)
        outcomes = {}
        for outcome in SOCCER_OUTCOMES:
            price = _price_for_outcome(game, outcome)
            outcomes[outcome] = {
                "team": _outcome_label(outcome, game),
                "consensus_price": price,
                "decimal_odds": _american_to_decimal(price) if price is not None else None,
                "consensus_probability": consensus.get(outcome),
                "signal": "PENDING_BASELINE" if not has_movement else ("HOT" if movement and movement.get("hot_outcome") == outcome else "NEUTRAL"),
            }
        public_language = movement.get("public_market_language") if movement else build_public_market_language(sport="soccer", prediction_status="NO_EDGE", reason_codes=[], prediction=None, soccer_context={"home_team": game.get("home_team"), "away_team": game.get("away_team")})
        matches.append({
            "game_id": game.get("id"),
            "game_key": key,
            "league": game.get("sport_key"),
            "match": f"{game.get('away_team')} at {game.get('home_team')}",
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "commence_time": game.get("commence_time"),
            "market_type": "3-way h2h",
            "books_tracked": consensus.get("book_count"),
            "outcomes": outcomes,
            "hot_side": movement.get("hot_label") if movement else None,
            "hot_outcome": movement.get("hot_outcome") if movement else None,
            "confidence": None,
            "status": movement.get("prediction_status") if movement else "WAITING_FOR_SECOND_POPULATED_SNAPSHOT",
            "reason_codes": movement.get("reason_codes", []) if movement else [],
            "public_market_language": public_language,
            "explanation": public_language.get("public_explanation"),
            "spread_market": _spread_summary(game),
            "total_market": _total_summary(game),
        })
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "feature_version": FEATURE_VERSION, "market_language": market_language_status(), "asof": latest.get("asof"), "populated_snapshot_count": len(snapshots), "count": len(matches), "matches": matches}


def _combo_confidence(score: float) -> str:
    if score >= 2.0:
        return "Clean Edge"
    if score >= 1.0:
        return "Playable Edge"
    return "Watchlist Edge"


def _eligible_matches_for_parlay(limit: int) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    snapshots = _recent_snapshots(limit=limit, populated_only=True)
    if len(snapshots) < 2:
        latest = snapshots[-1] if snapshots else None
        return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "previous_asof": None, "latest_asof": latest.get("asof") if latest else None, "latest_populated_asof": latest.get("asof") if latest else None}, []
    latest_snap = snapshots[-1]
    latest_games = _game_index(latest_snap)
    movement = soccer_movement_deltas(limit=limit)
    rows = []
    for row in movement.get("deltas", []):
        game = latest_games.get(row.get("game_key"))
        if not game:
            continue
        if row.get("latest_consensus_three_way", {}).get("book_count", 0) < 2:
            continue
        if any(_price_for_outcome(game, outcome) is None for outcome in SOCCER_OUTCOMES):
            continue
        rows.append({**row, "latest_game": game})
    rows.sort(key=lambda row: (_confidence_score(row), abs(float(row.get("hot_delta") or 0))), reverse=True)
    return movement, rows


def soccer_parlays(limit: int = 40, matches_per_parlay: int = 3) -> Dict[str, Any]:
    if matches_per_parlay != 3:
        return {"ok": False, "sport": "soccer", "error": "Soccer parlay engine currently requires exactly 3 matches for 27 combinations."}
    movement, rows = _eligible_matches_for_parlay(limit)
    if not movement or not movement.get("previous_asof"):
        return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "market_language": market_language_status(), "parlays_ready": False, "reason": "Need at least two populated soccer snapshots to compute movement deltas.", "latest_populated_asof": movement.get("latest_populated_asof") if movement else None, "required_next_step": "Wait for next HOT soccer snapshot with count > 0."}
    if len(rows) < 3:
        return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "market_language": market_language_status(), "parlays_ready": False, "reason": "Need at least three soccer matches with usable three-way market movement and actual h2h prices.", "usable_matches": len(rows)}

    selected = rows[:3]
    combos = []
    for outcomes in itertools.product(SOCCER_OUTCOMES, repeat=3):
        decimal_odds = 1.0
        score = 0.0
        legs = []
        reason_codes: List[str] = []
        valid = True
        for row, outcome in zip(selected, outcomes):
            game = row["latest_game"]
            price = _price_for_outcome(game, outcome)
            if price is None:
                valid = False
                break
            leg_decimal = _american_to_decimal(price)
            decimal_odds *= leg_decimal
            if row.get("hot_outcome") == outcome:
                score += 1.0 + abs(float(row.get("hot_delta") or 0)) * 100
                reason_codes.append(f"{_outcome_label(outcome, game)}_hot_side")
            if outcome == "draw":
                score += 0.15
            legs.append({"match": f"{game.get('away_team')} at {game.get('home_team')}", "outcome": outcome, "selection": _outcome_label(outcome, game), "american_odds": price, "decimal_odds": leg_decimal, "consensus_probability": row.get("latest_consensus_three_way", {}).get(outcome)})
        if not valid:
            continue
        decimal_odds = round(decimal_odds, 4)
        public_language = build_public_market_language(sport="soccer", prediction_status="PUBLISHED_MODERATE" if score >= 2 else "WATCHLIST", reason_codes=reason_codes, prediction=" + ".join(leg["selection"] for leg in legs), is_parlay=True)
        combos.append({"combo": " + ".join(leg["selection"] for leg in legs), "outcome_types": list(outcomes), "legs": legs, "parlay_decimal_odds": decimal_odds, "parlay_american_odds": _decimal_to_american(decimal_odds), "implied_win_probability_pct": round((1 / decimal_odds) * 100, 2), "signal_score_internal": round(score, 4), "confidence_band": public_language.get("market_status"), "public_market_language": public_language, "reason_codes": reason_codes or ["no_hot_side_alignment"]})
    combos.sort(key=lambda row: (row["signal_score_internal"], row["implied_win_probability_pct"]), reverse=True)
    for idx, combo in enumerate(combos, 1):
        combo["rank"] = idx
    selected_public = [{k: v for k, v in row.items() if k != "latest_game"} for row in selected]
    return {"ok": True, "sport": "soccer", "model": MODEL_VERSION, "feature_version": FEATURE_VERSION, "market_language": market_language_status(), "parlays_ready": True, "previous_asof": movement.get("previous_asof"), "latest_asof": movement.get("latest_asof"), "match_count": 3, "combo_count": len(combos), "selected_matches": selected_public, "ranked_combinations": combos}


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
        if method == "GET" and path in {"/v1/market-language/status", "/v1/sources/market-language/status"}:
            return _resp(200, market_language_status())
        if method == "GET" and path == "/v1/audit/soccer/snapshots":
            return _resp(200, soccer_audit_snapshots(min(int(params.get("limit") or 20), 100)))
        if method == "GET" and path in {"/v1/signals/soccer/deltas", "/v1/soccer/movement"}:
            return _resp(200, soccer_movement_deltas(min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path in {"/v1/predictions/soccer/hot-sides", "/v1/soccer/hot-sides"}:
            return _resp(200, soccer_hot_sides(min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true"))
        if method == "GET" and path == "/v1/soccer/matches/signals":
            return _resp(200, soccer_match_signals(min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/soccer/parlays":
            return _resp(200, soccer_parlays(min(int(params.get("limit") or 40), 200)))
        if method == "GET" and path == "/v1/results/soccer/status":
            return _resp(200, soccer_results_status(params.get("slate_date_et")))
        if method == "GET" and path == "/v1/sources/soccer/status":
            return _resp(200, soccer_source_status())
        return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": "soccer", "error": str(exc)})
