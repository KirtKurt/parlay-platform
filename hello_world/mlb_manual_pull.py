import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from audit_ledger import record_no_edge_prediction_rows, record_snapshot_audit
from mlb_signal_api import _delta_for_game, _game_index


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None

SPORT_KEY = "baseball_mlb"
ODDS_MARKETS = "h2h,spreads,totals"
DEFAULT_DAYS_AHEAD = 1
ML_FEATURE_VERSION = "mlb_hot_pull_movement_features_v1"
HOT_ONLY_POLICY = "MLB_B1_15_MIN_HOT_ONLY"


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ddb_safe(v) for v in value]
    return value


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
            "access-control-allow-methods": "POST,OPTIONS",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _event_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    body = _parse_json(event.get("body"))
    if body:
        return body
    return event if isinstance(event, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _game_date_et(commence_time: Optional[str]) -> Optional[str]:
    if not commence_time:
        return None
    try:
        dt = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_url() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return f"https://api.the-odds-api.com/v4/sports/{SPORT_KEY}/odds/?" + urllib.parse.urlencode(params)


def _oddsapi_auth_diagnostic(exc: Exception) -> Optional[Dict[str, Any]]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            upstream_body = exc.read().decode("utf-8")[:300]
        except Exception:
            upstream_body = ""
        return {
            "source": "theOddsAPI",
            "http_status": exc.code,
            "reason": exc.reason,
            "message": "Live MLB odds pull was rejected by the upstream odds feed.",
            "odds_api_key_present": bool(ODDS_API_KEY),
            "odds_api_key_length": len(ODDS_API_KEY or ""),
            "secret_exposed": False,
            "upstream_body_sample": upstream_body,
        }
    if str(exc) == "ODDS_API_KEY missing":
        return {
            "source": "theOddsAPI",
            "http_status": None,
            "reason": "missing_key",
            "message": "ODDS_API_KEY is not configured on the deployed Lambda.",
            "odds_api_key_present": False,
            "odds_api_key_length": 0,
            "secret_exposed": False,
        }
    return None


def _transparent_cached_pre_start_response(payload: Dict[str, Any], live_pull_error: Exception) -> Optional[Dict[str, Any]]:
    run = str(payload.get("run") or "")
    if "pre_start" not in run and "final" not in run:
        return None
    diagnostic = _oddsapi_auth_diagnostic(live_pull_error)
    if not diagnostic:
        return None
    try:
        from mlb_date_signal_api import hot_sides
        game_date = payload.get("game_date_et") or payload.get("slate_date_et") or _slate_date_et()
        cached = hot_sides(game_date=game_date, limit=80, store=True, include_no_edge=True)
        return {
            **cached,
            "ok": True,
            "sport": "mlb",
            "live_pull_ok": False,
            "fallback_used": True,
            "fallback_type": "latest_stored_date_isolated_snapshots_after_live_pull_auth_failure",
            "source_status": {
                "live_odds_feed": "AUTH_FAILED",
                "cached_snapshot_analysis": "USED",
                "predictions_storage": cached.get("storage_status"),
            },
            "upstream_error": diagnostic,
            "message": "Live MLB odds refresh failed because the upstream odds feed rejected authorization. Returned latest stored MLB date-isolated analysis.",
        }
    except Exception as fallback_exc:
        return {
            "ok": False,
            "sport": "mlb",
            "live_pull_ok": False,
            "fallback_used": False,
            "source_status": {"live_odds_feed": "AUTH_FAILED", "cached_snapshot_analysis": "FAILED"},
            "upstream_error": diagnostic,
            "fallback_error": str(fallback_exc),
            "message": "Live pull failed and cached pre-start analysis could not be generated.",
        }


def _filter_upcoming_et(games: List[Dict[str, Any]], *, start_date: str, days_ahead: int) -> List[Dict[str, Any]]:
    eastern = ZoneInfo("America/New_York")
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=eastern)
    allowed = {(start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(max(0, days_ahead) + 1)}
    out = []
    for game in games or []:
        game_date = _game_date_et(game.get("commence_time"))
        if game_date in allowed:
            out.append(game)
    return out


def _market(bookmaker: Dict[str, Any], key: str) -> Optional[Dict[str, Any]]:
    return next((m for m in bookmaker.get("markets", []) or [] if m.get("key") == key), None)


def _h2h(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, int]]:
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


def _spread(bookmaker: Dict[str, Any], home: str, away: str) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "spreads")
    if not market:
        return None
    result: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        if outcome.get("name") == home:
            result["home_point"] = outcome.get("point")
            result["home_price"] = outcome.get("price")
        elif outcome.get("name") == away:
            result["away_point"] = outcome.get("point")
            result["away_price"] = outcome.get("price")
    return result if len(result) == 4 else None


def _total(bookmaker: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    market = _market(bookmaker, "totals")
    if not market:
        return None
    result: Dict[str, Any] = {}
    for outcome in market.get("outcomes", []) or []:
        name = (outcome.get("name") or "").lower()
        if name == "over":
            result["over_point"] = outcome.get("point")
            result["over_price"] = outcome.get("price")
        elif name == "under":
            result["under_point"] = outcome.get("point")
            result["under_price"] = outcome.get("price")
    return result if len(result) == 4 else None


def _compact(raw_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    games_out = []
    books_seen = set()
    game_dates_seen = set()
    for raw_game in raw_games or []:
        home = raw_game.get("home_team")
        away = raw_game.get("away_team")
        if not home or not away:
            continue
        game_date = _game_date_et(raw_game.get("commence_time"))
        if not game_date:
            continue
        game_dates_seen.add(game_date)
        books: Dict[str, Any] = {}
        for bookmaker in raw_game.get("bookmakers", []) or []:
            book_key = (bookmaker.get("key") or "").lower().strip()
            if not book_key:
                continue
            payload: Dict[str, Any] = {}
            ml = _h2h(bookmaker, home, away)
            spread = _spread(bookmaker, home, away)
            total = _total(bookmaker)
            if ml:
                payload["ml"] = ml
            if spread:
                payload["spread"] = spread
            if total:
                payload["total"] = total
            if payload:
                books[book_key] = payload
                books_seen.add(book_key)
        game_key = f"mlb|{game_date}|{away.lower()}|{home.lower()}"
        games_out.append({
            "id": raw_game.get("id") or game_key,
            "game_key": game_key,
            "internal_key": game_key,
            "game_date_et": game_date,
            "commence_time": raw_game.get("commence_time"),
            "home_team": home,
            "away_team": away,
            "books": books,
            "markets_stored": ["ml", "spread", "total"],
        })
    return {"games": games_out, "count": len(games_out), "game_dates_et": sorted(game_dates_seen), "available_book_keys": sorted(books_seen), "markets": ["ml", "spread", "total"]}


def _compact_for_game_date(compact: Dict[str, Any], game_date: str) -> Dict[str, Any]:
    games = [game for game in compact.get("games", []) or [] if game.get("game_date_et") == game_date]
    books_seen = sorted({book for game in games for book in (game.get("books") or {}).keys()})
    return {**compact, "games": games, "count": len(games), "game_dates_et": [game_date] if games else [], "available_book_keys": books_seen, "date_isolated": True}


def _store_snapshot_item(*, t: str, slate_date: str, game_date: str, asof: str, run: str, compact: Dict[str, Any], date_isolated: bool, pk: str) -> Dict[str, str]:
    slate_id = f"MLB_DATE_{game_date}_{run}" if date_isolated else f"MLB_ROLLING_{'_'.join(compact.get('game_dates_et') or [slate_date])}_{run}"
    item = {
        "PK": pk,
        "SK": f"{t}#GAME_DATE#{game_date}#PULL_DATE#{slate_date}#ASOF#{asof}#SLATE#{slate_id}",
        "sport": "mlb",
        "t": t,
        "slate_id": slate_id,
        "slate_date_et": game_date,
        "pull_date_et": slate_date,
        "game_date_et": game_date,
        "game_dates_et": compact.get("game_dates_et") or [],
        "rolling_open_pull": True,
        "date_isolated": date_isolated,
        "asof": asof,
        "created_at": asof,
        "data": compact,
        "meta": {
            "source": "theOddsAPI",
            "run_type": run,
            "pulled_at": asof,
            "markets": ["h2h", "spreads", "totals"],
            "pull_policy": "rolling_open_today_plus_tomorrow_every_15_min_date_isolated_hot_only",
            "hot_only_policy": HOT_ONLY_POLICY,
        },
    }
    item = _ddb_safe(item)
    snapshots_tbl.put_item(Item=item)
    return {"pk": item["PK"], "sk": item["SK"]}


def _latest_two_hot_snapshots_for_game_date(game_date: str, limit: int = 12) -> List[Dict[str, Any]]:
    if snapshots_tbl is None:
        return []
    pk = f"SPORT#mlb#DATE#{game_date}"
    resp = snapshots_tbl.query(KeyConditionExpression=Key("PK").eq(pk) & Key("SK").begins_with(f"HOT#GAME_DATE#{game_date}"), ScanIndexForward=False, Limit=limit)
    rows = sorted(resp.get("Items", []), key=lambda x: x.get("asof") or "")
    return rows[-2:]


def _movement_strength(delta: float, agreeing_books: int, disagreeing_books: int) -> str:
    abs_delta = abs(float(delta or 0))
    if abs_delta >= 0.018 and agreeing_books >= 2 and disagreeing_books == 0:
        return "HIGH"
    if abs_delta >= 0.006 and agreeing_books >= 2:
        return "MEDIUM"
    if abs_delta > 0:
        return "LOW"
    return "FLAT"


def _store_hot_movement_features(*, game_date: str, asof: str, run: str) -> Dict[str, Any]:
    if signal_ledger_tbl is None:
        return {"ok": False, "stored": 0, "error": "SIGNAL_LEDGER_TABLE not configured"}
    snaps = _latest_two_hot_snapshots_for_game_date(game_date)
    if len(snaps) < 2:
        return {"ok": True, "stored": 0, "reason": "Need at least two HOT snapshots for this game date."}
    prev_snap, latest_snap = snaps[-2], snaps[-1]
    prev_games = _game_index(prev_snap)
    latest_games = _game_index(latest_snap)
    stored = 0
    errors: List[str] = []
    feature_rows: List[Dict[str, Any]] = []
    for game_key, latest_game in latest_games.items():
        prev_game = prev_games.get(game_key)
        if not prev_game:
            continue
        row = _delta_for_game({**prev_game, "_snapshot_asof": prev_snap.get("asof")}, {**latest_game, "_snapshot_asof": latest_snap.get("asof")})
        if not row.get("ok"):
            continue
        agreement = row.get("book_agreement") or {}
        hot_delta = float(row.get("hot_delta") or 0)
        favorite = row.get("favorite") or {}
        signal_tags = list(row.get("reason_codes") or [])
        strength = _movement_strength(hot_delta, int(agreement.get("agreeing_books") or 0), int(agreement.get("disagreeing_books") or 0))
        if strength != "FLAT":
            signal_tags.append(f"hot_move_{strength.lower()}")
        feature = {
            "PK": f"ML_FEATURE#mlb#{game_date}",
            "SK": f"HOT_DELTA#{latest_snap.get('asof')}#GAME#{game_key}",
            "entity_type": "HOT_PULL_MOVEMENT_FEATURE",
            "sport": "mlb",
            "game_date_et": game_date,
            "game_key": game_key,
            "feature_version": ML_FEATURE_VERSION,
            "created_at": _now_iso(),
            "run": run,
            "date_isolated": True,
            "hot_only": True,
            "previous_asof": prev_snap.get("asof"),
            "latest_asof": latest_snap.get("asof"),
            "hot_team": row.get("hot_team"),
            "hot_delta": hot_delta,
            "movement_strength": strength,
            "favorite_side": favorite.get("side"),
            "favorite_team": favorite.get("team"),
            "dog_side": favorite.get("dog_side"),
            "dog_team": favorite.get("dog_team"),
            "book_agreement": agreement,
            "latest_consensus": row.get("latest_consensus"),
            "previous_consensus": row.get("previous_consensus"),
            "prediction_status_at_feature_time": row.get("prediction_status"),
            "signal_tags": sorted(set(signal_tags)),
            "label_status": "PENDING_RESULT",
        }
        try:
            signal_ledger_tbl.put_item(Item=_ddb_safe(feature))
            stored += 1
            feature_rows.append({"game_key": game_key, "hot_team": row.get("hot_team"), "hot_delta": round(hot_delta, 6), "movement_strength": strength})
        except Exception as exc:
            errors.append(f"{game_key}: {exc}")
    return {"ok": len(errors) == 0, "stored": stored, "previous_asof": prev_snap.get("asof"), "latest_asof": latest_snap.get("asof"), "feature_version": ML_FEATURE_VERSION, "errors": errors, "sample": feature_rows[:10]}


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        payload = _event_payload(event)
        t = str(payload.get("t") or "HOT").upper()
        run = payload.get("run") or "rolling_open_hot_pull"
        if t != "HOT":
            return _resp(200, {
                "ok": True,
                "sport": "mlb",
                "skipped": True,
                "t": t,
                "run": run,
                "hot_only_policy": HOT_ONLY_POLICY,
                "message": "Legacy MLB T1/T2/T3/T4 pull ignored. MLB now stores HOT 15-minute snapshots only.",
            })
        days_ahead = int(payload.get("days_ahead", DEFAULT_DAYS_AHEAD))
        slate_date = _slate_date_et()
        asof = _now_iso()
        raw = _filter_upcoming_et(_http_get_json(_odds_url()), start_date=slate_date, days_ahead=days_ahead)
        compact = _compact(raw)
        if snapshots_tbl is None:
            raise RuntimeError("SNAPSHOTS_TABLE not configured")

        combined_stored = _store_snapshot_item(t=t, slate_date=slate_date, game_date=slate_date, asof=asof, run=run, compact=compact, date_isolated=False, pk="SPORT#mlb")

        isolated_stored = []
        audit_results = []
        prediction_audit_results = []
        hot_movement_feature_results = []
        for game_date in compact.get("game_dates_et") or []:
            date_compact = _compact_for_game_date(compact, game_date)
            if not date_compact.get("games"):
                continue
            stored_item = _store_snapshot_item(t=t, slate_date=slate_date, game_date=game_date, asof=asof, run=run, compact=date_compact, date_isolated=True, pk=f"SPORT#mlb#DATE#{game_date}")
            isolated_stored.append({"game_date_et": game_date, **stored_item, "count": date_compact.get("count")})
            audit_results.append({"game_date_et": game_date, **record_snapshot_audit(sport="mlb", slate_date_et=game_date, asof=asof, t=t, run_type=run, compact_snapshot=date_compact, raw_games=raw)})
            prediction_audit_results.append({"game_date_et": game_date, **record_no_edge_prediction_rows(sport="mlb", slate_date_et=game_date, asof=asof, compact_snapshot=date_compact)})
            hot_movement_feature_results.append({"game_date_et": game_date, **_store_hot_movement_features(game_date=game_date, asof=asof, run=run)})

        return _resp(200, {
            "ok": True,
            "sport": "mlb",
            "live_pull_ok": True,
            "fallback_used": False,
            "t": t,
            "run": run,
            "hot_only_policy": HOT_ONLY_POLICY,
            "pull_policy": "rolling_open_today_plus_tomorrow_every_15_min_date_isolated_hot_only",
            "ml_research_policy": {"enabled": True, "scope": "HOT pulls only", "feature_partition_pk_pattern": "ML_FEATURE#mlb#YYYY-MM-DD"},
            "data_isolation": {"enabled": True, "date_partition_pk_pattern": "SPORT#mlb#DATE#YYYY-MM-DD", "game_key_pattern": "mlb|YYYY-MM-DD|away|home"},
            "pull_date_et": slate_date,
            "game_dates_et": compact.get("game_dates_et") or [],
            "days_ahead": days_ahead,
            "asof": asof,
            "count": compact["count"],
            "stored": combined_stored,
            "date_isolated_stored": isolated_stored,
            "available_book_keys": compact["available_book_keys"],
            "markets": compact["markets"],
            "audit": audit_results,
            "prediction_audit": prediction_audit_results,
            "hot_movement_features": hot_movement_feature_results,
        })
    except Exception as exc:
        payload = _event_payload(event)
        fallback = _transparent_cached_pre_start_response(payload, exc)
        if fallback is not None:
            return _resp(200 if fallback.get("ok") else 500, fallback)
        return _resp(500, {"ok": False, "sport": "mlb", "live_pull_ok": False, "fallback_used": False, "upstream_error": _oddsapi_auth_diagnostic(exc), "error": str(exc)})
