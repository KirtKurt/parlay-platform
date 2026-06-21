from typing import Any, Dict

from inqsi_core import active_sport_keys, pull_and_analyze_all, pull_and_analyze_sport
from inqsi_release_tracking import record_release_tracking_for_sport


def _pull_track_one(sport_key: str) -> Dict[str, Any]:
    pull_result = pull_and_analyze_sport(sport_key)
    release_result = record_release_tracking_for_sport(sport_key)
    return {"ok": pull_result.get("ok") and release_result.get("ok"), "sport_key": sport_key, "pull": pull_result, "release_tracking": release_result}


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    sport_key = (event or {}).get("sport_key") or (event or {}).get("sport")
    if sport_key and sport_key != "all":
        return _pull_track_one(sport_key)

    # Pulls are allowed to iterate across all configured/active sports, but every write is sport-scoped.
    # No parlay/ranking API is allowed to mix sports.
    results = []
    for key in active_sport_keys():
        try:
            results.append(_pull_track_one(key))
        except Exception as exc:
            results.append({"ok": False, "sport_key": key, "error": str(exc)})
    return {"ok": all(r.get("ok") for r in results), "count": len(results), "results": results}
