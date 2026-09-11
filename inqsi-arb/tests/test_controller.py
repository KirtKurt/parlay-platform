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


def _healthy_health():
    return {
        "ok": True,
        "version": "INQSI-ARB-v3",
        "places_bets": False,
        "provider_key_present": True,
        "audit_persistence": True,
        "automatic_market_discovery": True,
        "websocket_push_configured": True,
        "websocket_public_url_present": True,
        "balance_aware_optimizer": True,
        "two_leg_completion_assistant": True,
        "opportunity_history": True,
        "rules_registry_entries": 5,
    }


def test_check_arb_validates_ui_history_and_capability_contract(monkeypatch):
    def fake_json(url, **kwargs):
        if url.endswith("/v1/arb/health"):
            return 200, {}, _healthy_health()
        if "/v1/arb/catalog" in url:
            return 200, {}, {"ok": True, "n_sports": 3}
        if url.endswith("/v1/arb/rules"):
            return 200, {}, {"ok": True, "count": 5, "rules": []}
        if "/v1/arb/history" in url:
            return 200, {}, {"ok": True, "kind": "SCAN", "count": 1, "history": [{"event_id": "1"}]}
        if url == "http://books":
            return 200, {}, {"complete": True, "sports_failed": 0, "sportsbook_count": 10}
        raise AssertionError(url)

    monkeypatch.setattr(controller, "http_json", fake_json)
    monkeypatch.setattr(controller, "http_text", lambda url, **kwargs: (200, {"Content-Type": "text/html; charset=utf-8"}, "<title>Inqsi ARB Console</title>"))
    result = controller.check_arb("http://arb", "http://books")
    assert result["ok"] is True
    assert result["summary"]["history_count"] == 1
    assert result["summary"]["ui_ok"] is True
    assert result["summary"]["sportsbook_count"] == 10


def test_check_arb_fails_closed_on_missing_runtime_capability(monkeypatch):
    health = _healthy_health()
    health["websocket_push_configured"] = False

    def fake_json(url, **kwargs):
        if url.endswith("/v1/arb/health"):
            return 200, {}, health
        if "/v1/arb/catalog" in url:
            return 200, {}, {"ok": True, "n_sports": 3}
        if url.endswith("/v1/arb/rules"):
            return 200, {}, {"ok": True, "count": 5}
        if "/v1/arb/history" in url:
            return 200, {}, {"ok": True, "kind": "SCAN", "history": []}
        if url == "http://books":
            return 200, {}, {"complete": True, "sports_failed": 0, "sportsbook_count": 10}
        raise AssertionError(url)

    monkeypatch.setattr(controller, "http_json", fake_json)
    monkeypatch.setattr(controller, "http_text", lambda url, **kwargs: (200, {"content-type": "text/html"}, "Inqsi ARB Console"))
    result = controller.check_arb("http://arb", "http://books")
    assert result["ok"] is False
    assert "health_capabilities" in result["failures"]
