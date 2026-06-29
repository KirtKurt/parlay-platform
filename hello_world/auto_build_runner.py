import json
import os
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import inqsi_pull_history as history
import mlb_b10_engine

try:
    import mlb_strength_gate_patch
    mlb_strength_gate_patch.apply(mlb_b10_engine)
except Exception:
    pass

try:
    import baseline_parlay_builder
except Exception:
    baseline_parlay_builder = None

try:
    import odds_live_ingestion
except Exception:
    odds_live_ingestion = None

try:
    import pull_health_diagnostics
except Exception:
    pull_health_diagnostics = None

try:
    import build_deadline_gate
except Exception:
    build_deadline_gate = None

MIN_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
MIN_STRONG = int(os.environ.get("INQSI_MIN_STRONG_LEGS", "2"))
MAX_COIN = int(os.environ.get("INQSI_MAX_COIN_FLIP_LEGS", "1"))
STRONG_GRADES = {"STRONG_SOLID", "SOLID"}
COIN = "COIN_FLIP"
STRICT_DAILY_SPORTS = {"mlb", "college_baseball_men", "nba", "wnba", "ncaam", "ncaaw", "nhl"}
SLATE_TZ = ZoneInfo(os.environ.get("INQSI_SLATE_TIMEZONE", "America/New_York"))
PULL_START_HOUR_ET = int(os.environ.get("INQSI_ALL_SPORTS_PULL_START_HOUR_ET", "1"))
PULL_INTERVAL_MINUTES = int(os.environ.get("INQSI_PULL_INTERVAL_MINUTES", "15"))
BUILD_DEADLINE_HOURS = int(os.environ.get("INQSI_BUILD_DEADLINE_HOURS_BEFORE_FIRST_EVENT", "2"))


def today_utc():
    return datetime.now(timezone.utc).date().isoformat()


def today_et():
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


def game_date(row):
    raw = row.get("commenceTime") or row.get("commence_time")
    dt = parse_dt(raw)
    return dt.astimezone(SLATE_TZ).date().isoformat() if dt else None


def et_iso(dt):
    return dt.astimezone(SLATE_TZ).isoformat() if dt else None


def slate_start_utc(slate_date):
    try:
        slate_day = date.fromisoformat(str(slate_date))
    except Exception:
        slate_day = datetime.now(SLATE_TZ).date()
    return datetime.combine(slate_day, time(PULL_START_HOUR_ET, 0), tzinfo=SLATE_TZ).astimezone(timezone.utc)


def pull_coverage(sport, slate_date):
    try:
        pulls = history.query_pulls(sport, slate_date, 500)
    except Exception as exc:
        return {"ok": False, "sport": sport, "error": str(exc)}
    times = sorted([parse_dt(p.get("pulled_at")) for p in pulls if parse_dt(p.get("pulled_at"))])
    start = slate_start_utc(slate_date)
    first = times[0] if times else None
    latest = times[-1] if times else None
    since_start = [t for t in times if t >= start]
    reference = latest or datetime.now(timezone.utc)
    expected = int((reference - start).total_seconds() // (PULL_INTERVAL_MINUTES * 60)) + 1 if reference >= start else 0
    actual = len(since_start)
    return {
        "ok": True,
        "sport": sport,
        "policy": "All active same-day sports should start timestamped pulls at 1:00 AM ET and continue every 15 minutes.",
        "startHourEt": PULL_START_HOUR_ET,
        "intervalMinutes": PULL_INTERVAL_MINUTES,
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
        "coverageComplete": expected > 0 and actual >= expected,
    }


def deadline_status(rows):
    if build_deadline_gate is None:
        return {"ok": False, "reason": "BUILD_DEADLINE_GATE_UNAVAILABLE"}
    return build_deadline_gate.check(rows, BUILD_DEADLINE_HOURS)


def unique(rows):
    seen, out = set(), []
    for row in rows:
        gid = row.get("gameId")
        if gid and gid not in seen:
            seen.add(gid)
            out.append(row)
    return out


def same_slate(rows, sport, slate_date):
    if sport not in STRICT_DAILY_SPORTS:
        return rows
    return [r for r in rows if game_date(r) == slate_date]


def apply_baseline_if_needed(result, sport, slate_date):
    if baseline_parlay_builder is None:
        return result
    try:
        return baseline_parlay_builder.apply_if_needed(result, sport, slate_date)
    except Exception as exc:
        result["baselineBuildError"] = str(exc)
        return result


def strict_result(sport):
    slate_date = today_et()
    sig = history.signals({"sport": sport, "slate_date": slate_date})
    pull_count = int(sig.get("pullCount") or 0)
    raw_signals = sig.get("signals", [])
    filtered_signals = same_slate(raw_signals, sport, slate_date)
    coverage = pull_coverage(sport, slate_date)
    deadline = deadline_status(filtered_signals)
    base = {
        "ok": True,
        "sport": sig.get("sport") or sport,
        "slate_date": slate_date,
        "pullCount": pull_count,
        "pullCoverage": coverage,
        "buildDeadline": deadline,
        "rawSignalCount": len(raw_signals),
        "sameSlateSignalCount": len(filtered_signals),
        "minimumParlayPulls": MIN_PULLS,
        "minimumStrongLegs": MIN_STRONG,
        "maximumCoinFlipLegs": MAX_COIN,
        "structure": "STRICT_2_STRONG_MAX_1_COIN_FLIP_WITH_BASELINE_AFTER_12",
        "strictDailySlate": sport in STRICT_DAILY_SPORTS,
    }
    if deadline.get("deadlinePassed"):
        return {**base, "buildStatus": "NO_BUILD", "reason": "MISSED_2_HOUR_BUILD_DEADLINE", "message": "Refused because the 3-leg build must be created no later than 2 hours before the first game or match."}
    if pull_count < MIN_PULLS:
        return {**base, "buildStatus": "NO_BUILD", "reason": "WAITING_FOR_12TH_PULL", "message": "Refused before minimum pull depth."}
    eligible = unique([s for s in filtered_signals if s.get("grade") in STRONG_GRADES or s.get("grade") == COIN])
    strong = unique([s for s in eligible if s.get("grade") in STRONG_GRADES])
    coins = unique([s for s in eligible if s.get("grade") == COIN])
    if len(strong) < MIN_STRONG:
        return apply_baseline_if_needed({**base, "buildStatus": "NO_BUILD", "reason": "MISSING_TWO_STRONG_LEGS", "eligibleCount": len(eligible), "strongCount": len(strong), "coinFlipCount": len(coins), "message": "Strict strong-leg gate failed; baseline 12-snapshot builder may still build from top available signals."}, sport, slate_date)
    selected, used = [], set()
    for row in strong:
        if len(selected) < MIN_STRONG and row.get("gameId") not in used:
            selected.append(row); used.add(row.get("gameId"))
    for row in [x for x in strong if x.get("gameId") not in used] + [x for x in coins if x.get("gameId") not in used]:
        if len(selected) < 3:
            selected.append(row); used.add(row.get("gameId"))
    selected_strong = sum(1 for s in selected if s.get("grade") in STRONG_GRADES)
    selected_coin = sum(1 for s in selected if s.get("grade") == COIN)
    if len(selected) < 3 or selected_strong < MIN_STRONG or selected_coin > MAX_COIN:
        return apply_baseline_if_needed({**base, "buildStatus": "NO_BUILD", "reason": "STRICT_STRUCTURE_NOT_MET", "selectedStrongCount": selected_strong, "selectedCoinFlipCount": selected_coin, "eligibleCount": len(eligible), "strongCount": len(strong), "coinFlipCount": len(coins), "message": "Strict structure failed; baseline 12-snapshot builder may still build from top available signals."}, sport, slate_date)
    combos = []
    for mask in range(8):
        legs, score = [], 0
        for i, s in enumerate(selected):
            home_pick = bool(mask & (1 << i))
            side_sig = s["homeSignal"] if home_pick else s["awaySignal"]
            legs.append({"gameId": s["gameId"], "selection": s["homeTeam"] if home_pick else s["awayTeam"], "side": "home" if home_pick else "away", "grade": side_sig["grade"], "tags": side_sig["tags"], "commenceTime": s.get("commenceTime")})
            score += side_sig["score"]
        combos.append({"rank": 0, "score": round(score / 3, 2), "legs": legs})
    combos.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(combos, 1):
        row.update({"rank": i, "top3": i <= 3})
    return {**base, "buildStatus": "BUILT", "selectedStrongCount": selected_strong, "selectedCoinFlipCount": selected_coin, "legs": selected, "rankedCombos": combos}


def latest_pull_diagnostics():
    if pull_health_diagnostics is not None:
        try:
            sports = os.environ.get("INQSI_AUTO_BUILD_SPORTS", "mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis")
            sports_list = [s.strip() for s in sports.split(",") if s.strip()]
            return pull_health_diagnostics.build(sports_list)
        except Exception as exc:
            return {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}
    try:
        with open("runtime_reports/pull_health_latest.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    sports = os.environ.get("INQSI_AUTO_BUILD_SPORTS", "mlb,wnba,nfl,cfb,nba,ncaam,nhl,soccer,tennis")
    sports_list = [s.strip() for s in sports.split(",") if s.strip()]
    store_fn = getattr(history, "store_" + "parlay_build")
    latest_fn = getattr(history, "latest_" + "parlay_build")
    results = []
    slate_date = today_et()
    for sport in sports_list:
        result = mlb_b10_engine.build(slate_date) if sport == "mlb" else strict_result(sport)
        result = apply_baseline_if_needed(result, sport, slate_date)
        try:
            result["stored"] = store_fn(result, mode="mlb_b10_or_strict_auto_after_live_pull_with_12_snapshot_baseline")
        except Exception as exc:
            result["storeError"] = str(exc)
        results.append(result)
    built = [row.get("sport") for row in results if row.get("buildStatus") == "BUILT"]
    pull_diag = latest_pull_diagnostics()
    health_ok = True if not pull_diag else bool(pull_diag.get("ok", True))
    report = {"ok": bool(built) and health_ok, "sports": sports_list, "builtSports": built, "mlbBuilt": "mlb" in built, "mlbLatestBuild": latest_fn({"sport": "mlb", "slate_date": slate_date}), "result": {"ok": True, "autoBuild": True, "builtCount": len(built), "results": results}}
    if pull_diag:
        report["pullDiagnostics"] = pull_diag
    os.makedirs("runtime_reports", exist_ok=True)
    with open("runtime_reports/parlay_latest.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    print(json.dumps({"ok": report["ok"], "builtSports": built, "mlbBuilt": report["mlbBuilt"], "hasPullDiagnostics": bool(pull_diag)}, indent=2))


if __name__ == "__main__":
    main()
