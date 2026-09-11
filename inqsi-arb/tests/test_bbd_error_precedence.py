"""Regressions from Codex review of PR #810; all provider calls are offline."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import bbd_provider


@pytest.fixture(autouse=True)
def configured_offline_provider(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test-not-a-live-key")
    for name in ("BIG_BALLS_DATA_API_KEY", "BIGBALLS_DATA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def install_responses(monkeypatch, payload, auth_status=200, discovery_error=None):
    def fake(path, **kwargs):
        if path == "/v1/user/me":
            return auth_status, {}, {"data": {"id": "fixture-user"}}
        assert path in {"/v1/sports", "/v1/matches"}
        if discovery_error is not None:
            raise bbd_provider.BBDError(discovery_error)
        return 200, {}, payload

    monkeypatch.setattr(bbd_provider, "_request", fake)


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("provider_error", [{"code": "denied"}, "denied", {}, [], False, 0, ""])
@pytest.mark.parametrize("nested", [False, True])
def test_non_null_provider_error_never_succeeds(monkeypatch, operation, provider_error, nested):
    payload = (
        {"data": {"items": [], "error": provider_error}}
        if nested else {"data": [], "meta": {}, "error": provider_error}
    )
    install_responses(monkeypatch, payload)

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_COLLECTION_SCHEMA_INVALID"
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("nested", [False, True])
def test_null_error_with_valid_empty_collection_remains_valid(monkeypatch, operation, nested):
    payload = (
        {"data": {"items": [], "error": None}}
        if nested else {"data": [], "meta": {}, "error": None}
    )
    install_responses(monkeypatch, payload)

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is True
    assert result["sports_count" if operation == "health" else "count"] == 0


@pytest.mark.parametrize("auth_status", [401, 403, 404])
@pytest.mark.parametrize("payload", [[], {}, {"data": [], "error": {"code": "denied"}}, True])
def test_auth_failure_wins_over_successful_discovery_http(monkeypatch, auth_status, payload):
    install_responses(monkeypatch, payload, auth_status=auth_status)

    result = bbd_provider.health()

    assert result["ok"] is False
    assert result["reason"] == "BBD_AUTH_OR_DISCOVERY_FAILED"
    assert result["auth_status"] == auth_status
    assert result["sports_status"] == 200
    assert result["sports_count"] is None


@pytest.mark.parametrize("auth_status", [401, 403, 404])
@pytest.mark.parametrize("discovery_error", ["BBD_REQUEST_FAILED: TimeoutError", "BBD_HTTP_429: rate limited"])
def test_auth_failure_is_preserved_when_discovery_raises(monkeypatch, auth_status, discovery_error):
    install_responses(monkeypatch, {}, auth_status=auth_status, discovery_error=discovery_error)

    result = bbd_provider.health()

    assert result["ok"] is False
    assert result["reason"] == "BBD_AUTH_OR_DISCOVERY_FAILED"
    assert result["auth_status"] == auth_status
    assert result["sports_status"] is None
    assert result["sports_count"] is None
