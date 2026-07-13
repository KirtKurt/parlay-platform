import os, uuid, json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

DDB = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
PULLS = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "lowvig"]
MIN_PARLAY_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
DEFAULT_AUTO_PARLAY_SPORTS = ["nfl", "cfb", "mlb", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]
SUPPORTED = {
    "nfl": {"label": "NFL", "level": "pro", "gender": "men"},
    "cfb": {"label": "College Football", "level": "college", "gender": "men", "aliases": ["ncaaf", "college_football", "college_football_men"]},
    "college_football_men": {"label": "College Football - Men", "level": "college", "gender": "men", "aliases": ["cfb", "ncaaf"]},
    "college_football_women": {"label": "College Football - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "mlb": {"label": "MLB", "level": "pro", "gender": "men"},
    "college_baseball_men": {"label": "College Baseball - Men", "level": "college", "gender": "men", "aliases": ["college_baseball", "ncaa_baseball"]},
    "college_baseball_women": {"label": "College Baseball - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "college_softball_women": {"label": "College Softball - Women", "level": "college", "gender": "women", "provider_status": "manual_or_future_provider"},
    "nba": {"label": "NBA", "level": "pro", "gender": "men"},
    "wnba": {"label": "WNBA", "level": "pro", "gender": "women"},
    "ncaam": {"label": "College Basketball - Men", "level": "college", "gender": "men", "aliases": ["college_basketball_men"]},
    "ncaaw": {"label": "College Basketball - Women", "level": "college", "gender": "women", "aliases": ["ncaawb", "college_basketball_women"]},
    "nhl": {"label": "NHL", "level": "pro", "gender": "men"},
    "tennis": {"label": "Tennis", "level": "mixed"},
    "soccer": {"label": "Soccer", "level": "mixed"},
}
ALIASES = {k: k for k in SUPPORTED}
for k, v in SUPPORTED.items():
    for a in v.get("aliases", []):
        ALIASES[a] = k


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def today() -> str:
    tz = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
    return datetime.now(timezone.utc).astimezone(tz).date().isoformat()


def sport_key(s: Optional[str]) -> str:
    raw = (s or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(raw, raw)


def ddb_safe(x: Any) -> Any:
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, list):
        return [ddb_safe(i) for i in x]
    if isinstance(x, dict):
        out = {k: ddb_safe(v) for k, v in x.items() if v is not None}
        # DynamoDB supports explicit NULL values. Preserve the two target slots
        # only for the canonical pregame ML vector so readback can prove that
        # neither outcome was available at lock. Other None fields remain omitted.
        if (
            x.get("version") == "MLB-ML-FROZEN-FEATURE-SNAPSHOT-v1-home-away-outcome"
            and isinstance(x.get("labels"), dict)
        ):
            labels = dict(out.get("labels") or {})
            for target in ("homeWon", "pickCorrect"):
                if target in x.get("labels", {}) and x["labels"].get(target) is None:
                    labels[target] = None
            out["labels"] = labels
        return out
    return x


def slate_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))).date().isoformat()
    except Exception:
        return today()


def team_key(name: Optional[str]) -> str:
    return " ".join((name or "").lower().strip().split())


def game_key(sport: str, g: Dict[str, Any]) -> str:
    return str(g.get("game_key") or g.get("game_id") or g.get("id") or f"{sport}|{team_key(g.get('away_team') or g.get('away'))}|{team_key(g.get('home_team') or g.get('home'))}")


def american_prob(v: Any) -> Optional[float]:
    try:
        a = int(v)
    except Exception:
        return None
    if a == 0:
        return None
    return abs(a) / (abs(a) + 100.0) if a < 0 else 100.0 / (a + 100.0)


def vig(home: Any, away: Any) -> Optional[tuple]:
    hp, ap = american_prob(home), american_prob(away)
    if hp is None or ap is None or hp + ap <= 0:
        return None
    return hp / (hp + ap), ap / (hp + ap)


def supported_sports() -> Dict[str, Any]:
    return {
        "ok": True,
        "architecture": "15_min_pull_history",
        "minimumParlayPulls": MIN_PARLAY_PULLS,
        "minimumParlayHistoryMinutes": MIN_PARLAY_PULLS * 15,
        "sports": [{"key": k, **v} for k, v in SUPPORTED.items()],
        "collegeCoverage": {"football": ["college_football_men", "college_football_women"], "baseball": ["college_baseball_men", "college_baseball_women", "college_softball_women"], "basketball": ["ncaam", "ncaaw"]},
        "note": "Three-leg parlays are refused until the 12th 15-minute pull for the sport/slate.",
    }


def normalize_pull(body: Dict[str, Any]) -> Dict[str, Any]:
    sport = sport_key(body.get("sport") or body.get("sport_key"))
    if sport not in SUPPORTED:
        return {"ok": False, "error": "unsupported_sport", "sport": sport, "supportedSports": [x["key"] for x in supported_sports()["sports"]]}
    pulled_at = body.get("pulled_at") or body.get("asof") or now()
    raw_games = body.get("games") or body.get("events") or []
    games = []
    for raw in raw_games if isinstance(raw_games, list) else []:
        if not isinstance(raw, dict):
            continue
        home = raw.get("home_team") or raw.get("home") or raw.get("homeTeam")
        away = raw.get("away_team") or raw.get("away") or raw.get("awayTeam")
        incoming = raw.get("books") or raw.get("bookmakers") or {}
        if isinstance(incoming, list):
            incoming = {str(b.get("key") or b.get("book") or b.get("title") or "").lower(): b for b in incoming if isinstance(b, dict)}
        books = {}
        for book, data in (incoming or {}).items():
            if not isinstance(data, dict):
                continue
            ml = data.get("ml") or data.get("moneyline") or data.get("h2h") or {}
            hp = ml.get("home") or ml.get("home_price") or ml.get("homePrice")
            ap = ml.get("away") or ml.get("away_price") or ml.get("awayPrice")
            if hp is None or ap is None:
                continue
            key = str(book).lower().strip().replace(" ", "_")
            books[key] = {"ml": {"home": int(hp), "away": int(ap)}}
            if "spread" in data:
                books[key]["spread"] = data["spread"]
            if "total" in data:
                books[key]["total"] = data["total"]
        if home and away and books:
            games.append({
                "game_id": str(raw.get("game_id") or raw.get("id") or game_key(sport, raw)),
                "game_key": game_key(sport, raw),
                "home_team": home,
                "away_team": away,
                "commence_time": raw.get("commence_time") or raw.get("start_time") or raw.get("startTime"),
                "league": raw.get("league") or SUPPORTED[sport]["label"],
                "level": raw.get("level") or SUPPORTED[sport].get("level"),
                "gender": raw.get("gender") or SUPPORTED[sport].get("gender"),
                "provider_sport_key": raw.get("provider_sport_key"),
                "books": books,
            })
    if not games:
        return {"ok": False, "error": "games_required", "message": "Provide at least one game with home/away teams and moneyline books."}
    return {"ok": True, "pull": {"pull_id": body.get("pull_id") or f"pull_{uuid.uuid4().hex[:16]}", "sport": sport, "pulled_at": pulled_at, "slate_date": body.get("slate_date") or slate_date(pulled_at), "source": body.get("source") or "manual_or_provider_payload", "interval_minutes": int(body.get("interval_minutes") or 15), "games": games, "meta": {"oddsApiOperational": body.get("source") == "the_odds_api", "architecture": "15_min_pull_history"}}}


def store_pull(body: Dict[str, Any]) -> Dict[str, Any]:
    if PULLS is None:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    n = normalize_pull(body)
    if not n.get("ok"):
        return n
    p = n["pull"]
    item = {"PK": f"PULLS#{p['sport']}#{p['slate_date']}", "SK": f"PULL#{p['pulled_at']}#{p['pull_id']}", "record_type": "pull_run", "sport": p["sport"], "slate_date": p["slate_date"], "pulled_at": p["pulled_at"], "pull_id": p["pull_id"], "data": ddb_safe(p), "created_at": now()}
    PULLS.put_item(Item=item)
    return {"ok": True, "stored": {"pk": item["PK"], "sk": item["SK"], "pull_id": p["pull_id"], "game_count": len(p["games"])}, "pull": p}


def query_pulls(sport: str, date: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    if PULLS is None:
        raise RuntimeError("SNAPSHOTS_TABLE is not configured")
    sport = sport_key(sport)
    date = date or today()
    limit = min(max(int(limit), 1), 500)
    out: List[Dict[str, Any]] = []
    start_key = None
    while len(out) < limit:
        args: Dict[str, Any] = {
            "KeyConditionExpression": Key("PK").eq(f"PULLS#{sport}#{date}"),
            "ScanIndexForward": True,
            "Limit": limit - len(out),
            "ConsistentRead": True,
        }
        if start_key:
            args["ExclusiveStartKey"] = start_key
        res = PULLS.query(**args)
        out.extend([i.get("data", {}) for i in res.get("Items", [])])
        start_key = res.get("LastEvaluatedKey")
        if not start_key:
            break
    return out


def book_probs(game: Dict[str, Any]) -> Dict[str, Any]:
    hv, av, bp = [], [], {}
    books = game.get("books") or {}
    for b in [x for x in BOOKS if x in books] + [x for x in books if x not in BOOKS]:
        pair = vig((books.get(b) or {}).get("ml", {}).get("home"), (books.get(b) or {}).get("ml", {}).get("away"))
        if pair:
            hp, ap = pair
            hv.append(hp); av.append(ap); bp[b] = {"home": hp, "away": ap}
    if not hv:
        return {}
    return {"home": sum(hv)/len(hv), "away": sum(av)/len(av), "book_count": len(hv), "book_divergence": (max(hv)-min(hv)) if len(hv) > 1 else 0, "book_probs": bp}


def mins(a: str, b: str) -> float:
    try:
        x = datetime.fromisoformat(str(a).replace("Z", "+00:00")); y = datetime.fromisoformat(str(b).replace("Z", "+00:00"))
        return max((y-x).total_seconds()/60.0, 1.0)
    except Exception:
        return 15.0


def side_signal(series: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    vals = [float(x["probs"][side]) for x in series]
    pull_count, start, latest = len(vals), vals[0], vals[-1]
    delta = latest - start
    dur = mins(series[0].get("pulled_at"), series[-1].get("pulled_at")) if pull_count > 1 else 0
    velocity = (delta*100.0)/max(dur/60.0, .25) if pull_count > 1 else 0
    mid = max(1, pull_count//2)
    first = (vals[mid-1]-vals[0])/max(mid-1, 1) if pull_count > 2 else 0
    second = (vals[-1]-vals[mid-1])/max(pull_count-mid, 1) if pull_count > 2 else 0
    accel = second - first
    latest_gap = abs(float(series[-1]["probs"]["home"])-float(series[-1]["probs"]["away"]))
    div = float(series[-1]["probs"].get("book_divergence") or 0)
    reversals = 0
    if pull_count >= 3:
        signs = [1 if vals[i]-vals[i-1] > .0005 else -1 if vals[i]-vals[i-1] < -.0005 else 0 for i in range(1, pull_count)]
        reversals = sum(1 for i in range(1, len(signs)) if signs[i] and signs[i-1] and signs[i] != signs[i-1])
    tags = []
    if pull_count < 3: tags.append("LOW_PULL_DEPTH")
    if delta >= .018: tags.append("STEAM")
    if delta <= -.018: tags.append("RESISTANCE")
    if velocity >= 1.75: tags.append("MOMENTUM")
    if accel >= .004: tags.append("ACCELERATION")
    if accel <= -.004: tags.append("DECELERATION")
    if reversals: tags.append("REVERSAL")
    if latest_gap < .05: tags.append("COMPRESSED_MARKET")
    if div >= .035: tags.append("BOOK_DIVERGENCE")
    if reversals >= 2 or div >= .06: tags.append("CHAOS")
    if latest >= .56 and delta >= .012 and div < .035: tags.append("CERTAINTY_ANCHOR")
    if delta > 0 and latest < .50: tags.append("PUBLIC_FADE_CANDIDATE")
    if pull_count < 3: grade = "INSUFFICIENT_HISTORY"
    elif "CHAOS" in tags or ("REVERSAL" in tags and "BOOK_DIVERGENCE" in tags): grade = "FRAGILE"
    elif latest_gap < .05 or div >= .035: grade = "COIN_FLIP"
    elif latest >= .56 and delta >= .018 and div < .025: grade = "STRONG_SOLID"
    elif latest >= .525 and delta >= .008: grade = "SOLID"
    else: grade = "COIN_FLIP" if latest_gap < .08 else "FRAGILE"
    score = round(max(0, min(100, 50 + delta*700 + (latest-.5)*80 - div*300 - reversals*8)), 2)
    return {"side": side, "probStart": round(start, 5), "probLatest": round(latest, 5), "delta": round(delta, 5), "velocityPpHr": round(velocity, 3), "acceleration": round(accel, 5), "pullCount": pull_count, "durationMinutes": round(dur, 2), "latestGap": round(latest_gap, 5), "bookCount": int(series[-1]["probs"].get("book_count") or 0), "bookDivergence": round(div, 5), "reversals": reversals, "tags": sorted(set(tags)), "grade": grade, "score": score}


def signals(params: Dict[str, Any]) -> Dict[str, Any]:
    sport = sport_key(params.get("sport") or params.get("sport_key"))
    pulls = query_pulls(sport, params.get("slate_date"), params.get("limit") or 500)
    if len(pulls) < 2:
        return {"ok": True, "sport": sport, "pullCount": len(pulls), "signals": [], "message": "Need at least two 15-minute pulls before signal calculation."}
    out = []
    latest_games = pulls[-1].get("games", []) or []
    for game in latest_games:
        key = game.get("game_key") or game.get("game_id")
        series = []
        for p in pulls:
            for g in p.get("games", []) or []:
                if g.get("game_key") == key or g.get("game_id") == key:
                    pr = book_probs(g)
                    if pr: series.append({"pulled_at": p.get("pulled_at"), "game": g, "probs": pr})
                    break
        if not series: continue
        hs, aws = side_signal(series, "home"), side_signal(series, "away")
        best = hs if hs["score"] >= aws["score"] else aws
        out.append({"gameId": game.get("game_id"), "gameKey": key, "sport": sport, "homeTeam": game.get("home_team"), "awayTeam": game.get("away_team"), "commenceTime": game.get("commence_time"), "level": game.get("level"), "gender": game.get("gender"), "providerSportKey": game.get("provider_sport_key"), "selection": game.get("home_team") if best["side"] == "home" else game.get("away_team"), "selectedSide": best["side"], "grade": best["grade"], "score": best["score"], "tags": best["tags"], "homeSignal": hs, "awaySignal": aws})
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"ok": True, "sport": sport, "slate_date": pulls[-1].get("slate_date"), "pullCount": len(pulls), "minimumParlayPulls": MIN_PARLAY_PULLS, "architecture": "15_min_pull_history", "signals": out}


def readiness(p: Dict[str, Any]) -> Dict[str, Any]:
    r = signals(p); ss = r.get("signals", [])
    elig = [s for s in ss if s.get("grade") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}]
    strong = [s for s in elig if s.get("grade") == "STRONG_SOLID"]
    pull_count = int(r.get("pullCount") or 0)
    if pull_count < MIN_PARLAY_PULLS:
        status = "WAITING_FOR_12TH_PULL"
    elif len(elig) >= 3 and strong:
        status = "READY"
    else:
        status = "BUILDING_ELIGIBLE_SIGNAL_DEPTH"
    return {"ok": True, "sport": r.get("sport"), "slate_date": r.get("slate_date"), "status": status, "pullCount": pull_count, "eligibleSignals": len(elig), "strongSignals": len(strong), "minimumParlayPulls": MIN_PARLAY_PULLS, "minimumParlayHistoryMinutes": MIN_PARLAY_PULLS * 15, "parlayEligible": status == "READY", "notes": ["Uses many timestamped pulls, not fixed T1-T3 snapshots.", "Three-leg parlays are refused until the 12th 15-minute pull for this sport/slate."]}


# Backward-compatible API route contract used by inqsi_api.
from inqsi_pull_history_routes import handle_pull_history_route
