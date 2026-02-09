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

SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

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
        raise RuntimeError("ODDS_API_KEY missing")
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
    TARGET = {"fanduel", "draftkings"}
    all_keys = set()
    fanatics_keys = set()
    games_out = []

    for g in raw_games:
        home = g.get("home_team")
        away = g.get("away_team")
        gid = g.get("id")
        ct = g.get("commence_time")

        books_out = {}

        for b in g.get("bookmakers", []):
            key = (b.get("key") or "").lower()
            if not key:
                continue

            all_keys.add(key)
            is_fanatics = "fanatic" in key
            if key not in TARGET and not is_fanatics:
                continue
            if is_fanatics:
                fanatics_keys.add(key)

            h2h = next((m for m in b.get("markets", []) if m.get("key") == "h2h"), None)
            if not h2h:
                continue

            ho = ao = None
            for o in h2h.get("outcomes", []):
                if o.get("name") == home:
                    ho = o.get("price")
                elif o.get("name") == away:
                    ao = o.get("price")

            if ho is None and ao is None:
                continue

            books_out[key] = {"ml": {"home": ho, "away": ao}}

        games_out.append({
            "id": gid,
            "commence_time": ct,
            "home_team": home,
            "away_team": away,
            "books": books_out,
        })

    return {
        "games": games_out,
        "count": len(games_out),
        "available_book_keys": sorted(all_keys),
        "fanatics_keys_detected": sorted(fanatics_keys),
    }


def _store_snapshot(sport: str, data: Dict[str, Any], run_type: str) -> Dict[str, Any]:
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


def _american_to_prob(a: int) -> float:
    return abs(a) / (abs(a) + 100) if a < 0 else 100 / (a + 100)


def _vig_norm(p1: float, p2: float) -> tuple[float, float]:
    s = p1 + p2
    return (p1 / s, p2 / s) if s > 0 else (0.5, 0.5)


def _steam_resistance_signals(books: dict) -> dict:
    fd = books.get("fanduel")
    dk = books.get("draftkings")
    if not fd or not dk:
        return {"steam": False, "resistance": False, "coinflip": False, "gap": None}

    fdm = fd["ml"]; dkm = dk["ml"]
    fdh, fda = _vig_norm(_american_to_prob(int(fdm["home"])), _american_to_prob(int(fdm["away"])))
    dkh, dka = _vig_norm(_american_to_prob(int(dkm["home"])), _american_to_prob(int(dkm["away"])))

    fd_fav = max(fdh, fda)
    dk_fav = max(dkh, dka)
    gap = abs(fd_fav - dk_fav)

    steam = gap >= 0.03
    coinflip = max(fd_fav, dk_fav) < 0.525
    resistance = steam and ((fd_fav > dk_fav and dk_fav > 0.5) or (dk_fav > fd_fav and fd_fav > 0.5))

    return {
        "steam": steam,
        "resistance": resistance,
        "coinflip": coinflip,
        "gap": round(gap, 4),
        "fd_fav_p": round(fd_fav, 4),
        "dk_fav_p": round(dk_fav, 4),
    }


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
    """
    Pick 3 UNIQUE games.
    Keep best candidate per game_id, then choose top 3 by gap.
    Prefer book order: draftkings > fanduel > betmgm > caesars
    """
    games = (snapshot.get("data") or {}).get("games") or []
    book_priority = ("draftkings", "fanduel", "betmgm", "caesars")

    best_by_game = {}  # game_id -> (gap, game_dict, book_used, ho, ao)

    for g in games:
        game_id = g.get("id")
        if not game_id:
            continue

        books = g.get("books", {}) or {}

        # Find best candidate for this game across allowed books
        best_cand = None

        for book in book_priority:
            if book not in books:
                continue

            ml = (books[book] or {}).get("ml", {})
            ho, ao = ml.get("home"), ml.get("away")
            if ho is None or ao is None:
                continue

            try:
                ho_i = int(ho); ao_i = int(ao)
            except Exception:
                continue

            # ignore extreme outliers
            if abs(ho_i) > 9000 or abs(ao_i) > 9000:
                continue

            p1 = _american_to_prob(ho_i)
            p2 = _american_to_prob(ao_i)
            gap = abs(_vig_norm(p1, p2)[0] - _vig_norm(p1, p2)[1])

            cand = (gap, g, book, ho_i, ao_i)

            # choose highest gap; tie-break by book priority (earlier is better)
            if best_cand is None:
                best_cand = cand
            else:
                if cand[0] > best_cand[0]:
                    best_cand = cand

        if best_cand is not None:
            best_by_game[game_id] = best_cand

    # Pick top 3 UNIQUE games by gap
    top3 = sorted(best_by_game.values(), key=lambda x: x[0], reverse=True)[:3]

    chosen = []
    for gap, g, book, ho, ao in top3:
        chosen.append({
            "game_id": g["id"],
            "home": g.get("home_team"),
            "away": g.get("away_team"),
            "ml": {"home": ho, "away": ao},
            "book_used": book,
            "gap": round(gap, 4),
            "signals": _steam_resistance_signals(g.get("books", {})),
        })

    return chosendef _choose_best_3(snapshot):
    scored = []
    for g in snapshot["data"]["games"]:
        books = g.get("books", {}) or {}
        for book in ("fanduel", "draftkings"):
            if book not in books:
                continue
            ml = books[book]["ml"]
            ho, ao = ml.get("home"), ml.get("away")
            if ho is None or ao is None:
                continue
            if abs(int(ho)) > 9000 or abs(int(ao)) > 9000:
                continue
            p1 = _american_to_prob(int(ho))
            p2 = _american_to_prob(int(ao))
            gap = abs(_vig_norm(p1, p2)[0] - _vig_norm(p1, p2)[1])
            scored.append((gap, g, book, int(ho), int(ao)))

    scored.sort(reverse=True, key=lambda x: x[0])
    top3 = scored[:3]

    chosen = []
    for gap, g, book, ho, ao in top3:
        chosen.append({
            "game_id": g["id"],
            "home": g["home_team"],
            "away": g["away_team"],
            "ml": {"home": ho, "away": ao},
            "book_used": book,
            "gap": round(gap, 4),
            "signals": _steam_resistance_signals(g.get("books", {})),
        })
    return chosen


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

        # Manual mode: user supplies 3 games
        if isinstance(payload.get("games"), list) and len(payload["games"]) == 3:
            return _resp(200, rank_nba_b11c1(payload["games"]))

        # Auto mode: rank from latest snapshot
        snap = _latest_snapshot()
        chosen = _choose_best_3(snap)
        if len(chosen) != 3:
            return _resp(500, {"ok": False, "error": "Unable to choose 3 games", "chosen": chosen})

        engine_games = [{"game_id": g["game_id"], "home": g["home"], "away": g["away"], "ml": g["ml"]} for g in chosen]
        ranked = rank_nba_b11c1(engine_games)

        ranked["chosen_games"] = chosen
        ranked["signals_summary"] = {
            "steam_games": [g["game_id"] for g in chosen if g["signals"]["steam"]],
            "resistance_games": [g["game_id"] for g in chosen if g["signals"]["resistance"]],
            "coinflip_games": [g["game_id"] for g in chosen if g["signals"]["coinflip"]],
        }
        ranked["source_snapshot"] = {"pk": snap["PK"], "sk": snap["SK"]}

        return _resp(200, ranked)

    return _resp(404, {"ok": False, "error": f"Route not found: {method} {path}"})


def scheduler_handler(event, context):
    return _pull_nba_snapshot((event or {}).get("run", "scheduled"))
