import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "controller.py"
spec = importlib.util.spec_from_file_location("arb_controller", MODULE_PATH)
controller = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
sys.modules["arb_controller"] = controller
spec.loader.exec_module(controller)


def test_report_healthy(monkeypatch):
    monkeypatch.setattr(controller, "check_arb", lambda api, books: {"ok": True, "failures": [], "summary": {}, "checks": {}})
    monkeypatch.setattr(controller, "check_bbd", lambda: {"ok": True, "configured": True, "blocked": False})
    report = controller.build_report("http://arb", "http://books")
    assert report["severity"] == "healthy"
    assert report["places_bets"] is False


def test_missing_bbd_is_degraded_not_arb_failure(monkeypatch):
    monkeypatch.delenv("BBD_API_KEY", raising=False)
    monkeypatch.delenv("BIG_BALLS_DATA_API_KEY", raising=False)
    monkeypatch.delenv("BIGBALLS_DATA_API_KEY", raising=False)
    monkeypatch.setattr(controller, "check_arb", lambda api, books: {"ok": True, "failures": [], "summary": {}, "checks": {}})
    report = controller.build_report("http://arb", "http://books")
    assert report["severity"] == "degraded_external_dependency"
    assert report["bbd"]["reason"] == "BBD_API_KEY_NOT_CONFIGURED"


def test_arb_failure_requires_repair(monkeypatch):
    monkeypatch.setattr(controller, "check_arb", lambda api, books: {"ok": False, "failures": ["health"], "summary": {}, "checks": {}})
    monkeypatch.setattr(controller, "check_bbd", lambda: {"ok": True, "configured": True, "blocked": False})
    report = controller.build_report("http://arb", "http://books")
    assert report["severity"] == "repair_required"


def test_bbd_provider_failure_does_not_become_arb_repair(monkeypatch):
    monkeypatch.setattr(controller, "check_arb", lambda api, books: {"ok": True, "failures": [], "summary": {}, "checks": {}})
    monkeypatch.setattr(controller, "check_bbd", lambda: {"ok": False, "configured": True, "blocked": False, "reason": "BBD_AUTH_OR_DISCOVERY_FAILED"})
    report = controller.build_report("http://arb", "http://books")
    assert report["severity"] == "degraded_provider"
