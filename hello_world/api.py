import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from decimal import Decimal
import urllib.request
import urllib.parse

import boto3
from boto3.dynamodb.conditions import Key

from nba_algorithm import rank_nba_b11c1

dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
SIGNALS_TABLE = os.environ.get("SIGNALS_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
signals_tbl = dynamodb.Table(SIGNALS_TABLE) if SIGNALS_TABLE else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_json(body: Optional[str]) -> Dict[str, Any]:
    if not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {}


def _json_default(o):
    # DynamoDB returns numbers as Decimal
    if isinstance(o, Decimal):
        if o % 1 == 0:
            return int(o)
        return float(o)
    return str(o)


def _resp(status: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type",
        },
        "body": json.dumps(body, default=_json_default),
    }


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
        return json.loads(raw)


def _build_oddsapi_url_nba_h2h() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("8ef7fbca52be7b648f8fe284791142f9 missing in environment")

    base = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    return base + "?" + urllib.parse.urlencode(params)


def _compact_nba_h2h(raw_games: list) -> Dict[str, Any]:
    compact = []
    for g in raw_games:
        home = g.get("home_team")
        away = g.get("away_team")
        gid = g.get("id")
        ct = g.get("commence_time")
        books = g.get("bookmakers") or []

        home_odds = None
        away_odds = None
        book_key = None

        if books:
            book_key = books[0].get("key")
            markets = books[0].get("markets") or []
            h2h = next((m for m in markets if m.get("key") == "h2h"), None)
            if h2h:
                for o in (h2h.get("outcomes") or []):
                    if o.get("name") == home:
                        home_odds = o.get("price")
                    elif o.get("name") == away:
                        away_odds = o.get("price")

        compact.append(
            {
                "id": gid,
                "commence_time": ct,
                "home_team": home,
                "away_team": away,
                "book": book_key,
                "ml": {"home": home_odds, "away": away_odds},
            }
        )
    return {"games": compact, "count": len(compact)}


def _store_snapshot(sport: str, slate_id: str, asof: str, data: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    pk = f"SPORT#{sport}"
    sk = f"ASOF#{asof}#SLATE#{slate_id}"

    snapshots_tbl.put_item(
        Item={
            "PK": pk,
            "SK": sk,
            "sport": sport,
            "slate_id": slate_id,
            "asof": asof,
            "data": data,
            "meta": meta,
            "created_at": _now_iso(),
        }
    )
    return {"pk": pk, "sk": sk}


def _pull_nba_snapshot(run_type: str) -> Dict[str, Any]:
    url = _build_oddsapi_url_nba_h2h()
    raw = _http_get_json(url)
    compact = _compact_nba_h2h(raw)

    asof = _now_iso()
    slate_id = f"NBA_{asof[:10]}_{run_type}"

    meta = {
        "source": "theOddsAPI",
        "run_type": run_type,
        "pulled_at": asof,
        "endpoint": "basketball_nba/odds?h2h",
    }

    keys = _store_snapshot("nba", slate_id, asof, compact, meta)
    return {"ok": True, "asof": asof, "slate_id": slate_id, "stored": keys, "count": compact.get("count", 0)}


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if path in ("/health", "/v1/health") and method == "GET":
        return _resp(200, {"ok": True, "ts": _now_iso()})

    if path in ("/hello", "/hello/") and method == "GET":
        return _resp(200, {"message": "hello world"})

    # Pull NBA now
    if path == "/v1/pull/nba" and method == "POST":
        try:
            return _resp(200, _pull_nba_snapshot("manual"))
        except Exception as e:
            return _resp(500, {"ok": False, "error": str(e)})

    # Read latest snapshots
    if path == "/v1/snapshots" and method == "GET":
        if snapshots_tbl is None:
            return _resp(500, {"ok": False, "error": "SNAPSHOTS_TABLE not configured"})

        qs = event.get("queryStringParameters") or {}
        sport = (qs.get("sport") or "unknown").lower()
        limit = int(qs.get("limit") or 5)

        pk = f"SPORT#{sport}"
        resp = snapshots_tbl.query(
            KeyConditionExpression=Key("PK").eq(pk),
            ScanIndexForward=False,
            Limit=limit,
        )
        return _resp(200, {"ok": True, "items": resp.get("Items", [])})

    # Rank NBA (still supports 3 games passed in)
    if path == "/v1/rank/nba" and method == "POST":
        payload = _parse_json(event.get("body"))
        games = payload.get("games")
        if not isinstance(games, list) or len(games) != 3:
            return _resp(400, {"ok": False, "error": "Provide exactly 3 games in body.games"})
        return _resp(200, rank_nba_b11c1(games))

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    run_type = (event or {}).get("run", "unknown")
    try:
        return _pull_nba_snapshot(run_type=run_type)
    except Exception as e:
        return {"ok": False, "run": run_type, "error": str(e)}
