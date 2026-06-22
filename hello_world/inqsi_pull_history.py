import os, uuid, json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

DDB = boto3.resource("dynamodb")
SNAPSHOTS_TABLE = os.environ.get("SNAPSHOTS_TABLE", "")
PULLS = DDB.Table(SNAPSHOTS_TABLE) if SNAPSHOTS_TABLE else None
BOOKS = ["fanatics", "draftkings", "fanduel", "betmgm", "caesars", "betrivers", "bovada", "lowvig"]
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


def sport_key(s: Optional[str]) -> str:
    raw = (s or "").strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(raw, raw)


def ddb_safe(x: Any) -> Any:
    if isinstance(x, float):
        return Decimal(str(x))
    if isinstance(x, list):
        return [ddb_safe(i) for i in x]
    if isinstance(x, dict):
        return {k: ddb_safe(v) for k, v in x.items() if v is not None}
    return x


def slate_date(ts: str) -> str:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return datetime.now(timezone.utc).date().isoformat()


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
    return {"ok": True, "architecture": "15_min_pull_history", "sports": [{"key": k, **v} for k, v in SUPPORTED.items()], "collegeCoverage": {"football": ["college_football_men", "college_football_women"], "baseball": ["college_baseball_men", "college_baseball_women", "college_softball_women"], "basketball": ["ncaam", "ncaaw"]}, "note": "Algorithm accepts these keys from manual/provider-shaped pulls now. Live provider coverage must still be verified before enabling Odds API ingestion."}


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
            games.append({"game_id": str(raw.get("game_id") or raw.get("id") or game_key(sport, raw)), "game_key": game_key(sport, raw), "home_team": home, "away_team": away, "commence_time": raw.get("commence_time") or raw.get("start_time") or raw.get("startTime"), "league": raw.get("league") or SUPPORTED[sport]["label"], "level": raw.get("level") or SUPPORTED[sport].get("level"), "gender": raw.get("gender") or SUPPORTED[sport].get("gender"), "books": books})
    if not games:
        return {"ok": False, "error": "games_required", "message": "Provide at least one game with home/away teams and moneyline books."}
    return {"ok": True, "pull": {"pull_id": body.get("pull_id") or f"pull_{uuid.uuid4().hex[:16]}", "sport": sport, "pulled_at": pulled_at, "slate_date": body.get("slate_date") or slate_date(pulled_at), "source": body.get("source") or "manual_or_provider_payload", "interval_minutes": int(body.get("interval_minutes") or 15), "games": games, "meta": {"oddsApiOperational": False, "architecture": "15_min_pull_history"}}}


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
    date = date or datetime.now(timezone.utc).date().isoformat()
    res = PULLS.query(KeyConditionExpression=Key("PK").eq(f"PULLS#{sport}#{date}"), ScanIndexForward=True, Limit=min(max(int(limit), 1), 500))
    return [i.get("data", {}) for i in res.get("Items", [])]


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
        out.append({"gameId": game.get("game_id"), "gameKey": key, "sport": sport, "homeTeam": game.get("home_team"), "awayTeam": game.get("away_team"), "commenceTime": game.get("commence_time"), "level": game.get("level"), "gender": game.get("gender"), "selection": game.get("home_team") if best["side"] == "home" else game.get("away_team"), "selectedSide": best["side"], "grade": best["grade"], "score": best["score"], "tags": best["tags"], "homeSignal": hs, "awaySignal": aws})
    out.sort(key=lambda x: x.get("score", 0), reverse=True)
    return {"ok": True, "sport": sport, "slate_date": pulls[-1].get("slate_date"), "pullCount": len(pulls), "architecture": "15_min_pull_history", "signals": out}


def readiness(p: Dict[str, Any]) -> Dict[str, Any]:
    r = signals(p); ss = r.get("signals", [])
    elig = [s for s in ss if s.get("grade") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}]
    strong = [s for s in elig if s.get("grade") == "STRONG_SOLID"]
    status = "READY" if r.get("pullCount", 0) >= 4 and len(elig) >= 3 and strong else "BUILDING_HISTORY" if r.get("pullCount", 0) >= 2 else "NOT_READY"
    return {"ok": True, "sport": r.get("sport"), "slate_date": r.get("slate_date"), "status": status, "pullCount": r.get("pullCount"), "eligibleSignals": len(elig), "strongSignals": len(strong), "minimumRecommendedPulls": 4, "notes": ["Uses many timestamped pulls, not fixed T1-T3 snapshots."]}


def parlay(p: Dict[str, Any]) -> Dict[str, Any]:
    r = signals(p); elig = [s for s in r.get("signals", []) if s.get("grade") in {"STRONG_SOLID", "SOLID", "COIN_FLIP"}]
    if len(elig) < 3:
        return {"ok": True, "buildStatus": "NO_BUILD", "reason": "not_enough_eligible_pull_history_signals", "eligibleCount": len(elig), "pullCount": r.get("pullCount"), "message": "InQsi refused to force a parlay."}
    selected, used = [], set()
    for s in [x for x in elig if x.get("grade") == "STRONG_SOLID"][:2] + elig:
        if s.get("gameId") not in used and len(selected) < 3:
            selected.append(s); used.add(s.get("gameId"))
    if len(selected) < 3:
        return {"ok": True, "buildStatus": "NO_BUILD", "reason": "could_not_create_three_unique_games"}
    combos = []
    for mask in range(8):
        legs, score = [], 0
        for i, s in enumerate(selected):
            home_pick = bool(mask & (1 << i)); sig = s["homeSignal"] if home_pick else s["awaySignal"]
            legs.append({"gameId": s["gameId"], "selection": s["homeTeam"] if home_pick else s["awayTeam"], "side": "home" if home_pick else "away", "grade": sig["grade"], "tags": sig["tags"]})
            score += sig["score"]
        combos.append({"rank": 0, "score": round(score/3, 2), "legs": legs})
    combos.sort(key=lambda x: x["score"], reverse=True)
    for i, c in enumerate(combos, 1): c.update({"rank": i, "top3": i <= 3})
    return {"ok": True, "buildStatus": "BUILT", "architecture": "15_min_pull_history", "sport": r.get("sport"), "slate_date": r.get("slate_date"), "pullCount": r.get("pullCount"), "structure": "PULL_HISTORY_BEST_AVAILABLE", "legs": selected, "rankedCombos": combos}


def scan_slip(p: Dict[str, Any]) -> Dict[str, Any]:
    legs = p.get("legs") or []
    if not legs: return {"ok": False, "error": "legs_required"}
    r = signals(p); by_game = {s.get("gameId"): s for s in r.get("signals", [])}
    reads = []
    for i, leg in enumerate(legs, 1):
        gid = leg.get("gameId") or leg.get("game_id"); sel = leg.get("selection"); s = by_game.get(gid)
        if not s:
            reads.append({"legIndex": i, "gameId": gid, "selection": sel, "riskLevel": "UNAVAILABLE", "grade": "INSUFFICIENT_HISTORY", "tags": ["MARKET_HISTORY_REQUIRED"]}); continue
        sig = s["homeSignal"] if sel == s.get("homeTeam") else s["awaySignal"] if sel == s.get("awayTeam") else None
        if not sig:
            reads.append({"legIndex": i, "gameId": gid, "selection": sel, "riskLevel": "UNMATCHED_SELECTION", "tags": ["SELECTION_NOT_IN_GAME"]}); continue
        risk = "LOW" if sig["grade"] in {"STRONG_SOLID", "SOLID"} else "MEDIUM" if sig["grade"] == "COIN_FLIP" else "HIGH"
        reads.append({"legIndex": i, "gameId": gid, "selection": sel, "riskLevel": risk, "grade": sig["grade"], "score": sig["score"], "tags": sig["tags"], "pullCount": sig["pullCount"]})
    overall = "DO_NOT_FORCE" if any(x.get("riskLevel") in {"HIGH", "UNAVAILABLE", "UNMATCHED_SELECTION"} for x in reads) else "CLEAR" if all(x.get("riskLevel") == "LOW" for x in reads) else "CAUTION"
    return {"ok": True, "architecture": "15_min_pull_history", "overallRead": overall, "legReads": reads, "pullCount": r.get("pullCount")}


def quality(p: Dict[str, Any]) -> Dict[str, Any]:
    keys = [sport_key(p.get("sport") or p.get("sport_key"))] if (p.get("sport") or p.get("sport_key")) else list(SUPPORTED.keys())
    reports = []
    for k in keys:
        if k not in SUPPORTED: continue
        try: pulls = query_pulls(k, p.get("slate_date"), 500)
        except Exception as e: reports.append({"sport": k, "status": "ERROR", "error": str(e)}); continue
        issues = []
        if not pulls: issues.append({"severity": "WARN", "type": "no_pull_history"})
        elif len(pulls) < 4: issues.append({"severity": "WARN", "type": "low_pull_depth", "pullCount": len(pulls)})
        status = "WARN" if issues else "PASS"
        reports.append({"sport": k, "label": SUPPORTED[k]["label"], "status": status, "pullCount": len(pulls), "issues": issues})
    return {"ok": True, "checkedAt": now(), "architecture": "15_min_pull_history", "oddsApiOperational": False, "reports": reports}


def latest(p: Dict[str, Any]) -> Dict[str, Any]:
    sport = sport_key(p.get("sport") or p.get("sport_key")); pulls = query_pulls(sport, p.get("slate_date"), 500)
    return {"ok": True, "sport": sport, "pull": pulls[-1] if pulls else None, "pullCount": len(pulls)}


def handle_pull_history_route(path: str, method: str, query: Dict[str, Any], body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    p = (path or "").rstrip("/") or "/"; params = {**(query or {}), **(body or {})}
    if p in {"/v1/inqsi/algorithm/sports", "/v1/inqsi/pull-history/sports"}: return supported_sports()
    if p in {"/v1/inqsi/markets/normalize-pull", "/v1/inqsi/pull-history/normalize"}: return normalize_pull(body)
    if p in {"/v1/inqsi/pulls", "/v1/inqsi/pull-history/pulls"} and method == "POST": return store_pull(body)
    if p in {"/v1/inqsi/pulls/latest", "/v1/inqsi/pull-history/latest"}: return latest(params)
    if p in {"/v1/inqsi/algorithm/signals", "/v1/inqsi/pull-history/signals"}: return signals(params)
    if p in {"/v1/inqsi/algorithm/readiness", "/v1/inqsi/pull-history/readiness"}: return readiness(params)
    if p in {"/v1/inqsi/parlays/build-pull-history", "/v1/inqsi/pull-history/parlay"}: return parlay(params)
    if p in {"/v1/inqsi/slips/scan-pull-history", "/v1/inqsi/pull-history/scan-slip"}: return scan_slip(params)
    if p in {"/v1/inqsi/monitoring/pull-data-quality", "/v1/inqsi/pull-history/data-quality"}: return quality(params)
    return None
