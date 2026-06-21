from typing import Any, Dict

from inqsi_public_predictions import generate_public_predictions_all_sports, generate_public_predictions_for_sport, grade_public_predictions_for_sport
from inqsi_core import active_sport_keys


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    sport_key = (event or {}).get("sport_key") or (event or {}).get("sport")
    mode = (event or {}).get("mode") or "generate"
    if mode == "grade":
        sports = [sport_key] if sport_key and sport_key != "all" else active_sport_keys()
        results = []
        for key in sports:
            try:
                results.append(grade_public_predictions_for_sport(key))
            except Exception as exc:
                results.append({"ok": False, "sport_key": key, "error": str(exc)})
        return {"ok": all(r.get("ok") for r in results), "mode": mode, "count": len(results), "results": results}
    if sport_key and sport_key != "all":
        return generate_public_predictions_for_sport(sport_key)
    return generate_public_predictions_all_sports()
