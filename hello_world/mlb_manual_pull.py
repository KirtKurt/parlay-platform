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

try:
    from audit_ledger import record_no_edge_prediction_rows, record_snapshot_audit
except Exception:  # keep diagnostics alive if optional audit layer is unavailable
    record_no_edge_prediction_rows = None
    record_snapshot_audit = None

try:
    from mlb_signal_api import _delta_for_game, _game_index
except Exception:
    _delta_for_game = None
    _game_index = None

try:
    import inqsi_pull_history as pull_history
except Exception:
    pull_history = None

try:
    import mlb_game_winner_engine
except Exception:
    mlb_game_winner_engine = None


dynamodb = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNAL_LEDGER_TABLE = os.environ.get("SIGNAL_LEDGER_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
MLB_PULL_START_AT_ET = os.environ.get("MLB_PULL_START_AT_ET", "2026-07-03T01:00:00-04:00")
MLB_SCHED_INTERVAL_MINUTES = int(os.environ.get("MLB_SCHED_INTERVAL_MINUTES", "15"))

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signal_ledger_tbl = dynamodb.Table(SIGNAL_LEDGER_TABLE) if SIGNAL_LEDGER_TABLE else None

SPORT_KEY = "baseball_mlb"
ODDS_MARKETS = "h2h,spreads,totals"
DEFAULT_DAYS_AHEAD = 1
PLATFORM_VERSION = "MLB_PREDICTIVE_PLATFORM_V1"
ML_FEATURE_VERSION = "mlb_hot_pull_movement_features_v1"
HOT_ONLY_POLICY = "MLB_B1_15_MIN_HOT_ONLY"
PULL_POLICY = "rolling_open_today_plus_tomorrow_every_15_min_date_isolated_hot_only"
EASTERN = ZoneInfo("America/New_York")


def _ddb_safe(value: Any) -> Any:
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _ddb_safe(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_ddb_safe(v) for v in value]
    return value


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
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
    body = _parse_json(event.get("body")) if isinstance(event, dict) else {}
    query = event.get("queryStringParameters") or {} if isinstance(event, dict) else {}
    payload: Dict[str, Any] = {}
    if isinstance(event, dict) and not event.get("httpMethod") and not event.get("requestContext"):
        payload.update(event)
    if isinstance(query, dict):
        payload.update(query)
    if body:
        payload.update(body)
    return payload


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slate_date_et() -> str:
    return datetime.now(EASTERN).strftime("%Y-%m-%d")


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return None


def _game_date_et(commence_time: Optional[str]) -> Optional[str]:
    parsed = _parse_dt(commence_time)
    return parsed.astimezone(EASTERN).strftime("%Y-%m-%d") if parsed else None


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _odds_url() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": os.environ.get("ODDS_REGIONS", "us"),
        "markets": os.environ.get("ODDS_MARKETS", ODDS_MARKETS),
        "oddsFormat": os.environ.get("ODDS_FORMAT", "american"),
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
            "platformVersion": PLATFORM_VERSION,
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
            "platformVersion": PLATFORM_VERSION,
            "live_pull_ok": False,
            "fallback_used": False,
            "source_status": {"live_odds_feed": "AUTH_FAILED", "cached_snapshot_analysis": "FAILED"},
            "upstream_error": diagnostic,
            "fallback_error": str(fallback_exc),
            "message": "Live pull failed and cached pre-start analysis could not be generated.",
        }


def _parse_start_at_et() -> Optional[datetime]:
    raw = (MLB_PULL_START_AT_ET or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=EASTERN)
        return parsed.astimezone(EASTERN)
    except Exception:
        return None


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "force"}


def _scheduled_start_gate(event: Dict[str, Any], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _truthy(payload.get("force")):
        return None
    is_http = bool(event.get("httpMethod") or event.get("requestContext", {}).get("http"))
    if is_http:
        return None
    start_at = _parse_start_at_et()
    if start_at is None:
        return None
    now_et = datetime.now(EASTERN)
    if now_et >= start_at:
        return None
    return {
        "ok": True,
        "sport": "mlb",
        "platformVersion": PLATFORM_VERSION,
        "skipped": True,
        "reason": "WAITING_FOR_CONFIGURED_1AM_ET_START_GATE",
        "startAtEt": start_at.isoformat(),
        "nowEt": now_et.isoformat(),
        "intervalMinutes": MLB_SCHED_INTERVAL_MINUTES,
        "message": "Scheduled MLB pulls are gated until the configured 1:00am ET start. Manual HTTP pulls or force=true still work for validation.",
    }


def _filter_upcoming_et(games: List[Dict[str, Any]], *, start_date: str, days_ahead: int) -> List[Dict[str, Any]]:
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=EASTERN)
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
        markets_stored = sorted({
            market
            for payload in books.values()
            for market in payload
        })
        games_out.append({
            "id": raw_game.get("id") or game_key,
            "game_id": raw_game.get("id") or game_key,
            "game_key": game_key,
            "internal_key": game_key,
            "game_date_et": game_date,
            "commence_time": raw_game.get("commence_time"),
            "home_team": home,
            "away_team": away,
            "provider_sport_key": SPORT_KEY,
            "books": books,
            "odds_available": bool(books),
            "moneyline_available": any("ml" in payload for payload in books.values()),
            "markets_stored": markets_stored,
        })
    return {
        "games": games_out,
        "count": len(games_out),
        "game_dates_et": sorted(game_dates_seen),
        "available_book_keys": sorted(books_seen),
        "markets": ["ml", "spread", "total"],
    }


def _compact_for_game_date(compact: Dict[str, Any], game_date: str) -> Dict[str, Any]:
    games = [game for game in compact.get("games", []) or [] if game.get("game_date_et") == game_date]
    books_seen = sorted({book for game in games for book in (game.get("books") or {}).keys()})
    return {**compact, "games": games, "count": len(games), "game_dates_et": [game_date] if games else [], "available_book_keys": books_seen, "date_isolated": True}


def _store_snapshot_item(*, t: str, slate_date: str, game_date: str, asof: str, run: str, compact: Dict[str, Any], date_isolated: bool, pk: str) -> Dict[str, str]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")
    slate_id = f"MLB_DATE_{game_date}_{run}" if date_isolated else f"MLB_ROLLING_{'_'.join(compact.get('game_dates_et') or [slate_date])}_{run}"
    item = {
        "PK": pk,
        "SK": f"{t}#GAME_DATE#{game_date}#PULL_DATE#{slate_date}#ASOF#{asof}#SLATE#{slate_id}",
        "sport": "mlb",
        "platform_version": PLATFORM_VERSION,
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
            "provider_sport_key": SPORT_KEY,
            "run_type": run,
            "pulled_at": asof,
            "markets": ["h2h", "spreads", "totals"],
            "interval_minutes": MLB_SCHED_INTERVAL_MINUTES,
            "pull_policy": PULL_POLICY,
            "hot_only_policy": HOT_ONLY_POLICY,
            "line_movement_prediction": True,
        },
    }
    item = _ddb_safe(item)
    snapshots_tbl.put_item(Item=item)
    return {"pk": item["PK"], "sk": item["SK"]}


def _canonical_games(compact: Dict[str, Any]) -> List[Dict[str, Any]]:
    games = []
    for game in compact.get("games") or []:
        if not (game.get("home_team") and game.get("away_team")):
            continue
        games.append({
            "game_id": str(game.get("game_id") or game.get("id") or game.get("game_key")),
            "id": str(game.get("id") or game.get("game_id") or game.get("game_key")),
            "game_key": game.get("game_key"),
            "home_team": game.get("home_team"),
            "away_team": game.get("away_team"),
            "commence_time": game.get("commence_time"),
            "league": "MLB",
            "level": "pro",
            "gender": "men",
            "provider_sport_key": SPORT_KEY,
            "books": game.get("books") or {},
            "odds_available": bool(game.get("books")),
            "moneyline_available": bool(game.get("moneyline_available")),
            "markets_stored": list(game.get("markets_stored") or []),
        })
    return sorted(
        games,
        key=lambda game: (
            str(game.get("commence_time") or ""),
            str(game.get("game_id") or ""),
        ),
    )


def _safe_pull_id(game_date: str, asof: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in asof)[:48]
    return f"mlb_v1_{game_date}_{safe}"


def _store_canonical_pull_history(*, game_date: str, asof: str, run: str, compact: Dict[str, Any]) -> Dict[str, Any]:
    if pull_history is None:
        return {"ok": False, "error": "inqsi_pull_history_unavailable", "games": 0}
    games = _canonical_games(compact)
    if not games:
        return {"ok": True, "stored": None, "games": 0, "reason": "no_provider_games_for_date"}
    body = {
        "pull_id": _safe_pull_id(game_date, asof),
        "sport": "mlb",
        "sport_key": "mlb",
        "slate_date": game_date,
        "pulled_at": asof,
        "source": "the_odds_api",
        "interval_minutes": MLB_SCHED_INTERVAL_MINUTES,
        "games": games,
        "meta": {
            "platform_version": PLATFORM_VERSION,
            "run": run,
            "provider_sport_key": SPORT_KEY,
            "date_isolated": True,
            "line_movement_prediction": True,
        },
    }
    try:
        stored = pull_history.store_pull(body)
        manifest = ((stored.get("stored") or {}).get("provider_manifest") or {})
        manifest_bound = bool(
            stored.get("ok") is True
            and manifest.get("immutable") is True
            and manifest.get("full_provider_schedule") is True
            and int(manifest.get("game_count") or -1) == len(games)
            and manifest.get("fingerprint")
            and manifest.get("pk")
            and manifest.get("sk")
        )
        return {
            "ok": bool(stored.get("ok")) and manifest_bound,
            "games": len(games),
            "stored": stored.get("stored"),
            "error": stored.get("error"),
            "pull_id": body["pull_id"],
            "pk": (stored.get("stored") or {}).get("pk"),
            "providerManifestVersion": manifest.get("version"),
            "providerManifestFingerprint": manifest.get("fingerprint"),
            "providerManifestGameCount": manifest.get("game_count"),
            "providerManifestPk": manifest.get("pk"),
            "providerManifestSk": manifest.get("sk"),
            "providerManifestImmutable": manifest.get("immutable") is True,
            "providerManifestFullSchedule": manifest.get("full_provider_schedule") is True,
            "providerManifestBound": manifest_bound,
        }
    except Exception as exc:
        return {"ok": False, "games": len(games), "error": str(exc), "pull_id": body["pull_id"]}


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
    if _game_index is None or _delta_for_game is None:
        return {"ok": False, "stored": 0, "error": "mlb_signal_api_helpers_unavailable"}
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
            "platform_version": PLATFORM_VERSION,
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


def _record_snapshot_audit_safe(*, game_date: str, asof: str, t: str, run: str, date_compact: Dict[str, Any], raw_games: List[Dict[str, Any]]) -> Dict[str, Any]:
    if record_snapshot_audit is None:
        return {"ok": False, "error": "record_snapshot_audit_unavailable"}
    try:
        return record_snapshot_audit(sport="mlb", slate_date_et=game_date, asof=asof, t=t, run_type=run, compact_snapshot=date_compact, raw_games=raw_games)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _record_no_edge_predictions_safe(*, game_date: str, asof: str, date_compact: Dict[str, Any]) -> Dict[str, Any]:
    if record_no_edge_prediction_rows is None:
        return {"ok": False, "error": "record_no_edge_prediction_rows_unavailable"}
    try:
        return record_no_edge_prediction_rows(sport="mlb", slate_date_et=game_date, asof=asof, compact_snapshot=date_compact)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _build_and_store_hot_sides(*, game_date: str, limit: int = 80) -> Dict[str, Any]:
    try:
        from mlb_date_signal_api import hot_sides
        return hot_sides(game_date=game_date, limit=limit, store=True, include_no_edge=True)
    except Exception as exc:
        return {"ok": False, "sport": "mlb", "game_date_et": game_date, "error": str(exc)}


def _build_and_store_game_winners(*, game_date: str) -> Dict[str, Any]:
    if mlb_game_winner_engine is None:
        return {"ok": False, "sport": "mlb", "game_date_et": game_date, "error": "mlb_game_winner_engine_unavailable"}
    try:
        return mlb_game_winner_engine.predict_all(game_date, store=True, limit=500)
    except Exception as exc:
        return {"ok": False, "sport": "mlb", "game_date_et": game_date, "error": str(exc)}


def _fetch_odds_with_completion_timestamp() -> tuple[List[Dict[str, Any]], str]:
    """Timestamp a pull only after the provider response has completed."""
    raw = _http_get_json(_odds_url())
    return raw, _now_iso()


def lambda_handler(event, context):
    event = event or {}
    if (event.get("httpMethod") or "").upper() == "OPTIONS":
        return _resp(200, {"ok": True})
    try:
        payload = _event_payload(event)
        gate = _scheduled_start_gate(event, payload)
        if gate is not None:
            return _resp(200, gate)

        t = str(payload.get("t") or "HOT").upper()
        run = payload.get("run") or "rolling_open_hot_pull"
        if t != "HOT":
            return _resp(200, {
                "ok": True,
                "sport": "mlb",
                "platformVersion": PLATFORM_VERSION,
                "skipped": True,
                "t": t,
                "run": run,
                "hot_only_policy": HOT_ONLY_POLICY,
                "message": "Legacy MLB T1/T2/T3/T4 pull ignored. MLB V1 stores HOT 15-minute snapshots only.",
            })

        days_ahead = int(payload.get("days_ahead", DEFAULT_DAYS_AHEAD))
        slate_date = payload.get("slate_date_et") or _slate_date_et()
        raw_all, asof = _fetch_odds_with_completion_timestamp()
        raw = _filter_upcoming_et(raw_all, start_date=slate_date, days_ahead=days_ahead)
        compact = _compact(raw)
        if snapshots_tbl is None:
            raise RuntimeError("SNAPSHOTS_TABLE not configured")

        combined_stored = _store_snapshot_item(t=t, slate_date=slate_date, game_date=slate_date, asof=asof, run=run, compact=compact, date_isolated=False, pk="SPORT#mlb")

        isolated_stored = []
        canonical_pull_history = []
        audit_results = []
        prediction_audit_results = []
        hot_movement_feature_results = []
        hot_side_prediction_results = []
        game_winner_prediction_results = []

        for game_date in compact.get("game_dates_et") or []:
            date_compact = _compact_for_game_date(compact, game_date)
            if not date_compact.get("games"):
                continue
            stored_item = _store_snapshot_item(t=t, slate_date=slate_date, game_date=game_date, asof=asof, run=run, compact=date_compact, date_isolated=True, pk=f"SPORT#mlb#DATE#{game_date}")
            isolated_stored.append({"game_date_et": game_date, **stored_item, "count": date_compact.get("count")})

            canonical = _store_canonical_pull_history(game_date=game_date, asof=asof, run=run, compact=date_compact)
            canonical_pull_history.append({"game_date_et": game_date, **canonical})

            audit_results.append({"game_date_et": game_date, **_record_snapshot_audit_safe(game_date=game_date, asof=asof, t=t, run=run, date_compact=date_compact, raw_games=raw)})
            prediction_audit_results.append({"game_date_et": game_date, **_record_no_edge_predictions_safe(game_date=game_date, asof=asof, date_compact=date_compact)})
            hot_movement_feature_results.append({"game_date_et": game_date, **_store_hot_movement_features(game_date=game_date, asof=asof, run=run)})
            hot_side_prediction_results.append({"game_date_et": game_date, **_build_and_store_hot_sides(game_date=game_date)})
            game_winner_prediction_results.append({"game_date_et": game_date, **_build_and_store_game_winners(game_date=game_date)})

        start_at = _parse_start_at_et()
        provider_schedule_manifests = [
            {
                "game_date_et": row.get("game_date_et"),
                "gameCount": row.get("games"),
                "version": row.get("providerManifestVersion"),
                "fingerprint": row.get("providerManifestFingerprint"),
                "pk": row.get("providerManifestPk"),
                "sk": row.get("providerManifestSk"),
                "immutable": row.get("providerManifestImmutable") is True,
                "fullProviderSchedule": row.get("providerManifestFullSchedule") is True,
                "boundToCanonicalPull": row.get("providerManifestBound") is True,
                "ok": row.get("ok") is True,
            }
            for row in canonical_pull_history
        ]
        return _resp(200, {
            "ok": True,
            "sport": "mlb",
            "platformVersion": PLATFORM_VERSION,
            "live_pull_ok": True,
            "fallback_used": False,
            "t": t,
            "run": run,
            "startAtEt": start_at.isoformat() if start_at else None,
            "intervalMinutes": MLB_SCHED_INTERVAL_MINUTES,
            "hot_only_policy": HOT_ONLY_POLICY,
            "pull_policy": PULL_POLICY,
            "ml_research_policy": {"enabled": True, "scope": "HOT pulls only", "feature_partition_pk_pattern": "ML_FEATURE#mlb#YYYY-MM-DD"},
            "prediction_policy": {
                "source": "The Odds API moneyline, spread, and total line movement",
                "canonical_pull_history": "PULLS#mlb#YYYY-MM-DD",
                "snapshots": "SPORT#mlb#DATE#YYYY-MM-DD",
                "winner_engine": "mlb_game_winner_engine.predict_all(store=True)",
                "runs_after_every_hot_pull": True,
            },
            "data_isolation": {"enabled": True, "date_partition_pk_pattern": "SPORT#mlb#DATE#YYYY-MM-DD", "game_key_pattern": "mlb|YYYY-MM-DD|away|home"},
            "pull_date_et": slate_date,
            "game_dates_et": compact.get("game_dates_et") or [],
            "days_ahead": days_ahead,
            "asof": asof,
            "count": compact["count"],
            "stored": combined_stored,
            "date_isolated_stored": isolated_stored,
            "canonical_pull_history": canonical_pull_history,
            "provider_schedule_manifests": provider_schedule_manifests,
            "providerScheduleManifestComplete": bool(
                compact["count"] == 0
                or (
                    provider_schedule_manifests
                    and all(row.get("ok") and row.get("immutable") and row.get("fullProviderSchedule") and row.get("boundToCanonicalPull") for row in provider_schedule_manifests)
                    and sum(int(row.get("gameCount") or 0) for row in provider_schedule_manifests) == int(compact["count"] or 0)
                )
            ),
            "available_book_keys": compact["available_book_keys"],
            "markets": compact["markets"],
            "audit": audit_results,
            "prediction_audit": prediction_audit_results,
            "hot_movement_features": hot_movement_feature_results,
            "hot_side_predictions": hot_side_prediction_results,
            "game_winner_predictions": game_winner_prediction_results,
        })
    except Exception as exc:
        payload = _event_payload(event)
        fallback = _transparent_cached_pre_start_response(payload, exc)
        if fallback is not None:
            return _resp(200 if fallback.get("ok") else 500, fallback)
        return _resp(500, {"ok": False, "sport": "mlb", "platformVersion": PLATFORM_VERSION, "live_pull_ok": False, "fallback_used": False, "upstream_error": _oddsapi_auth_diagnostic(exc), "error": str(exc)})
