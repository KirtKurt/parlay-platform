"""Script-path weather compatibility for the V8 historical context runtime.

Scheduled controller commands execute files from ``scripts`` while their PYTHONPATH
places ``hello_world`` before the repository root. Without this shim, those commands
bypass the repository's point-in-time weather compatibility layer and call the base
Single Runs request that asks deterministic ECMWF for an ensemble-only precipitation
probability field.

Load the canonical repository compatibility module under an unambiguous alias, retain
its exact-run/previous-run/official-timecode fallbacks, and add a bounded deterministic
precipitation-amount penalty without fabricating a probability.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path
from typing import Any, Mapping


RUNTIME_WEATHER_PATCH_VERSION = (
    "MLB-V8-HISTORICAL-WEATHER-RUNTIME-v3-deterministic-precipitation"
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_COMPATIBILITY_PATH = (
    _REPOSITORY_ROOT / "mlb_v8_historical_point_in_time_context_v1.py"
)
_SPEC = importlib.util.spec_from_file_location(
    "_inqsi_mlb_v8_weather_compatibility", _COMPATIBILITY_PATH
)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("mlb_v8_weather_compatibility_loader_unavailable")

_INSERTED_ROOT = str(_REPOSITORY_ROOT) not in sys.path
if _INSERTED_ROOT:
    sys.path.insert(0, str(_REPOSITORY_ROOT))
try:
    _compatibility = importlib.util.module_from_spec(_SPEC)
    _SPEC.loader.exec_module(_compatibility)
finally:
    if _INSERTED_ROOT:
        try:
            sys.path.remove(str(_REPOSITORY_ROOT))
        except ValueError:
            pass

_base = _compatibility._base


def _precipitation_penalty(hour: Mapping[str, Any]) -> float:
    """Return a bounded run-factor penalty from the available forecast quantity.

    Probability remains authoritative when a source genuinely provides it. Exact
    deterministic ECMWF runs provide precipitation amount instead; up to 10 mm/hour
    maps linearly to the same 1.5 percentage-point maximum penalty used by the legacy
    0-100% probability feature.
    """

    probability = _base._number(hour.get("precipitation_probability"))
    if probability is not None:
        return min(max(probability, 0.0), 100.0) * 0.00015
    amount_mm = _base._number(hour.get("precipitation")) or 0.0
    return min(max(amount_mm, 0.0), 10.0) * 0.0015


def weather_run_factor(hour: Mapping[str, Any]) -> float:
    celsius = _base._number(hour.get("temperature_2m")) or 20.0
    humidity = _base._number(hour.get("relative_humidity_2m")) or 50.0
    wind_mph = (_base._number(hour.get("wind_speed_10m")) or 0.0) * 0.621371
    value = (
        1
        + (celsius * 9 / 5 + 32 - 70) * 0.002
        + (humidity - 50) * 0.00035
    )
    value += min(max(wind_mph, 0.0), 30.0) * 0.0006
    value -= _precipitation_penalty(hour)
    return round(min(max(value, 0.85), 1.15), 6)


_base.weather_run_factor = weather_run_factor
_compatibility.weather_run_factor = weather_run_factor
_base.RUNTIME_WEATHER_PATCH_VERSION = RUNTIME_WEATHER_PATCH_VERSION
_compatibility.RUNTIME_WEATHER_PATCH_VERSION = RUNTIME_WEATHER_PATCH_VERSION

for _name in dir(_compatibility):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_compatibility, _name)

globals()["RUNTIME_WEATHER_PATCH_VERSION"] = RUNTIME_WEATHER_PATCH_VERSION
globals()["weather_run_factor"] = weather_run_factor
globals()["_precipitation_penalty"] = _precipitation_penalty
