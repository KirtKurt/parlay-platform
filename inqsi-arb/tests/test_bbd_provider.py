import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import bbd_provider


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
