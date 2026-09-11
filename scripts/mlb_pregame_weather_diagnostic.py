#!/usr/bin/env python3
"""Read-only MLB pregame weather source diagnostic.

This probe proves only that current forecast observations can be bound to an
exact official MLB venue/game before T-45. It never infers roof open/closed
status, never completes Fundamentals V2 weather/roof eligibility, and never
changes predictions, locks, models, or wagering authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

VERSION = "MLB-PREGAME-WEATHER-DIAGNOSTIC-v1-receipt-time"
REPORT_TYPE = "MLB_PREGAME_WEATHER_READ_ONLY_DIAGNOSTIC"
ET = ZoneInfo("America/New_York")
SCHEDULE_URL = "https://statsapi.mlb.com/api/v1/schedule"
VENUE_URL = "https://statsapi.mlb.com/api/v1/venues/{venue_id}"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HOURLY_VARIABLES = (
    "temperature_2m",
    "precipitation_probability",
    "wind_speed_10m",
    "wind_direction_10m",
)


def _http_json(url: str, timeout: int = 8) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "inqsi-mlb-weather-diagnostic/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("provider response must be an object")
    return payload


def _parse_dt(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _positive_id(value: Any) -> Optional[int]:
    number = _finite(value)
    return int(number) if number is not None and number > 0 and number.is_integer() else None


def _payload_fingerprint(payload: Mapping[str, Any]) -> str:
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(material.encode()).hexdigest()


def _venue_identity(game: Mapping[str, Any]) -> tuple[Optional[int], Optional[str]]:
    venue = game.get("venue") if isinstance(game.get("venue"), Mapping) else {}
    return _positive_id(venue.get("id")), str(venue.get("name") or "") or None


def _venue_coordinates(payload: Mapping[str, Any], expected_id: int) -> tuple[float, float, str]:
    venues = payload.get("venues")
    if not isinstance(venues, list) or len(venues) != 1 or not isinstance(venues[0], Mapping):
        raise ValueError("venue_response_not_unique")
    venue = venues[0]
    if _positive_id(venue.get("id")) != expected_id:
        raise ValueError("venue_identity_mismatch")
    location = venue.get("location") if isinstance(venue.get("location"), Mapping) else {}
    coords = location.get("defaultCoordinates") if isinstance(location.get("defaultCoordinates"), Mapping) else {}
    lat, lon = _finite(coords.get("latitude")), _finite(coords.get("longitude"))
    if lat is None or lon is None or not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError("venue_coordinates_missing_or_invalid")
    return lat, lon, str(venue.get("name") or "")


def _weather_row(payload: Mapping[str, Any], target: datetime) -> dict[str, Any]:
    hourly = payload.get("hourly") if isinstance(payload.get("hourly"), Mapping) else {}
    raw_times = hourly.get("time")
    if not isinstance(raw_times, list) or not raw_times:
        raise ValueError("weather_hourly_times_missing")
    times = [_parse_dt(value) for value in raw_times]
    candidates = [(abs((value-target).total_seconds()), index, value) for index, value in enumerate(times) if value]
    if not candidates:
        raise ValueError("weather_hourly_times_invalid")
    distance, index, observed_hour = min(candidates)
    if distance > 3600:
        raise ValueError("weather_hour_not_near_game_start")
    output = {"forecastTimeUtc": observed_hour.isoformat().replace("+00:00", "Z")}
    for name in HOURLY_VARIABLES:
        values = hourly.get(name)
        output[name] = values[index] if isinstance(values, list) and index < len(values) else None
    temp = _finite(output["temperature_2m"])
    precip = _finite(output["precipitation_probability"])
    wind = _finite(output["wind_speed_10m"])
    direction = _finite(output["wind_direction_10m"])
    if temp is None or wind is None:
        raise ValueError("required_weather_values_missing")
    if precip is not None and not 0 <= precip <= 100:
        raise ValueError("precipitation_probability_invalid")
    if wind < 0 or (direction is not None and not 0 <= direction <= 360):
        raise ValueError("wind_values_invalid")
    return {
        "forecastTimeUtc": output["forecastTimeUtc"],
        "temperatureF": temp,
        "precipitationRiskPct": precip,
        "windSpeedMph": wind,
        "windDirectionDegrees": direction,
    }


def diagnose_game(
    game: Mapping[str, Any], *, clock: Callable[[], datetime], fetch_json: Callable[[str, int], dict[str, Any]]
) -> dict[str, Any]:
    start = _parse_dt(game.get("gameDate"))
    game_pk = _positive_id(game.get("gamePk"))
    status = str((game.get("status") or {}).get("abstractGameState") or "") if isinstance(game.get("status"), Mapping) else ""
    venue_id, schedule_venue_name = _venue_identity(game)
    base = {
        "gamePk": game_pk,
        "commenceTimeUtc": start.isoformat().replace("+00:00", "Z") if start else None,
        "scheduleStatus": status,
        "venueId": venue_id,
        "venueName": schedule_venue_name,
        "weatherObservedAtUtc": None,
        "preT45": False,
        "sourceValid": False,
        "errors": [],
        "weather": None,
        "roofStatus": None,
        "roofStatusClaimed": False,
        "canCompleteWeatherRoofGroup": False,
        "canChangeProductionScoring": False,
    }
    if not game_pk or start is None or status != "Preview" or not venue_id:
        base["errors"] = ["official_game_or_venue_identity_incomplete"]
        return base
    if clock().astimezone(timezone.utc) >= start - timedelta(minutes=45):
        base["errors"] = ["game_not_pre_t45_at_probe_start"]
        return base
    venue_url = VENUE_URL.format(venue_id=venue_id) + "?" + urllib.parse.urlencode({"hydrate": "location,fieldInfo"})
    try:
        venue_payload = fetch_json(venue_url, 8)
        lat, lon, official_name = _venue_coordinates(venue_payload, venue_id)
        if schedule_venue_name and official_name and schedule_venue_name != official_name:
            raise ValueError("venue_name_mismatch")
        params = {
            "latitude": round(lat, 6),
            "longitude": round(lon, 6),
            "hourly": ",".join(HOURLY_VARIABLES),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
            "start_date": start.date().isoformat(),
            "end_date": start.date().isoformat(),
        }
        weather_url = OPEN_METEO_URL + "?" + urllib.parse.urlencode(params)
        weather_payload = fetch_json(weather_url, 8)
        received = clock().astimezone(timezone.utc)
        base["weatherObservedAtUtc"] = received.isoformat().replace("+00:00", "Z")
        base["preT45"] = received < start - timedelta(minutes=45)
        if not base["preT45"]:
            base["errors"] = ["weather_response_received_at_or_after_t45"]
            return base
        weather = _weather_row(weather_payload, start)
        base.update({
            "sourceValid": True,
            "errors": [],
            "venueName": official_name or schedule_venue_name,
            "weather": weather,
            "sourceProvenance": {
                "provider": "Open-Meteo forecast + MLB Stats API venue identity",
                "retrievedAtUtc": base["weatherObservedAtUtc"],
                "sourceEffectiveAtUtc": weather["forecastTimeUtc"],
                "venueEndpoint": venue_url,
                "weatherEndpoint": weather_url,
                "venuePayloadFingerprint": _payload_fingerprint(venue_payload),
                "weatherPayloadFingerprint": _payload_fingerprint(weather_payload),
            },
        })
        return base
    except Exception as exc:
        base["weatherObservedAtUtc"] = clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        base["errors"] = [f"weather_source_failed:{type(exc).__name__}:{exc}"]
        return base


def build_report(*, clock: Callable[[], datetime] | None = None, fetch_json=_http_json) -> dict[str, Any]:
    clock = clock or (lambda: datetime.now(timezone.utc))
    created = clock().astimezone(timezone.utc)
    slate = created.astimezone(ET).date().isoformat()
    schedule_url = SCHEDULE_URL + "?" + urllib.parse.urlencode({"sportId": 1, "date": slate, "hydrate": "venue"})
    schedule = fetch_json(schedule_url, 8)
    games = [game for day in schedule.get("dates") or [] if isinstance(day, Mapping)
             for game in day.get("games") or [] if isinstance(game, Mapping)
             and str((game.get("status") or {}).get("abstractGameState") or "") == "Preview"]
    rows = [diagnose_game(game, clock=clock, fetch_json=fetch_json) for game in games]
    eligible = [row for row in rows if row.get("errors") != ["game_not_pre_t45_at_probe_start"]]
    return {
        "ok": True,
        "version": VERSION,
        "reportType": REPORT_TYPE,
        "createdAtUtc": created.isoformat().replace("+00:00", "Z"),
        "slateDateEt": slate,
        "readOnly": True,
        "previewGameCount": len(rows),
        "preT45ProbeGameCount": sum(row.get("preT45") is True for row in rows),
        "sourceValidGameCount": sum(row.get("sourceValid") is True for row in rows),
        "roofStatusClaimCount": 0,
        "weatherRoofCompletenessChanged": False,
        "productionAuthorityChanged": False,
        "automaticWagerAllowed": False,
        "recommendation": "Capture source-valid temperature/wind/precipitation prospectively; keep roof status unknown until independently sourced.",
        "games": rows,
        "sourceOfTruth": {"scheduleEndpoint": schedule_url, "venueEndpointTemplate": VENUE_URL, "weatherProvider": OPEN_METEO_URL},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="runtime_reports/mlb_pregame_weather_diagnostic_latest.json")
    args = parser.parse_args(argv)
    report = build_report()
    path = Path(args.output);path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("previewGameCount","preT45ProbeGameCount","sourceValidGameCount","roofStatusClaimCount")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
