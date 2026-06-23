import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from boto3.dynamodb.conditions import Key

try:
    import inqsi_pull_history
except Exception:
    inqsi_pull_history = None


DEFAULT_SPORTS = ["nfl", "cfb", "mlb", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]
MIN_OFFICIAL_PULLS = int(os.environ.get("INQSI_MIN_PARLAY_PULLS", "12"))
BUILD_CUTOFF_MINUTES = int(os.environ.get("INQSI_PARLAY_BUILD_CUTOFF_MINUTES", "60"))
OFFICIAL_BUILD_MODES = {"official_hourly_lifecycle", "auto_after_live_pull"}


def clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    return value


def out(status: int, body: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id",
        },
        "body": json.dumps(clean(body)),
    }


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_dt().isoformat()


def today() -> str:
    return now_dt().date().isoformat()


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def params(event: Dict[str, Any]) -> Dict[str, Any]:
    q = event.get("queryStringParameters") or {}
    data: Dict[str, Any] = dict(q)
    try:
        body = json.loads(event.get("body") or "{}")
        if isinstance(body, dict):
            data.update(body)
    except Exception:
        pass
    return data


def sport_key(value: Any) -> str:
    raw = str(value or "").strip()
    if inqsi_pull_history is not None:
        return inqsi_pull_history.sport_key(raw)
    return raw.lower().replace("-", "_").replace(" ", "_")


def sports_from(value: Any) -> List[str]:
    raw = str(value or ",".join(DEFAULT_SPORTS))
    return [sport_key(s) for s in raw.split(",") if s.strip()]


def get_pulls(sport: str, slate_date: Optional[str]) -> List[Dict[str, Any]]:
    if inqsi_pull_history is None:
        raise RuntimeError("pull_history_module_unavailable")
    return inqsi_pull_history.query_pulls(sport, slate_date or today(), 500)


def first_game_time(pulls: List[Dict[str, Any]]) -> Tuple[Optional[datetime], Optional[Dict[str, Any]]]:
    latest = pulls[-1] if pulls else {}
    candidates: List[Tuple[datetime, Dict[str, Any]]] = []
    for game in latest.get("games", []) or []:
        dt = parse_dt(game.get("commence_time"))
        if dt:
            candidates.append((dt, game))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0])
    return candidates[0]


def cutoff_status(first_dt: Optional[datetime]) -> Dict[str, Any]:
    if not first_dt:
        return {
            "cutoffKnown": False,
            "buildWindowOpen": True,
            "reason": "first_game_time_unknown",
            "firstGameTime": None,
            "buildCutoffTime": None,
            "minutesUntilFirstGame": None,
        }
    cutoff = first_dt - timedelta(minutes=BUILD_CUTOFF_MINUTES)
    minutes = round((first_dt - now_dt()).total_seconds() / 60.0, 2)
    return {
        "cutoffKnown": True,
        "buildWindowOpen": now_dt() < cutoff,
        "reason": "open" if now_dt() < cutoff else "one_hour_pre_game_cutoff_reached",
        "firstGameTime": first_dt.isoformat(),
        "buildCutoffTime": cutoff.isoformat(),
        "minutesUntilFirstGame": minutes,
        "buildCutoffMinutesBeforeFirstGame": BUILD_CUTOFF_MINUTES,
    }


def line_movement_report(sport: str, slate_date: Optional[str]) -> Dict[str, Any]:
    if inqsi_pull_history is None:
        return {"ok": False, "error": "pull_history_module_unavailable"}
    report = inqsi_pull_history.signals({"sport": sport, "slate_date": slate_date, "limit": 500})
    signals = report.get("signals") or []
    return {
        "ok": report.get("ok", True),
        "sport": sport,
        "slate_date": report.get("slate_date") or slate_date,
        "pullCount": report.get("pullCount", 0),
        "movementReportingContinues": True,
        "topLineMovement": signals[:25],
    }


def build_one(sport: str, slate_date: Optional[str], store: bool = True) -> Dict[str, Any]:
    sport = sport_key(sport)
    if inqsi_pull_history is None:
        return {"ok": False, "sport": sport, "error": "pull_history_module_unavailable"}
    pulls = get_pulls(sport, slate_date)
    pull_count = len(pulls)
    first_dt, first_game = first_game_time(pulls)
    cutoff = cutoff_status(first_dt)
    movement = line_movement_report(sport, slate_date)

    base = {
        "ok": True,
        "officialParlayLifecycle": True,
        "sport": sport,
        "slate_date": slate_date or today(),
        "checkedAt": now_iso(),
        "pullCount": pull_count,
        "minimumOfficialParlayPulls": MIN_OFFICIAL_PULLS,
        "minimumHistoryMinutes": MIN_OFFICIAL_PULLS * 15,
        "buildCadenceMinutes": 60,
        "buildCutoffMinutesBeforeFirstGame": BUILD_CUTOFF_MINUTES,
        "personalSlipUploadAllowed": True,
        "personalSlipUploadRule": "Personal bet slips may be uploaded/scanned anytime, including live games. Official Inqis parlay creation stops at cutoff only.",
        "firstGame": first_game,
        "buildWindow": cutoff,
        "lineMovement": movement,
    }

    if pull_count < MIN_OFFICIAL_PULLS:
        result = {
            **base,
            "buildStatus": "NO_BUILD",
            "reason": "WAITING_FOR_12TH_PULL",
            "message": "Official Inqis three-leg parlays are not built before the 12th completed 15-minute pull.",
        }
    elif not cutoff.get("buildWindowOpen"):
        result = {
            **base,
            "buildStatus": "NO_BUILD",
            "reason": "ONE_HOUR_PRE_GAME_CUTOFF_REACHED",
            "message": "Official Inqis three-leg parlay creation is stopped for this sport because the first game is inside the one-hour cutoff. Line movement reporting continues.",
        }
    else:
        built = inqsi_pull_history.parlay({"sport": sport, "slate_date": slate_date, "limit": 500})
        result = {
            **base,
            **built,
            "officialBuild": True,
            "buildStatus": built.get("buildStatus"),
            "reason": built.get("reason"),
            "message": built.get("message") or "Official Inqis hourly three-leg parlay build evaluated using pull history from first pull through current pull.",
        }

    if store:
        try:
            result["stored"] = inqsi_pull_history.store_parlay_build(result, mode="official_hourly_lifecycle")
        except Exception as exc:
            result["storeError"] = str(exc)
    return result


def run_many(p: Dict[str, Any]) -> Dict[str, Any]:
    sports = sports_from(p.get("sports") or p.get("sport"))
    slate_date = p.get("slate_date")
    store = str(p.get("store") if p.get("store") is not None else "true").lower() != "false"
    results = [build_one(s, slate_date, store=store) for s in sports]
    return {
        "ok": True,
        "officialParlayLifecycle": True,
        "cadence": "hourly",
        "minimumOfficialParlayPulls": MIN_OFFICIAL_PULLS,
        "buildCutoffMinutesBeforeFirstGame": BUILD_CUTOFF_MINUTES,
        "personalSlipUploadsAllowedAnytime": True,
        "sportsChecked": sports,
        "builtCount": sum(1 for r in results if r.get("buildStatus") == "BUILT"),
        "waitingCount": sum(1 for r in results if r.get("reason") == "WAITING_FOR_12TH_PULL"),
        "cutoffCount": sum(1 for r in results if r.get("reason") == "ONE_HOUR_PRE_GAME_CUTOFF_REACHED"),
        "results": results,
    }


def _latest_official_item(sport: str, slate_date: Optional[str]) -> Optional[Dict[str, Any]]:
    if inqsi_pull_history is None or getattr(inqsi_pull_history, "PULLS", None) is None:
        return None
    sport = sport_key(sport)
    slate = slate_date or today()
    res = inqsi_pull_history.PULLS.query(
        KeyConditionExpression=Key("PK").eq(f"PARLAY_BUILDS#{sport}#{slate}"),
        ScanIndexForward=False,
        Limit=25,
    )
    for item in res.get("Items") or []:
        if item.get("record_type") != "three_leg_parlay_build":
            continue
        mode = str(item.get("mode") or "")
        data = item.get("data") or {}
        if mode in OFFICIAL_BUILD_MODES or data.get("officialParlayLifecycle") is True or data.get("officialBuild") is True:
            return item
    return None


def _audit_build_item(item: Optional[Dict[str, Any]], sport: str, slate_date: Optional[str]) -> Dict[str, Any]:
    if not item:
        return {
            "ok": True,
            "sport": sport,
            "slate_date": slate_date or today(),
            "auditStatus": "NO_OFFICIAL_BUILD_FOUND",
            "memberSlipsIncluded": False,
            "sourcePartition": f"PARLAY_BUILDS#{sport}#{slate_date or today()}",
            "message": "No official Inqis parlay build exists yet for this sport/slate. Member slips were not scanned or included.",
        }
    data = item.get("data") or {}
    legs = data.get("legs") or []
    ranked = data.get("rankedCombos") or []
    issues: List[Dict[str, Any]] = []
    if item.get("record_type") != "three_leg_parlay_build":
        issues.append({"severity": "ERROR", "type": "wrong_record_type", "actual": item.get("record_type")})
    if str(item.get("mode") or "") not in OFFICIAL_BUILD_MODES and not data.get("officialParlayLifecycle") and not data.get("officialBuild"):
        issues.append({"severity": "ERROR", "type": "non_official_build_mode", "mode": item.get("mode")})
    if data.get("sport") != sport:
        issues.append({"severity": "ERROR", "type": "sport_mismatch", "expected": sport, "actual": data.get("sport")})
    if data.get("buildStatus") == "BUILT" and len(legs) != 3:
        issues.append({"severity": "ERROR", "type": "official_build_does_not_have_three_legs", "legCount": len(legs)})
    if data.get("buildStatus") == "BUILT" and len(ranked) != 8:
        issues.append({"severity": "ERROR", "type": "official_build_does_not_have_eight_ranked_combos", "comboCount": len(ranked)})
    game_ids = [leg.get("gameId") or leg.get("game_id") for leg in legs]
    if len([g for g in game_ids if g]) != len(set([g for g in game_ids if g])):
        issues.append({"severity": "ERROR", "type": "duplicate_game_in_official_three_leg_build", "gameIds": game_ids})
    if data.get("pullCount") is not None and int(data.get("pullCount") or 0) < MIN_OFFICIAL_PULLS and data.get("buildStatus") == "BUILT":
        issues.append({"severity": "ERROR", "type": "built_before_12_pull_gate", "pullCount": data.get("pullCount")})
    return {
        "ok": not issues,
        "sport": sport,
        "slate_date": slate_date or today(),
        "auditStatus": "PASS" if not issues else "FAIL",
        "memberSlipsIncluded": False,
        "memberSlipSourceExcluded": True,
        "auditScope": "latest_official_inqis_build_only",
        "sourcePartition": item.get("PK"),
        "sourceSortKey": item.get("SK"),
        "recordType": item.get("record_type"),
        "mode": item.get("mode"),
        "createdAt": item.get("created_at"),
        "buildId": item.get("build_id"),
        "buildStatus": data.get("buildStatus"),
        "reason": data.get("reason"),
        "pullCount": data.get("pullCount"),
        "minimumOfficialParlayPulls": MIN_OFFICIAL_PULLS,
        "legCount": len(legs),
        "rankedComboCount": len(ranked),
        "topThreeCombos": [c for c in ranked if c.get("top3")][:3],
        "latestOfficialBuild": data,
        "issues": issues,
    }


def audit_latest_official(p: Dict[str, Any]) -> Dict[str, Any]:
    sports = sports_from(p.get("sports") or p.get("sport"))
    slate_date = p.get("slate_date")
    reports = []
    for sport in sports:
        item = _latest_official_item(sport, slate_date)
        reports.append(_audit_build_item(item, sport, slate_date))
    return {
        "ok": all(r.get("ok") for r in reports),
        "audit": "latest_official_parlay_build_only_v1",
        "checkedAt": now_iso(),
        "sportsChecked": sports,
        "memberSlipsIncluded": False,
        "memberSlipsExcludedByDesign": True,
        "source": "PARLAY_BUILDS partition only",
        "summary": {
            "reports": len(reports),
            "passed": sum(1 for r in reports if r.get("ok")),
            "failed": sum(1 for r in reports if not r.get("ok")),
            "noOfficialBuildFound": sum(1 for r in reports if r.get("auditStatus") == "NO_OFFICIAL_BUILD_FOUND"),
        },
        "reports": reports,
    }


def latest(sport: str, slate_date: Optional[str]) -> Dict[str, Any]:
    if inqsi_pull_history is None:
        return {"ok": False, "error": "pull_history_module_unavailable"}
    latest_build = inqsi_pull_history.latest_parlay_build({"sport": sport, "slate_date": slate_date})
    return {
        **latest_build,
        "officialParlayLifecycle": True,
        "personalSlipUploadsAllowedAnytime": True,
        "note": "This returns the latest stored official lifecycle evaluation. Personal slip uploads/scans are separate and remain allowed anytime.",
    }


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/parlays") or path.startswith("/v1/inqsi/admin/parlays") or path.startswith("/v1/admin/parlays")):
        return out(200, {"ok": True})
    p = params(event)
    if path in {"/v1/inqsi/admin/parlays/hourly-build", "/v1/admin/parlays/hourly-build"} and method in {"GET", "POST"}:
        return out(200, run_many(p))
    if path in {"/v1/inqsi/admin/parlays/audit-latest", "/v1/admin/parlays/audit-latest", "/v1/inqsi/admin/parlays/latest-audit"} and method == "GET":
        return out(200, audit_latest_official(p))
    if path in {"/v1/inqsi/parlays/official/status", "/v1/inqsi/parlays/official/run-status"} and method == "GET":
        return out(200, run_many({**p, "store": "false"}))
    if path in {"/v1/inqsi/parlays/official/latest", "/v1/inqsi/pull-history/parlay/official/latest"} and method == "GET":
        sport = sport_key(p.get("sport") or p.get("sport_key"))
        if not sport:
            return out(400, {"ok": False, "error": "sport_required"})
        return out(200, latest(sport, p.get("slate_date")))
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    routed = route(event)
    return routed or out(404, {"ok": False, "error": "not_found"})
