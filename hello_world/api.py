import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from nba_algorithm import rank_nba_b11c1
from mlb_algorithm import rank_mlb_b10a3
from mlb_audit import pull_mlb_results, evaluate_mlb_predictions, mlb_training_export
from mlb_signal_api import audit_snapshots, audit_game, movement_deltas, hot_sides, results_status, source_status
from soccer_signal_api import soccer_audit_snapshots, soccer_movement_deltas, soccer_hot_sides
from soccer_audit import soccer_results_status, soccer_source_status
from sports_discovery import discover_available_sports


dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNALS_TABLE = os.environ.get("SIGNALS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
PREDICTIONS_TABLE = os.environ.get("PREDICTIONS_TABLE", "")
OUTCOMES_TABLE = os.environ.get("OUTCOMES_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signals_tbl = dynamodb.Table(SIGNALS_TABLE) if SIGNALS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None
predictions_tbl = dynamodb.Table(PREDICTIONS_TABLE) if PREDICTIONS_TABLE else None

SPORT_KEYS = {"nba": "basketball_nba", "ncaam": "basketball_ncaab", "mlb": "baseball_mlb"}
PREFERRED_BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "lowvig"]
PANEL_BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars"]
STRONG_GAP = 0.08
COINFLIP_GAP = 0.05
HOT_MOVE_THRESHOLD = 0.015
ODDS_MARKETS = "h2h,spreads,totals"


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _resp(status: int, body: Any) -> Dict[str, Any]:
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


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        payload = json.loads(body)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _normalize_team(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def _game_key_day(sport: str, slate_date_et: str, away_team: Optional[str], home_team: Optional[str]) -> str:
    return f"{sport}|{slate_date_et}|{_normalize_team(away_team)}|{_normalize_team(home_team)}"


def _american_to_prob(american: int) -> float:
    if american == 0:
        raise ValueError("American odds cannot be 0")
    return abs(american) / (abs(american) + 100.0) if american < 0 else 100.0 / (american + 100.0)


def _vig_norm(p1: float, p2: float) -> Tuple[float, float]:
    total = p1 + p2
    return (p1 / total, p2 / total) if total > 0 else (0.5, 0.5)


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _oddsapi_url(sport: str) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    sport_key = SPORT_KEYS.get(sport)
    if not sport_key:
        raise ValueError(f"Unsupported sport: {sport}")
    params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": ODDS_MARKETS, "oddsFormat": "american", "dateFormat": "iso"}
    return f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/?" + urllib.parse.urlencode(params)


def _filter_games_by_slate_date(games: List[Dict[str, Any]], slate_date_et: str) -> List[Dict[str, Any]]:
    eastern = ZoneInfo("America/New_York")
    out = []
    for game in games or []:
        commence = game.get("commence_time")
        if not commence:
            continue
        try:
            commence_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        except ValueError:
            continue
        if commence_dt.astimezone(eastern).strftime("%Y-%m-%d") == slate_date_et:
            out.append(game)
    return out


def _market(bookmaker: Dict[str, Any], market_key: str) -> Optional[Dict[str, Any]]:
    return next((m for m in bookmaker.get("markets", []) or [] if m.get("key") == market_key), None)


def _extract_h2h(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, int]]:
    market = _market(bookmaker, "h2h")
    if not market:
        return None
    home_price = away_price = None
    for outcome in market.get("outcomes", []) or []:
        if outcome.get("name") == home:
            home_price = outcome.get("price")
        elif outcome.get("name") == away:
            away_price = outcome.get("price")
    if home_price is None or away_price is None:
        return None
    return {"home": int(home_price), "away": int(away_price)}


def _extract_spread(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "spreads")
    if not market:
        return None
    home_point = home_price = away_point = away_price = None
    for outcome in market.get("outcomes", []) or []:
        if outcome.get("name") == home:
            home_point = outcome.get("point")
            home_price = outcome.get("price")
        elif outcome.get("name") == away:
            away_point = outcome.get("point")
            away_price = outcome.get("price")
    if home_point is None or home_price is None or away_point is None or away_price is None:
        return None
    return {"home_point": float(home_point), "home_price": int(home_price), "away_point": float(away_point), "away_price": int(away_price)}


def _extract_total(bookmaker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "totals")
    if not market:
        return None
    over_point = over_price = under_point = under_price = None
    for outcome in market.get("outcomes", []) or []:
        name = (outcome.get("name") or "").lower()
        if name == "over":
            over_point = outcome.get("point")
            over_price = outcome.get("price")
        elif name == "under":
            under_point = outcome.get("point")
            under_price = outcome.get("price")
    if over_point is None or over_price is None or under_point is None or under_price is None:
        return None
    return {"over_point": float(over_point), "over_price": int(over_price), "under_point": float(under_point), "under_price": int(under_price)}


def _compact_markets(raw_games: List[Dict[str, Any]], sport: str, slate_date_et: str) -> Dict[str, Any]:
    games_out = []
    all_books = set()
    for raw_game in raw_games or []:
        home = raw_game.get("home_team")
        away = raw_game.get("away_team")
        game_id = raw_game.get("id")
        commence_time = raw_game.get("commence_time")
        if not home or not away:
            continue
        books_out: Dict[str, Any] = {}
        for bookmaker in raw_game.get("bookmakers", []) or []:
            book_key = (bookmaker.get("key") or "").lower().strip()
            if not book_key:
                continue
            book_payload: Dict[str, Any] = {}
            ml = _extract_h2h(bookmaker, home, away)
            spread = _extract_spread(bookmaker, home, away)
            total = _extract_total(bookmaker)
            if ml:
                book_payload["ml"] = ml
            if spread:
                book_payload["spread"] = spread
            if total:
                book_payload["total"] = total
            if book_payload:
                books_out[book_key] = book_payload
                all_books.add(book_key)
        game_key = _game_key_day(sport, slate_date_et, away, home)
        games_out.append({"id": game_id or game_key, "game_key": game_key, "internal_key": game_key, "commence_time": commence_time, "home_team": home, "away_team": away, "books": books_out, "markets_stored": ["ml", "spread", "total"]})
    return {"games": games_out, "count": len(games_out), "available_book_keys": sorted(all_books), "panel_books": PANEL_BOOKS, "markets": ["ml", "spread", "total"]}


def _store_snapshot(run_type: str, data: Dict[str, Any], slate_date_et: str, t: Optional[str], sport: str) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    asof = _now_iso()
    slate_id = f"{sport.upper()}_{slate_date_et}_{run_type}"
    sk = f"{t}#DATE#{slate_date_et}#ASOF#{asof}#SLATE#{slate_id}" if t else f"DATE#{slate_date_et}#ASOF#{asof}#SLATE#{slate_id}"
    item = {"PK": f"SPORT#{sport}", "SK": sk, "sport": sport, "t": t, "slate_id": slate_id, "slate_date_et": slate_date_et, "asof": asof, "created_at": asof, "data": data, "meta": {"source": "theOddsAPI", "run_type": run_type, "pulled_at": asof, "markets": ["h2h", "spreads", "totals"]}}
    snapshots_tbl.put_item(Item=item)
    return item


def _pull_snapshot(sport: str, run_type: str, t: Optional[str] = None) -> Dict[str, Any]:
    sport = (sport or "").lower()
    slate_date_et = _get_slate_date_et()
    raw = _http_get_json(_oddsapi_url(sport))
    compact = _compact_markets(_filter_games_by_slate_date(raw, slate_date_et), sport, slate_date_et)
    stored = _store_snapshot(run_type, compact, slate_date_et, t, sport)
    return {"ok": True, "sport": sport, "t": t, "slate_date_et": slate_date_et, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}, "available_book_keys": compact["available_book_keys"], "markets": compact["markets"]}


def _latest_snapshot(t: Optional[str], sport: str) -> Optional[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    if not t:
        raise ValueError("t is required for latest snapshot lookup")
    today_et = _get_slate_date_et()
    response = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}") & Key("SK").begins_with(f"{t}#DATE#{today_et}"), ScanIndexForward=False, Limit=1)
    items = response.get("Items", [])
    return items[0] if items else None


def _recent_snapshots(sport: str, limit: int = 30) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    response = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}"), ScanIndexForward=False, Limit=limit)
    return sorted(response.get("Items", []), key=lambda x: x.get("asof") or "")


def _best_ml_for_engine(game: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    books = game.get("books", {}) or {}
    for book in PREFERRED_BOOKS:
        ml = (books.get(book) or {}).get("ml", {})
        if ml.get("home") is not None and ml.get("away") is not None:
            return {"book": book, "home": int(ml["home"]), "away": int(ml["away"])}
    for book, data in books.items():
        ml = (data or {}).get("ml", {})
        if ml.get("home") is not None and ml.get("away") is not None:
            return {"book": book, "home": int(ml["home"]), "away": int(ml["away"])}
    return None


def _favorite_from_ml(ml: Dict[str, int], home_team: str, away_team: str) -> Tuple[str, str, float, Dict[str, float]]:
    home_p, away_p = _vig_norm(_american_to_prob(int(ml["home"])), _american_to_prob(int(ml["away"])))
    if home_p >= away_p:
        return home_team, away_team, home_p - away_p, {"home": home_p, "away": away_p}
    return away_team, home_team, away_p - home_p, {"home": home_p, "away": away_p}


def _panel_side_probs(game: Dict[str, Any]) -> Dict[str, float]:
    home_vals = []
    away_vals = []
    for book in PANEL_BOOKS:
        ml = (game.get("books", {}).get(book) or {}).get("ml")
        if not ml:
            continue
        home_p, away_p = _vig_norm(_american_to_prob(int(ml["home"])), _american_to_prob(int(ml["away"])))
        home_vals.append(home_p)
        away_vals.append(away_p)
    if not home_vals or not away_vals:
        ml_pack = _best_ml_for_engine(game)
        if not ml_pack:
            return {}
        home_p, away_p = _vig_norm(_american_to_prob(ml_pack["home"]), _american_to_prob(ml_pack["away"]))
        return {"home": home_p, "away": away_p}
    return {"home": sum(home_vals) / len(home_vals), "away": sum(away_vals) / len(away_vals)}


def _game_index(snapshot: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {g.get("game_key") or g.get("id"): g for g in snapshot.get("data", {}).get("games", []) if g.get("game_key") or g.get("id")}


def _classify_game_simple(game: Dict[str, Any]) -> Dict[str, Any]:
    home = game.get("home_team") or game.get("home")
    away = game.get("away_team") or game.get("away")
    ml_pack = _best_ml_for_engine(game)
    if not home or not away:
        return {"class": "INELIGIBLE", "factors": ["MISSING_TEAMS"], "game": game}
    if not ml_pack:
        return {"class": "INELIGIBLE", "factors": ["NO_MONEYLINE_ODDS"], "game": game}
    ml = {"home": ml_pack["home"], "away": ml_pack["away"]}
    favorite, dog, gap, p_norm = _favorite_from_ml(ml, home, away)
    factors = ["COMPRESSED_MARKET"] if gap < COINFLIP_GAP else []
    class_name = "STRONG_SOLID" if gap >= STRONG_GAP else "COIN_FLIP" if gap < COINFLIP_GAP else "SOLID"
    return {"game_id": game.get("id") or game.get("game_key"), "game_key": game.get("game_key"), "home_team": home, "away_team": away, "commence_time": game.get("commence_time"), "book": ml_pack["book"], "ml": ml, "p_norm": {"home": round(p_norm["home"], 4), "away": round(p_norm["away"], 4)}, "favorite": favorite, "dog": dog, "gap": round(gap, 4), "class": class_name, "factors": factors, "disallowed": False}


def _game_to_rank_input(game: Dict[str, Any]) -> Dict[str, Any]:
    return {"game_id": game["game_id"], "home": game["home_team"], "away": game["away_team"], "ml": game["ml"]}


def _rank_for_sport(sport: str, games: List[Dict[str, Any]]) -> Dict[str, Any]:
    return rank_mlb_b10a3(games) if sport == "mlb" else rank_nba_b11c1(games)


def _class_counts(classified: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for game in classified:
        key = game.get("class", "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_parlays_from_latest(sport: str, max_parlays: int) -> Dict[str, Any]:
    required_t = ["T1", "T2", "T3", "T4"]
    snapshots = {t: _latest_snapshot(t, sport) for t in required_t}
    missing = [t for t, snapshot in snapshots.items() if snapshot is None]
    slate_date_et = _get_slate_date_et()
    if missing:
        return {"ok": True, "sport": sport, "slate_date_et": slate_date_et, "parlays_requested": max_parlays, "parlays_built": 0, "refusal": {"code": "MISSING_REQUIRED_T_SNAPSHOTS", "reason": "T1, T2, T3, and T4 must exist before building.", "missing": missing}}
    games_t4 = snapshots["T4"].get("data", {}).get("games", [])
    classified = [_classify_game_simple(game) for game in games_t4]
    eligible = [game for game in classified if game.get("class") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}]
    eligible.sort(key=lambda game: (game.get("class") == "STRONG_SOLID", game.get("gap", 0)), reverse=True)
    built = []
    used_game_ids = set()
    for _ in range(max_parlays):
        available = [game for game in eligible if game["game_id"] not in used_game_ids]
        strong = [game for game in available if game["class"] == "STRONG_SOLID"]
        variable = [game for game in available if game["class"] in {"COIN_FLIP", "SOLID"}]
        if len(strong) >= 2 and variable:
            slate = strong[:2] + [variable[0]]
        elif len(available) >= 3 and len(strong) >= 1:
            slate = available[:3]
        else:
            break
        ranked = _rank_for_sport(sport, [_game_to_rank_input(game) for game in slate])
        for game in slate:
            used_game_ids.add(game["game_id"])
        built.append({"structure": "2_STRONG_1_VARIABLE" if len([g for g in slate if g["class"] == "STRONG_SOLID"]) >= 2 else "BEST_AVAILABLE", "legs": slate, "ranking": ranked})
    refusal = None if len(built) >= max_parlays else {"code": "INSUFFICIENT_ELIGIBLE_GAMES", "reason": "Could not build all requested no-overlap parlays from eligible T4 games.", "eligible_games": len(eligible), "class_counts": _class_counts(classified)}
    return {"ok": True, "sport": sport, "model": "MLB-B1.0A.3" if sport == "mlb" else "B1.1C-clean", "slate_date_et": slate_date_et, "parlays_requested": max_parlays, "parlays_built": len(built), "refusal": refusal, "parlays": built}


def _generate_hot_predictions(sport: str, limit: int, store: bool) -> Dict[str, Any]:
    snapshots = _recent_snapshots(sport, limit)
    if len(snapshots) < 2:
        return {"ok": True, "sport": sport, "predictions": [], "message": "Need at least two snapshots before generating hot predictions."}
    previous = snapshots[-2]
    latest = snapshots[-1]
    prev_games = _game_index(previous)
    predictions = []
    now = _now_iso()
    slate_date = latest.get("slate_date_et") or _get_slate_date_et()
    for game_key, latest_game in _game_index(latest).items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        latest_probs = _panel_side_probs(latest_game)
        prev_probs = _panel_side_probs(prev_game)
        if not latest_probs or not prev_probs:
            continue
        home_delta = latest_probs["home"] - prev_probs["home"]
        away_delta = latest_probs["away"] - prev_probs["away"]
        if abs(home_delta) < HOT_MOVE_THRESHOLD and abs(away_delta) < HOT_MOVE_THRESHOLD:
            continue
        if home_delta >= away_delta:
            team = latest_game.get("home_team")
            side = "home"
            delta = home_delta
        else:
            team = latest_game.get("away_team")
            side = "away"
            delta = away_delta
        confidence = min(95, max(50, round(50 + abs(delta) * 1000)))
        prediction_id = f"{sport}#{slate_date}#{game_key}#{latest.get('asof')}".replace(" ", "_")
        item = {"PK": f"PRED#{sport}#{slate_date}", "SK": f"PRED#{latest.get('asof')}#{game_key}", "prediction_id": prediction_id, "sport": sport, "slate_date_et": slate_date, "created_at": now, "asof": latest.get("asof"), "previous_asof": previous.get("asof"), "game_key": game_key, "game_id": latest_game.get("id"), "home_team": latest_game.get("home_team"), "away_team": latest_game.get("away_team"), "market": "moneyline", "prediction_type": "HOT_TEAM_MOVEMENT", "predicted_team": team, "predicted_side": side, "home_delta": Decimal(str(round(home_delta, 5))), "away_delta": Decimal(str(round(away_delta, 5))), "confidence": Decimal(str(confidence)), "target_success_rate": Decimal("75"), "status": "OPEN", "evaluation": {}}
        predictions.append(item)
        if store and predictions_tbl is not None:
            predictions_tbl.put_item(Item=item)
    return {"ok": True, "sport": sport, "stored": bool(store and predictions_tbl is not None), "count": len(predictions), "target_success_rate": 75, "predictions": predictions}


def _record_prediction_result(body: Dict[str, Any]) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    sport = (body.get("sport") or "mlb").lower()
    slate_date = body.get("slate_date_et") or _get_slate_date_et()
    sk = body.get("sk") or body.get("SK")
    if not sk:
        raise ValueError("Provide prediction SK to evaluate")
    success = bool(body.get("success"))
    evaluation = body.get("evaluation") or {}
    predictions_tbl.update_item(Key={"PK": f"PRED#{sport}#{slate_date}", "SK": sk}, UpdateExpression="SET #s=:s, evaluated_at=:e, success=:x, evaluation=:v", ExpressionAttributeNames={"#s": "status"}, ExpressionAttributeValues={":s": "CORRECT" if success else "WRONG", ":e": _now_iso(), ":x": success, ":v": evaluation})
    return {"ok": True, "sport": sport, "slate_date_et": slate_date, "sk": sk, "status": "CORRECT" if success else "WRONG"}


def _prediction_accuracy(sport: str, slate_date: Optional[str]) -> Dict[str, Any]:
    if predictions_tbl is None:
        raise RuntimeError("PREDICTIONS_TABLE not configured")
    slate_date = slate_date or _get_slate_date_et()
    resp = predictions_tbl.query(KeyConditionExpression=Key("PK").eq(f"PRED#{sport}#{slate_date}"))
    items = resp.get("Items", [])
    evaluated = [i for i in items if i.get("status") in {"CORRECT", "WRONG"}]
    correct = [i for i in evaluated if i.get("status") == "CORRECT"]
    rate = round((len(correct) / len(evaluated)) * 100, 2) if evaluated else None
    return {"ok": True, "sport": sport, "slate_date_et": slate_date, "target_success_rate": 75, "total_predictions": len(items), "evaluated": len(evaluated), "correct": len(correct), "wrong": len(evaluated) - len(correct), "accuracy_pct": rate, "meets_target": rate is not None and rate >= 75}


def compute_game_signals(sport: str, t: Optional[str], slate_date_et: str, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
    signals = []
    for game in snapshot.get("data", {}).get("games", []) if snapshot else []:
        classified = _classify_game_simple(game)
        if classified.get("class") == "INELIGIBLE":
            continue
        signals.append({"game_id": classified["game_id"], "game_key": classified.get("game_key"), "sport": sport, "t": t, "slate_date_et": slate_date_et, "home_team": classified["home_team"], "away_team": classified["away_team"], "book": classified["book"], "class": classified["class"], "favorite": classified["favorite"], "dog": classified["dog"], "gap": Decimal(str(classified["gap"])), "factors": classified["factors"], "created_at": _now_iso()})
    return signals


def _store_signals(sport: str, t: Optional[str], slate_date_et: str, snapshot: Dict[str, Any]) -> None:
    if signal_ledger_tbl is None or not t:
        return
    for signal in compute_game_signals(sport, t, slate_date_et, snapshot):
        signal_ledger_tbl.put_item(Item={"PK": f"LEDGER#{sport}#{slate_date_et}#{t}", "SK": f"GAME#{signal['game_id']}", **signal})


def _pull_and_store_with_signals(sport: str, run_type: str, t: Optional[str]) -> Dict[str, Any]:
    result = _pull_snapshot(sport, run_type, t)
    snapshot = _latest_snapshot(t, sport) if t else None
    if snapshot:
        _store_signals(sport, t, result["slate_date_et"], snapshot)
    return result


def _query_snapshots(sport: str, t: Optional[str], limit: int) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    slate_date_et = _get_slate_date_et()
    key_condition = Key("PK").eq(f"SPORT#{sport}") & Key("SK").begins_with(f"{t}#DATE#{slate_date_et}") if t else Key("PK").eq(f"SPORT#{sport}")
    response = snapshots_tbl.query(KeyConditionExpression=key_condition, ScanIndexForward=False, Limit=limit)
    return {"ok": True, "sport": sport, "t": t, "items": response.get("Items", [])}


def _query_games(sport: str, t: str) -> Dict[str, Any]:
    snapshot = _latest_snapshot(t, sport)
    if not snapshot:
        return {"ok": True, "sport": sport, "t": t, "games": [], "message": "No snapshot found for this sport/t today."}
    games = snapshot.get("data", {}).get("games", [])
    return {"ok": True, "sport": sport, "t": t, "asof": snapshot.get("asof"), "count": len(games), "games": games}


def _query_odds_history(sport: str, game_key: Optional[str], game_id: Optional[str], limit: int) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    response = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}"), ScanIndexForward=False, Limit=limit)
    timeline = []
    for item in response.get("Items", []):
        for game in item.get("data", {}).get("games", []) or []:
            if (game_key and game.get("game_key") == game_key) or (game_id and game.get("id") == game_id):
                timeline.append({"asof": item.get("asof"), "t": item.get("t"), "slate_date_et": item.get("slate_date_et"), "run_type": item.get("meta", {}).get("run_type"), "game": game})
                break
    timeline.sort(key=lambda row: row.get("asof") or "")
    return {"ok": True, "sport": sport, "game_key": game_key, "game_id": game_id, "count": len(timeline), "timeline": timeline}


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    if method == "GET" and path in {"/", "/health", "/v1/health"}:
        return _resp(200, {"ok": True, "status": "healthy", "service": "parlay-platform", "ts": _now_iso()})
    if method == "GET" and path == "/v1/sports/available":
        try:
            return _resp(200, discover_available_sports())
        except Exception as exc:
            return _resp(500, {"ok": False, "error": str(exc)})
    if method == "GET" and path == "/v1/snapshots":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _query_snapshots((params.get("sport") or "nba").lower(), params.get("t"), min(int(params.get("limit") or 10), 100)))
    if method == "GET" and path == "/v1/games":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _query_games((params.get("sport") or "mlb").lower(), params.get("t") or "T1"))
    if method == "GET" and path == "/v1/odds/history":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _query_odds_history((params.get("sport") or "mlb").lower(), params.get("game_key"), params.get("game_id"), min(int(params.get("limit") or 200), 500)))
    if method == "GET" and path == "/v1/predictions/hot":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _generate_hot_predictions((params.get("sport") or "mlb").lower(), min(int(params.get("limit") or 30), 100), params.get("store", "false").lower() == "true"))
    if method == "GET" and path == "/v1/predictions/accuracy":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _prediction_accuracy((params.get("sport") or "mlb").lower(), params.get("slate_date_et")))
    if method == "GET" and path == "/v1/audit/mlb/snapshots":
        params = event.get("queryStringParameters") or {}
        return _resp(200, audit_snapshots(min(int(params.get("limit") or 20), 100)))
    if method == "GET" and path == "/v1/audit/mlb/game":
        params = event.get("queryStringParameters") or {}
        return _resp(200, audit_game(params.get("game_key"), min(int(params.get("limit") or 50), 200)))
    if method == "GET" and path == "/v1/signals/mlb/deltas":
        params = event.get("queryStringParameters") or {}
        return _resp(200, movement_deltas(min(int(params.get("limit") or 40), 200)))
    if method == "GET" and path == "/v1/predictions/mlb/hot-sides":
        params = event.get("queryStringParameters") or {}
        return _resp(200, hot_sides(min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true"))
    if method == "GET" and path == "/v1/results/mlb/status":
        params = event.get("queryStringParameters") or {}
        return _resp(200, results_status(params.get("slate_date_et")))
    if method == "GET" and path == "/v1/sources/mlb/status":
        return _resp(200, source_status())
    if method == "GET" and path == "/v1/audit/soccer/snapshots":
        params = event.get("queryStringParameters") or {}
        return _resp(200, soccer_audit_snapshots(min(int(params.get("limit") or 20), 100)))
    if method == "GET" and path == "/v1/signals/soccer/deltas":
        params = event.get("queryStringParameters") or {}
        return _resp(200, soccer_movement_deltas(min(int(params.get("limit") or 40), 200)))
    if method == "GET" and path == "/v1/predictions/soccer/hot-sides":
        params = event.get("queryStringParameters") or {}
        return _resp(200, soccer_hot_sides(min(int(params.get("limit") or 40), 200), params.get("store", "false").lower() == "true"))
    if method == "GET" and path == "/v1/results/soccer/status":
        params = event.get("queryStringParameters") or {}
        return _resp(200, soccer_results_status(params.get("slate_date_et")))
    if method == "GET" and path == "/v1/sources/soccer/status":
        return _resp(200, soccer_source_status())
    if method == "GET" and path == "/v1/audit/mlb/training":
        params = event.get("queryStringParameters") or {}
        return _resp(200, mlb_training_export(params.get("slate_date_et")))
    if method == "POST" and path == "/v1/results/pull/mlb":
        body = _parse_json(event.get("body"))
        return _resp(200, pull_mlb_results(int(body.get("days_from", 3))))
    if method == "POST" and path == "/v1/audit/mlb/evaluate":
        body = _parse_json(event.get("body"))
        return _resp(200, evaluate_mlb_predictions(body.get("slate_date_et")))
    if method == "POST" and path == "/v1/predictions/result":
        return _resp(200, _record_prediction_result(_parse_json(event.get("body"))))
    if method == "POST" and path in {"/v1/pull/nba", "/v1/pull/ncaam", "/v1/pull/mlb"}:
        sport = path.rsplit("/", 1)[-1]
        body = _parse_json(event.get("body"))
        try:
            return _resp(200, _pull_and_store_with_signals(sport, body.get("run", "manual"), body.get("t")))
        except Exception as exc:
            return _resp(500, {"ok": False, "sport": sport, "error": str(exc)})
    if method == "POST" and path in {"/v1/rank/nba", "/v1/rank/mlb"}:
        sport = path.rsplit("/", 1)[-1]
        body = _parse_json(event.get("body"))
        games = body.get("games")
        if not isinstance(games, list) or len(games) != 3:
            return _resp(400, {"ok": False, "error": "Provide exactly 3 games in body.games"})
        return _resp(200, _rank_for_sport(sport, games))
    if method == "POST" and path == "/v1/build/mlb/3":
        return _resp(200, _build_parlays_from_latest("mlb", 3))
    if method == "POST" and path == "/v1/build/nba/4":
        return _resp(200, _build_parlays_from_latest("nba", 4))
    if method == "POST" and path == "/v1/build/ncaam/b1c23":
        body = _parse_json(event.get("body"))
        return _resp(200, _build_parlays_from_latest("ncaam", min(max(int(body.get("max_parlays", 1)), 1), 7)))
    if method == "POST" and path.startswith("/v1/build/ncaam/"):
        try:
            return _resp(200, _build_parlays_from_latest("ncaam", min(max(int(path.rstrip("/").split("/")[-1]), 1), 7)))
        except ValueError:
            return _resp(400, {"ok": False, "error": "Invalid max_parlays value"})
    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    event = event or {}
    sport = (event.get("sport") or "").lower()
    t = event.get("t")
    run_type = event.get("run") or "scheduled"
    if sport not in SPORT_KEYS:
        return _resp(400, {"ok": False, "error": "Unsupported sport", "sport": sport})
    try:
        return _resp(200, {"ok": True, "sport": sport, "t": t, "run": run_type, "result": _pull_and_store_with_signals(sport, run_type, t)})
    except Exception as exc:
        return _resp(500, {"ok": False, "sport": sport, "t": t, "run": run_type, "error": str(exc)})
