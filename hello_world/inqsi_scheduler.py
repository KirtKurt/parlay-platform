from typing import Any, Dict

from inqsi_core import pull_and_analyze_all, pull_and_analyze_sport


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    sport_key = (event or {}).get("sport_key") or (event or {}).get("sport")
    if sport_key and sport_key != "all":
        return pull_and_analyze_sport(sport_key)
    return pull_and_analyze_all()
