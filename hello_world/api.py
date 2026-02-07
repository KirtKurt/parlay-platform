import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from decimal import Decimal
import urllib.request
import urllib.parse

import boto3
from boto3.dynamodb.conditions import Key

from nba_algorithm import rank_nba_b11c1

dynamodb = boto3.resource("dynamodb")

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

snapshots_tbl = dynamodb.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None


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
    if isinstance(o, Decimal):
        return int(o) if o % 1 == 0 else float(o)
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
        return json.loads(r.read().decode("utf-8"))


def _build_oddsapi_url_nba_h2h() -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing in environment")

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
    TARGET_KEYS = {"fanduel", "draftkings"}
    all_keys_seen = set()
    fanatics_keys_seen = set()

    compact = []
    for g in raw_games:
        home = g.get("home_team")
        away = g.get("away_team")
        gid = g.get("id")
        ct = g.get("commence_time")

        books_out: Dict[str, Any] = {}

        for b in g.get("bookmakers", []) or []:
            key = (b.get("key") or "").lower().strip()
            if not key:
                continue

            all_keys_seen.add(key)
            is_fanatics = "fanatic" in key
            if key not in TARGET_KEYS and not is_fanatics:
                continue
            if is_fanatics:
                fanatics_keys_seen.add(key)

            h2h = next((m for m in (b.get("markets") or []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue

            home_odds = None
            away_odds = None
            for o in (h2h.get("outcomes") or []):
                if o.get("name") == home:
                    home_odds = o.get("price")
                elif o.get("name") == away:
                    away_odds = o.get("price")

            if home_odds is None and away_odds is None:
                continue

            books_out[key] = {"ml": {"home": home_odds, "away": away_odds}}

        compact.append(
            {
                "id": gid,
                "commence_time": ct,
                "home_team": home,
                "away_team": away,
                "books": books_out,
            }
        )

    return {
        "games": compact,
        "count": len(compact),
        "available_book_keys": sorted(all_keys_seen),
        "fanatics_keys_detected": sorted(fanatics_keys_seen),
    }


def _store_snapshot(sport: str, data: Dict[str, Any], run_type: str) -> Dict[str, Any]:
    if snapshots_tbl is None:
        raise RuntimeError("SNAPSHOTS_TABLE not configured")

    asof = _now_iso()
    slate_id = f"{sport.upper()}_{asof[:10]}_{run_type}"

    item = {
        "PK": f"SPORT#{sport}",
        "SK": f"ASOF#{asof}#SLATE#{slate_id}",
        "sport": sport,
        "slate_id": slate_id,
        "asof": asof,
        "data": data,
        "meta": {
            "source": "theOddsAPI",
            "run_type": run_type,
            "pulled_at": asof,
        },
        "created_at": asof,
    }

    snapshots_tbl.put_item(Item=item)
    return item


def _pull_nba_snapshot(run_type: str) -> Dict[str, Any]:
    raw = _http_get_json(_build_oddsapi_url_nba_h2h())
    compact = _compact_nba_h2h(raw)
    stored = _store_snapshot("nba", compact, run_type)
    return {"ok": True, "count": compact["count"], "stored": {"pk": stored["PK"], "sk": stored["SK"]}}


# --- auto pick best 3 (uses first available book per game, prefers fanduel then draftkings) ---
def _american_to_prob(a: int) -> float:
    if a < 0:
        return abs(a) / (abs(a) + 100)
    return 100 / (a + 100)


def _vig_gap(ho: int, ao: int) -> float:
    p1 = _american_to_prob(ho)
    p2 = _american_to_prob(ao)
    s = p1 + p2
    return abs((p1 / s) - (p2 / s))


def _latest_snapshot():
    resp = snapshots_tbl.query(
        KeyConditionExpression=Key("PK").eq("SPORT#nba"),
        ScanIndexForward=False,
        Limit=1,
    )
    if not resp.get("Items"):
        raise RuntimeError("No NBA snapshots found")
    return resp["Items"][0]


def _choose_best_3(snapshot):
    games = snapshot["data"]["games"]
    scored = []
    for g in games:
        books = g.get("books", {}) or {}
        pick_order = ["fanduel", "draftkings"] + [k for k in books.keys() if "fanatic" in k]
        chosen_book = next((k for k in pick_order if k in books), None)
        if not chosen_book:
            continue
        ml = books[chosen_book].get("ml", {})
        ho, ao = ml.get("home"), ml.get("away")
        if ho is None or ao is None:
            continue
        if abs(int(ho)) > 9000 or abs(int(ao)) > 9000:
            continue
        gap = _vig_gap(int(ho), int(ao))
        scored.append((gap, g, chosen_book, int(ho), int(ao)))

    scored.sort(reverse=True, key=lambda x: x[0])
    top3 = scored[:3]
    return [{
        "game_id": g["id"],
        "home": g["home_team"],
        "away": g["away_team"],
        "ml": {"home": ho, "away": ao},
        "book_used": book,
        "gap": round(gap, 4),
    } for gap, g, book, ho, ao in top3]


def lambda_handler(event, context):
    method = (event.get("httpMethod") or "").upper()
    path = event.get("path") or "/"

    if method == "OPTIONS":
        return _resp(200, {"ok": True})

    if path in ("/health", "/v1/health") and method == "GET":
        return _resp(200, {"ok": True, "ts": _now_iso()})

    if path == "/v1/pull/nba" and method == "POST":
        return _resp(200, _pull_nba_snapshot("manual"))

    if path == "/v1/snapshots" and method == "GET":
        qs = event.get("queryStringParameters") or {}
        sport = (qs.get("sport") or "nba").lower()
        limit = int(qs.get("limit", 5))
        resp = snapshots_tbl.query(
            KeyConditionExpression=Key("PK").eq(f"SPORT#{sport}"),
            ScanIndexForward=False,
            Limit=limit,
        )
        return _resp(200, {"ok": True, "items": resp.get("Items", [])})

    if path == "/v1/rank/nba" and method == "POST":
        payload = _parse_json(event.get("body"))
        games = payload.get("games")

        # Manual mode
        if isinstance(games, list) and len(games) == 3:
            return _resp(200, rank_nba_b11c1(games))

        # Auto mode
        snap = _latest_snapshot()
        chosen = _choose_best_3(snap)
        if len(chosen) != 3:
            return _resp(500, {"ok": False, "error": "Unable to choose 3 games from snapshot", "chosen": chosen})

        engine_games = [{"game_id": g["game_id"], "home": g["home"], "away": g["away"], "ml": g["ml"]} for g in chosen]
        ranked = rank_nba_b11c1(engine_games)
        ranked["chosen_games"] = chosen
        ranked["source_snapshot"] = {"pk": snap["PK"], "sk": snap["SK"]}
        return _resp(200, ranked)

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    run_type = (event or {}).get("run", "unknown")
    return _pull_nba_snapshot(run_type)
