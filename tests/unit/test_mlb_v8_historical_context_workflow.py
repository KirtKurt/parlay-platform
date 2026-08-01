from __future__ import annotations

from pathlib import Path

import mlb_v8_historical_point_in_time_context_v1 as context


WORKFLOW = Path(".github/workflows/mlb-v8-historical-context-backfill.yml")


def test_workflow_runs_isolated_context_backfill_and_canonical_settled_tick():
    text = WORKFLOW.read_text()

    assert "run_mlb_v8_historical_context_backfill_entrypoint.py" in text
    assert "MLB_V8_HISTORICAL_CONTEXT#V1" in text
    assert '"mode":"orchestrate"' in text
    assert "manualCursorMutation':False" in text
    assert "mlb-supervised-shadow-v2-recurring.yml" not in text
    assert "cancel-in-progress: false" in text
    assert "git reset --hard origin/main" in text
    assert "git clean -fd" in text
    assert "productionAuthorityChanged') is False" in text


def test_workflow_accelerates_with_bounded_serial_batch_and_provider_delay():
    text = WORKFLOW.read_text()

    assert "default: '25'" in text
    assert "inputs.limit || '25'" in text
    assert "BBS_HISTORICAL_REQUEST_DELAY_SECONDS: '1.0'" in text
    assert "timeout-minutes: 90" in text
    assert "cancel-in-progress: false" in text


def test_workflow_revalidates_unchanged_selection_guard_thresholds():
    text = WORKFLOW.read_text()

    assert "guard.MIN_OOF_ACCURACY_UPLIFT == 0.005" in text
    assert "guard.MIN_OOF_NET_CORRECT == 3" in text
    assert "guard.MAX_WORST_FOLD_ACCURACY_REGRESSION == 0.01" in text
    assert "guard.MIN_POSITIVE_FOLD_RATIO == 2.0 / 3.0" in text
    assert "guard.MIN_FUNDAMENTALS_COVERAGE == 0.50" in text


def _canonical():
    return {
        "officialGamePk": "777001",
        "slateDateEt": "2026-07-28",
        "commenceTime": "2026-07-28T23:00:00Z",
        "predictionLockAtUtc": "2026-07-28T22:15:00Z",
    }


def test_weather_fallback_retries_earlier_single_run_with_minimal_contract():
    source = context.OfficialContextSource()
    source._venue_coordinates = lambda _current: (42.3467, -71.0972)
    calls = []

    def get(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        if len(calls) == 1:
            raise RuntimeError("run_not_available")
        return {
            "hourly": {
                "time": ["2026-07-28T23:00"],
                "temperature_2m": [28.0],
                "relative_humidity_2m": [60.0],
                "precipitation": [0.0],
                "wind_speed_10m": [8.0],
                "wind_direction_10m": [225.0],
                "wind_gusts_10m": [12.0],
            }
        }

    source._get = get
    value = source.weather(_canonical(), {})

    assert value["source"] == "Open-Meteo Single Runs archived forecast fallback"
    assert value["fallbackAttemptHoursBack"] == 6
    assert value["conservativeAvailableAtUtc"] <= _canonical()["predictionLockAtUtc"]
    assert calls[0][1]["models"] == "ecmwf_ifs"
    assert calls[0][1]["forecast_hours"] >= 12
    assert "forecast_days" not in calls[0][1]
    assert "precipitation_probability" not in calls[0][1]["hourly"]


def test_weather_fallback_uses_exact_lock_mlb_timecode_when_archive_is_unavailable():
    source = context.OfficialContextSource()
    source._venue_coordinates = lambda _current: (42.3467, -71.0972)
    calls = []

    def get(endpoint, params=None):
        calls.append((endpoint, dict(params or {})))
        if endpoint == context.OPEN_METEO_SINGLE_RUN:
            raise RuntimeError("archive_unavailable")
        assert endpoint.endswith("/777001/feed/live")
        return {
            "gameData": {
                "weather": {
                    "temp": 82,
                    "condition": "Partly Cloudy",
                    "wind": "8 mph, Out To RF",
                },
                "venue": {"fieldInfo": {"roofType": "Open"}},
            }
        }

    source._get = get
    value = source.weather(_canonical(), {})

    assert value["source"] == "MLB StatsAPI exact-lock timecode pregame weather"
    assert value["timecode"] == "20260728_221500"
    assert value["conservativeAvailableAtUtc"] == "2026-07-28T22:15:00+00:00"
    assert value["targetOutcomeUsed"] is False
    assert value["sameDayResultsUsed"] is False
    assert any(
        params.get("timecode") == "20260728_221500"
        for endpoint, params in calls
        if endpoint.endswith("/777001/feed/live")
    )


def test_weather_fallback_remains_fail_closed_when_both_sources_fail():
    source = context.OfficialContextSource()
    source._venue_coordinates = lambda _current: (42.3467, -71.0972)
    source._get = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("unavailable")
    )

    try:
        source.weather(_canonical(), {})
    except RuntimeError as exc:
        assert "point_in_time_weather_sources_exhausted" in str(exc)
    else:
        raise AssertionError("weather fallback must fail closed when no source is available")
