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


dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNALS_TABLE = os.environ.get("SIGNALS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signals_tbl = dynamodb.Table(SIGNALS_TABLE) if SIGNALS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None

SPORT_KEYS = {"nba": "basketball_nba", "ncaam": "basketball_ncaab", "mlb": "baseball_mlb"}
PREFERRED_BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "lowvig"]
PANEL_BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars"]
STRONG_GAP = 0.08
COINFLIP_GAP = 0.05


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
    params = {"apiKey": ODDS_API_KEY, "regions": "us", "markets": "h2h", "oddsFormat": "american", "dateFormat": "iso"}
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


def _compact_h2h(raw_games: List[Dict[str, Any]], sport: str, slate_date_et: str) -> Dict[str, Any]:
    games_out = []
    all_books = set()
    for raw_game in raw_games or []:
        home = raw_game.get("home_team")
        away = raw_game.get("away_team")
        game_id = raw_game.get("id")
        commence_time = raw_game.get("commence_time")
        if not home or not away:
            continue
        books_out = {}
        for bookmaker in raw_game.get("bookmakers", []) or []:
            book_key = (bookmaker.get("key") or "").lower().strip()
            if not book_key:
                continue
            all_books.add(book_key)
            h2h = next((m for m in bookmaker.get("markets", []) or [] if m.get("key") == "h2h"), None)
            if not h2h:
                continue
            home_odds = away_odds = None
            for outcome in h2h.get("outcomes", []) or []:
                if outcome.get("name") == home:
                    home_odds = outcome.get("price")
                elif outcome.get("name") == away:
                    away_odds = outcome.get("price")
            if home_odds is not None and away_odds is not None:
                books_out[book_key] = {"ml": {"home": int(home_odds), "away": int(away_odds)}}
        game_key = _game_key_day(sport, slate_date_et, away, home)
        games_out.append({"id": game_id or game_key, "game_key": game_key, "internal_key": game_key, "commence_time": commence_time, "home_team": home, "away_team": away, "books": books_out})
    return {"games": games_out, "count": len(games_out), "available_book_keys": sorted(all_books), "panel_books": PANEL_BOOKS}


def _store_snapshot(run_type: str, data: Dict[str, Any], slate_date_et: str, t: Optional[str], sport: str) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    asof = _now_iso()
    slate_id = f"{sport.upper()}_{slate_date_et}_{run_type}"
    sk = f"{t}#DATE#{slate_date_et}#ASOF#{asof}#SLATE#{slate_id}" if t else f"DATE#{slate_date_et}#ASOF#{asof}#SLATE#{slate_id}"
    item = {"PK": f"SPORT#{sport}", "SK": sk, "sport": sport, "t": t, "slate_id": slate_id, "slate_date_et": slate_date_et, "asof": asof, "created_at": asof, "data": data, "meta": {"source": "theOddsAPI", "run_type": run_type, "pulled_at": asof}}
    snapshots_tbl.put_item(Item=item)
    return item


def _pull_snapshot(sport: str, run_type: str, t: Optional[str] = None) -> Dict[str, Any]:
    sport = (sport or "").lower()
    slate_date_et = _get_slate_date_et()
    raw = _http_get_json(_oddsapi_url(sport))
    compact = _compact_h2h(_filter_games_by_slate_date(raw, slate_date_et), sport, slate_date_et)
    stored = _store_snapshot(run_type, compact, slate_date_et, t, sport)
    return {"ok": True, "sport": sport, "t": t, "slate_date_et": slate_date_et, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}, "available_book_keys": compact["available_book_keys"]}


def _latest_snapshot(t: Optional[str], sport: str) -> Optional[Dict[str, Any]]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    if not t:
        raise ValueError("t is required for latest snapshot lookup")
    today_et = _get_slate_date_et()
    response = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}") & Key("SK").begins_with(f"{t}#DATE#{today_et}"), ScanIndexForward=False, Limit=1)
    items = response.get("Items", [])
    return items[0] if items else None


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
    counts = {}
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


def lambda_handler(event, context):
    event = event or {}
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"
    if method == "OPTIONS":
        return _resp(200, {"ok": True})
    if method == "GET" and path in {"/", "/health", "/v1/health"}:
        return _resp(200, {"ok": True, "status": "healthy", "service": "parlay-platform", "ts": _now_iso()})
    if method == "GET" and path == "/v1/snapshots":
        params = event.get("queryStringParameters") or {}
        return _resp(200, _query_snapshots((params.get("sport") or "nba").lower(), params.get("t"), min(int(params.get("limit") or 10), 100)))
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
