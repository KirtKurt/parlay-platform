"""MLB V8 baseball-fundamentals shadow collector.

This collector intentionally preserves the existing V8 odds-shadow collector while
adding an independent fundamentals authority for pitchers, bullpens, lineups,
injuries, and team/player context. It never writes V7 production authority.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

import boto3

from bigballsdata_client import BBSClientError, BigBallsDataClient

VERSION = "MLB-V8-FUNDAMENTALS-v1"
REQUIRED_DOMAINS = ("pitchers", "bullpens", "lineups", "injuries", "team_context")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date_from_event(event: Mapping[str, Any], now: datetime) -> str:
    value = event.get("gameDate") or event.get("date")
    if isinstance(value, str) and value.strip():
        return value.strip()[:10]
    return now.date().isoformat()


def _first(mapping: Mapping[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _as_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _team_block(match: Mapping[str, Any], side: str) -> Dict[str, Any]:
    team = _as_dict(_first(match, (side, f"{side}Team", f"{side}_team"), {}))
    return {
        "id": _first(team, ("id", "teamId", "team_id")),
        "name": _first(team, ("name", "fullName", "displayName")),
        "abbreviation": _first(team, ("abbreviation", "abbr", "code")),
        "record": _first(team, ("record", "seasonRecord", "standing")),
        "recentForm": _first(team, ("recentForm", "last10", "streak")),
        "homeAwaySplit": _first(team, ("homeAwaySplit", "splits")),
        "restDays": _first(team, ("restDays", "daysRest")),
        "travel": _first(team, ("travel", "travelDistance", "timezoneChange")),
    }


def _pitcher_block(match: Mapping[str, Any], side: str) -> Dict[str, Any]:
    candidates = (
        f"{side}Starter",
        f"{side}StartingPitcher",
        f"{side}_starter",
        f"{side}_starting_pitcher",
    )
    raw = _as_dict(_first(match, candidates, {}))
    stats = _as_dict(_first(raw, ("stats", "seasonStats", "metrics"), {}))
    recent = _as_dict(_first(raw, ("recent", "recentForm", "last3Starts"), {}))
    return {
        "id": _first(raw, ("id", "playerId", "player_id")),
        "name": _first(raw, ("name", "fullName", "displayName")),
        "throws": _first(raw, ("throws", "handedness")),
        "confirmed": bool(_first(raw, ("confirmed", "isConfirmed"), False)),
        "era": _first(stats, ("era", "ERA")),
        "fip": _first(stats, ("fip", "FIP")),
        "xera": _first(stats, ("xera", "xERA")),
        "whip": _first(stats, ("whip", "WHIP")),
        "kMinusBbPct": _first(stats, ("kMinusBbPct", "k_bb_pct", "K-BB%")),
        "velocity": _first(stats, ("velocity", "avgFastballVelocity", "fastballVelocity")),
        "pitchMix": _first(stats, ("pitchMix", "pitch_mix")),
        "expectedInnings": _first(raw, ("expectedInnings", "projectedInnings")),
        "recentThreeStarts": recent,
        "health": _first(raw, ("health", "injuryStatus", "status")),
    }


def _bullpen_block(match: Mapping[str, Any], side: str) -> Dict[str, Any]:
    raw = _as_dict(_first(match, (f"{side}Bullpen", f"{side}_bullpen"), {}))
    return {
        "era": _first(raw, ("era", "ERA")),
        "fip": _first(raw, ("fip", "FIP")),
        "whip": _first(raw, ("whip", "WHIP")),
        "last3DaysInnings": _first(raw, ("last3DaysInnings", "inningsLast3Days")),
        "last2DaysPitches": _first(raw, ("last2DaysPitches", "pitchesLast2Days")),
        "highLeverageAvailable": _first(raw, ("highLeverageAvailable", "leverageArmsAvailable")),
        "closerAvailable": _first(raw, ("closerAvailable", "closerStatus")),
        "freshnessScore": _first(raw, ("freshnessScore", "restScore")),
        "qualityScore": _first(raw, ("qualityScore", "strengthScore")),
        "expectedInnings": _first(raw, ("expectedInnings", "projectedInnings")),
    }


def _lineup_block(match: Mapping[str, Any], side: str) -> Dict[str, Any]:
    raw = _first(match, (f"{side}Lineup", f"{side}_lineup", f"{side}BattingOrder"), [])
    entries = _as_list(raw)
    if not entries and isinstance(raw, Mapping):
        entries = _as_list(_first(raw, ("players", "batters", "order"), []))
    normalized = []
    for index, player in enumerate(entries, start=1):
        p = _as_dict(player)
        normalized.append({
            "slot": _first(p, ("slot", "battingOrder", "order"), index),
            "id": _first(p, ("id", "playerId", "player_id")),
            "name": _first(p, ("name", "fullName", "displayName")),
            "position": _first(p, ("position", "pos")),
            "handedness": _first(p, ("bats", "handedness")),
            "ops": _first(p, ("ops", "OPS")),
            "wrcPlus": _first(p, ("wrcPlus", "wRC+")),
            "status": _first(p, ("status", "availability")),
        })
    confirmed = bool(_first(_as_dict(raw), ("confirmed", "isConfirmed"), False)) if isinstance(raw, Mapping) else False
    return {"confirmed": confirmed, "players": normalized, "playerCount": len(normalized)}


def _injuries(match: Mapping[str, Any], side: str) -> List[Dict[str, Any]]:
    rows = _as_list(_first(match, (f"{side}Injuries", f"{side}_injuries"), []))
    out = []
    for row in rows:
        item = _as_dict(row)
        out.append({
            "playerId": _first(item, ("playerId", "id", "player_id")),
            "name": _first(item, ("name", "playerName", "fullName")),
            "status": _first(item, ("status", "designation")),
            "injury": _first(item, ("injury", "bodyPart", "description")),
            "impact": _first(item, ("impact", "importance", "role")),
            "updatedAt": _first(item, ("updatedAt", "lastUpdated")),
        })
    return out


def normalize_match(match: Mapping[str, Any], captured_at: datetime) -> Dict[str, Any]:
    away = _team_block(match, "away")
    home = _team_block(match, "home")
    match_id = _first(match, ("id", "matchId", "eventId", "gameId"))
    normalized = {
        "matchId": match_id,
        "gameDate": str(_first(match, ("date", "gameDate", "startTime"), ""))[:10],
        "startTimeUtc": _first(match, ("startTime", "commenceTime", "scheduledAt")),
        "awayTeam": away,
        "homeTeam": home,
        "pitchers": {"away": _pitcher_block(match, "away"), "home": _pitcher_block(match, "home")},
        "bullpens": {"away": _bullpen_block(match, "away"), "home": _bullpen_block(match, "home")},
        "lineups": {"away": _lineup_block(match, "away"), "home": _lineup_block(match, "home")},
        "injuries": {"away": _injuries(match, "away"), "home": _injuries(match, "home")},
        "teamContext": {"away": away, "home": home},
        "weather": _first(match, ("weather", "forecast")),
        "park": _first(match, ("park", "venue", "ballpark")),
        "capturedAtUtc": captured_at.isoformat(),
    }
    normalized["coverage"] = coverage(normalized)
    normalized["trainingEligible"] = normalized["coverage"]["trainingEligible"]
    return normalized


def coverage(game: Mapping[str, Any]) -> Dict[str, Any]:
    pitchers = _as_dict(game.get("pitchers"))
    bullpens = _as_dict(game.get("bullpens"))
    lineups = _as_dict(game.get("lineups"))
    injuries = _as_dict(game.get("injuries"))
    team_context = _as_dict(game.get("teamContext"))
    present = {
        "pitchers": all(bool(_as_dict(pitchers.get(side)).get("id") or _as_dict(pitchers.get(side)).get("name")) for side in ("away", "home")),
        "bullpens": all(any(value is not None for value in _as_dict(bullpens.get(side)).values()) for side in ("away", "home")),
        "lineups": all(_as_dict(lineups.get(side)).get("playerCount", 0) >= 9 for side in ("away", "home")),
        "injuries": all(side in injuries for side in ("away", "home")),
        "team_context": all(bool(_as_dict(team_context.get(side)).get("id") or _as_dict(team_context.get(side)).get("name")) for side in ("away", "home")),
    }
    missing = [name for name in REQUIRED_DOMAINS if not present[name]]
    confirmed_lineups = all(bool(_as_dict(lineups.get(side)).get("confirmed")) for side in ("away", "home"))
    confirmed_starters = all(bool(_as_dict(pitchers.get(side)).get("confirmed")) for side in ("away", "home"))
    return {
        "domainsPresent": present,
        "missingDomains": missing,
        "confirmedLineups": confirmed_lineups,
        "confirmedStarters": confirmed_starters,
        "trainingEligible": not missing and confirmed_lineups and confirmed_starters,
    }


def build_snapshot(matches: List[Mapping[str, Any]], game_date: str, captured_at: datetime, deploy_sha: str) -> Dict[str, Any]:
    games = [normalize_match(match, captured_at) for match in matches]
    body = {
        "version": VERSION,
        "authority": "V8_FUNDAMENTALS_SHADOW_ONLY",
        "gameDate": game_date,
        "capturedAtUtc": captured_at.isoformat(),
        "deployGitSha": deploy_sha,
        "productionV7Unchanged": True,
        "automaticWagerAllowed": False,
        "gameCount": len(games),
        "trainingEligibleGameCount": sum(1 for game in games if game["trainingEligible"]),
        "games": games,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    return body


def lambda_handler(event: Optional[Dict[str, Any]], context: Any) -> Dict[str, Any]:
    event = event or {}
    now = _now()
    game_date = _date_from_event(event, now)
    bucket = os.environ.get("MLB_V8_FUNDAMENTALS_BUCKET", "").strip()
    if not bucket:
        return {"ok": False, "error": "MLB_V8_FUNDAMENTALS_BUCKET_NOT_CONFIGURED"}
    deploy_sha = os.environ.get("INQSI_DEPLOY_GIT_SHA", "unknown")
    try:
        envelope = BigBallsDataClient(
            timeout_seconds=int(os.environ.get("MLB_V8_FUNDAMENTALS_HTTP_TIMEOUT_SECONDS", "8")),
            max_attempts=int(os.environ.get("MLB_V8_FUNDAMENTALS_MAX_ATTEMPTS", "2")),
        ).list_mlb_matches(game_date, limit=int(os.environ.get("MLB_V8_FUNDAMENTALS_MATCH_LIMIT", "50")))
        matches = envelope.get("data", [])
        snapshot = build_snapshot(matches, game_date, now, deploy_sha)
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
        key = f"mlb/v8/fundamentals/{game_date}/{snapshot['fingerprint']}.json"
        boto3.client("s3").put_object(
            Bucket=bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
            Metadata={"sha256": snapshot["fingerprint"], "authority": "v8-fundamentals-shadow"},
        )
        return {
            "ok": True,
            "authority": snapshot["authority"],
            "gameDate": game_date,
            "gameCount": snapshot["gameCount"],
            "trainingEligibleGameCount": snapshot["trainingEligibleGameCount"],
            "fingerprint": snapshot["fingerprint"],
            "artifactKey": key,
            "productionV7Unchanged": True,
        }
    except BBSClientError as exc:
        return {"ok": False, "authority": "V8_FUNDAMENTALS_SHADOW_ONLY", "error": str(exc), "productionV7Unchanged": True}
