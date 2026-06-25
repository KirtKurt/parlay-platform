import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    import odds_live_ingestion
except Exception:
    odds_live_ingestion = None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def build(sports: List[str]) -> Dict[str, Any]:
    sports = [str(s).strip() for s in sports if str(s).strip()]
    if odds_live_ingestion is None:
        return {
            "ok": False,
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "error": "odds_live_ingestion_unavailable",
            "sportsRequested": sports,
        }

    results = []
    for sport in sports:
        try:
            results.append(odds_live_ingestion.pull_sport(sport))
        except Exception as exc:
            results.append({
                "ok": False,
                "appSport": sport,
                "rawGamesReturned": None,
                "rawGamesInWindow": None,
                "gamesStored": 0,
                "error": {"type": type(exc).__name__, "message": str(exc)},
            })

    sports_with_stored = [r.get("appSport") for r in results if _safe_int(r.get("gamesStored")) > 0]
    sports_zero = [r.get("appSport") for r in results if _safe_int(r.get("gamesStored")) == 0]
    active_storage_failures = [
        r.get("appSport")
        for r in results
        if _safe_int(r.get("rawGamesInWindow")) > 0 and _safe_int(r.get("gamesStored")) == 0
    ]

    provider_errors = []
    for sport_result in results:
        for provider in sport_result.get("providerPulls") or []:
            if provider.get("error"):
                provider_errors.append({
                    "appSport": sport_result.get("appSport"),
                    "providerSportKey": provider.get("providerSportKey"),
                    "rawGamesReturned": provider.get("rawGamesReturned"),
                    "rawGamesInWindow": provider.get("rawGamesInWindow"),
                    "gamesStored": provider.get("gamesStored"),
                    "error": provider.get("error"),
                })

    report = {
        "ok": len(active_storage_failures) == 0,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sportsRequested": sports,
        "sportsWithStoredGames": sports_with_stored,
        "sportsWithZeroStoredGames": sports_zero,
        "sportsFailedActiveStorage": active_storage_failures,
        "providerErrorCount": len(provider_errors),
        "providerErrors": provider_errors,
        "results": results,
        "policy": "Every requested sport with active same-day games or matches must store timestamped pulls. Zero storage with active games is a hard failure.",
    }

    os.makedirs("runtime_reports", exist_ok=True)
    with open("runtime_reports/pull_health_latest.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    return report
