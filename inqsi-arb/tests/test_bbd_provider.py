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


@pytest.mark.parametrize("row", [
    {}, {"id": None}, {"id": True}, {"id": False}, {"id": 1.5},
    {"id": []}, {"id": {}}, {"id": ""}, {"id": " \t"},
    {"id": " event-1"}, {"id": "event-1 "},
    {"id": "one", "match_id": "two"},
    {"id": "one", "event_id": None},
])
def test_events_reject_invalid_identity_without_partial_context(monkeypatch, row):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (
        200, {}, {"data": [{"id": "valid"}, row]},
    ))

    result = bbd_provider.events()

    assert result["ok"] is False
    assert result["reason"] == "BBD_EVENT_ID_INVALID"
    assert result["events"] == []


@pytest.mark.parametrize("field", ["id", "match_id", "event_id"])
@pytest.mark.parametrize("identity", [0, 123, "event-1", "00123"])
def test_events_preserve_valid_opaque_identity(monkeypatch, field, identity):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    row = {field: identity, "status": "scheduled"}
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, [row]))

    result = bbd_provider.events()

    assert result["ok"] is True
    assert result["events"][0]["bbd_event_id"] == str(identity)
    assert result["events"][0]["raw"] == row


@pytest.mark.parametrize("rows", [
    [{"id": "same"}, {"id": "same"}],
    [{"id": 123}, {"event_id": "123"}],
    [{"match_id": "same", "status": "scheduled"},
     {"event_id": "same", "status": "finished"}],
])
def test_events_reject_duplicate_identity_without_partial_context(monkeypatch, rows):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, rows))

    result = bbd_provider.events()

    assert result["ok"] is False
    assert result["reason"] == "BBD_EVENT_ID_DUPLICATE"
    assert result["events"] == []


def test_events_accept_consistent_aliases_and_distinct_opaque_ids(monkeypatch):
    monkeypatch.setenv("ARB_BBD_ENABLED", "true")
    monkeypatch.setenv("BBD_API_KEY", "test")
    rows = [{"id": 123, "match_id": "123", "event_id": "123"}, {"id": "00123"}]
    monkeypatch.setattr(bbd_provider, "_request", lambda *args, **kwargs: (200, {}, rows))

    result = bbd_provider.events()

    assert result["ok"] is True
    assert [row["bbd_event_id"] for row in result["events"]] == ["123", "00123"]
