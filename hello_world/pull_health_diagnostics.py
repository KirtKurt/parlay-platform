import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

try:
    import odds_live_ingestion
except Exception:
    odds_live_ingestion = None

REPORT_PATH = "runtime_reports/pull_health_latest.json"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _existing_report() -> Optional[Dict[str, Any]]:
    try:
        with open(REPORT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("results"):
            data["source"] = data.get("source") or "existing_same_run_pull_report"
            return data
    except Exception:
        return None
    return None


def summarize_pull_report(report: Dict[str, Any], sports: List[str]) -> Dict[str, Any]:
    results = report.get("results") or []
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
    return {
        **report,
        "ok": len(active_storage_failures) == 0,
        "createdAt": report.get("createdAt") or datetime.now(timezone.utc).isoformat(),
        "sportsRequested": report.get("sportsRequested") or sports,
        "sportsWithStoredGames": sports_with_stored,
        "sportsWithZeroStoredGames": sports_zero,
        "sportsFailedActiveStorage": active_storage_failures,
        "providerErrorCount": len(provider_errors),
        "providerErrors": provider_errors,
        "policy": "Every requested sport with active same-day games or matches must store timestamped pulls. Zero storage with active games is a hard failure.",
    }


def build(sports: List[str]) -> Dict[str, Any]:
    sports = [str(s).strip() for s in sports if str(s).strip()]
    existing = _existing_report()
    if existing:
        return summarize_pull_report(existing, sports)

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

    report = summarize_pull_report({
        "source": "direct_provider_health_probe",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "sportsRequested": sports,
        "results": results,
    }, sports)

    os.makedirs("runtime_reports", exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
        f.write("\n")
    return report
