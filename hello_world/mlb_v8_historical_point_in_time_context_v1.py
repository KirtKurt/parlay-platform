"""Leakage-safe target-game historical context for the MLB V8 shadow learner.

Confirmed target starters and lineups use archived BBD T-45 evidence when present.
Otherwise they are projections made only from earlier official games. Bullpen,
injury, park, team, and weather inputs are reconstructed from data whose effective
time is no later than the immutable lock. Target scores and same-day results are
never exposed to feature construction.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

VERSION = "MLB-V8-HISTORICAL-POINT-IN-TIME-CONTEXT-v1"
MLB_API = "https://statsapi.mlb.com/api/v1"
OPEN_METEO_SINGLE_RUN = "https://single-runs-api.open-meteo.com/v1/forecast"
WEATHER_MODEL = "ecmwf_ifs025"
WEATHER_PUBLICATION_LAG_HOURS = 6
RECENT_CONTEXT_DAYS = 45
PROJECTION_HISTORY_DAYS = 120
PARK_HISTORY_DAYS = 400
TRANSACTION_LOOKBACK_DAYS = 365
SIDES = ("away", "home")
FINAL_STATES = {"final", "game over", "completed early"}

_CANONICAL_BY_PROVIDER_ID: Dict[str, Dict[str, Any]] = {}
OFFICIAL_REQUEST_COUNT = 0
SYNTHETIC_OFFICIAL_IDENTITY_COUNT = 0
STORED_DISCOVERY_ERROR_COUNT = 0


def _dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _number(value: Any) -> Optional[float]:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def _date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def _parse_utc(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _sha(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode()).hexdigest()


def _strict_prior_cutoff(canonical: Mapping[str, Any]) -> datetime:
    target = _date(canonical.get("slateDateEt"))
    lock = _parse_utc(canonical.get("predictionLockAtUtc"))
    if target is None or lock is None:
        raise ValueError("target slate date or prediction lock is invalid")
    cutoff = datetime.combine(
        target - timedelta(days=1),
        time(23, 59, 59),
        tzinfo=ZoneInfo("America/New_York"),
    ).astimezone(timezone.utc)
    if cutoff > lock:
        raise ValueError("strictly prior cutoff exceeds prediction lock")
    return cutoff


def latest_conservatively_available_weather_run(lock_at: Any) -> datetime:
    lock = _parse_utc(lock_at)
    if lock is None:
        raise ValueError("prediction lock is invalid")
    available = lock - timedelta(hours=WEATHER_PUBLICATION_LAG_HOURS)
    return available.replace(
        hour=(available.hour // 6) * 6, minute=0, second=0, microsecond=0
    )


def _innings_to_outs(value: Any) -> int:
    text = str(value or "0")
    try:
        if "." not in text:
            return max(0, int(float(text)) * 3)
        whole, fraction = text.split(".", 1)
        return max(0, int(whole) * 3 + min(max(int(fraction[:1] or 0), 0), 2))
    except Exception:
        return 0


def _aggregate_pitching(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    outs = sum(_innings_to_outs(row.get("inningsPitched")) for row in rows)
    pitches = sum(
        int(_number(row.get("numberOfPitches") or row.get("pitchesThrown")) or 0)
        for row in rows
    )
    if not outs:
        return {
            "era": None,
            "fip": None,
            "whip": None,
            "kMinusBbPct": None,
            "innings": 0.0,
            "pitches": pitches,
        }
    innings = outs / 3.0
    er = sum(int(_number(row.get("earnedRuns")) or 0) for row in rows)
    hits = sum(int(_number(row.get("hits")) or 0) for row in rows)
    walks = sum(int(_number(row.get("baseOnBalls")) or 0) for row in rows)
    hbp = sum(int(_number(row.get("hitBatsmen")) or 0) for row in rows)
    hr = sum(int(_number(row.get("homeRuns")) or 0) for row in rows)
    strikeouts = sum(int(_number(row.get("strikeOuts")) or 0) for row in rows)
    batters = sum(int(_number(row.get("battersFaced")) or 0) for row in rows)
    return {
        "era": round(er * 9 / innings, 4),
        "fip": round((13 * hr + 3 * (walks + hbp) - 2 * strikeouts) / innings + 3.1, 4),
        "whip": round((walks + hits) / innings, 4),
        "kMinusBbPct": round((strikeouts - walks) * 100 / batters, 4) if batters else None,
        "innings": round(innings, 3),
        "pitches": pitches,
    }


def _aggregate_hitting(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    ab = sum(int(_number(row.get("atBats")) or 0) for row in rows)
    hits = sum(int(_number(row.get("hits")) or 0) for row in rows)
    doubles = sum(int(_number(row.get("doubles")) or 0) for row in rows)
    triples = sum(int(_number(row.get("triples")) or 0) for row in rows)
    hr = sum(int(_number(row.get("homeRuns")) or 0) for row in rows)
    walks = sum(int(_number(row.get("baseOnBalls")) or 0) for row in rows)
    hbp = sum(int(_number(row.get("hitByPitch")) or 0) for row in rows)
    sf = sum(int(_number(row.get("sacFlies")) or 0) for row in rows)
    denominator = ab + walks + hbp + sf
    singles = max(0, hits - doubles - triples - hr)
    obp = (hits + walks + hbp) / denominator if denominator else None
    slg = (singles + 2 * doubles + 3 * triples + 4 * hr) / ab if ab else None
    return {"ops": round(obp + slg, 4) if obp is not None and slg is not None else None}


def _filtered_log(payload: Mapping[str, Any], cutoff: date) -> List[Mapping[str, Any]]:
    output = []
    for block in payload.get("stats") or []:
        for split in _dict(block).get("splits") or []:
            split = _dict(split)
            day = _date(
                split.get("date")
                or split.get("gameDate")
                or _dict(split.get("game")).get("gameDate")
            )
            stat = split.get("stat")
            if day is not None and day < cutoff and isinstance(stat, Mapping):
                output.append({"date": day.isoformat(), **dict(stat)})
    return sorted(output, key=lambda row: str(row.get("date") or ""))


def reconstruct_active_injuries(
    transactions: Sequence[Mapping[str, Any]], cutoff_day: date
) -> List[Dict[str, Any]]:
    active: Dict[str, Dict[str, Any]] = {}
    ordered = sorted(
        (row for row in transactions if isinstance(row, Mapping)),
        key=lambda row: (
            str(row.get("effectiveDate") or row.get("date") or ""),
            str(row.get("id") or ""),
        ),
    )
    for row in ordered:
        effective = _date(row.get("effectiveDate") or row.get("date"))
        if effective is None or effective > cutoff_day:
            continue
        text = " ".join(
            str(row.get(key) or "")
            for key in ("description", "typeDesc", "typeCode", "resolutionCode")
        ).lower()
        if not any(token in text for token in ("injured list", "disabled list", " il ")):
            continue
        person = _dict(row.get("person") or row.get("player"))
        person_id = str(person.get("id") or row.get("personId") or "")
        name = str(
            person.get("fullName") or person.get("name") or row.get("playerName") or ""
        )
        identity = person_id or name.lower()
        if not identity:
            continue
        if any(
            token in text
            for token in ("reinstated", "activated from", "returned from", "removed from")
        ):
            active.pop(identity, None)
        elif any(token in text for token in ("placed on", "transferred to", "added to")):
            active[identity] = {
                "id": person_id or None,
                "name": name or None,
                "effectiveDate": effective.isoformat(),
                "source": "official_mlb_transaction",
                "impactScore": 1.0,
            }
    return sorted(
        active.values(), key=lambda row: (str(row.get("name") or ""), str(row.get("id") or ""))
    )


def weather_run_factor(hour: Mapping[str, Any]) -> float:
    c = _number(hour.get("temperature_2m")) or 20.0
    humidity = _number(hour.get("relative_humidity_2m")) or 50.0
    wind_mph = (_number(hour.get("wind_speed_10m")) or 0.0) * 0.621371
    rain = _number(hour.get("precipitation_probability")) or 0.0
    value = 1 + (c * 9 / 5 + 32 - 70) * 0.002 + (humidity - 50) * 0.00035
    value += min(max(wind_mph, 0), 30) * 0.0006 - min(max(rain, 0), 100) * 0.00015
    return round(min(max(value, 0.85), 1.15), 6)


def _stored_pitchers_ready(envelope: Mapping[str, Any]) -> bool:
    data = _dict(envelope.get("data")) if isinstance(envelope, Mapping) else {}
    return envelope.get("error") is None and all(
        _dict(data.get(side)).get("confirmed") is True
        and any(
            _dict(data.get(side)).get(key)
            for key in ("id", "playerId", "name", "fullName")
        )
        for side in SIDES
    )


def _stored_lineups_ready(envelope: Mapping[str, Any]) -> bool:
    data = _dict(envelope.get("data")) if isinstance(envelope, Mapping) else {}
    for side in SIDES:
        raw = _dict(data.get(side))
        players = [_dict(row) for row in raw.get("players") or []]
        identities = {
            str(row.get("id") or row.get("playerId") or row.get("name") or "")
            for row in players
        }
        if (
            envelope.get("error") is not None
            or raw.get("confirmed") is not True
            or len({value for value in identities if value}) < 9
        ):
            return False
    return True


def _projection_verified(envelope: Mapping[str, Any]) -> bool:
    meta = _dict(envelope.get("meta")) if isinstance(envelope, Mapping) else {}
    return bool(
        envelope.get("error") is None
        and meta.get("complete") is True
        and meta.get("pointInTimeProjectionVerified") is True
    )


def _schedule_games(payload: Mapping[str, Any]) -> List[Dict[str, Any]]:
    return [
        dict(game)
        for row in payload.get("dates") or []
        if isinstance(row, Mapping)
        for game in row.get("games") or []
        if isinstance(game, Mapping)
    ]


def _team_id(game: Mapping[str, Any], side: str) -> Optional[str]:
    value = _dict(_dict(_dict(game.get("teams")).get(side)).get("team")).get("id")
    return str(value) if value not in (None, "") else None


def _game_side(game: Mapping[str, Any], team_id: str) -> Optional[str]:
    return next((side for side in SIDES if _team_id(game, side) == str(team_id)), None)


def _score(game: Mapping[str, Any], side: str) -> Optional[float]:
    return _number(_dict(_dict(game.get("teams")).get(side)).get("score"))


def _final(game: Mapping[str, Any]) -> bool:
    status = _dict(game.get("status"))
    return bool(
        str(status.get("abstractGameState") or "").lower() == "final"
        or str(status.get("detailedState") or "").lower() in FINAL_STATES
    )


def _official_day(game: Mapping[str, Any]) -> Optional[date]:
    day = _date(game.get("officialDate"))
    start = _parse_utc(game.get("gameDate"))
    return day or (
        start.astimezone(ZoneInfo("America/New_York")).date() if start else None
    )


def _venue_id(game: Mapping[str, Any]) -> Optional[str]:
    value = _dict(game.get("venue")).get("id")
    return str(value) if value not in (None, "") else None


def _coordinates(game: Mapping[str, Any]) -> Optional[Tuple[float, float]]:
    venue = _dict(game.get("venue"))
    location = _dict(venue.get("location"))
    coordinates = _dict(
        location.get("defaultCoordinates") or venue.get("defaultCoordinates")
    )
    lat = _number(coordinates.get("latitude"))
    lon = _number(coordinates.get("longitude"))
    return (lat, lon) if lat is not None and lon is not None else None


def _haversine(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    value = math.sin((lat2 - lat1) / 2) ** 2
    value += math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 3958.7613 * 2 * math.asin(min(1.0, math.sqrt(value)))


class OfficialContextSource:
    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout_seconds: int = 15,
    ) -> None:
        self.opener = opener
        self.timeout = max(1, int(timeout_seconds))
        self.cache: Dict[str, Any] = {}

    def _get(
        self, endpoint: str, params: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        query = urllib.parse.urlencode(
            {str(key): value for key, value in (params or {}).items() if value not in (None, "")}
        )
        url = endpoint + ("?" + query if query else "")
        if url not in self.cache:
            request = urllib.request.Request(
                url,
                headers={
                    "accept": "application/json",
                    "user-agent": "inqsi-mlb-context/1.0",
                },
            )
            with self.opener(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode())
            if not isinstance(payload, Mapping):
                raise RuntimeError("official context response is not an object")
            global OFFICIAL_REQUEST_COUNT
            OFFICIAL_REQUEST_COUNT += 1
            self.cache[url] = dict(payload)
        return copy.deepcopy(self.cache[url])

    def schedule(self, start: date, end: date) -> Dict[str, Any]:
        return self._get(
            f"{MLB_API}/schedule",
            {
                "sportId": 1,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "hydrate": "probablePitcher,venue,linescore",
            },
        )

    def boxscore(self, game_pk: str) -> Dict[str, Any]:
        return self._get(
            f"{MLB_API}/game/{urllib.parse.quote(str(game_pk), safe='')}/boxscore"
        )

    def game_log(self, person_id: str, group: str, season: int) -> Dict[str, Any]:
        return self._get(
            f"{MLB_API}/people/{urllib.parse.quote(str(person_id), safe='')}/stats",
            {"stats": "gameLog", "group": group, "season": season},
        )

    def transactions(self, team_id: str, start: date, end: date) -> Dict[str, Any]:
        return self._get(
            f"{MLB_API}/transactions",
            {
                "sportId": 1,
                "teamId": team_id,
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
            },
        )

    def current_game(self, canonical: Mapping[str, Any]) -> Dict[str, Any]:
        day = _date(canonical.get("slateDateEt"))
        game_pk = str(canonical.get("officialGamePk") or "")
        if day is None:
            raise RuntimeError("target slate date missing")
        matches = [
            game
            for game in _schedule_games(self.schedule(day, day))
            if str(game.get("gamePk") or "") == game_pk
        ]
        if len(matches) != 1:
            raise RuntimeError("official target game identity unavailable or ambiguous")
        raw = matches[0]
        teams = {}
        for side in SIDES:
            source = _dict(_dict(raw.get("teams")).get(side))
            teams[side] = {
                "team": copy.deepcopy(source.get("team") or {}),
                "probablePitcher": copy.deepcopy(source.get("probablePitcher") or {}),
            }
        return {
            "gamePk": raw.get("gamePk"),
            "officialDate": raw.get("officialDate"),
            "gameDate": raw.get("gameDate"),
            "venue": copy.deepcopy(raw.get("venue") or {}),
            "teams": teams,
            "targetOutcomeFieldsDiscarded": True,
        }

    def _venue_coordinates(self, game: Mapping[str, Any]) -> Tuple[float, float]:
        direct = _coordinates(game)
        if direct:
            return direct
        venue_id = _venue_id(game)
        payload = (
            self._get(
                f"{MLB_API}/venues/{venue_id}",
                {"hydrate": "location,fieldInfo"},
            )
            if venue_id
            else {}
        )
        venues = payload.get("venues") or []
        value = _coordinates({"venue": venues[0]}) if len(venues) == 1 else None
        if value is None:
            raise RuntimeError("official target venue coordinates missing")
        return value

    def _history(
        self, canonical: Mapping[str, Any], days: int
    ) -> List[Dict[str, Any]]:
        target = _date(canonical.get("slateDateEt"))
        return (
            _schedule_games(
                self.schedule(
                    target - timedelta(days=days), target - timedelta(days=1)
                )
            )
            if target
            else []
        )

    def _team_history(
        self, games: Sequence[Mapping[str, Any]], team_id: str
    ) -> List[Dict[str, Any]]:
        output = []
        for game in games:
            side = _game_side(game, team_id)
            opponent = "home" if side == "away" else "away"
            own = _score(game, side) if side else None
            other = _score(game, opponent) if side else None
            day = _official_day(game)
            if side and _final(game) and own is not None and other is not None and day:
                output.append(
                    {
                        "game": game,
                        "gamePk": str(game.get("gamePk") or ""),
                        "date": day.isoformat(),
                        "side": side,
                        "won": int(own > other),
                        "runsFor": own,
                        "runsAgainst": other,
                    }
                )
        return sorted(output, key=lambda row: (row["date"], row["gamePk"]))

    def starter_stats(self, person_id: str, target: date) -> Dict[str, Any]:
        rows = _filtered_log(self.game_log(person_id, "pitching", target.year), target)
        starts = [
            row for row in rows if (_number(row.get("gamesStarted")) or 0) > 0
        ] or rows
        if not starts:
            return {}
        season = _aggregate_pitching(starts)
        recent3 = _aggregate_pitching(starts[-3:])
        recent5 = _aggregate_pitching(starts[-5:])
        return {
            "stats": {
                key: season[key] for key in ("era", "fip", "whip", "kMinusBbPct")
            },
            "expectedInnings": round(
                (recent5.get("innings") or 0) / max(1, len(starts[-5:])), 3
            ),
            "recentThreeStarts": {
                **{
                    key: recent3[key]
                    for key in ("era", "fip", "whip", "kMinusBbPct")
                },
                "expectedInnings": round(
                    (recent3.get("innings") or 0) / max(1, len(starts[-3:])), 3
                ),
            },
        }

    def hitter_stats(self, person_id: str, target: date) -> Dict[str, Any]:
        rows = _filtered_log(self.game_log(person_id, "hitting", target.year), target)
        return _aggregate_hitting(rows) if rows else {}

    def projected_pitchers(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        games = self._history(canonical, PROJECTION_HISTORY_DAYS)
        if target is None:
            raise RuntimeError("target starter projection cutoff missing")
        output = {}
        for side in SIDES:
            team_id = _team_id(current, side)
            history = self._team_history(games, team_id or "")[-18:]
            if len(history) < 5:
                raise RuntimeError(f"{side} starter projection history floor not met")
            candidates: Dict[str, Dict[str, Any]] = {}
            for row in history:
                team = _dict(
                    _dict(self.boxscore(row["gamePk"]).get("teams")).get(row["side"])
                )
                pitchers = [str(value) for value in team.get("pitchers") or []]
                if not pitchers:
                    continue
                person_id = pitchers[0]
                player = _dict(_dict(team.get("players")).get(f"ID{person_id}"))
                person = _dict(player.get("person"))
                entry = candidates.setdefault(
                    person_id,
                    {
                        "id": person_id,
                        "name": person.get("fullName"),
                        "starts": 0,
                        "last": date.fromisoformat(row["date"]),
                    },
                )
                entry["starts"] += 1
                entry["last"] = max(entry["last"], date.fromisoformat(row["date"]))
            ranked = []
            for entry in candidates.values():
                days = (target - entry["last"]).days
                if 4 <= days <= 10:
                    ranked.append(
                        (
                            abs(days - 5),
                            -min(entry["starts"], 6),
                            -entry["last"].toordinal(),
                            entry["id"],
                            entry,
                        )
                    )
            if not ranked:
                raise RuntimeError(f"{side} rotation projection unavailable")
            entry = min(ranked)[-1]
            output[side] = {
                "id": entry["id"],
                "name": entry["name"],
                "confirmed": False,
                "projected": True,
                "projectionMethod": "strictly_prior_rotation_rest_cadence",
                "lastStartDate": entry["last"].isoformat(),
                **self.starter_stats(entry["id"], target),
            }
        cutoff = _strict_prior_cutoff(canonical).isoformat()
        return {
            "data": output,
            "meta": {
                "confirmed": False,
                "complete": True,
                "authoritative": True,
                "pointInTimeProjectionVerified": True,
                "targetIdentityMode": "STRICTLY_PRIOR_ROTATION_PROJECTION",
                "source": "MLB Stats API strictly prior rotation projection",
                "asOfUtc": cutoff,
                "updatedAt": cutoff,
                "derivationVersion": VERSION,
            },
            "error": None,
        }

    def projected_lineups(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        games = self._history(canonical, PROJECTION_HISTORY_DAYS)
        if target is None:
            raise RuntimeError("target lineup projection cutoff missing")
        output = {}
        for side in SIDES:
            history = self._team_history(games, _team_id(current, side) or "")
            if not history:
                raise RuntimeError(f"{side} prior lineup history unavailable")
            latest = history[-1]
            team = _dict(
                _dict(self.boxscore(latest["gamePk"]).get("teams")).get(
                    latest["side"]
                )
            )
            players_map = _dict(team.get("players"))
            by_slot: Dict[int, Dict[str, Any]] = {}
            fallback = []
            for person_id in [str(value) for value in team.get("batters") or []]:
                player = _dict(
                    players_map.get(f"ID{person_id}") or players_map.get(person_id)
                )
                person = _dict(player.get("person"))
                try:
                    slot = int(str(player.get("battingOrder") or "")) // 100
                except Exception:
                    slot = 0
                row = {
                    "id": person_id,
                    "name": person.get("fullName"),
                    "projectedFromOfficialGamePk": latest["gamePk"],
                }
                stats = self.hitter_stats(person_id, target)
                if stats.get("ops") is not None:
                    row["ops"] = stats["ops"]
                if 1 <= slot <= 9 and slot not in by_slot:
                    by_slot[slot] = {"slot": slot, **row}
                fallback.append(row)
            selected = [by_slot[index] for index in range(1, 10) if index in by_slot]
            used = {row["id"] for row in selected}
            missing = [index for index in range(1, 10) if index not in by_slot]
            for row in fallback:
                if not missing:
                    break
                if row["id"] not in used:
                    selected.append({"slot": missing.pop(0), **row})
                    used.add(row["id"])
            selected.sort(key=lambda row: row["slot"])
            if len(selected) != 9:
                raise RuntimeError(
                    f"{side} prior lineup projection does not contain nine unique hitters"
                )
            output[side] = {
                "confirmed": False,
                "projected": True,
                "projectionMethod": "most_recent_strictly_prior_official_starting_lineup",
                "players": selected,
            }
        cutoff = _strict_prior_cutoff(canonical).isoformat()
        return {
            "data": output,
            "meta": {
                "confirmed": False,
                "complete": True,
                "authoritative": True,
                "pointInTimeProjectionVerified": True,
                "targetIdentityMode": "STRICTLY_PRIOR_LINEUP_PROJECTION",
                "source": "MLB Stats API strictly prior lineup projection",
                "asOfUtc": cutoff,
                "updatedAt": cutoff,
                "derivationVersion": VERSION,
            },
            "error": None,
        }

    def enrich_pitchers(
        self, canonical: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        result = copy.deepcopy(dict(envelope))
        data = _dict(result.get("data"))
        if target is None:
            raise RuntimeError("target starter cutoff missing")
        for side in SIDES:
            raw = _dict(data.get(side))
            person_id = str(raw.get("id") or raw.get("playerId") or "")
            if not person_id or raw.get("confirmed") is not True:
                raise RuntimeError(f"{side} confirmed stored starter missing")
            enriched = self.starter_stats(person_id, target)
            stats = _dict(raw.get("stats"))
            stats.update(
                {
                    key: value
                    for key, value in _dict(enriched.get("stats")).items()
                    if value is not None
                }
            )
            raw.update(
                {
                    "stats": stats,
                    "expectedInnings": enriched.get("expectedInnings"),
                    "recentThreeStarts": enriched.get("recentThreeStarts")
                    or raw.get("recentThreeStarts")
                    or {},
                }
            )
            data[side] = raw
        result["data"] = data
        result.setdefault("meta", {}).update(
            {
                "targetIdentityMode": "ARCHIVED_CONFIRMED_T_MINUS_45",
                "derivationVersion": VERSION,
            }
        )
        return result

    def enrich_lineups(
        self, canonical: Mapping[str, Any], envelope: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        result = copy.deepcopy(dict(envelope))
        data = _dict(result.get("data"))
        if target is None:
            raise RuntimeError("target lineup cutoff missing")
        for side in SIDES:
            raw = _dict(data.get(side))
            players = []
            for item in raw.get("players") or []:
                player = _dict(item)
                person_id = str(player.get("id") or player.get("playerId") or "")
                if (
                    person_id
                    and player.get("ops") is None
                    and player.get("wrcPlus") is None
                ):
                    stats = self.hitter_stats(person_id, target)
                    if stats.get("ops") is not None:
                        player["ops"] = stats["ops"]
                players.append(player)
            raw["players"] = players
            data[side] = raw
        result["data"] = data
        result.setdefault("meta", {}).update(
            {
                "targetIdentityMode": "ARCHIVED_CONFIRMED_T_MINUS_45",
                "derivationVersion": VERSION,
            }
        )
        return result

    def team_context(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        games = self._history(canonical, RECENT_CONTEXT_DAYS)
        target_coords = self._venue_coordinates(current)
        if target is None:
            raise RuntimeError("target date missing")
        output = {}
        for side in SIDES:
            team_id = _team_id(current, side)
            history = self._team_history(games, team_id or "")
            if len(history) < 5:
                raise RuntimeError(f"{side} prior official team history floor not met")
            recent = history[-10:]
            last = history[-1]
            same_side = [row for row in history if row["side"] == side][-10:]
            previous_coords = self._venue_coordinates(last["game"])
            rest = max(0, (target - date.fromisoformat(last["date"])).days - 1)
            wins = sum(row["won"] for row in history)
            output[side] = {
                "id": team_id,
                "name": _dict(
                    _dict(_dict(current.get("teams")).get(side)).get("team")
                ).get("name"),
                "record": {"wins": wins, "losses": len(history) - wins},
                "recentForm": {
                    "games": len(recent),
                    "winRate": sum(row["won"] for row in recent) / len(recent),
                },
                "homeAwaySplit": {
                    "games": len(same_side),
                    "winRate": (
                        sum(row["won"] for row in same_side) / len(same_side)
                        if same_side
                        else None
                    ),
                },
                "restDays": rest,
                "travel": {
                    "miles": round(_haversine(previous_coords, target_coords), 3)
                },
                "offense": {
                    "runsPerGame10": round(
                        sum(row["runsFor"] for row in recent) / len(recent), 4
                    )
                },
                "defense": {
                    "rating": round(
                        -sum(row["runsAgainst"] for row in recent) / len(recent), 4
                    )
                },
            }
        return output

    def bullpen(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        games = self._history(canonical, 30)
        if target is None:
            raise RuntimeError("target bullpen cutoff missing")
        output = {}
        for side in SIDES:
            history = self._team_history(games, _team_id(current, side) or "")[-10:]
            if len(history) < 5:
                raise RuntimeError(f"{side} prior bullpen history floor not met")
            stats = []
            recent_pitches: Dict[str, int] = defaultdict(int)
            recent_outs = 0
            last_counts: Counter[str] = Counter()
            relievers_seen = set()
            for row in history:
                team = _dict(
                    _dict(self.boxscore(row["gamePk"]).get("teams")).get(row["side"])
                )
                pitchers = [str(value) for value in team.get("pitchers") or []]
                relievers = pitchers[1:]
                players = _dict(team.get("players"))
                game_day = date.fromisoformat(row["date"])
                for person_id in relievers:
                    pitching = _dict(
                        _dict(_dict(players.get(f"ID{person_id}")).get("stats")).get(
                            "pitching"
                        )
                    )
                    if not pitching:
                        continue
                    stats.append(pitching)
                    relievers_seen.add(person_id)
                    days = (target - game_day).days
                    if 0 < days <= 2:
                        recent_pitches[person_id] += int(
                            _number(pitching.get("numberOfPitches")) or 0
                        )
                    if 0 < days <= 3:
                        recent_outs += _innings_to_outs(pitching.get("inningsPitched"))
                if relievers:
                    last_counts[relievers[-1]] += 1
            aggregate = _aggregate_pitching(stats)
            if aggregate["era"] is None:
                raise RuntimeError(f"{side} prior bullpen quality unavailable")
            closer = last_counts.most_common(1)[0][0] if last_counts else None
            available = [
                person for person in relievers_seen if recent_pitches[person] < 50
            ]
            output[side] = {
                "era": aggregate["era"],
                "fip": aggregate["fip"],
                "last3DaysInnings": round(recent_outs / 3, 3),
                "last2DaysPitches": sum(recent_pitches.values()),
                "closerAvailable": bool(
                    closer and recent_pitches[closer] < 40
                ),
                "highLeverageAvailable": len(available) >= 2,
                "expectedInnings": round(
                    (aggregate["innings"] or 0) / len(history), 3
                ),
                "availableRelievers": len(available),
                "historyGameCount": len(history),
            }
        return output

    def injuries(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        output = {}
        if target is None:
            raise RuntimeError("target injury cutoff missing")
        cutoff = target - timedelta(days=1)
        for side in SIDES:
            team_id = _team_id(current, side)
            payload = self.transactions(
                team_id or "",
                target - timedelta(days=TRANSACTION_LOOKBACK_DAYS),
                cutoff,
            )
            output[side] = reconstruct_active_injuries(
                payload.get("transactions") or [], cutoff
            )
        return output

    def park(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        target = _date(canonical.get("slateDateEt"))
        venue_id = _venue_id(current)
        if target is None or not venue_id:
            raise RuntimeError("target park identity missing")
        games = [
            game
            for game in _schedule_games(
                self.schedule(
                    target - timedelta(days=PARK_HISTORY_DAYS),
                    target - timedelta(days=1),
                )
            )
            if _final(game)
        ]

        def totals(rows: Sequence[Mapping[str, Any]]) -> List[float]:
            values = []
            for game in rows:
                home = _score(game, "home")
                away = _score(game, "away")
                if home is not None and away is not None:
                    values.append(home + away)
            return values

        league = totals(games)
        venue = totals([game for game in games if _venue_id(game) == venue_id])
        if len(venue) < 10 or len(league) < 100:
            raise RuntimeError("prior-only park factor history floor not met")
        factor = (sum(venue) / len(venue)) / (sum(league) / len(league))
        return {
            "venueId": venue_id,
            "name": _dict(current.get("venue")).get("name"),
            "runFactor": round(min(max(factor, 0.7), 1.3), 6),
            "priorVenueGameCount": len(venue),
            "priorLeagueGameCount": len(league),
            "historyEndDate": (target - timedelta(days=1)).isoformat(),
        }

    def weather(
        self, canonical: Mapping[str, Any], current: Mapping[str, Any]
    ) -> Dict[str, Any]:
        lat, lon = self._venue_coordinates(current)
        run = latest_conservatively_available_weather_run(
            canonical.get("predictionLockAtUtc")
        )
        payload = self._get(
            OPEN_METEO_SINGLE_RUN,
            {
                "latitude": round(lat, 6),
                "longitude": round(lon, 6),
                "run": run.strftime("%Y-%m-%dT%H:%M"),
                "models": WEATHER_MODEL,
                "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,wind_speed_10m,wind_direction_10m,wind_gusts_10m",
                "timezone": "UTC",
                "forecast_days": 10,
            },
        )
        hourly = _dict(payload.get("hourly"))
        target = _parse_utc(canonical.get("commenceTime"))
        times = [_parse_utc(value) for value in hourly.get("time") or []]
        candidates = [
            (abs((value - target).total_seconds()), index)
            for index, value in enumerate(times)
            if value and target
        ]
        if not candidates:
            raise RuntimeError("archived forecast target time unavailable")
        index = min(candidates)[1]
        row = {"forecastTimeUtc": times[index].isoformat()}
        for name, values in hourly.items():
            if name != "time" and isinstance(values, list) and index < len(values):
                row[name] = values[index]
        row.update(
            {
                "runInitialisedAtUtc": run.isoformat(),
                "conservativeAvailableAtUtc": (
                    run + timedelta(hours=WEATHER_PUBLICATION_LAG_HOURS)
                ).isoformat(),
                "model": WEATHER_MODEL,
                "runFactor": weather_run_factor(row),
                "derivationVersion": VERSION,
            }
        )
        return row

    def build_bundle(
        self,
        canonical: Mapping[str, Any],
        stored_pitchers: Mapping[str, Any],
        stored_lineups: Mapping[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        cutoff = _strict_prior_cutoff(canonical)
        meta = {
            "confirmed": True,
            "complete": True,
            "authoritative": True,
            "asOfUtc": cutoff.isoformat(),
            "updatedAt": cutoff.isoformat(),
            "source": "official strictly prior context",
            "derivationVersion": VERSION,
        }

        def failed(name: str, exc: Exception) -> Dict[str, Any]:
            return {
                "data": None,
                "meta": {**meta, "confirmed": False},
                "error": f"HISTORICAL_CONTEXT_{name.upper()}_{type(exc).__name__}:{str(exc)[:240]}",
            }

        try:
            current = self.current_game(canonical)
        except Exception as exc:
            return {
                name: failed(name, exc)
                for name in (
                    "pitchers",
                    "lineups",
                    "bullpens",
                    "team_context",
                    "injuries",
                    "park",
                    "weather",
                )
            }
        bundle: Dict[str, Dict[str, Any]] = {}
        for name, builder in (
            (
                "pitchers",
                lambda: (
                    self.enrich_pitchers(canonical, stored_pitchers)
                    if _stored_pitchers_ready(stored_pitchers)
                    else self.projected_pitchers(canonical, current)
                ),
            ),
            (
                "lineups",
                lambda: (
                    self.enrich_lineups(canonical, stored_lineups)
                    if _stored_lineups_ready(stored_lineups)
                    else self.projected_lineups(canonical, current)
                ),
            ),
        ):
            try:
                bundle[name] = builder()
                bundle[name].setdefault("error", None)
            except Exception as exc:
                bundle[name] = failed(name, exc)
        for name, builder in (
            ("bullpens", lambda: self.bullpen(canonical, current)),
            ("team_context", lambda: self.team_context(canonical, current)),
            ("injuries", lambda: self.injuries(canonical, current)),
            ("park", lambda: self.park(canonical, current)),
        ):
            try:
                bundle[name] = {
                    "data": builder(),
                    "meta": copy.deepcopy(meta),
                    "error": None,
                }
            except Exception as exc:
                bundle[name] = failed(name, exc)
        try:
            weather = self.weather(canonical, current)
            bundle["weather"] = {
                "data": weather,
                "meta": {
                    **meta,
                    "asOfUtc": weather["conservativeAvailableAtUtc"],
                    "updatedAt": weather["conservativeAvailableAtUtc"],
                    "source": "Open-Meteo Single Runs archived forecast",
                    "modelRunInitialisedAtUtc": weather["runInitialisedAtUtc"],
                },
                "error": None,
            }
        except Exception as exc:
            bundle["weather"] = failed("weather", exc)
        return bundle


def install_best_effort_stored_discovery(client_class: Any) -> Any:
    if getattr(
        client_class, "_INQSI_MLB_BEST_EFFORT_STORED_DISCOVERY_INSTALLED", False
    ):
        return client_class
    original = client_class.list_mlb_matches

    def wrapped(self: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        try:
            return original(self, *args, **kwargs)
        except Exception as exc:
            global STORED_DISCOVERY_ERROR_COUNT
            STORED_DISCOVERY_ERROR_COUNT += 1
            return {
                "data": [],
                "meta": {
                    "source": "bigballsdata",
                    "degradedToOfficialIdentity": True,
                },
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }

    client_class.list_mlb_matches = wrapped
    client_class._INQSI_MLB_BEST_EFFORT_STORED_DISCOVERY_INSTALLED = True
    return client_class


def install_official_identity_fallback(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_OFFICIAL_IDENTITY_FALLBACK_INSTALLED", False):
        return module
    original = module.crosswalk_provider_rows

    def wrapped(
        provider_rows: Sequence[Mapping[str, Any]],
        canonical_games: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        result = original(provider_rows, canonical_games, **kwargs)
        accepted = {
            str(key): copy.deepcopy(dict(value))
            for key, value in (result.get("accepted") or {}).items()
        }
        synthetic = 0
        for canonical in canonical_games:
            game_pk = str(canonical.get("officialGamePk") or "")
            commence = _parse_utc(canonical.get("commenceTime"))
            if not game_pk or game_pk in accepted or commence is None:
                continue
            provider_id = f"official-mlb:{game_pk}"
            provider_row = {
                "match_id": provider_id,
                "official_game_pk": game_pk,
                "kickoff_utc": commence.isoformat(),
                "home": {"name": canonical.get("homeTeam")},
                "away": {"name": canonical.get("awayTeam")},
                "source": VERSION,
            }
            accepted[game_pk] = {
                "providerMatchId": provider_id,
                "providerKickoffUtc": commence.isoformat(),
                "crosswalkMethod": "DIRECT_CANONICAL_OFFICIAL_GAME_ID_CONTEXT_FALLBACK",
                "providerRowFingerprint": _sha(provider_row),
                "providerRow": provider_row,
                "syntheticOfficialIdentity": True,
            }
            synthetic += 1
        global SYNTHETIC_OFFICIAL_IDENTITY_COUNT
        SYNTHETIC_OFFICIAL_IDENTITY_COUNT += synthetic
        result.update(
            {
                "accepted": accepted,
                "acceptedCount": len(accepted),
                "syntheticOfficialIdentityCount": synthetic,
                "completeCanonicalCoverage": len(accepted) == len(canonical_games),
                "selectionUsedOutcomes": False,
            }
        )
        return result

    module.crosswalk_provider_rows = wrapped
    module._INQSI_MLB_OFFICIAL_IDENTITY_FALLBACK_INSTALLED = True
    return module


def install_crosswalk_registry(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_CONTEXT_CROSSWALK_REGISTRY_INSTALLED", False):
        return module
    original = module.crosswalk_provider_rows

    def wrapped(
        provider_rows: Sequence[Mapping[str, Any]],
        canonical_games: Sequence[Mapping[str, Any]],
        **kwargs: Any,
    ) -> Dict[str, Any]:
        result = original(provider_rows, canonical_games, **kwargs)
        canonical = {
            str(row.get("officialGamePk") or ""): dict(row)
            for row in canonical_games
        }
        for game_pk, row in (result.get("accepted") or {}).items():
            provider_id = str(_dict(row).get("providerMatchId") or "")
            if provider_id and str(game_pk) in canonical:
                _CANONICAL_BY_PROVIDER_ID[provider_id] = canonical[str(game_pk)]
        return result

    module.crosswalk_provider_rows = wrapped
    module._INQSI_MLB_CONTEXT_CROSSWALK_REGISTRY_INSTALLED = True
    return module


def install_resource_provider(
    client_class: Any,
    *,
    source_factory: Callable[[], OfficialContextSource] = OfficialContextSource,
) -> Any:
    if getattr(client_class, "_INQSI_MLB_POINT_IN_TIME_CONTEXT_INSTALLED", False):
        return client_class
    original = client_class.get_mlb_match_resource

    def resource(
        self: Any,
        match_id: str,
        name: str,
        *,
        game_date: Optional[str] = None,
        as_of: Optional[str] = None,
    ) -> Dict[str, Any]:
        canonical = _CANONICAL_BY_PROVIDER_ID.get(str(match_id))
        normalized = str(name).lower()
        if canonical is None:
            return {
                "data": None,
                "meta": {"source": VERSION},
                "error": "HISTORICAL_CONTEXT_CANONICAL_IDENTITY_UNAVAILABLE",
            }
        cache = getattr(self, "_inqsi_historical_context_bundles", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(self, "_inqsi_historical_context_bundles", cache)
        identity = (
            str(canonical.get("officialGamePk") or ""),
            str(canonical.get("predictionLockAtUtc") or ""),
        )
        if identity not in cache:

            def unavailable(resource_name: str, error: str) -> Dict[str, Any]:
                return {
                    "data": None,
                    "meta": {
                        "source": "bigballsdata_stored_pregame",
                        "confirmed": False,
                        "requestedAsOfUtc": as_of,
                    },
                    "error": f"{resource_name}:{error}",
                }

            if str(match_id).startswith("official-mlb:"):
                pitchers = unavailable(
                    "pitchers", "provider_archive_identity_unavailable"
                )
                lineups = unavailable(
                    "lineups", "provider_archive_identity_unavailable"
                )
            else:
                try:
                    pitchers = original(
                        self,
                        match_id,
                        "pitchers",
                        game_date=game_date,
                        as_of=as_of,
                    )
                except Exception as exc:
                    pitchers = unavailable(
                        "pitchers", f"{type(exc).__name__}:{str(exc)[:160]}"
                    )
                try:
                    lineups = original(
                        self,
                        match_id,
                        "lineups",
                        game_date=game_date,
                        as_of=as_of,
                    )
                except Exception as exc:
                    lineups = unavailable(
                        "lineups", f"{type(exc).__name__}:{str(exc)[:160]}"
                    )
            source = getattr(self, "_inqsi_official_context_source", None)
            if source is None:
                source = source_factory()
                setattr(self, "_inqsi_official_context_source", source)
            cache[identity] = source.build_bundle(canonical, pitchers, lineups)
        return copy.deepcopy(
            cache[identity].get(normalized)
            or {
                "data": None,
                "meta": {"source": VERSION},
                "error": "HISTORICAL_CONTEXT_RESOURCE_UNSUPPORTED",
            }
        )

    client_class.get_mlb_match_resource = resource
    client_class._INQSI_MLB_POINT_IN_TIME_CONTEXT_INSTALLED = True
    return client_class


def install_strict_optional_point_in_time_gate(module: Any) -> Any:
    if getattr(module, "_INQSI_MLB_STRICT_OPTIONAL_CONTEXT_GATE_INSTALLED", False):
        return module
    original = module.point_in_time_errors

    def wrapped(
        resources: Mapping[str, Any], lock_at: Any
    ) -> List[str]:
        errors = list(original(resources, lock_at))
        lock = module._parse_time(lock_at)
        if lock is None:
            return sorted(set(errors + ["prediction_lock_invalid"]))
        for name in module.OPTIONAL_RESOURCES:
            envelope = resources.get(name)
            if not isinstance(envelope, Mapping) or envelope.get("error") is not None:
                errors.append(f"{name}_resource_unavailable")
                continue
            effective = module._effective_at(envelope)
            if effective is None:
                errors.append(f"{name}_source_effective_time_missing")
            elif effective > lock + timedelta(seconds=1):
                errors.append(f"{name}_source_effective_time_after_lock")
        return sorted(set(errors))

    module.point_in_time_errors = wrapped
    module._INQSI_MLB_STRICT_OPTIONAL_CONTEXT_GATE_INSTALLED = True
    return module
