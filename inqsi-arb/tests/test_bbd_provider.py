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
    {"data": [], "events": [{"id": "hidden"}]},
    {"events": [{"id": "hidden"}], "data": []},
    {"data": [{"id": "visible"}], "results": []},
    {"data": None, "sports": []},
    {"data": [], "matches": "invalid"},
    {"data": [], "sports": []},
    {"data": {"events": [], "items": [{"id": "hidden"}]}},
    {"data": {"items": [{"id": "hidden"}], "events": []}},
    {"data": {"sports": None, "items": []}},
    {"data": {"matches": [], "results": "invalid"}},
    {"data": {"events": [], "items": []}},
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
@pytest.mark.parametrize("payload", [[], {"data": []}, {"data": {"items": []}}])
def test_recognized_empty_collections_remain_valid(monkeypatch, operation, payload):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, payload))

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is True
    assert result["sports_count" if operation == "health" else "count"] == 0


@pytest.mark.parametrize("operation", ["health", "sports", "events"])
@pytest.mark.parametrize("outer", ["data", "sports", "matches", "events", "results"])
@pytest.mark.parametrize("inner", [None, "sports", "matches", "events", "results", "items"])
def test_unambiguous_envelopes_preserve_rows_and_metadata(monkeypatch, operation, outer, inner):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    rows = [{"id": "fixture-event", "status": "scheduled"}]
    value = rows if inner is None else {inner: rows, "meta": {"count": 1}, "error": None}
    payload = {outer: value, "meta": {"count": 1}, "error": None}
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, payload))

    result = getattr(bbd_provider, operation)()

    assert result["ok"] is True
    assert result["sports_count" if operation == "health" else "count"] == 1
    if operation == "events":
        assert result["events"][0]["bbd_event_id"] == "fixture-event"
        assert result["events"][0]["raw"] == rows[0]
    elif operation == "sports":
        assert result["sports"] == rows


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
