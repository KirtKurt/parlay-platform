import itertools
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key

DDB = boto3.resource("dynamodb")
TARGET_SPORTS = ["NFL", "CFB", "NBA", "NCAAM", "NHL", "MLB", "WNBA", "SOCCER", "TENNIS", "MMA", "BOXING", "GOLF", "ESPORTS"]
BOOKS = ["Fanatics", "DraftKings", "FanDuel", "BetMGM", "Caesars", "ESPN BET", "BetRivers", "PointsBet"]
TS = ["T1", "T2", "T3", "T4", "T5"]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def table_from_env(name: str):
    table_name = os.environ.get(name)
    if not table_name:
        raise RuntimeError(f"{name} is not configured")
    return DDB.Table(table_name)


def snapshots_table():
    return table_from_env("SNAPSHOTS_TABLE")


def signals_table():
    return table_from_env("SIGNALS_TABLE")


def predictions_table():
    return table_from_env("PREDICTIONS_TABLE")


def outcomes_table():
    return table_from_env("OUTCOMES_TABLE")


def dnum(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def deep_decimal(value: Any) -> Any:
    if isinstance(value, list):
        return [deep_decimal(v) for v in value]
    if isinstance(value, dict):
        return {k: deep_decimal(v) for k, v in value.items() if v is not None}
    return dnum(value)


def require_sport(value: str) -> str:
    sport = str(value or "").strip().upper()
    if not sport:
        raise ValueError("sport is required")
    if sport not in TARGET_SPORTS:
        raise ValueError(f"unsupported sport: {sport}")
    return sport


def require_t(value: str) -> str:
    t = str(value or "").strip().upper()
    if t not in TS:
        raise ValueError("t must be one of T1, T2, T3, T4, T5")
    return t


def american_to_implied(odds: Any) -> Optional[float]:
    if odds is None or odds == "":
        return None
    try:
        value = float(odds)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    if value < 0:
        return abs(value) / (abs(value) + 100)
    return 100 / (value + 100)


def snapshot_pk(sport: str, slate_date: str) -> str:
    return f"MARKET#{sport}#{slate_date}"


def snapshot_sk(t: str) -> str:
    return f"SNAPSHOT#{t}"


def store_manual_snapshot(body: Dict[str, Any]) -> Dict[str, Any]:
    sport = require_sport(body.get("sport"))
    t = require_t(body.get("t"))
    slate_date = str(body.get("slateDate") or body.get("slate_date") or "").strip()
    if not slate_date:
        raise ValueError("slateDate is required")
    games = body.get("games") or []
    books = body.get("books") or []
    if not isinstance(games, list) or not games:
        raise ValueError("games are required; no default market data will be created")
    if not isinstance(books, list) or not books:
        raise ValueError("books are required; no single-book hidden fallback is allowed")
    item = {
        "PK": snapshot_pk(sport, slate_date),
        "SK": snapshot_sk(t),
        "sport": sport,
        "slate_date": slate_date,
        "t": t,
        "books": books,
        "games": games,
        "source": body.get("source") or "MANUAL_UPLOAD",
        "immutable": True,
        "created_at": now(),
    }
    snapshots_table().put_item(
        Item=deep_decimal(item),
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )
    return {"ok": True, "stored": True, "snapshotKey": {"PK": item["PK"], "SK": item["SK"]}, "sport": sport, "t": t, "gameCount": len(games), "bookCount": len(books)}


def list_snapshots(sport: str, slate_date: str) -> List[Dict[str, Any]]:
    sport = require_sport(sport)
    if not slate_date:
        raise ValueError("slateDate is required")
    result = snapshots_table().query(KeyConditionExpression=Key("PK").eq(snapshot_pk(sport, slate_date)))
    items = result.get("Items", [])
    return sorted(items, key=lambda item: item.get("t", ""))


def normalize_snapshot_payload(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    games = snapshot.get("games") or []
    rows = []
    for game in games:
        game_id = game.get("gameId") or game.get("game_id") or game.get("id")
        if not game_id:
            continue
        for market in game.get("markets") or []:
            book = market.get("book")
            if book and book not in BOOKS:
                pass
            odds = market.get("oddsAmerican") or market.get("odds_american")
            rows.append({
                "gameId": game_id,
                "team": market.get("team") or market.get("selection"),
                "marketType": market.get("marketType") or market.get("market_type") or "moneyline",
                "book": book,
                "oddsAmerican": odds,
                "impliedProbability": american_to_implied(odds),
                "line": market.get("line"),
                "total": market.get("total"),
            })
    return {"gameCount": len(games), "marketRows": rows, "bookCoverage": sorted({row.get("book") for row in rows if row.get("book")})}


def normalize_market_data(body: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = body.get("snapshot")
    if not snapshot:
        items = list_snapshots(body.get("sport"), body.get("slateDate") or body.get("slate_date"))
        if not items:
            return {"ok": False, "status": "NO_SNAPSHOTS", "message": "No stored snapshots found. Odds API ingestion was not used."}
        snapshot = items[-1]
    normalized = normalize_snapshot_payload(snapshot)
    return {"ok": True, "normalizationStatus": "NORMALIZED_FROM_STORED_OR_MANUAL_DATA", **normalized}


def build_signals(body: Dict[str, Any]) -> Dict[str, Any]:
    snapshots = body.get("snapshots")
    sport = require_sport(body.get("sport"))
    slate_date = str(body.get("slateDate") or body.get("slate_date") or "").strip()
    if not snapshots:
        snapshots = list_snapshots(sport, slate_date)
    if not snapshots or len(snapshots) < 1:
        return {"ok": False, "status": "NO_MARKET_DATA", "message": "No snapshots available. Signal engine did not fabricate odds."}
    rows_by_game: Dict[str, List[Dict[str, Any]]] = {}
    for snap in snapshots:
        t = snap.get("t") or "T?"
        for row in normalize_snapshot_payload(snap).get("marketRows", []):
            row = {**row, "t": t}
            rows_by_game.setdefault(str(row.get("gameId")), []).append(row)
    signal_rows = []
    for game_id, rows in rows_by_game.items():
        probs = [row.get("impliedProbability") for row in rows if row.get("impliedProbability") is not None]
        tags = []
        score = 50
        if len(probs) >= 2:
            delta = probs[-1] - probs[0]
            if delta > 0.025:
                tags.append("STEAM")
                score += 12
            elif delta < -0.025:
                tags.append("RESISTANCE")
                score -= 10
            if max(probs) - min(probs) > 0.08:
                tags.append("CHAOS")
                score -= 8
        else:
            tags.append("INSUFFICIENT_DELTA")
        if len({row.get("book") for row in rows if row.get("book")}) >= 2:
            tags.append("MULTI_BOOK")
            score += 5
        score = max(1, min(99, score))
        signal = {"gameId": game_id, "signalScore": score, "tags": tags, "rowCount": len(rows), "computedAt": now()}
        signal_rows.append(signal)
    item = {"PK": f"SIGNALS#{sport}#{slate_date or now()[:10]}", "SK": f"RUN#{now()}#{uuid.uuid4().hex[:8]}", "sport": sport, "slate_date": slate_date, "signals": signal_rows, "created_at": now()}
    signals_table().put_item(Item=deep_decimal(item))
    return {"ok": True, "sport": sport, "signalCount": len(signal_rows), "signals": signal_rows, "stored": True}


def score_leg(leg: Dict[str, Any], signal_lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    game_id = str(leg.get("gameId") or leg.get("game_id") or "")
    signal = signal_lookup.get(game_id, {})
    tags = list(signal.get("tags") or [])
    score = int(signal.get("signalScore") or 50)
    if not game_id:
        tags.append("MISSING_GAME_ID")
        score -= 20
    if not leg.get("selection"):
        tags.append("MISSING_SELECTION")
        score -= 20
    if not leg.get("marketType") and not leg.get("market_type"):
        tags.append("MISSING_MARKET_TYPE")
        score -= 10
    score = max(1, min(99, score))
    return {**leg, "scanScore": score, "riskTags": sorted(set(tags)), "confidenceBand": "HIGH" if score >= 72 else "MODERATE" if score >= 50 else "FRAGILE"}


def scan_slip(body: Dict[str, Any]) -> Dict[str, Any]:
    legs = body.get("legs") or []
    if not isinstance(legs, list) or not legs:
        raise ValueError("legs are required")
    if len(legs) > 3:
        raise ValueError("InQsi scanner only accepts up to 3 legs")
    signals = body.get("signals") or []
    signal_lookup = {str(s.get("gameId") or s.get("game_id")): s for s in signals}
    scanned = [score_leg(leg, signal_lookup) for leg in legs]
    avg = sum(int(leg["scanScore"]) for leg in scanned) / len(scanned)
    weakest = min(scanned, key=lambda leg: leg["scanScore"])
    read = "CLEAR" if avg >= 72 and weakest["scanScore"] >= 55 else "CAUTION" if avg >= 50 else "DO_NOT_FORCE"
    scan = {"scanId": f"scan_{uuid.uuid4().hex[:16]}", "createdAt": now(), "overallRead": read, "averageScore": round(avg, 2), "weakestLeg": weakest, "legs": scanned}
    predictions_table().put_item(Item=deep_decimal({"PK": f"SCAN#{body.get('memberId') or 'anonymous'}", "SK": scan["scanId"], **scan}))
    return {"ok": True, "scan": scan, "stored": True, "marketDataStatus": "USED_PROVIDED_SIGNALS" if signals else "NO_MARKET_SIGNALS_PROVIDED"}


def build_parlay(body: Dict[str, Any]) -> Dict[str, Any]:
    candidates = body.get("candidates") or []
    if not isinstance(candidates, list) or len(candidates) < 3:
        raise ValueError("at least 3 candidate legs are required")
    combos = []
    for combo in itertools.combinations(candidates, 3):
        teams = [leg.get("selection") or leg.get("team") for leg in combo]
        if len(set(teams)) < 3:
            continue
        avg = sum(float(leg.get("scanScore") or leg.get("score") or 50) for leg in combo) / 3
        fragile = sum(1 for leg in combo if float(leg.get("scanScore") or leg.get("score") or 50) < 50)
        combos.append({"legs": list(combo), "score": round(avg - fragile * 8, 2), "structure": "CLEAN_3_SOLID" if fragile == 0 and avg >= 70 else "MIXED_2_SOLID_1_VARIABLE"})
    ranked = sorted(combos, key=lambda item: item["score"], reverse=True)[:8]
    build_id = f"build_{uuid.uuid4().hex[:16]}"
    predictions_table().put_item(Item=deep_decimal({"PK": "PARLAY_BUILDER", "SK": build_id, "created_at": now(), "rankings": ranked}))
    return {"ok": True, "buildId": build_id, "rankings": ranked, "count": len(ranked), "rule": "no forced structure; no fake odds"}


def manual_result_grade(body: Dict[str, Any]) -> Dict[str, Any]:
    outcome_id = body.get("outcomeId") or f"outcome_{uuid.uuid4().hex[:16]}"
    item = {"PK": "MANUAL_RESULT", "SK": outcome_id, "created_at": now(), "payload": body, "learningTags": body.get("learningTags") or []}
    outcomes_table().put_item(Item=deep_decimal(item))
    return {"ok": True, "stored": True, "outcomeId": outcome_id, "learningLoop": "manual_result_recorded"}


def access_check(body: Dict[str, Any]) -> Dict[str, Any]:
    status = str(body.get("memberStatus") or body.get("status") or "").upper()
    plan = str(body.get("plan") or "").upper()
    entitled = status in {"TRIAL", "TRIALING", "ACTIVE"} or plan in {"FULL ACCESS", "MASTER"}
    return {"ok": True, "entitled": entitled, "requiredFor": ["slip_scanner", "parlay_builder", "scan_history"], "status": status or "UNKNOWN", "message": "Access allowed" if entitled else "Subscription or trial is required"}


def data_quality_check() -> Dict[str, Any]:
    checks = []
    for env_name, label in [("SNAPSHOTS_TABLE", "market_snapshots"), ("SIGNALS_TABLE", "signals"), ("PREDICTIONS_TABLE", "predictions_scans_builds"), ("OUTCOMES_TABLE", "outcomes_learning")]:
        try:
            tbl = table_from_env(env_name)
            count = len(tbl.scan(Limit=5).get("Items", []))
            checks.append({"name": label, "status": "PASS", "sampleCount": count})
        except Exception as exc:
            checks.append({"name": label, "status": "FAIL", "error": type(exc).__name__})
    checks.append({"name": "odds_api_ingestion", "status": "SKIPPED", "reason": "User requested no Odds API-dependent work right now"})
    summary = "PASS" if all(c["status"] in {"PASS", "SKIPPED"} for c in checks) else "FAIL"
    item = {"PK": "DATA_QUALITY", "SK": f"CHECK#{now()}", "created_at": now(), "summary": summary, "checks": checks}
    predictions_table().put_item(Item=deep_decimal(item))
    return {"ok": summary == "PASS", "summary": summary, "checks": checks, "stored": True}
