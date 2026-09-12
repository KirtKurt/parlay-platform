import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import bbd_provider


@pytest.fixture(autouse=True)
def isolate_bbd_credentials(monkeypatch):
    for name in ("BBD_API_KEY", "BIG_BALLS_DATA_API_KEY", "BIGBALLS_DATA_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("payload", [
    None, "invalid", {}, {"error": "access denied"}, {"data": None},
    {"data": {"items": "invalid"}}, [None],
    {"data": [{"id": "valid"}, "invalid"]},
])
def test_malformed_collections_fail_closed(monkeypatch, operation, payload):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, payload))

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_COLLECTION_SCHEMA_INVALID"
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("payload", [
    {"data": [], "events": [{"id": "hidden"}]},
    {"events": [{"id": "hidden"}], "data": []},
    {"data": None, "results": []},
    {"data": [], "sports": "invalid"},
    {"matches": [], "events": []},
    {"data": {"items": [], "events": [{"id": "hidden"}]}},
    {"data": {"events": [{"id": "hidden"}], "items": []}},
    {"data": {"sports": None, "items": []}},
    {"data": {"matches": [], "results": []}},
])
def test_competing_collection_envelopes_fail_closed(monkeypatch, operation, payload):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, payload))

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is False
    assert result["reason"] == "BBD_COLLECTION_SCHEMA_INVALID"
    if operation == "health":
        assert result["sports_count"] is None
    else:
        assert result[operation] == []


@pytest.mark.parametrize("key", ["data", "sports", "matches", "events", "results"])
@pytest.mark.parametrize("rows", [[], [{"id": "context-1"}]])
def test_single_collection_envelopes_preserve_rows_and_metadata(key, rows):
    assert bbd_provider._items({key: rows, "pagination": {"next": None}}) == rows


@pytest.mark.parametrize("key", ["sports", "matches", "events", "results", "items"])
@pytest.mark.parametrize("rows", [[], [{"id": "context-1"}]])
def test_single_nested_collection_envelopes_preserve_rows_and_metadata(key, rows):
    assert bbd_provider._items({"data": {key: rows, "total": len(rows)}}) == rows


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("payload", [[], {"data": []}, {"data": {"items": []}}])
def test_recognized_empty_collections_remain_valid(monkeypatch, operation, payload):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, payload))

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is True
    assert result["sports_count" if operation == "health" else "count"] == 0


@pytest.mark.parametrize("status", [401, 403, 404])
def test_health_preserves_access_failure_reason(monkeypatch, status):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (status, {}, {}))
    result = bbd_provider.health()
    assert result["ok"] is False
    assert result["reason"] == "BBD_AUTH_OR_DISCOVERY_FAILED"
    assert result["sports_status"] == status


def test_bbd_disabled_is_safe(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "false")
    monkeypatch.delenv("BBD_API_KEY", raising=False)
    monkeypatch.delenv("BIG_BALLS_DATA_API_KEY", raising=False)
    result = bbd_provider.health()
    assert result["enabled"] is False
    assert result["ok"] is True
    assert result["reason"] == "DISABLED_BY_CONFIG"


def test_bbd_enabled_missing_key_fails_closed(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.delenv("BBD_API_KEY", raising=False)
    monkeypatch.delenv("BIG_BALLS_DATA_API_KEY", raising=False)
    result = bbd_provider.health()
    assert result["enabled"] is True
    assert result["configured"] is False
    assert result["ok"] is False
    assert result["reason"] == "BBD_API_KEY_NOT_CONFIGURED"


def test_health_proves_auth_and_sport_discovery(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")

    def fake(path, **kwargs):
        if path == "/v1/user/me":
            return 200, {}, {"data": {"id": "user"}}
        if path == "/v1/sports":
            return 200, {}, {"data": [{"id": "baseball"}, {"id": "football"}]}
        raise AssertionError(path)

    monkeypatch.setattr(bbd_provider, "_request", fake)
    result = bbd_provider.health()
    assert result["ok"] is True
    assert result["sports_count"] == 2
    assert result["auth_status"] == 200


def test_events_preserve_bbd_identity_and_no_prices(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")

    def fake(path, **kwargs):
        assert path == "/v1/matches"
        return 200, {"x-request-id": "req-1"}, {
            "data": [{
                "id": 123,
                "sport": "baseball",
                "league": "mlb",
                "status": "scheduled",
                "start_time": "2030-01-01T00:00:00Z",
                "home_team": {"id": "h", "name": "Home"},
                "away_team": {"id": "a", "name": "Away"},
            }]
        }

    monkeypatch.setattr(bbd_provider, "_request", fake)
    result = bbd_provider.events(sport="baseball", league="mlb")
    assert result["ok"] is True
    assert result["count"] == 1
    row = result["events"][0]
    assert row["bbd_event_id"] == "123"
    assert row["source"] == "big_balls_data"
    assert "price" not in row
    assert result["request_id"] == "req-1"
