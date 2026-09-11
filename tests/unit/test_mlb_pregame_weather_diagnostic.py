from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "mlb_pregame_weather_diagnostic",
    ROOT / "scripts" / "mlb_pregame_weather_diagnostic.py",
)
assert SPEC and SPEC.loader
SUBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SUBJECT)

NOW = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
START = datetime(2026, 9, 11, 22, 40, tzinfo=timezone.utc)


def _game():
    return {
        "gamePk": 824227,
        "gameDate": START.isoformat().replace("+00:00", "Z"),
        "status": {"abstractGameState": "Preview"},
        "venue": {"id": 2394, "name": "Comerica Park"},
    }


def _venue():
    return {
        "venues": [{
            "id": 2394,
            "name": "Comerica Park",
            "location": {"defaultCoordinates": {"latitude": 42.339, "longitude": -83.0485}},
        }]
    }


def _weather():
    return {
        "hourly": {
            "time": ["2026-09-11T22:00", "2026-09-11T23:00"],
            "temperature_2m": [74.0, 72.0],
            "precipitation_probability": [20, 25],
            "wind_speed_10m": [9.0, 8.0],
            "wind_direction_10m": [225, 230],
        }
    }


def _provider(*, game=None, venue=None, weather=None, state=None):
    schedule_game = game or _game()
    venue_payload = venue or _venue()
    weather_payload = weather or _weather()
    def fetch(url, timeout):
        assert timeout == 8
        if "/schedule?" in url:
            return {"dates": [{"date": "2026-09-11", "games": [schedule_game]}]}
        if "/venues/2394" in url:
            return venue_payload
        assert "open-meteo.com/v1/forecast" in url
        if state is not None and "weather_receipt" in state:
            state["now"] = state["weather_receipt"]
        return weather_payload
    return fetch


def test_valid_weather_is_passive_source_evidence_only():
    report = SUBJECT.build_report(clock=lambda: NOW, fetch_json=_provider())
    assert report["previewGameCount"] == 1
    assert report["sourceValidGameCount"] == 1
    assert report["roofStatusClaimCount"] == 0
    assert report["weatherRoofCompletenessChanged"] is False
    assert report["productionAuthorityChanged"] is False
    row = report["games"][0]
    assert row["sourceValid"] is True and row["preT45"] is True
    assert row["weather"]["temperatureF"] == 72.0
    assert row["weather"]["windSpeedMph"] == 8.0
    assert row["roofStatus"] is None and row["canCompleteWeatherRoofGroup"] is False


def test_exact_official_venue_identity_is_required():
    venue = _venue(); venue["venues"][0]["id"] = 999
    report = SUBJECT.build_report(clock=lambda: NOW, fetch_json=_provider(venue=venue))
    row = report["games"][0]
    assert row["sourceValid"] is False
    assert any("venue_identity_mismatch" in error for error in row["errors"])
    assert report["roofStatusClaimCount"] == 0


def test_slow_weather_response_crossing_t45_is_not_backdated():
    state = {"now": START - timedelta(minutes=46), "weather_receipt": START - timedelta(minutes=44)}
    report = SUBJECT.build_report(clock=lambda: state["now"], fetch_json=_provider(state=state))
    row = report["games"][0]
    assert row["sourceValid"] is False
    assert row["preT45"] is False
    assert row["errors"] == ["weather_response_received_at_or_after_t45"]


def test_post_cutoff_probe_makes_no_weather_request():
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        if "/schedule?" in url:
            return {"dates": [{"date": "2026-09-11", "games": [_game()]}]}
        raise AssertionError("post-cutoff probe must not fetch venue or weather")
    report = SUBJECT.build_report(clock=lambda: START - timedelta(minutes=30), fetch_json=fetch)
    assert len(calls) == 1
    assert report["sourceValidGameCount"] == 0
    assert report["games"][0]["errors"] == ["game_not_pre_t45_at_probe_start"]


def test_missing_temperature_fails_closed_without_roof_or_scoring_claim():
    weather = _weather(); weather["hourly"]["temperature_2m"] = [None, None]
    report = SUBJECT.build_report(clock=lambda: NOW, fetch_json=_provider(weather=weather))
    row = report["games"][0]
    assert row["sourceValid"] is False
    assert row["roofStatusClaimed"] is False
    assert row["canChangeProductionScoring"] is False
    assert any("required_weather_values_missing" in error for error in row["errors"])
