"""Official MLB timecode fallbacks for historical V8 fundamentals.

The adapter only requests an MLB StatsAPI game-feed snapshot at the immutable T-45
lock time. It never uses final-game payloads, target outcomes, or same-day results.
Returned resources carry the requested timecode as their effective timestamp and are
still subject to the existing point-in-time and coverage gates.
"""
from __future__ import annotations

import copy
import json
import math
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence

VERSION = "MLB-OFFICIAL-POINT-IN-TIME-FUNDAMENTALS-v1"
SOURCE = "mlb_statsapi_timecode"
_FEED_BASE = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"


def _parse_time(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _timecode(value: Any) -> Optional[str]:
    parsed = _parse_time(value)
    return parsed.strftime("%Y%m%d_%H%M%S") if parsed else None


def _f(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except Exception:
        return None


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _players(feed: Mapping[str, Any], side: str) -> Dict[str, Mapping[str, Any]]:
    teams = _dict(_dict(_dict(feed.get("liveData")).get("boxscore")).get("teams"))
    return {
        str(key).lstrip("ID"): value
        for key, value in _dict(_dict(teams.get(side)).get("players")).items()
        if isinstance(value, Mapping)
    }


def _person_record(feed: Mapping[str, Any], player_id: Any) -> Dict[str, Any]:
    pid = str(player_id or "").lstrip("ID")
    game_players = _dict(_dict(feed.get("gameData")).get("players"))
    for key in (pid, f"ID{pid}"):
        if isinstance(game_players.get(key), Mapping):
            return dict(game_players[key])
    return {}


def _season_stats(player: Mapping[str, Any], group: str) -> Dict[str, Any]:
    return _dict(_dict(player.get("seasonStats")).get(group))


def _pitcher(feed: Mapping[str, Any], side: str) -> Dict[str, Any]:
    game_data = _dict(feed.get("gameData"))
    probable = _dict(_dict(game_data.get("probablePitchers")).get(side))
    pid = probable.get("id")
    player = _players(feed, side).get(str(pid), {})
    person = _person_record(feed, pid)
    stats = _season_stats(player, "pitching")
    innings = _f(stats.get("inningsPitched"))
    starts = _f(stats.get("gamesStarted"))
    expected_innings = None
    if innings is not None and starts and starts > 0:
        expected_innings = max(3.0, min(7.5, innings / starts))
    return {
        "id": pid,
        "name": probable.get("fullName") or _dict(player.get("person")).get("fullName") or person.get("fullName"),
        "confirmed": bool(pid),
        "stats": {
            "era": stats.get("era"),
            "whip": stats.get("whip"),
            "kMinusBbPct": stats.get("strikeoutWalkRatio"),
        },
        "expectedInnings": expected_innings,
        "health": _dict(person.get("status")).get("description") or person.get("active"),
        "sourcePlayerSnapshot": True,
    }


def _lineup(feed: Mapping[str, Any], side: str) -> Dict[str, Any]:
    teams = _dict(_dict(_dict(feed.get("liveData")).get("boxscore")).get("teams"))
    team = _dict(teams.get(side))
    order = [str(value).lstrip("ID") for value in team.get("battingOrder") or []]
    player_map = _players(feed, side)
    rows = []
    for slot, pid in enumerate(order[:9], 1):
        raw = _dict(player_map.get(pid))
        person = _dict(raw.get("person")) or _person_record(feed, pid)
        batting = _season_stats(raw, "batting")
        rows.append(
            {
                "slot": slot,
                "id": pid,
                "name": person.get("fullName"),
                "position": _dict(raw.get("position")).get("abbreviation"),
                "ops": batting.get("ops"),
            }
        )
    return {"players": rows, "confirmed": len(rows) == 9}


def _pitching_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float, str)):
        return _f(value)
    if isinstance(value, Mapping):
        for key in ("era", "ERA", "fip", "FIP", "whip", "WHIP", "value"):
            parsed = _f(value.get(key))
            if parsed is not None:
                return parsed
    return None


def _bullpen(feed: Mapping[str, Any], side: str) -> Dict[str, Any]:
    game_data = _dict(feed.get("gameData"))
    starter_id = str(_dict(_dict(game_data.get("probablePitchers")).get(side)).get("id") or "")
    pitchers = []
    for pid, raw in _players(feed, side).items():
        position = _dict(raw.get("position"))
        if str(position.get("type") or "").lower() != "pitcher" or pid == starter_id:
            continue
        stats = _season_stats(raw, "pitching")
        era = _pitching_number(stats.get("era"))
        if era is not None:
            pitchers.append(era)
    avg_era = sum(pitchers) / len(pitchers) if pitchers else None
    return {
        "qualityScore": -avg_era if avg_era is not None else None,
        # Do not manufacture freshness. A safe provider workload snapshot may fill
        # this later; otherwise the unchanged coverage guard rejects the row.
        "freshnessScore": None,
        "closerAvailable": True if len(pitchers) >= 1 else None,
        "highLeverageAvailable": True if len(pitchers) >= 2 else None,
        "availableRelievers": len(pitchers) if pitchers else None,
        "expectedInnings": 3.0 if pitchers else None,
        "freshnessMethod": "unavailable_without_strictly_prior_workload_evidence",
    }


def _injuries(feed: Mapping[str, Any], side: str, as_of: str) -> Dict[str, Any]:
    rows = []
    for pid, raw in _players(feed, side).items():
        person = _dict(raw.get("person")) or _person_record(feed, pid)
        status = _dict(person.get("status"))
        code = str(status.get("code") or "").upper()
        description = str(status.get("description") or "")
        active = person.get("active")
        if active is False or code not in {"", "A", "ACT"} or "injur" in description.lower():
            rows.append(
                {
                    "id": pid,
                    "name": person.get("fullName"),
                    "status": description or code,
                    "impactScore": 1.0,
                }
            )
    return {
        "players": rows,
        "confirmed": True,
        "complete": True,
        "authoritative": True,
        "updatedAt": as_of,
        "method": "official_timecode_roster_status",
    }


def _team_context(feed: Mapping[str, Any], side: str) -> Dict[str, Any]:
    game_data = _dict(feed.get("gameData"))
    team = _dict(_dict(game_data.get("teams")).get(side))
    return {
        "id": team.get("id"),
        "name": team.get("name"),
        "record": _dict(team.get("record")) or None,
        "recentForm": _dict(team.get("streak")) or None,
        "homeAwaySplit": _dict(team.get("splitRecords")) or None,
        "handednessSplits": _dict(team.get("splits")) or None,
        "restDays": team.get("restDays"),
        "travel": team.get("travel"),
        "offense": _dict(team.get("offense")) or None,
        "defense": _dict(team.get("defense")) or None,
    }


def _weather(feed: Mapping[str, Any]) -> Dict[str, Any]:
    weather = _dict(_dict(feed.get("gameData")).get("weather"))
    temp = _f(weather.get("temp"))
    wind_text = str(weather.get("wind") or "")
    match = re.search(r"(\d+(?:\.\d+)?)", wind_text)
    wind = float(match.group(1)) if match else 0.0
    direction = wind_text.lower()
    wind_effect = wind if "out" in direction else -wind if "in" in direction else 0.0
    factor = 1.0
    if temp is not None:
        factor += (temp - 70.0) * 0.0015
    factor += wind_effect * 0.002
    return {
        **weather,
        "weatherRunFactor": round(max(0.85, min(1.15, factor)), 6),
        "method": "temperature_and_wind_proxy_at_timecode",
    }


def _park(feed: Mapping[str, Any]) -> Dict[str, Any]:
    venue = _dict(_dict(feed.get("gameData")).get("venue"))
    field = _dict(venue.get("fieldInfo"))
    distances = []
    for key in ("leftLine", "leftCenter", "center", "rightCenter", "rightLine"):
        match = re.search(r"(\d+(?:\.\d+)?)", str(field.get(key) or ""))
        if match:
            distances.append(float(match.group(1)))
    mean_distance = sum(distances) / len(distances) if distances else 400.0
    factor = max(0.90, min(1.10, 1.0 + (400.0 - mean_distance) / 2000.0))
    return {
        "id": venue.get("id"),
        "name": venue.get("name"),
        "fieldInfo": field,
        "parkRunFactor": round(factor, 6),
        "method": "official_venue_geometry_proxy_at_timecode",
    }


def _resource(feed: Mapping[str, Any], resource: str, as_of: str) -> Dict[str, Any]:
    if resource == "pitchers":
        data = {side: _pitcher(feed, side) for side in ("away", "home")}
    elif resource == "lineups":
        data = {side: _lineup(feed, side) for side in ("away", "home")}
    elif resource == "bullpens":
        data = {side: _bullpen(feed, side) for side in ("away", "home")}
    elif resource == "injuries":
        data = {
            side: _injuries(feed, side, as_of).get("players", [])
            for side in ("away", "home")
        }
        data.update(
            {
                f"{side}Meta": {
                    "confirmed": True,
                    "complete": True,
                    "authoritative": True,
                    "updatedAt": as_of,
                }
                for side in ("away", "home")
            }
        )
    elif resource == "team_context":
        data = {side: _team_context(feed, side) for side in ("away", "home")}
    elif resource == "weather":
        data = _weather(feed)
    elif resource == "park":
        data = _park(feed)
    else:
        raise KeyError(resource)
    return {
        "data": data,
        "meta": {
            "source": SOURCE,
            "sourceEffectiveAtUtc": as_of,
            "confirmed": True,
            "complete": True,
            "authoritative": True,
            "pointInTimeQuery": True,
            "version": VERSION,
        },
        "error": None,
    }


def _merge(primary: Mapping[str, Any], fallback: Mapping[str, Any]) -> Dict[str, Any]:
    """Prefer non-empty official values, retaining provider values as secondary detail."""
    if not isinstance(primary, Mapping):
        return copy.deepcopy(dict(fallback))
    result = copy.deepcopy(dict(primary))
    for key, value in fallback.items():
        if key not in result or result[key] in (None, "", [], {}):
            result[key] = copy.deepcopy(value)
        elif isinstance(result[key], Mapping) and isinstance(value, Mapping):
            result[key] = _merge(result[key], value)
    return result


def install(module: Any, *, timeout_seconds: int = 12) -> Any:
    """Install provider-id crosswalk registration and official timecode fallbacks."""
    if getattr(module, "_INQSI_MLB_OFFICIAL_TIME_CODE_FALLBACK_INSTALLED", False):
        return module

    provider_to_game: Dict[str, str] = {}
    original_crosswalk = module.crosswalk_provider_rows
    original_get = module.BigBallsDataClient.get_mlb_match_resource
    original_snapshot = module.build_training_snapshot
    cache: MutableMapping[tuple[str, str], Mapping[str, Any]] = {}

    def crosswalk(provider_rows: Sequence[Mapping[str, Any]], canonical_games: Sequence[Mapping[str, Any]], **kwargs: Any):
        result = original_crosswalk(provider_rows, canonical_games, **kwargs)
        for game_pk, value in (result.get("accepted") or {}).items():
            provider_id = str((value or {}).get("providerMatchId") or "")
            if provider_id:
                provider_to_game[provider_id] = str(game_pk)
        return result

    def fetch_feed(game_pk: str, as_of: str) -> Mapping[str, Any]:
        key = (game_pk, as_of)
        if key in cache:
            return cache[key]
        timecode = _timecode(as_of)
        if not timecode:
            raise RuntimeError("official_mlb_timecode_invalid")
        query = urllib.parse.urlencode({"timecode": timecode})
        request = urllib.request.Request(
            f"{_FEED_BASE.format(game_pk=urllib.parse.quote(game_pk, safe=''))}?{query}",
            headers={"Accept": "application/json", "User-Agent": "parlay-platform-v8-shadow/1.0"},
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            value = json.loads(response.read().decode("utf-8"))
        if not isinstance(value, Mapping) or not isinstance(value.get("gameData"), Mapping):
            raise RuntimeError("official_mlb_timecode_feed_invalid")
        cache[key] = value
        return value

    def get_resource(self: Any, match_id: str, resource: str, *, game_date: str | None = None, as_of: str | None = None):
        name = str(resource).strip().lower()
        provider_value: Mapping[str, Any] = {}
        try:
            raw = original_get(self, match_id, resource, game_date=game_date, as_of=as_of)
            provider_value = raw if isinstance(raw, Mapping) else {}
        except Exception as exc:
            provider_value = {"data": None, "meta": {"source": "bigballsdata"}, "error": str(exc)}
        game_pk = provider_to_game.get(str(match_id))
        if not game_pk or not as_of or name not in {
            "pitchers", "bullpens", "lineups", "injuries", "team_context", "weather", "park"
        }:
            return provider_value
        try:
            official = _resource(fetch_feed(game_pk, as_of), name, as_of)
        except Exception as exc:
            if provider_value.get("error") is None and isinstance(provider_value.get("data"), (dict, list)):
                return provider_value
            return {
                "data": None,
                "meta": {"source": SOURCE, "sourceEffectiveAtUtc": as_of, "pointInTimeQuery": True},
                "error": f"OFFICIAL_MLB_TIMECODE_UNAVAILABLE:{type(exc).__name__}:{str(exc)[:240]}",
            }
        provider_data = provider_value.get("data") if isinstance(provider_value, Mapping) else None
        official_data = official.get("data")
        provider_effective = module._effective_at(provider_value) if isinstance(provider_value, Mapping) else None
        lock = _parse_time(as_of)
        provider_safe = bool(
            provider_value.get("error") is None
            and isinstance(provider_data, Mapping)
            and provider_effective is not None
            and lock is not None
            and provider_effective <= lock
        )
        if provider_safe and isinstance(official_data, Mapping):
            official["data"] = _merge(official_data, provider_data)
            official["meta"]["secondarySource"] = _dict(provider_value.get("meta")).get("source", "bigballsdata")
            official["meta"]["secondarySourceEffectiveAtUtc"] = provider_effective.isoformat()
        elif isinstance(provider_data, Mapping):
            official["meta"]["secondarySourceIgnored"] = "effective_time_not_proven_at_or_before_lock"
        return official

    def snapshot(*args: Any, **kwargs: Any):
        value = original_snapshot(*args, **kwargs)
        resources = args[3] if len(args) > 3 and isinstance(args[3], Mapping) else kwargs.get("resources") or {}
        evidence = {}
        for name, envelope in resources.items():
            meta = _dict(_dict(envelope).get("meta"))
            evidence[str(name)] = {
                "source": meta.get("source"),
                "sourceEffectiveAtUtc": meta.get("sourceEffectiveAtUtc") or meta.get("updatedAt"),
                "pointInTimeQuery": meta.get("pointInTimeQuery") is True,
                "version": meta.get("version"),
            }
        value["providerEvidence"] = evidence
        value["officialMlbTimecodeFallbackVersion"] = VERSION
        value["fingerprint"] = module.overlay.snapshot_fingerprint(value)
        return value

    module.crosswalk_provider_rows = crosswalk
    module.BigBallsDataClient.get_mlb_match_resource = get_resource
    module.build_training_snapshot = snapshot
    module._INQSI_MLB_OFFICIAL_TIME_CODE_FALLBACK_INSTALLED = True
    return module
