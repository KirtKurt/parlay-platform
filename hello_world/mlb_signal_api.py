import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from itertools import product
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


def _to_ddb(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(round(value, 12)))
    if isinstance(value, dict):
        return {k: _to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_to_ddb(v) for v in value if v is not None]
    return value


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


def _american_to_decimal(american: float) -> float:
    return 1.0 + (100.0 / abs(float(american))) if float(american) < 0 else 1.0 + (float(american) / 100.0)


def _decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def _book_probs(game: Dict[str, Any]) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
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
    return {"home": sum(home_vals) / len(home_vals), "away": sum(away_vals) / len(away_vals), "books": sorted(probs.keys())}


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
    return sorted(resp.get("Items", []), key=lambda x: x.get("asof") or "")


def _confidence_score(row: Dict[str, Any]) -> int:
    score = 50 + min(35, int(abs(float(row.get("hot_delta") or 0)) * 1000))
    score += min(10, int(row.get("book_agreement", {}).get("agreeing_books", 0)))
    if row.get("spread_signal", {}).get("direction") == "supports_hot_side":
        score += 5
    return min(95, score)


def _confidence_label(row: Dict[str, Any]) -> str:
    status = row.get("prediction_status")
    if status == "NO_EDGE":
        return "No Clean Edge"
    if status == "WATCHLIST":
        return "Watchlist"
    if status and str(status).startswith("PUBLISHED"):
        return "Playable Edge"
    return str(row.get("confidence") or "Unknown")


def _avg_american(game: Dict[str, Any], side: str) -> Optional[float]:
    vals = []
    for payload in (game.get("books") or {}).values():
        ml = payload.get("ml") or {}
        if ml.get(side) is not None:
            vals.append(float(ml[side]))
    if not vals:
        return None
    return sum(vals) / len(vals)


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


def _public_language(prediction_status: str, hot_team: Optional[str], prediction: str, reason_codes: List[str], book_agreement: Dict[str, Any], spread_signal: Dict[str, Any], total_signal: Dict[str, Any]) -> Dict[str, Any]:
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

    public = _public_language(prediction_status, hot_team, prediction, reason_codes, book_agreement, spread_signal, total_signal)
    row = {
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
        "display_confidence_scores": True,
        "book_agreement": book_agreement,
        "spread_signal": spread_signal,
        "total_signal": total_signal,
        "latest_consensus": {"home": round(latest_cp["home"], 6), "away": round(latest_cp["away"], 6), "books": latest_cp["books"]},
        "previous_consensus": {"home": round(prev_cp["home"], 6), "away": round(prev_cp["away"], 6), "books": prev_cp["books"]},
    }
    row["confidence_score"] = _confidence_score(row)
    row["confidence_label"] = _confidence_label(row)
    return row


def _simple_no_edge_row(game: Dict[str, Any], snapshot_asof: Optional[str]) -> Optional[Dict[str, Any]]:
    cp = _consensus_probs(game)
    if cp["home"] is None:
        return None
    fav = _favorite_side(game)
    selected_side = fav.get("side") or "home"
    selected_team = fav.get("team") or game.get("home_team")
    public = _public_language("NO_EDGE", selected_team, f"{selected_team} consensus lean", [], {"agreeing_books": 0, "disagreeing_books": 0}, {"direction": "flat"}, {"direction": "flat"})
    row = {
        "ok": True,
        "game_key": game.get("game_key"),
        "game_id": game.get("id"),
        "home_team": game.get("home_team"),
        "away_team": game.get("away_team"),
        "commence_time": game.get("commence_time"),
        "favorite": fav,
        "previous_asof": None,
        "latest_asof": snapshot_asof,
        "home_delta": 0.0,
        "away_delta": 0.0,
        "hot_side": selected_side,
        "hot_team": selected_team,
        "hot_delta": 0.0,
        "hot_side_label": f"{selected_team} consensus lean",
        "prediction": f"{selected_team} consensus lean",
        "prediction_status": "NO_EDGE",
        "confidence": "no_edge",
        "reason_codes": [],
        "public_market_language": public,
        "public_prediction": public["prediction_label"],
        "market_status": public["market_status"],
        "best_use": public["best_use"],
        "why": public["why"],
        "display_confidence_scores": True,
        "book_agreement": {"common_books": len(cp["books"]), "agreeing_books": 0, "disagreeing_books": 0, "agreeing": [], "disagreeing": []},
        "spread_signal": {"direction": "flat"},
        "total_signal": {"direction": "flat"},
        "latest_consensus": {"home": round(cp["home"], 6), "away": round(cp["away"], 6), "books": cp["books"]},
        "previous_consensus": {},
        "confidence_score": 50,
        "confidence_label": "No Clean Edge",
    }
    return row


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


def _select_side_for_row(row: Dict[str, Any]) -> Tuple[str, str, str]:
    status = row.get("prediction_status") or "NO_EDGE"
    fav = row.get("favorite") or {}
    if status != "NO_EDGE":
        return row.get("hot_side") or fav.get("side") or "home", row.get("hot_team") or fav.get("team"), "movement"
    if "dog_tightening" in (row.get("reason_codes") or []) and row.get("hot_team"):
        return row.get("hot_side") or fav.get("side") or "home", row.get("hot_team"), "dog_tightening_watch"
    return fav.get("side") or row.get("hot_side") or "home", fav.get("team") or row.get("hot_team"), "consensus_favorite"


def _enrich_attempted_winner(row: Dict[str, Any], latest_game: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    side, team, basis = _select_side_for_row(row)
    cp = row.get("latest_consensus") or {}
    prob = cp.get(side)
    odds = _avg_american(latest_game or {}, side) if latest_game else None
    out = {
        **row,
        "attempted_winner_side": side,
        "attempted_winner": team,
        "attempted_winner_basis": basis,
        "attempted_winner_probability_pct": round(float(prob) * 100, 1) if prob is not None else None,
        "attempted_winner_american_odds": round(float(odds), 2) if odds is not None else None,
    }
    return out


def _prediction_item(row: Dict[str, Any], slate_date: str, now: str) -> Dict[str, Any]:
    status = row.get("prediction_status") or "UNKNOWN"
    return {
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
        "commence_time": row.get("commence_time"),
        "market": "moneyline",
        "prediction_type": "MLB_GAME_WINNER_ATTEMPT",
        "prediction_status": status,
        "status": "OPEN",
        "predicted_team": row.get("attempted_winner"),
        "predicted_side": row.get("attempted_winner_side"),
        "attempted_winner_basis": row.get("attempted_winner_basis"),
        "published_team": row.get("hot_team") if str(status).startswith("PUBLISHED") else None,
        "hot_team": row.get("hot_team"),
        "hot_side": row.get("hot_side"),
        "hot_side_label": row.get("hot_side_label"),
        "confidence_label": row.get("confidence_label"),
        "confidence": row.get("confidence_score"),
        "confidence_score": row.get("confidence_score"),
        "display_confidence_scores": True,
        "target_success_rate": TARGET_SUCCESS_RATE,
        "reason_codes": row.get("reason_codes", []),
        "market_intelligence_tags": row.get("public_market_language", {}).get("market_intelligence_tags", []),
        "explanation": row.get("prediction"),
        "public_prediction": row.get("public_prediction"),
        "market_status": row.get("market_status"),
        "best_use": row.get("best_use"),
        "why": row.get("why"),
        "public_market_language": row.get("public_market_language", {}),
        "favorite": row.get("favorite", {}),
        "book_agreement": row.get("book_agreement", {}),
        "spread_signal": row.get("spread_signal", {}),
        "total_signal": row.get("total_signal", {}),
        "latest_consensus": row.get("latest_consensus", {}),
        "previous_consensus": row.get("previous_consensus", {}),
        "movement": {"home_delta": row.get("home_delta"), "away_delta": row.get("away_delta"), "hot_delta": row.get("hot_delta")},
        "ml_training_row": True,
        "ml_outcome_status": "PENDING_RESULT",
        "evaluation": {},
    }


def _row_side_payload(row: Dict[str, Any], game: Dict[str, Any], side: str) -> Dict[str, Any]:
    team = game.get("home_team") if side == "home" else game.get("away_team")
    cp = _consensus_probs(game)
    odds = _avg_american(game, side)
    prob = cp.get(side) if cp.get(side) is not None else 0.5
    return {
        "side": side,
        "team": team,
        "american_odds": round(float(odds), 2) if odds is not None else None,
        "decimal_odds": round(_american_to_decimal(odds), 4) if odds is not None else None,
        "consensus_probability_pct": round(prob * 100, 1),
    }


def _build_three_leg_parlay(rows: List[Dict[str, Any]], latest_games: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    candidates = []
    for row in rows:
        game = latest_games.get(row.get("game_key"))
        if not game:
            continue
        status_bonus = 20 if str(row.get("prediction_status", "")).startswith("PUBLISHED") else 10 if row.get("prediction_status") == "WATCHLIST" else 0
        fav_gap = float((row.get("favorite") or {}).get("gap") or 0)
        candidates.append({"row": row, "game": game, "pick_score": float(row.get("confidence_score") or 50) + status_bonus + (fav_gap * 100)})
    candidates.sort(key=lambda x: x["pick_score"], reverse=True)
    selected = candidates[:3]
    if len(selected) < 3:
        return {"ok": False, "reason": "Need at least 3 MLB games with moneyline data to create a 3-leg parlay.", "available_candidates": len(candidates)}

    legs = []
    for item in selected:
        row = item["row"]
        game = item["game"]
        legs.append({
            "game_key": row.get("game_key"),
            "match": f"{game.get('away_team')} at {game.get('home_team')}",
            "home": _row_side_payload(row, game, "home"),
            "away": _row_side_payload(row, game, "away"),
            "attempted_winner": row.get("attempted_winner"),
            "attempted_winner_side": row.get("attempted_winner_side"),
            "market_status": row.get("market_status"),
            "confidence_score": row.get("confidence_score"),
            "tags": row.get("public_market_language", {}).get("market_intelligence_tags", []),
        })

    combos = []
    for sides in product(["home", "away"], repeat=3):
        picks = []
        dec = 1.0
        prob = 1.0
        dog_count = 0
        score = 0.0
        for leg, side in zip(legs, sides):
            payload = leg[side]
            picks.append(payload["team"])
            dec *= float(payload["decimal_odds"] or 1.0)
            prob *= float(payload["consensus_probability_pct"] or 0.0) / 100.0
            if payload["team"] != leg["attempted_winner"]:
                score -= 2.0
            else:
                score += float(leg.get("confidence_score") or 50) / 10.0
            # Positive American odds are underdogs in the displayed market.
            if payload.get("american_odds") is not None and float(payload["american_odds"]) > 0:
                dog_count += 1
        combos.append({
            "picks": picks,
            "underdogs": dog_count,
            "parlay_decimal": round(dec, 4),
            "parlay_american": _decimal_to_american(dec),
            "implied_win_probability_pct": round((1.0 / dec) * 100.0, 2) if dec > 0 else None,
            "consensus_win_probability_pct": round(prob * 100.0, 2),
            "confidence_score": round(score, 2),
        })
    combos.sort(key=lambda r: (r["confidence_score"], r["consensus_win_probability_pct"]), reverse=True)
    for idx, combo in enumerate(combos, start=1):
        combo["rank"] = idx
    return {
        "ok": True,
        "card_type": "three_leg_mlb_parlay",
        "construction_mode": "best_available_pre_start_attempt",
        "title": "MLB 3-Leg Parlay Attempt",
        "display_confidence_scores": True,
        "legs": legs,
        "ranked_combos": combos,
    }


def hot_sides(limit: int = 40, store: bool = False, include_no_edge: bool = True) -> Dict[str, Any]:
    data = movement_deltas(limit=limit)
    snapshots = _recent_snapshots(limit=limit)
    latest_snapshot = snapshots[-1] if snapshots else {}
    latest_games = _game_index(latest_snapshot) if latest_snapshot else {}
    rows_by_key: Dict[str, Dict[str, Any]] = {}
    rows = []
    actionable_rows = []
    stored_count = 0
    storage_errors: List[str] = []
    now = _now_iso()
    slate_date = latest_snapshot.get("slate_date_et") or _slate_date_et()
    status_counts: Dict[str, int] = {}

    for row in data.get("deltas", []):
        latest_game = latest_games.get(row.get("game_key"))
        status = row.get("prediction_status") or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1
        is_actionable = status != "NO_EDGE"
        if not include_no_edge and not is_actionable:
            continue
        row = {**row, "display_confidence_scores": True, "confidence_score": _confidence_score(row), "confidence_label": _confidence_label(row)}
        row = _enrich_attempted_winner(row, latest_game)
        rows_by_key[row.get("game_key")] = row

    # Guarantee one attempted game-winner row for every available MLB moneyline game, even if the market is flat.
    for game_key, game in latest_games.items():
        if game_key not in rows_by_key:
            simple = _simple_no_edge_row(game, latest_snapshot.get("asof"))
            if simple:
                rows_by_key[game_key] = _enrich_attempted_winner(simple, game)

    for row in rows_by_key.values():
        status = row.get("prediction_status") or "UNKNOWN"
        is_actionable = status != "NO_EDGE"
        if is_actionable:
            actionable_rows.append(row)
        item = _prediction_item(row, slate_date, now)
        stored_prediction_key = None
        if store:
            if predictions_tbl is None:
                storage_errors.append("PREDICTIONS_TABLE not configured")
            else:
                predictions_tbl.put_item(Item=_to_ddb(item))
                stored_count += 1
                stored_prediction_key = item["SK"]
        rows.append({**row, "stored_prediction_key": stored_prediction_key})

    rows.sort(key=lambda x: (x.get("prediction_status") != "NO_EDGE", x.get("confidence_score") or 0, (x.get("favorite") or {}).get("gap") or 0), reverse=True)
    parlay = _build_three_leg_parlay(rows, latest_games)

    return {
        "ok": True,
        "sport": "mlb",
        "stored": store,
        "stored_count": stored_count,
        "storage_status": "CONNECTED" if store and predictions_tbl is not None else ("NOT_REQUESTED" if not store else "NOT_CONFIGURED"),
        "storage_errors": storage_errors,
        "count": len(rows),
        "movement_count": data.get("count", 0),
        "individual_prediction_count": len(rows),
        "actionable_count": len(actionable_rows),
        "status_counts": status_counts,
        "target_success_rate": 75,
        "display_confidence_scores": True,
        "message": "MLB game-winner attempts and the 3-leg parlay attempt are returned for pre-start display; No Clean Edge rows remain marked as No Clean Edge.",
        "previous_asof": data.get("previous_asof"),
        "latest_asof": latest_snapshot.get("asof") or data.get("latest_asof"),
        "game_predictions": rows,
        "three_leg_parlay": parlay,
        "hot_sides": rows,
    }


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
    return {"ok": True, "sport": "mlb", "slate_date_et": slate_date, "outcomes_count": len(outcomes), "predictions_count": len(predictions), "graded_count": len(graded), "correct": len(correct), "wrong": len(graded) - len(correct), "accuracy_pct": accuracy, "target_success_rate": 75, "outcomes": outcomes}


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
            "6_game_winner_attempts_all_games": "CONNECTED",
            "7_three_leg_parlay_attempt": "CONNECTED",
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
