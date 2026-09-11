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
    def fake_worker(url, timeout, measured_requests):
        assert measured_requests == 4
        return {
            "warmup": (True, 10.0, 200),
            "rows": [
                (True, 20.0, 200),
                (True, 30.0, 200),
                (True, 40.0, 200),
                (True, 50.0, 200),
            ],
            "error": None,
        }

    monkeypatch.setattr(latency_probe, "_worker", fake_worker)
    result = latency_probe.run_probe("https://example.test/health", requests_count=4, concurrency=1, timeout=1)
    assert result["ok"] is True
    assert result["successes"] == 4
    assert result["latency_ms"]["p95"] == 50.0
    assert result["sample_size"] == 4
    assert result["warmup_requests_excluded"] == 1
    assert result["connection_model"] == "one persistent HTTPS connection per concurrent client"


def test_probe_tracks_failure(monkeypatch):
    def fake_worker(url, timeout, measured_requests):
        assert measured_requests == 2
        return {
            "warmup": (True, 10.0, 200),
            "rows": [
                (True, 20.0, 200),
                (False, 40.0, 503),
            ],
            "error": None,
        }

    monkeypatch.setattr(latency_probe, "_worker", fake_worker)
    result = latency_probe.run_probe("https://example.test/health", requests_count=2, concurrency=1, timeout=1)
    assert result["ok"] is False
    assert result["failures"] == 1
    assert result["status_counts"]["503"] == 1


def test_probe_distributes_requests_across_persistent_clients(monkeypatch):
    calls = []

    def fake_worker(url, timeout, measured_requests):
        calls.append(measured_requests)
        return {
            "warmup": (True, 5.0, 200),
            "rows": [(True, 10.0, 200)] * measured_requests,
            "error": None,
        }

    monkeypatch.setattr(latency_probe, "_worker", fake_worker)
    result = latency_probe.run_probe("https://example.test/health", requests_count=10, concurrency=3, timeout=1)
    assert sorted(calls) == [3, 3, 4]
    assert result["sample_size"] == 10
    assert result["warmup_requests_excluded"] == 3
    assert result["successes"] == 10
