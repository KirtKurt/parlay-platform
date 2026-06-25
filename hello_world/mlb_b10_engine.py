import os
from datetime import date, datetime, time, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo

import inqsi_pull_history as history

FREEZE_MINUTES = int(os.environ.get("INQSI_MLB_FREEZE_MINUTES", "120"))
MIN_POINTS = int(os.environ.get("INQSI_MLB_MIN_GAME_POINTS", "4"))
SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
HOT_PULL_START_HOUR_ET = int(os.environ.get("INQSI_MLB_HOT_PULL_START_HOUR_ET", "1"))
HOT_PULL_INTERVAL_MINUTES = int(os.environ.get("INQSI_MLB_HOT_PULL_INTERVAL_MINUTES", "15"))
ENGINE = "MLB-B1.0"
THRESHOLD_VERSION = os.environ.get("INQSI_MLB_THRESHOLD_VERSION", "MLB-B1.0-2026-06")


def now():
    return datetime.now(timezone.utc)


def today():
    return datetime.now(SLATE_TZ).date().isoformat()


def parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def game_day(game):
    dt = parse_dt(game.get("commence_time") or game.get("commenceTime"))
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def gid(game):
    return str(game.get("game_id") or game.get("id") or game.get("game_key") or "")


def avg(values):
    clean = [float(v) for v in values if v is not None]
    return mean(clean) if clean else None


def slate_hot_start_utc(slate):
    try:
        slate_day = date.fromisoformat(slate)
    except Exception:
        slate_day = datetime.now(SLATE_TZ).date()
    start_local = datetime.combine(slate_day, time(HOT_PULL_START_HOUR_ET, 0), tzinfo=SLATE_TZ)
    return start_local.astimezone(timezone.utc)


def et_iso(dt):
    return dt.astimezone(SLATE_TZ).isoformat() if dt else None


def pull_coverage(pulls, slate):
    times = sorted([parse_dt(p.get("pulled_at")) for p in pulls if parse_dt(p.get("pulled_at"))])
    start = slate_hot_start_utc(slate)
    first = times[0] if times else None
    latest = times[-1] if times else None
    since_start = [t for t in times if t >= start]
    reference = latest or now()
    expected = 0
    if reference >= start:
        expected = int((reference - start).total_seconds() // (HOT_PULL_INTERVAL_MINUTES * 60)) + 1
    actual = len(since_start)
    return {
        "policy": "MLB HOT pull history should start at 1:00 AM ET and continue every 15 minutes.",
        "startHourEt": HOT_PULL_START_HOUR_ET,
        "intervalMinutes": HOT_PULL_INTERVAL_MINUTES,
        "expectedStartAtUtc": start.isoformat(),
        "expectedStartAtEt": et_iso(start),
        "firstPullAtUtc": first.isoformat() if first else None,
        "firstPullAtEt": et_iso(first),
        "latestPullAtUtc": latest.isoformat() if latest else None,
        "latestPullAtEt": et_iso(latest),
        "actualPullCount": len(times),
        "actualPullCountSinceStart": actual,
        "expectedPullCountSinceStart": expected,
        "missingPullCountSinceStart": max(expected - actual, 0),
        "coverageRatio": round(actual / expected, 4) if expected else None,
        "overnightCoverageComplete": expected > 0 and actual >= expected,
    }


def run_line_snapshot(game):
    books = game.get("books") or {}
    hp, ap, hpt, apt = [], [], [], []
    for book in books.values():
        if not isinstance(book, dict):
            continue
        spread = book.get("spread") or {}
        if not isinstance(spread, dict):
            continue
        hp.append(spread.get("home_price"))
        ap.append(spread.get("away_price"))
        hpt.append(spread.get("home_point"))
        apt.append(spread.get("away_point"))
    return {
        "homeRunLinePrice": avg(hp),
        "awayRunLinePrice": avg(ap),
        "homeRunLinePoint": avg(hpt),
        "awayRunLinePoint": avg(apt),
    }


def reversals(values):
    signs = []
    for i in range(1, len(values)):
        diff = values[i] - values[i - 1]
        signs.append(1 if diff > 0.0005 else -1 if diff < -0.0005 else 0)
    return sum(1 for i in range(1, len(signs)) if signs[i] and signs[i - 1] and signs[i] != signs[i - 1])


def run_line_movement(points, side):
    key = "homeRunLinePrice" if side == "home" else "awayRunLinePrice"
    vals = [float(p.get(key)) for p in points if p.get(key) is not None]
    if len(vals) < 2:
        return None
    return vals[-1] - vals[0]


def store_history(slate, game_id, item):
    if history.PULLS is None:
        return None
    record = {
        "PK": f"MLB_GAME_HISTORY#{slate}",
        "SK": f"GAME#{item.get('commenceTime') or 'unknown'}#{game_id}",
        "record_type": "mlb_game_history_b10",
        "sport": "mlb",
        "slate_date": slate,
        "slate_timezone": str(SLATE_TZ),
        "game_id": game_id,
        "home_team": item.get("homeTeam"),
        "away_team": item.get("awayTeam"),
        "commence_time": item.get("commenceTime"),
        "cutoff_time": item.get("cutoffTime"),
        "frozen": item.get("frozen"),
        "point_count": item.get("pointCount"),
        "data": history.ddb_safe(item),
        "created_at": now().isoformat(),
    }
    history.PULLS.put_item(Item=record)
    return {"pk": record["PK"], "sk": record["SK"]}


def histories(slate=None):
    slate = slate or today()
    pulls = history.query_pulls("mlb", slate, 500)
    by_game = {}
    for pull in pulls:
        pulled_at = parse_dt(pull.get("pulled_at"))
        if not pulled_at:
            continue
        for game in pull.get("games", []) or []:
            if game_day(game) != slate:
                continue
            game_id = gid(game)
            probs = history.book_probs(game)
            if not game_id or not probs:
                continue
            row = by_game.setdefault(game_id, {
                "gameId": game_id,
                "gameKey": game.get("game_key"),
                "homeTeam": game.get("home_team"),
                "awayTeam": game.get("away_team"),
                "commenceTime": game.get("commence_time"),
                "slateDate": game_day(game),
                "slateTimezone": str(SLATE_TZ),
                "providerSportKey": game.get("provider_sport_key"),
                "points": [],
            })
            rl = run_line_snapshot(game)
            row["points"].append({
                "pulledAt": pulled_at.isoformat(),
                "home": float(probs.get("home")),
                "away": float(probs.get("away")),
                "bookCount": int(probs.get("book_count") or 0),
                "bookDivergence": float(probs.get("book_divergence") or 0),
                "bookAgreement": round(1.0 - float(probs.get("book_divergence") or 0), 5),
                **rl,
            })
    output = []
    for game_id, row in by_game.items():
        commence = parse_dt(row.get("commenceTime"))
        if not commence:
            row.update({"frozen": False, "status": "NO_PLAY", "reason": "missing_game_time", "pointCount": len(row.get("points") or [])})
            output.append(row)
            continue
        cutoff = commence - timedelta(minutes=FREEZE_MINUTES)
        frozen_points = [p for p in sorted(row.get("points") or [], key=lambda x: x.get("pulledAt") or "") if parse_dt(p.get("pulledAt")) and parse_dt(p.get("pulledAt")) <= cutoff]
        row.update({"cutoffTime": cutoff.isoformat(), "frozen": now() >= cutoff, "pointCount": len(frozen_points), "points": frozen_points, "status": "FROZEN" if now() >= cutoff else "WAITING_FOR_FREEZE"})
        output.append(row)
    return pulls, output


def score_side(game, side):
    pts = game.get("points") or []
    vals = [float(p.get(side)) for p in pts if p.get(side) is not None]
    if len(vals) < MIN_POINTS:
        return {"side": side, "grade": "NO_PLAY", "score": 0, "reason": "incomplete_game_history", "pointCount": len(vals)}
    start, latest = vals[0], vals[-1]
    delta = latest - start
    duration = max((parse_dt(pts[-1]["pulledAt"]) - parse_dt(pts[0]["pulledAt"])).total_seconds() / 60.0, 1.0)
    velocity = (delta * 100.0) / max(duration / 60.0, 0.25)
    rev = reversals(vals)
    avg_div = mean([float(p.get("bookDivergence") or 0) for p in pts])
    late = vals[-3:] if len(vals) >= 3 else vals
    instability = max(late) - min(late) if len(late) > 1 else 0
    total_move = abs(delta)
    rl_move = run_line_movement(pts, side)
    tags = []
    if delta >= 0.010: tags.append("STEAM")
    if delta <= -0.010: tags.append("RESISTANCE")
    if velocity >= 0.85: tags.append("MOMENTUM")
    if avg_div <= 0.020: tags.append("BOOK_AGREEMENT")
    if avg_div >= 0.040: tags.append("BOOK_DIVERGENCE")
    if rev: tags.append("REVERSAL")
    if instability >= 0.018: tags.append("LATE_INSTABILITY")
    if total_move >= 0.018: tags.append("TOTAL_MOVEMENT")
    if rl_move is not None and abs(rl_move) >= 10: tags.append("RUN_LINE_MOVEMENT")
    if rl_move is not None and delta > 0 and rl_move < -10: tags.append("RUN_LINE_CONFIRMATION")
    score = round(max(0, min(100, 50 + delta * 900 + (latest - 0.5) * 75 - avg_div * 275 - rev * 7 - instability * 140 + (3 if "RUN_LINE_CONFIRMATION" in tags else 0))), 2)
    if avg_div >= 0.055 or rev >= 3 or instability >= 0.030:
        grade = "FRAGILE"
    elif latest >= 0.535 and delta >= 0.012 and avg_div <= 0.030 and rev <= 1 and instability < 0.020:
        grade = "MLB_STRONG"
    elif latest >= 0.515 and delta >= 0.006 and avg_div <= 0.040 and rev <= 2:
        grade = "MLB_LEAN"
    elif abs(latest - 0.5) < 0.035 or avg_div >= 0.040:
        grade = "COIN_FLIP"
    else:
        grade = "FRAGILE"
    return {"side": side, "grade": grade, "score": score, "probStart": round(start, 5), "probLatest": round(latest, 5), "delta": round(delta, 5), "velocityPpHr": round(velocity, 3), "bookDivergenceAvg": round(avg_div, 5), "bookAgreementAvg": round(1.0 - avg_div, 5), "reversalCount": rev, "lateInstability": round(instability, 5), "totalMovement": round(total_move, 5), "runLineMovement": round(rl_move, 3) if rl_move is not None else None, "tags": sorted(set(tags)), "pointCount": len(vals)}


def build(slate=None):
    slate = slate or today()
    pulls, games = histories(slate)
    coverage = pull_coverage(pulls, slate)
    candidates = []
    stored = []
    for game in games:
        home = score_side(game, "home")
        away = score_side(game, "away")
        best = home if home.get("score", 0) >= away.get("score", 0) else away
        game.update({"homeSignal": home, "awaySignal": away, "selectedSide": best.get("side"), "selection": game.get("homeTeam") if best.get("side") == "home" else game.get("awayTeam"), "grade": best.get("grade"), "score": best.get("score"), "tags": best.get("tags"), "engine": ENGINE})
        try:
            stored.append(store_history(slate, game["gameId"], game))
        except Exception as exc:
            game["historyStoreError"] = str(exc)
        if game.get("frozen") and game.get("grade") in {"MLB_STRONG", "MLB_LEAN"}:
            candidates.append(game)
    strong = [g for g in candidates if g.get("grade") == "MLB_STRONG"]
    lean = [g for g in candidates if g.get("grade") == "MLB_LEAN"]
    base = {"ok": True, "sport": "mlb", "slate_date": slate, "slateTimezone": str(SLATE_TZ), "engine": ENGINE, "thresholdVersion": THRESHOLD_VERSION, "pullCount": len(pulls), "pullCoverage": coverage, "gameCount": len(games), "gameHistoryStored": len([x for x in stored if x]), "candidateCount": len(candidates), "strongCount": len(strong), "leanCount": len(lean), "freezeMinutesBeforeGame": FREEZE_MINUTES, "minimumGameHistoryPoints": MIN_POINTS, "requires": "2 MLB_STRONG + 1 MLB_LEAN_OR_STRONG; zero true COIN_FLIP by default", "audit": {"status": "PENDING_RESULTS", "tracks": ["top1", "top3", "top5"]}}
    if len(strong) < 2 or (len(strong) + len(lean)) < 3:
        message = "MLB-B1.0 refused because frozen same-slate history did not produce 2 MLB_STRONG plus 1 MLB_LEAN/STRONG."
        if not coverage.get("overnightCoverageComplete"):
            message += " Pull coverage is incomplete versus the 1:00 AM ET HOT capture policy."
        return {**base, "buildStatus": "NO_BUILD", "reason": "NO_BUILD_MLB_STRUCTURE_NOT_MET", "message": message, "candidates": candidates[:16]}
    selected = strong[:2]
    used = {g["gameId"] for g in selected}
    for g in [x for x in strong if x["gameId"] not in used] + [x for x in lean if x["gameId"] not in used]:
        if len(selected) < 3:
            selected.append(g); used.add(g["gameId"])
    combos = []
    for mask in range(8):
        legs, total = [], 0.0
        for i, g in enumerate(selected):
            home_pick = bool(mask & (1 << i))
            sig = g["homeSignal"] if home_pick else g["awaySignal"]
            legs.append({"gameId": g["gameId"], "selection": g["homeTeam"] if home_pick else g["awayTeam"], "side": "home" if home_pick else "away", "grade": sig["grade"], "tags": sig["tags"], "commenceTime": g.get("commenceTime"), "cutoffTime": g.get("cutoffTime")})
            total += float(sig.get("score") or 0)
        combos.append({"rank": 0, "score": round(total / 3.0, 2), "legs": legs})
    combos.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(combos, 1): row.update({"rank": i, "top3": i <= 3})
    return {**base, "buildStatus": "BUILT", "selectedStrongCount": sum(1 for g in selected if g.get("grade") == "MLB_STRONG"), "selectedLeanCount": sum(1 for g in selected if g.get("grade") == "MLB_LEAN"), "legs": selected, "rankedCombos": combos}
