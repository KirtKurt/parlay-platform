from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SHIM = ROOT / "scripts" / "mlb_v8_historical_point_in_time_context_v1.py"
AUTONOMOUS_CONTEXT = (
    ROOT / "scripts" / "run_mlb_v8_historical_context_backfill_autonomous.py"
)
SHARED_CONTEXT_WRITER_GROUP = "mlb-v8-autonomous-control-plane"


def _load_runtime_shim():
    spec = importlib.util.spec_from_file_location(
        "_test_mlb_v8_weather_runtime_shim", SHIM
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_context_writers_share_one_non_cancelling_group():
    controller = (
        ROOT / ".github" / "workflows" / "mlb-v8-autonomous-controller.yml"
    ).read_text()
    acceleration = (
        ROOT / ".github" / "workflows" / "mlb-v8-context-acceleration.yml"
    ).read_text()
    weather = (
        ROOT / ".github" / "workflows" / "mlb-v8-weather-runtime-repair.yml"
    ).read_text()
    expected = f"group: {SHARED_CONTEXT_WRITER_GROUP}"

    for source in (controller, acceleration, weather):
        assert expected in source
        assert "cancel-in-progress: false" in source
    assert (
        f'CONTEXT_WRITER_CONCURRENCY_GROUP = "{SHARED_CONTEXT_WRITER_GROUP}"'
        in AUTONOMOUS_CONTEXT.read_text()
    )


def test_scripts_path_resolves_weather_runtime_shim_first():
    env = dict(os.environ)
    env["PYTHONPATH"] = "hello_world:scripts"
    output = subprocess.check_output(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, 'scripts'); "
                "import mlb_v8_historical_point_in_time_context_v1 as m; "
                "print(m.__file__); print(m.RUNTIME_WEATHER_PATCH_VERSION)"
            ),
        ],
        cwd=ROOT,
        env=env,
        text=True,
    ).splitlines()

    assert Path(output[0]).resolve() == SHIM.resolve()
    assert output[1].endswith("deterministic-precipitation")


def test_exact_ecmwf_request_uses_deterministic_precipitation_amount():
    runtime = _load_runtime_shim()
    calls = []

    class Source:
        @staticmethod
        def _venue_coordinates(_current):
            return 40.8296, -73.9262

        @staticmethod
        def _get(endpoint, params):
            calls.append((endpoint, dict(params)))
            return {
                "hourly": {
                    "time": ["2026-08-11T23:00"],
                    "temperature_2m": [28.0],
                    "relative_humidity_2m": [70.0],
                    "precipitation": [2.5],
                    "wind_speed_10m": [12.0],
                    "wind_direction_10m": [180.0],
                    "wind_gusts_10m": [20.0],
                }
            }

    row = runtime._single_run_weather(
        Source(),
        {
            "predictionLockAtUtc": "2026-08-11T22:15:00Z",
            "commenceTime": "2026-08-11T23:00:00Z",
        },
        {},
    )

    assert len(calls) == 1
    _, params = calls[0]
    variables = set(str(params["hourly"]).split(","))
    assert "precipitation" in variables
    assert "precipitation_probability" not in variables
    assert params["models"] == "ecmwf_ifs"
    assert row["precipitation"] == 2.5
    assert row["weatherFeatureComplete"] is True
    assert row["pointInTimeProjectionVerified"] is True


def test_precipitation_amount_has_bounded_direct_penalty():
    runtime = _load_runtime_shim()
    dry = runtime.weather_run_factor(
        {
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 0,
            "precipitation": 0,
        }
    )
    wet = runtime.weather_run_factor(
        {
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 0,
            "precipitation": 10,
        }
    )
    saturated = runtime.weather_run_factor(
        {
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 0,
            "precipitation": 100,
        }
    )

    assert round(dry - wet, 6) == 0.015
    assert saturated == wet


def test_true_probability_sources_remain_backward_compatible():
    runtime = _load_runtime_shim()
    dry = runtime.weather_run_factor(
        {
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 0,
            "precipitation_probability": 0,
        }
    )
    probability = runtime.weather_run_factor(
        {
            "temperature_2m": 20,
            "relative_humidity_2m": 50,
            "wind_speed_10m": 0,
            "precipitation_probability": 100,
            "precipitation": 0,
        }
    )

    assert round(dry - probability, 6) == 0.015
