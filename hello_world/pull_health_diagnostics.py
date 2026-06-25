import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    import odds_live_ingestion as odds
except Exception:
    odds = None

REPORT_PATH = "runtime_reports/pull_health_latest.json"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _provider_probe(app_sport: str, provider_key: str) -> Dict[str, Any]:
    if odds is None:
        return {"ok": False, "appSport": app_sport, "providerSportKey": provider_key, "gamesStored": 0, "error": "odds_live_ingestion_unavailable"}
    app_sport = odds.sport_key(app_sport)
    raw = odds.http_get_json(odds.odds_url(provider_key))
    raw_count = len(raw) if isinstance(raw, list) else 0
    active = odds.filter_active_slate(raw, app_sport)
    converted = []
    dropped = 0
    for item in active:
        game = odds.convert_game(item, app_sport, provider_key)
        if game:
            converted.append(game)
        else:
            dropped += 1
    start, end, days = odds.active_window(app_sport)
    error = None
    if not active:
        error = "active_slate_window_empty"
    elif not converted:
        error = "active_slate_games_missing_supported_books_or_markets"
    return {
        "ok": bool(converted),
        "appSport": app_sport,
        "providerSportKey": provider_key,
        "rawGamesReturned": raw_count,
        "rawGamesInWindow": len(active),
        "gamesConvertible": len(converted),
        "gamesDroppedDuringConversion": dropped,
        "gamesStored": 0,
        "diagnosticOnlyNoSnapshotStored": True,
        "activeWindowStart": start.isoformat(),
        "activeWindowEnd": end.isoformat(),
        "slateWindowDays": days,
        "error": error,
    }


def _sport_probe(app_sport: str) -> Dict[str, Any]:
    if odds is None:
        return {"ok": False, "appSport": app_sport, "rawGamesReturned": None, "rawGamesInWindow": None, "gamesStored": 0, "error": "odds_live_ingestion_unavailable"}
    sport = odds.sport_key(app_sport)
    provider_results = []
    for provider_key in odds.provider_keys_for(sport):
        try:
            provider_results.append(_provider_probe(sport, provider_key))
        except Exception as exc:
            provider_results.append({"ok": False, "appSport": sport, "providerSportKey": provider_key, "gamesStored": 0, "error": {"type": type(exc).__name__, "message": str(exc)}})
    raw = sum(_safe_int(r.get("rawGamesReturned")) for r in provider_results)
    window = sum(_safe_int(r.get("rawGamesInWindow")) for r in provider_results)
    convertible = sum(_safe_int(r.get("gamesConvertible")) for r in provider_results)
    return {
        "ok": convertible > 0,
        "appSport": sport,
        "rawGamesReturned": raw,
        "rawGamesInWindow": window,
        "gamesConvertible": convertible,
        "gamesStored": None,
        "diagnosticOnlyNoSnapshotStored": True,
        "activeStorageFailure": window > 0 and convertible == 0,
        "providerPulls": provider_results,
    }


def build(sports: List[str]) -> Dict[str, Any]:
    sports = [str(s).strip() for s in sports if str(s).strip()]
    results = [_sport_probe(s) for s in sports]
    sports_with_markets = [r.get("appSport") for r in results if _safe_int(r.get("gamesConvertible")) > 0]
    sports_zero = [r.get("appSport") for r in results if _safe_int(r.get("gamesConvertible")) == 0]
    active_failures = [r.get("appSport") for r in results if r.get("activeStorageFailure")]
    provider_errors = []
    for sport_result in results:
        for provider in sport_result.get("providerPulls") or []:
            if provider.get("error"):
                provider_errors.append({
                    "appSport": sport_result.get("appSport"),
                    "providerSportKey": provider.get("providerSportKey"),
                    "rawGamesReturned": provider.get("rawGamesReturned"),
                    "rawGamesInWindow": provider.get("rawGamesInWindow"),
                    "gamesConvertible": provider.get("gamesConvertible"),
                    "error": provider.get("error"),
                })
    report = {
        "ok": len(active_failures) == 0,
        "source": "diagnostic_provider_probe_no_snapshot_storage",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sportsRequested": sports,
        "sportsWithConvertibleMarkets": sports_with_markets,
        "sportsWithZeroConvertibleMarkets": sports_zero,
        "sportsFailedActiveConversion": active_failures,
        "providerErrorCount": len(provider_errors),
        "providerErrors": provider_errors,
        "results": results,
        "policy": "Every requested sport with active same-day games or matches must have convertible markets for timestamped storage. Diagnostic probes do not create snapshots.",
    }
    os.makedirs("runtime_reports", exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    return report
