from typing import Any, Dict

from inqsi_core import active_sport_keys
from inqsi_autopsy import run_daily_autopsy_for_sport, save_platform_auto_parlay


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    sport_key = (event or {}).get("sport_key") or (event or {}).get("sport")
    mode = (event or {}).get("mode") or "grade"
    sports = [sport_key] if sport_key and sport_key != "all" else active_sport_keys()
    results = []
    for key in sports:
        try:
            if mode == "save_auto_parlay":
                results.append(save_platform_auto_parlay(key))
            else:
                results.append(run_daily_autopsy_for_sport(key))
        except Exception as exc:
            results.append({"ok": False, "sport_key": key, "error": str(exc)})
    return {"ok": all(r.get("ok") for r in results), "mode": mode, "count": len(results), "results": results}
