from typing import Any, Dict

from inqsi_core import active_sport_keys
from inqsi_live import ingest_live_sport


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    sport_key = (event or {}).get("sport_key") or (event or {}).get("sport")
    sports = [sport_key] if sport_key and sport_key != "all" else active_sport_keys()
    results = []
    for key in sports:
        try:
            results.append(ingest_live_sport(key))
        except Exception as exc:
            results.append({"ok": False, "sport_key": key, "error": str(exc)})
    return {"ok": all(r.get("ok") for r in results), "count": len(results), "results": results}
