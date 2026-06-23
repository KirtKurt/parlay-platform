import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import inqsi_pull_history
except Exception:
    inqsi_pull_history = None

try:
    import odds_live_ingestion
except Exception:
    odds_live_ingestion = None


CANONICAL_BOOKS = {"fanatics", "draftkings", "fanduel"}
DEFAULT_SPORTS = ["nfl", "cfb", "mlb", "nba", "wnba", "ncaam", "nhl", "tennis", "soccer"]
MIN_PULLS = 12
BUILD_CUTOFF_MINUTES = 60

FALLBACK_PROVIDER_MAP = {
    "nfl": {"americanfootball_nfl"},
    "cfb": {"americanfootball_ncaaf"},
    "college_football_men": {"americanfootball_ncaaf"},
    "mlb": {"baseball_mlb"},
    "nba": {"basketball_nba"},
    "wnba": {"basketball_wnba"},
    "ncaam": {"basketball_ncaab"},
    "ncaaw": {"basketball_ncaab"},
    "nhl": {"icehockey_nhl"},
    "tennis": {"tennis_atp_singles", "tennis_wta_singles"},
    "soccer": {"soccer_usa_mls", "soccer_epl", "soccer_uefa_champs_league"},
}


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
            "access-control-allow-methods": "GET,OPTIONS",
            "access-control-allow-headers": "content-type,authorization,x-inqsi-admin-token,x-inqsi-member-id,x-inqsi-session-id",
        },
        "body": json.dumps(clean(body)),
    }


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def today() -> str:
    return now_dt().date().isoformat()


def parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def params(event: Dict[str, Any]) -> Dict[str, Any]:
    return event.get("queryStringParameters") or {}


def sport_key(value: Any) -> str:
    raw = str(value or "").strip()
    if inqsi_pull_history is not None:
        return inqsi_pull_history.sport_key(raw)
    return raw.lower().replace("-", "_").replace(" ", "_")


def sports_from(raw: Any) -> List[str]:
    if not raw:
        return DEFAULT_SPORTS
    return [sport_key(s) for s in str(raw).split(",") if s.strip()]


def expected_provider_keys(sport: str) -> Set[str]:
    if odds_live_ingestion is not None:
        mapped = getattr(odds_live_ingestion, "SPORT_PROVIDER_MAP", {}).get(sport)
        if mapped:
            return set(mapped)
    return FALLBACK_PROVIDER_MAP.get(sport, {sport})


def first_game_time(pulls: List[Dict[str, Any]]) -> Tuple[Optional[datetime], Optional[Dict[str, Any]]]:
    latest = pulls[-1] if pulls else {}
    candidates: List[Tuple[datetime, Dict[str, Any]]] = []
    for game in latest.get("games", []) or []:
        dt = parse_dt(game.get("commence_time"))
        if dt:
            candidates.append((dt, game))
    if not candidates:
        return None, None
    candidates.sort(key=lambda row: row[0])
    return candidates[0]


def cutoff_report(first_dt: Optional[datetime]) -> Dict[str, Any]:
    if not first_dt:
        return {
            "firstGameTimeKnown": False,
            "buildWindowOpen": True,
            "reason": "first_game_time_unknown",
        }
    cutoff = first_dt - timedelta(minutes=BUILD_CUTOFF_MINUTES)
    minutes_until = round((first_dt - now_dt()).total_seconds() / 60.0, 2)
    return {
        "firstGameTimeKnown": True,
        "firstGameTime": first_dt.isoformat(),
        "buildCutoffTime": cutoff.isoformat(),
        "buildCutoffMinutesBeforeFirstGame": BUILD_CUTOFF_MINUTES,
        "minutesUntilFirstGame": minutes_until,
        "buildWindowOpen": now_dt() < cutoff,
        "reason": "open" if now_dt() < cutoff else "one_hour_pre_game_cutoff_reached",
    }


def audit_sport(sport: str, slate_date: Optional[str], limit: int) -> Dict[str, Any]:
    sport = sport_key(sport)
    date = slate_date or today()
    issues: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if inqsi_pull_history is None:
        return {"ok": False, "sport": sport, "slate_date": date, "error": "pull_history_module_unavailable"}

    try:
        pulls = inqsi_pull_history.query_pulls(sport, date, limit)
    except Exception as exc:
        return {"ok": False, "sport": sport, "slate_date": date, "error": str(exc)}

    seen_pull_times: Set[str] = set()
    provider_keys_seen: Set[str] = set()
    books_seen: Set[str] = set()
    missing_game_time = 0
    missing_home_away = 0
    missing_books = 0
    game_count = 0
    duplicate_pull_times = 0
    expected_providers = expected_provider_keys(sport)

    for idx, pull in enumerate(pulls):
        pulled_at = str(pull.get("pulled_at") or "")
        if not pulled_at:
            issues.append({"severity": "ERROR", "type": "pull_missing_pulled_at", "pullIndex": idx})
        elif pulled_at in seen_pull_times:
            duplicate_pull_times += 1
            issues.append({"severity": "ERROR", "type": "duplicate_pull_timestamp", "pulled_at": pulled_at})
        else:
            seen_pull_times.add(pulled_at)
        if str(pull.get("sport") or "") != sport:
            issues.append({"severity": "ERROR", "type": "stored_sport_mismatch", "expectedSport": sport, "actualSport": pull.get("sport"), "pulled_at": pulled_at})
        if str(pull.get("slate_date") or "") != date:
            warnings.append({"severity": "WARN", "type": "slate_date_mismatch", "expectedSlateDate": date, "actualSlateDate": pull.get("slate_date"), "pulled_at": pulled_at})
        for game in pull.get("games", []) or []:
            game_count += 1
            provider_key = game.get("provider_sport_key")
            if provider_key:
                provider_keys_seen.add(str(provider_key))
                if str(provider_key) not in expected_providers:
                    issues.append({"severity": "ERROR", "type": "provider_sport_key_bleed", "sport": sport, "providerSportKey": provider_key, "gameId": game.get("game_id"), "expectedProviderKeys": sorted(expected_providers)})
            else:
                warnings.append({"severity": "WARN", "type": "game_missing_provider_sport_key", "gameId": game.get("game_id")})
            if not game.get("commence_time"):
                missing_game_time += 1
            if not game.get("home_team") or not game.get("away_team"):
                missing_home_away += 1
            books = set((game.get("books") or {}).keys())
            books_seen.update(books)
            if not books:
                missing_books += 1

    if not pulls:
        warnings.append({"severity": "WARN", "type": "no_pull_history"})
    if len(pulls) < MIN_PULLS:
        warnings.append({"severity": "WARN", "type": "below_12_pull_gate", "pullCount": len(pulls), "minimumPulls": MIN_PULLS})
    if missing_game_time:
        warnings.append({"severity": "WARN", "type": "games_missing_commence_time", "count": missing_game_time})
    if missing_home_away:
        issues.append({"severity": "ERROR", "type": "games_missing_home_or_away", "count": missing_home_away})
    if missing_books:
        warnings.append({"severity": "WARN", "type": "games_missing_books", "count": missing_books})

    canonical_missing = sorted(CANONICAL_BOOKS - books_seen)
    if canonical_missing:
        warnings.append({"severity": "WARN", "type": "missing_canonical_books", "missingBooks": canonical_missing, "requiredBooks": sorted(CANONICAL_BOOKS)})

    first_dt, first_game = first_game_time(pulls)
    cutoff = cutoff_report(first_dt)
    parlay_status = "READY_TO_BUILD" if len(pulls) >= MIN_PULLS and cutoff.get("buildWindowOpen") and not issues else "NO_BUILD"
    if len(pulls) < MIN_PULLS:
        parlay_status = "WAITING_FOR_12TH_PULL"
    elif not cutoff.get("buildWindowOpen"):
        parlay_status = "BUILD_CUTOFF_REACHED_LINE_MOVEMENT_ONLY"
    elif issues:
        parlay_status = "INTEGRITY_BLOCKED"

    return {
        "ok": not issues,
        "sport": sport,
        "slate_date": date,
        "pullCount": len(pulls),
        "gameRowsScanned": game_count,
        "duplicatePullTimestamps": duplicate_pull_times,
        "providerKeysExpected": sorted(expected_providers),
        "providerKeysSeen": sorted(provider_keys_seen),
        "booksSeen": sorted(books_seen),
        "canonicalBooksRequired": sorted(CANONICAL_BOOKS),
        "canonicalBooksMissing": canonical_missing,
        "firstGame": first_game,
        "cutoff": cutoff,
        "parlayLifecycleStatus": parlay_status,
        "officialParlayBuildAllowed": parlay_status == "READY_TO_BUILD",
        "lineMovementReportingAllowed": True,
        "personalSlipUploadsAllowedAnytime": True,
        "issues": issues,
        "warnings": warnings,
    }


def audit_all(p: Dict[str, Any]) -> Dict[str, Any]:
    sports = sports_from(p.get("sports") or p.get("sport"))
    slate_date = p.get("slate_date")
    try:
        limit = max(1, min(int(p.get("limit") or 500), 500))
    except Exception:
        limit = 500
    reports = [audit_sport(s, slate_date, limit) for s in sports]
    return {
        "ok": all(r.get("ok") for r in reports),
        "audit": "pull_integrity_v1",
        "checkedAt": now_dt().isoformat(),
        "sportsChecked": sports,
        "minimumOfficialParlayPulls": MIN_PULLS,
        "buildCutoffMinutesBeforeFirstGame": BUILD_CUTOFF_MINUTES,
        "personalSlipUploadsAllowedAnytime": True,
        "summary": {
            "sportsWithErrors": sum(1 for r in reports if not r.get("ok")),
            "sportsWaitingFor12thPull": sum(1 for r in reports if r.get("parlayLifecycleStatus") == "WAITING_FOR_12TH_PULL"),
            "sportsReadyToBuild": sum(1 for r in reports if r.get("parlayLifecycleStatus") == "READY_TO_BUILD"),
            "sportsCutoffReached": sum(1 for r in reports if r.get("parlayLifecycleStatus") == "BUILD_CUTOFF_REACHED_LINE_MOVEMENT_ONLY"),
        },
        "reports": reports,
    }


def route(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    event = event or {}
    method = (event.get("httpMethod") or event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    path = (event.get("rawPath") or event.get("path") or "/").rstrip("/") or "/"
    if method == "OPTIONS" and (path.startswith("/v1/inqsi/admin/data") or path.startswith("/v1/admin/data")):
        return out(200, {"ok": True})
    if path in {"/v1/inqsi/admin/data/pull-integrity", "/v1/admin/data/pull-integrity", "/v1/inqsi/admin/pull-integrity"} and method == "GET":
        return out(200, audit_all(params(event)))
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    routed = route(event)
    return routed or out(404, {"ok": False, "error": "not_found"})
