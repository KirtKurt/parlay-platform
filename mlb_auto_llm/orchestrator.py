from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Set

import handler as base

_ORIGINAL_BUNDLE = base._bbs_event_bundle
_ORIGINAL_ASSEMBLE = base._assemble


def _extract_ids(value: Any, *, limit: int = 40) -> List[str]:
    found: List[str] = []
    seen: Set[str] = set()

    def walk(node: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(node, dict):
            raw = node.get("id")
            if isinstance(raw, str) and raw and raw not in seen and len(raw) >= 8:
                seen.add(raw)
                found.append(raw)
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return found


def _team_id(side: Any) -> str:
    if not isinstance(side, dict):
        return ""
    for key in ("id", "team_id"):
        value = side.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _player_bundle(player_id: str) -> Dict[str, Any]:
    quoted = base.urllib.parse.quote(player_id, safe="")
    return {
        "id": player_id,
        "seasonStats": base._safe_bbs(f"/v1/players/{quoted}/stats", {"sport": "baseball"}),
        "gameLog": base._safe_bbs(f"/v1/players/{quoted}/game-log", {"sport": "baseball", "limit": 20}),
        "rollingStats": base._safe_bbs(f"/v1/players/{quoted}/rolling-stats", {"sport": "baseball"}),
    }


def _enriched_bundle(event: Dict[str, Any]) -> Dict[str, Any]:
    bundle = _ORIGINAL_BUNDLE(event)
    match_id = str(event.get("id") or event.get("match_id") or "").strip()
    if not match_id:
        return bundle
    quoted = base.urllib.parse.quote(match_id, safe="")
    bundle["events"] = base._safe_bbs(f"/v1/matches/{quoted}/events", {"sport": "baseball"})
    bundle["weather"] = base._safe_bbs(f"/v1/matches/{quoted}/weather")

    home = event.get("home") if isinstance(event.get("home"), dict) else {}
    away = event.get("away") if isinstance(event.get("away"), dict) else {}
    home_id = _team_id(home)
    away_id = _team_id(away)
    bundle["teamForm"] = {
        "home": base._safe_bbs(f"/v1/teams/{base.urllib.parse.quote(home_id, safe='')}/form") if home_id else {"ok": False, "error": "BBS_HOME_TEAM_ID_MISSING"},
        "away": base._safe_bbs(f"/v1/teams/{base.urllib.parse.quote(away_id, safe='')}/form") if away_id else {"ok": False, "error": "BBS_AWAY_TEAM_ID_MISSING"},
    }

    lineup_data = ((bundle.get("lineups") or {}).get("data"))
    player_ids = _extract_ids(lineup_data, limit=30)
    player_bundles: List[Dict[str, Any]] = []
    if player_ids:
        with ThreadPoolExecutor(max_workers=min(12, len(player_ids))) as pool:
            futures = {pool.submit(_player_bundle, player_id): player_id for player_id in player_ids}
            for future in as_completed(futures):
                try:
                    player_bundles.append(future.result())
                except Exception as exc:
                    player_bundles.append({"id": futures[future], "error": type(exc).__name__})
    bundle["players"] = sorted(player_bundles, key=lambda row: str(row.get("id") or ""))
    return bundle


def _league_context() -> Dict[str, Any]:
    return {
        "standings": base._safe_bbs("/v1/standings", {"sport": "baseball", "league": "mlb"}),
        "injuries": base._safe_bbs("/v1/injuries", {"sport": "baseball", "league": "mlb"}),
        "coverage": base._safe_bbs("/v1/coverage"),
    }


def _assemble_with_full_bbd(slate: str, *, expanded: bool) -> Dict[str, Any]:
    packet = _ORIGINAL_ASSEMBLE(slate, expanded=expanded)
    if not expanded:
        return packet
    league = _league_context()
    for game in packet.get("games") or []:
        game["bbsLeagueContext"] = copy.deepcopy(league)
    packet.setdefault("sourceStatus", {}).setdefault("bigBallsDataPro", {})["expandedCoverage"] = {
        "matchDetail": True,
        "matchOdds": True,
        "matchStatistics": True,
        "lineups": True,
        "events": True,
        "weather": True,
        "teamRecentForm": True,
        "playerSeasonStats": True,
        "playerGameLog": True,
        "playerRollingStats": True,
        "standings": True,
        "injuriesAttempted": True,
        "coverageDiscovery": True,
        "statcastPublicEndpoint": False,
        "statcastReason": "Big Balls documents Statcast as loaded but its public endpoint remains in development; no unavailable endpoint is fabricated.",
    }
    return packet


base._bbs_event_bundle = _enriched_bundle
base._assemble = _assemble_with_full_bbd


def lambda_handler(event: Any, context: Any) -> Any:
    return base.lambda_handler(event, context)
