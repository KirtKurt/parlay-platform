import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "latency_probe.py"
spec = importlib.util.spec_from_file_location("latency_probe", MODULE_PATH)
latency_probe = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules["latency_probe"] = latency_probe
spec.loader.exec_module(latency_probe)


def test_percentile_nearest_rank():
    values = list(range(1, 101))
    assert latency_probe.percentile(values, 95) == 95
    assert latency_probe.percentile(values, 99) == 99


def test_probe_report_shape(monkeypatch):
    samples = iter([
        (True, 10.0, 200),  # warmup
        (True, 20.0, 200),
        (True, 30.0, 200),
        (True, 40.0, 200),
        (True, 50.0, 200),
    ])
    monkeypatch.setattr(latency_probe, "once", lambda url, timeout: next(samples))
    result = latency_probe.run_probe("https://example.test/health", requests_count=4, concurrency=1, timeout=1)
    assert result["ok"] is True
    assert result["successes"] == 4
    assert result["latency_ms"]["p95"] == 50.0
    assert result["sample_size"] == 4


def test_probe_tracks_failure(monkeypatch):
    samples = iter([
        (True, 10.0, 200),
        (True, 20.0, 200),
        (False, 40.0, 503),
    ])
    monkeypatch.setattr(latency_probe, "once", lambda url, timeout: next(samples))
    result = latency_probe.run_probe("https://example.test/health", requests_count=2, concurrency=1, timeout=1)
    assert result["ok"] is False
    assert result["failures"] == 1
    assert result["status_counts"]["503"] == 1
