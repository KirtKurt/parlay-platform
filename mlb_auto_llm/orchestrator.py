from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Set

import handler as base

_ORIGINAL_BUNDLE = base._bbs_event_bundle
_ORIGINAL_ASSEMBLE = base._assemble
_ORIGINAL_BUILD_CARD = base._build_card


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


def _record_id(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("id") or value.get("match_id") or value.get("event_id") or "").strip()


def _source_presence(game: Dict[str, Any]) -> Dict[str, bool]:
    official = game.get("official")
    odds = game.get("oddsCore")
    bbs = game.get("bbs")
    bbs_match = bbs.get("match") if isinstance(bbs, dict) else None
    return {
        "mlbStatsApi": bool(isinstance(official, dict) and official.get("gamePk")),
        "theOddsApi": bool(_record_id(odds)),
        "bigBallsDataPro": bool(
            isinstance(bbs, dict)
            and bbs.get("ok", True) is not False
            and _record_id(bbs_match)
        ),
    }


def _apply_source_coverage(packet: Dict[str, Any]) -> Dict[str, Any]:
    games = [row for row in packet.get("games") or [] if isinstance(row, dict)]
    scheduled = len(games)
    presence = {str(row.get("gamePk") or ""): _source_presence(row) for row in games}

    def matched(source: str) -> List[str]:
        return [game_pk for game_pk, state in presence.items() if state.get(source)]

    source_status = packet.setdefault("sourceStatus", {})
    official_matches = matched("mlbStatsApi")
    odds_matches = matched("theOddsApi")
    bbs_matches = matched("bigBallsDataPro")

    official_status = source_status.setdefault("mlbStatsApi", {})
    official_status.update({
        "ok": len(official_matches) == scheduled,
        "scheduledGames": scheduled,
        "matchedGames": len(official_matches),
        "missingGamePks": sorted(set(presence) - set(official_matches)),
    })
    odds_status = source_status.setdefault("theOddsApi", {})
    odds_status.update({
        "ok": len(odds_matches) == scheduled,
        "scheduledGames": scheduled,
        "matchedGames": len(odds_matches),
        "missingGamePks": sorted(set(presence) - set(odds_matches)),
    })
    bbs_status = source_status.setdefault("bigBallsDataPro", {})
    bbs_status.update({
        "ok": len(bbs_matches) == scheduled,
        "scheduledGames": scheduled,
        "matchedGames": len(bbs_matches),
        "missingGamePks": sorted(set(presence) - set(bbs_matches)),
    })

    packet["threeSourceCoverageComplete"] = all(
        (source_status.get(name) or {}).get("ok") is True
        for name in ("mlbStatsApi", "theOddsApi", "bigBallsDataPro")
    )
    packet["sourcePresenceByGamePk"] = presence
    return packet


def _assemble_with_full_bbd(slate: str, *, expanded: bool) -> Dict[str, Any]:
    packet = _ORIGINAL_ASSEMBLE(slate, expanded=expanded)
    if expanded:
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
    return _apply_source_coverage(packet)


def _missing_three_source_games(packet: Dict[str, Any]) -> List[Dict[str, Any]]:
    missing: List[Dict[str, Any]] = []
    for game in packet.get("games") or []:
        if not isinstance(game, dict):
            continue
        state = _source_presence(game)
        absent = sorted(name for name, available in state.items() if not available)
        if absent:
            missing.append({
                "gamePk": game.get("gamePk"),
                "awayTeam": (game.get("away") or {}).get("name"),
                "homeTeam": (game.get("home") or {}).get("name"),
                "missingSources": absent,
            })
    return missing


def _build_card_three_source_bedrock(packet: Dict[str, Any]) -> Dict[str, Any]:
    packet = _apply_source_coverage(packet)
    missing = _missing_three_source_games(packet)
    if missing or packet.get("threeSourceCoverageComplete") is not True:
        raise RuntimeError(
            "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:"
            + json.dumps(missing, sort_keys=True, separators=(",", ":"))
        )

    card = _ORIGINAL_BUILD_CARD(packet)
    non_bedrock = [
        {
            "gamePk": row.get("gamePk"),
            "decisionAuthority": row.get("decisionAuthority"),
        }
        for row in card.get("picks") or []
        if row.get("decisionAuthority") != "BEDROCK_LLM"
    ]
    if non_bedrock:
        raise RuntimeError(
            "BEDROCK_DECISION_REQUIRED:"
            + json.dumps(non_bedrock, sort_keys=True, separators=(",", ":"))
        )

    card["threeSourceCoverageComplete"] = True
    card["bedrockAuthorityComplete"] = True
    card["sourcePresenceByGamePk"] = packet.get("sourcePresenceByGamePk") or {}
    return card


def _validate_deployment_smoke(result: Dict[str, Any]) -> None:
    status = result.get("status")
    if status == "NO_GAMES":
        return
    if status == "COLLECTING":
        source = result.get("sourceStatus") or {}
        failures = {
            name: value
            for name in ("mlbStatsApi", "theOddsApi", "bigBallsDataPro")
            if (value := source.get(name) or {}).get("ok") is not True
        }
        if failures:
            raise RuntimeError(
                "THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:"
                + json.dumps(failures, sort_keys=True, separators=(",", ":"))
            )
        return
    if status in {"CARD_PUBLISHED", "CARD_ALREADY_PUBLISHED"}:
        card = result.get("card") or {}
        if card.get("threeSourceCoverageComplete") is not True:
            raise RuntimeError("THREE_SOURCE_GAME_COVERAGE_INCOMPLETE:published_card")
        if card.get("bedrockAuthorityComplete") is not True:
            raise RuntimeError("BEDROCK_DECISION_REQUIRED:published_card")
        return
    raise RuntimeError(f"UNEXPECTED_DEPLOYMENT_SMOKE_STATUS:{status}")


base._bbs_event_bundle = _enriched_bundle
base._assemble = _assemble_with_full_bbd
base._build_card = _build_card_three_source_bedrock


def lambda_handler(event: Any, context: Any) -> Any:
    result = base.lambda_handler(event, context)
    if isinstance(event, dict) and event.get("mode") == "deployment_provider_smoke":
        if not isinstance(result, dict):
            raise RuntimeError("DEPLOYMENT_SMOKE_RESPONSE_NOT_OBJECT")
        _validate_deployment_smoke(result)
    return result
