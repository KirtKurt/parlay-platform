from __future__ import annotations

from pathlib import Path

import mlb_v8_historical_point_in_time_context_v1 as context


COMPATIBILITY_WORKFLOW = Path(
    ".github/workflows/mlb-v8-historical-context-backfill.yml"
)
CONTROLLER_WORKFLOW = Path(
    ".github/workflows/mlb-v8-autonomous-controller.yml"
)
OFFICIAL_ENTRYPOINT = Path(
    "scripts/run_mlb_v8_historical_context_backfill_entrypoint.py"
)
AUTONOMOUS_ENTRYPOINT = Path(
    "scripts/run_mlb_v8_historical_context_backfill_autonomous.py"
)


def test_single_controller_runs_isolated_official_context_backfill():
    compatibility = COMPATIBILITY_WORKFLOW.read_text()
    controller = CONTROLLER_WORKFLOW.read_text()
    official = OFFICIAL_ENTRYPOINT.read_text()
    autonomous = AUTONOMOUS_ENTRYPOINT.read_text()

    assert "run_mlb_v8_historical_context_backfill_entrypoint.py" in compatibility
    assert "run_mlb_v8_historical_context_backfill_autonomous.py" in controller
    assert "mlb-v8-autonomous-controller.yml" in compatibility
    assert "schedule:" not in compatibility
    assert "cron: '8/15 * * * *'" in controller
    assert "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY" in official
    assert "official_mlb_plus_internal_canonical_context" in official
    assert "install_artifact_bucket_alias" in autonomous
    assert "HistoricalArtifactsBucketName" in autonomous
    assert "cancel-in-progress: false" in compatibility
    assert "cancel-in-progress: false" in controller
    assert "git reset --hard refs/remotes/origin/main" in controller
    assert "git clean -fd" in controller


def test_controller_uses_frequent_micro_batches_without_retired_credentials():
    compatibility = COMPATIBILITY_WORKFLOW.read_text()
    controller = CONTROLLER_WORKFLOW.read_text()
    combined = compatibility + "\n" + controller

    assert "default: '5'" in compatibility
    assert "inputs.limit || '5'" in compatibility
    assert "inputs.context_limit || '5'" in controller
    assert "timeout-minutes: 55" in controller
    assert "--limit \"$CONTEXT_LIMIT\"" in controller
    assert "cron: '8/15 * * * *'" in controller
    assert "cancel-in-progress: false" in combined
    assert "BBS_API_KEY" not in combined
    assert "BBS_API_SECRET_ARN" not in combined
    assert "api.bigballsdata.com" not in combined
    assert "MLB_V8_HISTORICAL_BBS_OVERLAY_REQUIRED: 'false'" in controller


def test_controller_enforces_no_bbd_and_leakage_safe_evidence_contract():
    controller = CONTROLLER_WORKFLOW.read_text()
    official = OFFICIAL_ENTRYPOINT.read_text()

    assert "bbsApiUsed') is False" in controller
    assert "bbsCredentialRead') is False" in controller
    assert "selectionUsedOutcomes') is False" in controller
    assert "productionAuthorityChanged') is False" in controller
    assert "automaticWagerAllowed') is False" in controller
    assert '"targetGameOutcomeUsed": False' in official
    assert '"sameDayResultsExcluded": True' in official
    assert '"authority": AUTHORITY' in official
    assert 'AUTHORITY = "V8_HISTORICAL_OFFICIAL_CONTEXT_SHADOW_ONLY"' in official


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
