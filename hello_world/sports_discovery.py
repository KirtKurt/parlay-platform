import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List


ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")

# Sports currently wired into snapshot pulls / ranking experiments.
ENABLED_SPORTS = {
    "baseball_mlb": {"app_key": "mlb", "status": "enabled", "model_type": "2-way winner"},
    "basketball_nba": {"app_key": "nba", "status": "enabled", "model_type": "2-way winner"},
    "basketball_ncaab": {"app_key": "ncaam", "status": "enabled", "model_type": "2-way winner"},
}

# Sports we expect to add as separate silos once available from the odds feed.
PLANNED_SPORT_HINTS = {
    "icehockey_nhl": {"app_key": "nhl", "model_type": "2-way winner"},
    "americanfootball_nfl": {"app_key": "nfl", "model_type": "2-way winner"},
    "basketball_wnba": {"app_key": "wnba", "model_type": "2-way winner"},
    "soccer": {"app_key": "soccer", "model_type": "3-way home/draw/away"},
}


def _http_get_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _sports_url(include_all: bool = False) -> str:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY missing")
    params = {"apiKey": ODDS_API_KEY}
    if include_all:
        params["all"] = "true"
    return "https://api.the-odds-api.com/v4/sports/?" + urllib.parse.urlencode(params)


def _planned_for_key(key: str, group: str) -> Dict[str, Any]:
    if key in PLANNED_SPORT_HINTS:
        return PLANNED_SPORT_HINTS[key]
    if group == "Soccer":
        return {"app_key": "soccer", "model_type": "3-way home/draw/away"}
    return {}


def _build_rows(raw_sports: List[Dict[str, Any]]) -> Dict[str, Any]:
    available = []
    enabled = []
    not_enabled = []
    soccer = []

    for sport in raw_sports or []:
        key = sport.get("key")
        group = sport.get("group")
        enabled_meta = ENABLED_SPORTS.get(key)
        planned_meta = _planned_for_key(key, group)
        row = {
            "odds_api_key": key,
            "title": sport.get("title"),
            "group": group,
            "active": bool(sport.get("active")),
            "description": sport.get("description"),
            "has_outrights": bool(sport.get("has_outrights")),
            "app_status": "enabled" if enabled_meta else "not_enabled",
            "app_key": (enabled_meta or planned_meta).get("app_key"),
            "model_type": (enabled_meta or planned_meta).get("model_type"),
            "notes": [],
        }
        if group == "Soccer":
            row["notes"].append("Soccer must use a separate 3-way home/draw/away model.")
            soccer.append(row)
        if enabled_meta:
            enabled.append(row)
        else:
            not_enabled.append(row)
        available.append(row)

    return {"available": available, "enabled": enabled, "not_enabled": not_enabled, "soccer": soccer}


def _fallback_rows(error_message: str) -> Dict[str, Any]:
    enabled = []
    for odds_key, meta in ENABLED_SPORTS.items():
        enabled.append({
            "odds_api_key": odds_key,
            "title": odds_key,
            "group": "fallback_enabled",
            "active": None,
            "description": "Known app-enabled sport. Live Odds API discovery failed.",
            "has_outrights": None,
            "app_status": "enabled",
            "app_key": meta.get("app_key"),
            "model_type": meta.get("model_type"),
            "notes": ["Fallback row because live sports discovery failed."],
        })
    planned = []
    for odds_key, meta in PLANNED_SPORT_HINTS.items():
        planned.append({
            "odds_api_key": odds_key,
            "title": odds_key,
            "group": "fallback_planned",
            "active": None,
            "description": "Planned sport silo. Confirm availability after Odds API discovery works.",
            "has_outrights": None,
            "app_status": "not_enabled",
            "app_key": meta.get("app_key"),
            "model_type": meta.get("model_type"),
            "notes": ["Fallback row because live sports discovery failed."],
        })
    soccer = [row for row in planned if row.get("app_key") == "soccer"]
    return {
        "ok": True,
        "source": "fallback_config",
        "discovery_status": "failed_fallback_returned",
        "odds_api_key_detected": bool(ODDS_API_KEY),
        "upstream_error": error_message,
        "rule": "Each sport remains siloed. No sport algorithm can touch another sport.",
        "counts": {
            "available_total": len(enabled) + len(planned),
            "enabled_in_app": len(enabled),
            "not_enabled_yet": len(planned),
            "soccer_available": len(soccer),
        },
        "enabled": enabled,
        "not_enabled": planned,
        "soccer": soccer,
        "all_available": enabled + planned,
    }


def discover_available_sports() -> Dict[str, Any]:
    try:
        try:
            raw_sports: List[Dict[str, Any]] = _http_get_json(_sports_url(include_all=False))
            discovery_status = "live_without_all_param"
        except urllib.error.HTTPError as first_error:
            if first_error.code in {401, 403}:
                raise
            raw_sports = _http_get_json(_sports_url(include_all=True))
            discovery_status = "live_with_all_param"

        rows = _build_rows(raw_sports)
        return {
            "ok": True,
            "source": "theOddsAPI /v4/sports",
            "discovery_status": discovery_status,
            "odds_api_key_detected": bool(ODDS_API_KEY),
            "rule": "Each sport remains siloed. No sport algorithm can touch another sport.",
            "counts": {
                "available_total": len(rows["available"]),
                "enabled_in_app": len(rows["enabled"]),
                "not_enabled_yet": len(rows["not_enabled"]),
                "soccer_available": len(rows["soccer"]),
            },
            "enabled": rows["enabled"],
            "not_enabled": rows["not_enabled"],
            "soccer": rows["soccer"],
            "all_available": rows["available"],
        }
    except Exception as exc:
        return _fallback_rows(str(exc))
