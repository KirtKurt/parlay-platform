"""MLB V8 fundamentals-only shadow collector.

Collects authoritative per-game resources and fails closed when provider evidence is
incomplete. It never writes V7 or production wagering authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import boto3
from botocore.exceptions import ClientError

from bigballsdata_client import BBSClientError, BigBallsDataClient

VERSION = "MLB-V8-FUNDAMENTALS-v2-provider-gated"
AUTHORITY = "V8_FUNDAMENTALS_SHADOW_ONLY"
SIDES = ("away", "home")
REQUIRED_DOMAINS = ("pitchers", "bullpens", "lineups", "injuries", "team_context")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _strict_bool(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if value is False or value in (0, None, ""):
        return False
    return isinstance(value, str) and value.strip().lower() in {
        "true", "1", "yes", "confirmed", "available"
    }


def _first(mapping: Mapping[str, Any], names: Iterable[str], default: Any = None) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return default


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _data(resources: Mapping[str, Any], name: str) -> Any:
    value = resources.get(name)
    return value.get("data") if isinstance(value, Mapping) and "data" in value else value


def _evidence(resources: Mapping[str, Any], name: str) -> Dict[str, Any]:
    envelope = _dict(resources.get(name))
    meta = _dict(envelope.get("meta"))
    return {
        "available": isinstance(envelope.get("data"), (dict, list)),
        "confirmed": _strict_bool(_first(meta, ("confirmed", "complete", "authoritative"), False)),
        "updatedAt": _first(meta, ("updatedAt", "lastUpdated", "generatedAt", "timestamp")),
        "source": _first(meta, ("source", "provider", "feed"), "bigballsdata"),
    }


def _team(match: Mapping[str, Any], side: str, resources: Mapping[str, Any]) -> Dict[str, Any]:
    base = _dict(_first(match, (side, f"{side}Team", f"{side}_team"), {}))
    context = _dict(_data(resources, "team_context"))
    merged = {**base, **_dict(_first(context, (side, f"{side}Team"), {}))}
    return {
        "id": _first(merged, ("id", "teamId", "team_id")),
        "name": _first(merged, ("name", "fullName", "displayName")),
        "record": _first(merged, ("record", "seasonRecord", "standing")),
        "recentForm": _first(merged, ("recentForm", "last10", "streak")),
        "homeAwaySplit": _first(merged, ("homeAwaySplit", "splits")),
        "handednessSplits": _first(merged, ("handednessSplits", "vsHandedness", "platoonSplits")),
        "restDays": _first(merged, ("restDays", "daysRest")),
        "travel": _first(merged, ("travel", "travelDistance", "timezoneChange")),
        "offense": _first(merged, ("offense", "batting", "offensiveMetrics")),
        "defense": _first(merged, ("defense", "fielding", "defensiveMetrics")),
    }


def _pitcher(match: Mapping[str, Any], side: str, resources: Mapping[str, Any]) -> Dict[str, Any]:
    detail = _dict(_data(resources, "pitchers"))
    raw = _dict(_first(detail, (side, f"{side}Starter", f"{side}StartingPitcher"), {}))
    if not raw:
        raw = _dict(_first(match, (f"{side}Starter", f"{side}StartingPitcher", f"{side}_starter"), {}))
    stats = _dict(_first(raw, ("stats", "seasonStats", "metrics"), {}))
    return {
        "id": _first(raw, ("id", "playerId", "player_id")),
        "name": _first(raw, ("name", "fullName", "displayName")),
        "confirmed": _strict_bool(_first(raw, ("confirmed", "isConfirmed", "starterConfirmed"), False)),
        "era": _first(stats, ("era", "ERA")),
        "fip": _first(stats, ("fip", "FIP")),
        "xera": _first(stats, ("xera", "xERA")),
        "whip": _first(stats, ("whip", "WHIP")),
        "kMinusBbPct": _first(stats, ("kMinusBbPct", "k_bb_pct", "K-BB%")),
        "velocity": _first(stats, ("velocity", "avgFastballVelocity")),
        "pitchMix": _first(stats, ("pitchMix", "pitch_mix")),
        "expectedInnings": _first(raw, ("expectedInnings", "projectedInnings")),
        "recentThreeStarts": _first(raw, ("recent", "recentForm", "last3Starts"), {}),
        "health": _first(raw, ("health", "injuryStatus", "status")),
    }


def _bullpen(match: Mapping[str, Any], side: str, resources: Mapping[str, Any]) -> Dict[str, Any]:
    detail = _dict(_data(resources, "bullpens"))
    raw = _dict(_first(detail, (side, f"{side}Bullpen"), {}))
    if not raw:
        raw = _dict(_first(match, (f"{side}Bullpen", f"{side}_bullpen"), {}))
    return {
        "era": _first(raw, ("era", "ERA")), "fip": _first(raw, ("fip", "FIP")),
        "qualityScore": _first(raw, ("qualityScore", "strengthScore")),
        "freshnessScore": _first(raw, ("freshnessScore", "restScore")),
        "last3DaysInnings": _first(raw, ("last3DaysInnings", "inningsLast3Days")),
        "last2DaysPitches": _first(raw, ("last2DaysPitches", "pitchesLast2Days")),
        "closerAvailable": _first(raw, ("closerAvailable", "closerStatus")),
        "highLeverageAvailable": _first(raw, ("highLeverageAvailable", "leverageArmsAvailable")),
        "expectedInnings": _first(raw, ("expectedInnings", "projectedInnings")),
        "availableRelievers": _first(raw, ("availableRelievers", "activeRelievers", "availableArms")),
    }


def _lineup(match: Mapping[str, Any], side: str, resources: Mapping[str, Any]) -> Dict[str, Any]:
    detail = _dict(_data(resources, "lineups"))
    raw = _first(detail, (side, f"{side}Lineup"), None)
    if raw is None:
        raw = _first(match, (f"{side}Lineup", f"{side}_lineup", f"{side}BattingOrder"), [])
    raw_map = _dict(raw)
    rows = _list(raw) or _list(_first(raw_map, ("players", "batters", "order"), []))
    players = []
    for index, row in enumerate(rows, 1):
        item = _dict(row)
        players.append({
            "slot": _first(item, ("slot", "battingOrder", "order"), index),
            "id": _first(item, ("id", "playerId", "player_id")),
            "name": _first(item, ("name", "fullName", "displayName")),
            "position": _first(item, ("position", "pos")),
            "ops": _first(item, ("ops", "OPS")),
            "wrcPlus": _first(item, ("wrcPlus", "wRC+")),
        })
    slots = [str(p["slot"]) for p in players if p.get("slot") not in (None, "")]
    identities = [str(p.get("id") or p.get("name") or "") for p in players]
    confirmed = _first(raw_map, ("confirmed", "isConfirmed", "lineupConfirmed"), None)
    if confirmed is None:
        confirmed = _first(detail, (f"{side}Confirmed", f"{side}LineupConfirmed", "confirmed"), False)
    return {
        "confirmed": _strict_bool(confirmed), "players": players, "playerCount": len(players),
        "uniqueSlotCount": len(set(slots)),
        "uniquePlayerCount": len({x for x in identities if x}),
    }


def _injuries(match: Mapping[str, Any], side: str, resources: Mapping[str, Any]) -> Dict[str, Any]:
    envelope = _dict(resources.get("injuries"))
    detail = _dict(_data(resources, "injuries"))
    rows = _list(_first(detail, (side, f"{side}Injuries"), None))
    if not rows:
        rows = _list(_first(match, (f"{side}Injuries", f"{side}_injuries"), []))
    meta = _dict(_first(detail, (f"{side}Meta", f"{side}_meta"), {}))
    evidence = _evidence(resources, "injuries")
    return {
        "players": rows, "count": len(rows), "feedAvailable": evidence["available"],
        "reportConfirmed": _strict_bool(_first(meta, ("confirmed", "complete", "authoritative"), evidence["confirmed"])),
        "updatedAt": _first(meta, ("updatedAt", "lastUpdated"), evidence["updatedAt"]),
        "source": evidence["source"],
    }


def _valid_team(value: Mapping[str, Any]) -> bool:
    return bool(value.get("id") or value.get("name")) and value.get("record") is not None \
        and value.get("recentForm") is not None and value.get("restDays") is not None \
        and value.get("travel") is not None \
        and (value.get("homeAwaySplit") is not None or value.get("handednessSplits") is not None)


def _valid_bullpen(value: Mapping[str, Any]) -> bool:
    quality = value.get("qualityScore") is not None or (value.get("era") is not None and value.get("fip") is not None)
    freshness = value.get("freshnessScore") is not None or (value.get("last3DaysInnings") is not None and value.get("last2DaysPitches") is not None)
    availability = value.get("closerAvailable") is not None and value.get("highLeverageAvailable") is not None
    workload = value.get("expectedInnings") is not None or value.get("availableRelievers") is not None
    return quality and freshness and availability and workload


def coverage(game: Mapping[str, Any]) -> Dict[str, Any]:
    pitchers, bullpens = _dict(game.get("pitchers")), _dict(game.get("bullpens"))
    lineups, injuries = _dict(game.get("lineups")), _dict(game.get("injuries"))
    teams, evidence = _dict(game.get("teamContext")), _dict(game.get("providerEvidence"))
    present = {
        "pitchers": all(bool(_dict(pitchers.get(s)).get("id") or _dict(pitchers.get(s)).get("name")) for s in SIDES),
        "bullpens": all(_valid_bullpen(_dict(bullpens.get(s))) for s in SIDES),
        "lineups": all(_dict(lineups.get(s)).get("playerCount") == 9 and _dict(lineups.get(s)).get("uniqueSlotCount") == 9 and _dict(lineups.get(s)).get("uniquePlayerCount") == 9 for s in SIDES),
        "injuries": all(_dict(injuries.get(s)).get("feedAvailable") is True and _dict(injuries.get(s)).get("reportConfirmed") is True and bool(_dict(injuries.get(s)).get("updatedAt")) for s in SIDES),
        "team_context": all(_valid_team(_dict(teams.get(s))) for s in SIDES),
    }
    provider = {name: _dict(evidence.get(name)).get("available") is True for name in REQUIRED_DOMAINS}
    missing = [name for name in REQUIRED_DOMAINS if not present[name] or not provider[name]]
    confirmed_lineups = all(_dict(lineups.get(s)).get("confirmed") is True for s in SIDES)
    confirmed_starters = all(_dict(pitchers.get(s)).get("confirmed") is True for s in SIDES)
    return {
        "domainsPresent": present, "providerResourcesAvailable": provider,
        "missingDomains": missing, "confirmedLineups": confirmed_lineups,
        "confirmedStarters": confirmed_starters,
        "trainingEligible": not missing and confirmed_lineups and confirmed_starters,
    }


def normalize_match(match: Mapping[str, Any], captured_at: datetime, resources: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    resources = _dict(resources)
    away, home = _team(match, "away", resources), _team(match, "home", resources)
    game = {
        "matchId": _first(match, ("id", "matchId", "eventId", "gameId")),
        "gameDate": str(_first(match, ("date", "gameDate", "startTime"), ""))[:10],
        "startTimeUtc": _first(match, ("startTime", "commenceTime", "scheduledAt")),
        "awayTeam": away, "homeTeam": home,
        "pitchers": {s: _pitcher(match, s, resources) for s in SIDES},
        "bullpens": {s: _bullpen(match, s, resources) for s in SIDES},
        "lineups": {s: _lineup(match, s, resources) for s in SIDES},
        "injuries": {s: _injuries(match, s, resources) for s in SIDES},
        "teamContext": {"away": away, "home": home},
        "weather": _data(resources, "weather") or _first(match, ("weather", "forecast")),
        "park": _data(resources, "park") or _first(match, ("park", "venue", "ballpark")),
        "providerEvidence": {name: _evidence(resources, name) for name in REQUIRED_DOMAINS},
        "capturedAtUtc": captured_at.isoformat(),
    }
    game["coverage"] = coverage(game)
    game["trainingEligible"] = game["coverage"]["trainingEligible"]
    return game


def build_snapshot(games: Sequence[Mapping[str, Any]], game_date: str, captured_at: datetime, deploy_sha: str) -> Dict[str, Any]:
    normalized = list(games)
    stable_games = [{k: v for k, v in g.items() if k != "capturedAtUtc"} for g in normalized]
    stable = {
        "version": VERSION, "authority": AUTHORITY, "gameDate": game_date,
        "productionV7Unchanged": True, "automaticWagerAllowed": False, "games": stable_games,
    }
    fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        **stable, "capturedAtUtc": captured_at.isoformat(), "deployGitSha": deploy_sha,
        "gameCount": len(normalized),
        "trainingEligibleGameCount": sum(g.get("trainingEligible") is True for g in normalized),
        "fingerprint": fingerprint,
    }


def _fetch_resources(client: BigBallsDataClient, match: Mapping[str, Any], game_date: str) -> Dict[str, Any]:
    match_id = str(_first(match, ("id", "matchId", "eventId", "gameId"), "")).strip()
    if not match_id:
        return {}
    resources = {}
    for name in (*REQUIRED_DOMAINS, "weather", "park"):
        try:
            resources[name] = client.get_mlb_match_resource(match_id, name, game_date=game_date)
        except BBSClientError as exc:
            resources[name] = {"data": None, "meta": {"source": "bigballsdata"}, "error": str(exc)}
    return resources


def _put_immutable(s3: Any, bucket: str, key: str, payload: bytes, fingerprint: str) -> str:
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/json",
                      Metadata={"sha256": fingerprint, "authority": "v8-fundamentals-shadow"}, IfNoneMatch="*")
        return "CREATED"
    except ClientError as exc:
        code = str(((exc.response or {}).get("Error") or {}).get("Code") or "")
        status = int(((exc.response or {}).get("ResponseMetadata") or {}).get("HTTPStatusCode") or 0)
        if code in {"PreconditionFailed", "ConditionalRequestConflict"} or status in {409, 412}:
            existing = str((s3.head_object(Bucket=bucket, Key=key).get("Metadata") or {}).get("sha256") or "")
            if existing != fingerprint:
                raise RuntimeError("V8_IMMUTABLE_ARTIFACT_FINGERPRINT_CONFLICT") from None
            return "EXISTING_IDENTICAL"
        raise


def lambda_handler(event: Optional[Dict[str, Any]], context: Any) -> Dict[str, Any]:
    event, now = event or {}, _now()
    game_date = str(event.get("gameDate") or event.get("date") or now.date().isoformat())[:10]
    bucket = os.environ.get("MLB_V8_FUNDAMENTALS_BUCKET", "").strip()
    if not bucket:
        return {"ok": False, "authority": AUTHORITY, "error": "MLB_V8_FUNDAMENTALS_BUCKET_NOT_CONFIGURED", "productionV7Unchanged": True}
    try:
        client = BigBallsDataClient(
            timeout_seconds=_env_int("MLB_V8_FUNDAMENTALS_HTTP_TIMEOUT_SECONDS", 8, 1, 30),
            max_attempts=_env_int("MLB_V8_FUNDAMENTALS_MAX_ATTEMPTS", 2, 1, 5),
        )
        matches = client.list_mlb_matches(game_date, limit=_env_int("MLB_V8_FUNDAMENTALS_MATCH_LIMIT", 50, 1, 200)).get("data", [])
        games = [normalize_match(m, now, _fetch_resources(client, m, game_date)) for m in matches if isinstance(m, Mapping)]
        snapshot = build_snapshot(games, game_date, now, os.environ.get("INQSI_DEPLOY_GIT_SHA", "unknown"))
        payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
        key = f"mlb/v8/fundamentals/{game_date}/{snapshot['fingerprint']}.json"
        write_status = _put_immutable(boto3.client("s3"), bucket, key, payload, snapshot["fingerprint"])
        return {
            "ok": True, "authority": AUTHORITY, "gameDate": game_date,
            "gameCount": snapshot["gameCount"],
            "trainingEligibleGameCount": snapshot["trainingEligibleGameCount"],
            "fingerprint": snapshot["fingerprint"], "artifactKey": key,
            "artifactWriteStatus": write_status, "productionV7Unchanged": True,
        }
    except BBSClientError as exc:
        return {"ok": False, "authority": AUTHORITY, "error": str(exc), "productionV7Unchanged": True}
    except Exception as exc:
        return {"ok": False, "authority": AUTHORITY, "error": f"{type(exc).__name__}:{str(exc)[:300]}", "productionV7Unchanged": True}
