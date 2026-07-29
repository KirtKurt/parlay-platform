"""Compatibility shim with bounded point-in-time weather fallbacks for MLB V8.

The canonical implementation lives in ``hello_world``. This shim is resolved first
by the repository's existing PYTHONPATH and patches only the weather retrieval path:

1. retry a bounded set of conservatively available Open-Meteo single runs using a
   minimal variable set and ``forecast_hours``;
2. if the archive remains unavailable, read the official MLB game feed at the exact
   immutable prediction-lock timecode;
3. preserve the existing hard weather coverage and point-in-time gates.

No model-selection, promotion, production, prediction, or wagering authority changes.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import timedelta
from typing import Any, Dict, Mapping

from hello_world import mlb_v8_historical_point_in_time_context_v1 as _base

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)

WEATHER_FALLBACK_VERSION = "MLB-V8-HISTORICAL-WEATHER-FALLBACK-v1"
MLB_TIMECODE_FEED = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
SINGLE_RUN_MODELS = ("ecmwf_ifs", "ecmwf_ifs025", "ncep_gfs013")
SINGLE_RUN_BACKOFF_HOURS = (0, 6, 12, 18)
SINGLE_RUN_VARIABLES = (
    "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,"
    "wind_direction_10m,wind_gusts_10m"
)


def _nearest_hour(payload: Mapping[str, Any], target: Any) -> Dict[str, Any]:
    hourly = _base._dict(payload.get("hourly"))
    target_time = _base._parse_utc(target)
    times = [_base._parse_utc(value) for value in hourly.get("time") or []]
    candidates = [
        (abs((value - target_time).total_seconds()), index)
        for index, value in enumerate(times)
        if value is not None and target_time is not None
    ]
    if not candidates:
        raise RuntimeError("archived_forecast_target_time_unavailable")
    index = min(candidates)[1]
    row: Dict[str, Any] = {"forecastTimeUtc": times[index].isoformat()}
    for name, values in hourly.items():
        if name != "time" and isinstance(values, list) and index < len(values):
            row[name] = values[index]
    if _base._number(row.get("temperature_2m")) is None:
        raise RuntimeError("archived_forecast_temperature_missing")
    return row


def _single_run_weather(
    source: Any,
    canonical: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Dict[str, Any]:
    lat, lon = source._venue_coordinates(current)
    lock = _base._parse_utc(canonical.get("predictionLockAtUtc"))
    target = _base._parse_utc(canonical.get("commenceTime"))
    if lock is None or target is None:
        raise RuntimeError("weather_lock_or_target_time_invalid")
    latest = _base.latest_conservatively_available_weather_run(lock)
    errors = []
    for model in SINGLE_RUN_MODELS:
        for hours_back in SINGLE_RUN_BACKOFF_HOURS:
            run = latest - timedelta(hours=hours_back)
            available_at = run + timedelta(hours=_base.WEATHER_PUBLICATION_LAG_HOURS)
            if available_at > lock:
                continue
            lead_hours = max(0, int(math.ceil((target - run).total_seconds() / 3600.0)))
            forecast_hours = min(360, max(12, lead_hours + 6))
            try:
                payload = source._get(
                    _base.OPEN_METEO_SINGLE_RUN,
                    {
                        "latitude": round(lat, 6),
                        "longitude": round(lon, 6),
                        "run": run.strftime("%Y-%m-%dT%H:%M"),
                        "models": model,
                        "hourly": SINGLE_RUN_VARIABLES,
                        "timezone": "UTC",
                        "forecast_hours": forecast_hours,
                    },
                )
                row = _nearest_hour(payload, target)
                row.update(
                    {
                        "runInitialisedAtUtc": run.isoformat(),
                        "conservativeAvailableAtUtc": available_at.isoformat(),
                        "model": model,
                        "runFactor": _base.weather_run_factor(row),
                        "derivationVersion": WEATHER_FALLBACK_VERSION,
                        "source": "Open-Meteo Single Runs archived forecast fallback",
                        "fallbackAttemptHoursBack": hours_back,
                        "forecastHoursRequested": forecast_hours,
                    }
                )
                return row
            except Exception as exc:
                errors.append(
                    f"{model}@{run.isoformat()}:{type(exc).__name__}:{str(exc)[:120]}"
                )
    raise RuntimeError("single_run_weather_exhausted:" + "|".join(errors[-12:]))


def _condition_precipitation_probability(condition: str) -> float:
    text = condition.lower()
    if any(token in text for token in ("thunder", "storm", "heavy rain")):
        return 90.0
    if any(token in text for token in ("rain", "shower", "drizzle")):
        return 70.0
    if any(token in text for token in ("snow", "sleet", "ice")):
        return 80.0
    return 0.0


def _official_timecode_weather(
    source: Any,
    canonical: Mapping[str, Any],
) -> Dict[str, Any]:
    game_pk = str(canonical.get("officialGamePk") or "").strip()
    lock = _base._parse_utc(canonical.get("predictionLockAtUtc"))
    target = _base._parse_utc(canonical.get("commenceTime"))
    if not game_pk or lock is None:
        raise RuntimeError("official_weather_game_or_lock_identity_invalid")
    payload = source._get(
        MLB_TIMECODE_FEED.format(game_pk=game_pk),
        {"timecode": lock.strftime("%Y%m%d_%H%M%S")},
    )
    game_data = _base._dict(payload.get("gameData"))
    weather = _base._dict(game_data.get("weather"))
    venue = _base._dict(game_data.get("venue"))
    field = _base._dict(venue.get("fieldInfo"))
    condition = str(
        weather.get("condition")
        or weather.get("weatherCondition")
        or _base._dict(game_data.get("gameInfo")).get("weather")
        or ""
    )
    roof = str(field.get("roofType") or venue.get("roofType") or "")
    indoor = any(
        token in f"{condition} {roof}".lower()
        for token in ("dome", "indoor", "closed roof")
    )
    temperature = _base._number(weather.get("temp") or weather.get("temperature"))
    humidity = _base._number(weather.get("humidity"))
    wind_text = str(weather.get("wind") or weather.get("windDescription") or "")
    wind_match = re.search(r"(\d+(?:\.\d+)?)", wind_text)
    wind_speed = float(wind_match.group(1)) if wind_match else 0.0
    if not indoor and temperature is None:
        raise RuntimeError("official_timecode_weather_temperature_missing")
    factor_row = {
        "temperature_2m": 20.0
        if indoor
        else (float(temperature) - 32.0) * 5.0 / 9.0,
        "relative_humidity_2m": humidity if humidity is not None else 50.0,
        "wind_speed_10m": 0.0 if indoor else wind_speed,
        "precipitation_probability": 0.0
        if indoor
        else _condition_precipitation_probability(condition),
    }
    run_factor = 1.0 if indoor else _base.weather_run_factor(factor_row)
    return {
        **factor_row,
        "forecastTimeUtc": (target or lock).isoformat(),
        "runInitialisedAtUtc": lock.isoformat(),
        "conservativeAvailableAtUtc": lock.isoformat(),
        "model": "mlb_statsapi_timecode_pregame_weather",
        "runFactor": run_factor,
        "condition": condition or None,
        "windDescription": wind_text or None,
        "roofType": roof or None,
        "indoor": indoor,
        "source": "MLB StatsAPI exact-lock timecode pregame weather",
        "derivationVersion": WEATHER_FALLBACK_VERSION,
        "timecode": lock.strftime("%Y%m%d_%H%M%S"),
        "targetOutcomeUsed": False,
        "sameDayResultsUsed": False,
    }


def _weather_with_fallbacks(
    self: Any,
    canonical: Mapping[str, Any],
    current: Mapping[str, Any],
) -> Dict[str, Any]:
    errors = []
    try:
        return _single_run_weather(self, canonical, current)
    except Exception as exc:
        errors.append(f"single_run:{type(exc).__name__}:{str(exc)[:500]}")
    try:
        value = _official_timecode_weather(self, canonical)
        value["priorFallbackErrors"] = errors
        return value
    except Exception as exc:
        errors.append(f"official_timecode:{type(exc).__name__}:{str(exc)[:500]}")
    raise RuntimeError("point_in_time_weather_sources_exhausted:" + "|".join(errors))


_ORIGINAL_BUILD_BUNDLE = _base.OfficialContextSource.build_bundle


def _build_bundle_with_weather_source(
    self: Any, *args: Any, **kwargs: Any
) -> Dict[str, Any]:
    bundle = _ORIGINAL_BUILD_BUNDLE(self, *args, **kwargs)
    envelope = bundle.get("weather") if isinstance(bundle, Mapping) else None
    data = envelope.get("data") if isinstance(envelope, Mapping) else None
    meta = envelope.get("meta") if isinstance(envelope, Mapping) else None
    if isinstance(data, Mapping) and isinstance(meta, dict):
        meta["source"] = data.get("source") or meta.get("source")
        meta["derivationVersion"] = (
            data.get("derivationVersion") or WEATHER_FALLBACK_VERSION
        )
        meta["modelRunInitialisedAtUtc"] = data.get("runInitialisedAtUtc")
        meta["pointInTimeWeatherFallback"] = True
    return copy.deepcopy(bundle)


_base.OfficialContextSource.weather = _weather_with_fallbacks
_base.OfficialContextSource.build_bundle = _build_bundle_with_weather_source
_base.WEATHER_FALLBACK_VERSION = WEATHER_FALLBACK_VERSION
OfficialContextSource = _base.OfficialContextSource
