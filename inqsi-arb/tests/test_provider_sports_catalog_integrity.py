"""Provider sports catalog integrity: valid peers retained, incomplete data not certified."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import provider


def _catalog(monkeypatch, payload, meta=None):
    monkeypatch.setattr(provider, "api_key", lambda: "test-key")
    monkeypatch.setattr(
        provider, "_get",
        lambda *args, **kwargs: (
            payload, meta if meta is not None else {"ok": True, "status": 200}
        ),
    )
    return provider.list_sports()


def test_non_list_http_200_catalog_fails_closed(monkeypatch):
    sports, meta = _catalog(monkeypatch, {"sports": []})
    assert sports == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["error"] == "MALFORMED_PROVIDER_SPORT_CATALOG"
    assert meta["status"] == 200


def test_mixed_catalog_retains_valid_sports_and_reports_partial(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "soccer_epl", "active": True},
        {"key": " "},
        {"title": "missing key"},
        None,
        {"key": True},
    ])
    assert [row["key"] for row in sports] == ["soccer_epl"]
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["n_sports_received"] == 5
    assert meta["n_sports_invalid"] == 4
    assert meta["error"] == "MALFORMED_PROVIDER_SPORT_CATALOG"


def test_all_invalid_catalog_is_not_successful_empty_inventory(monkeypatch):
    sports, meta = _catalog(monkeypatch, [{"title": "no sport key"}])
    assert sports == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_sports_invalid"] == 1


def test_legitimate_empty_catalog_remains_successful(monkeypatch):
    sports, meta = _catalog(monkeypatch, [])
    assert sports == []
    assert meta["ok"] is True
    assert meta["partial"] is False
    assert meta["n_sports_received"] == 0
    assert meta["n_sports_invalid"] == 0


def test_provider_authentication_failure_preserves_original_evidence(monkeypatch):
    upstream = {"ok": False, "status": 401, "error": "PROVIDER_HTTP_401"}
    sports, meta = _catalog(monkeypatch, None, upstream)
    assert sports == []
    assert meta == upstream


def test_duplicate_sport_keys_are_deduplicated_and_coverage_is_partial(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "basketball_nba", "title": "NBA", "active": True},
        {"key": "basketball_nba", "title": "Duplicate", "active": False},
        {"key": "soccer_epl", "active": True},
    ])
    assert [row["key"] for row in sports] == ["basketball_nba", "soccer_epl"]
    assert sports[0]["title"] == "NBA"
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "DUPLICATE_PROVIDER_SPORT_KEY"
    assert meta["n_sports_received"] == 3
    assert meta["n_sports_invalid"] == 0
    assert meta["n_sports_duplicates"] == 1


def test_duplicate_keys_after_canonical_whitespace_normalization(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "icehockey_nhl"},
        {"key": " icehockey_nhl "},
    ])
    assert [row["key"] for row in sports] == ["icehockey_nhl"]
    assert meta["partial"] is True
    assert meta["n_sports_duplicates"] == 1


def test_string_false_active_flag_is_not_coerced_to_true(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "basketball_nba", "active": "false"},
        {"key": "icehockey_nhl", "active": False},
        {"key": "soccer_epl", "active": True},
    ])
    assert [row["key"] for row in sports] == ["icehockey_nhl", "soccer_epl"]
    assert sports[0]["active"] is False
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "MALFORMED_PROVIDER_SPORT_CATALOG"
    assert meta["n_sports_invalid"] == 1


def test_all_malformed_active_flags_cannot_claim_complete_empty_catalog(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "basketball_nba", "active": "true"},
        {"key": "soccer_epl", "active": 1},
        {"key": "icehockey_nhl", "active": None},
    ])
    assert sports == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_sports_invalid"] == 3


def test_absent_active_flag_keeps_provider_default(monkeypatch):
    sports, meta = _catalog(monkeypatch, [{"key": "basketball_nba"}])
    assert sports[0]["active"] is True
    assert meta["ok"] is True
    assert meta["partial"] is False


def test_malformed_has_outrights_flag_is_not_coerced_to_true(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "basketball_nba", "active": True, "has_outrights": "false"},
        {"key": "soccer_epl", "active": True, "has_outrights": 0},
        {"key": "icehockey_nhl", "active": True, "has_outrights": None},
        {"key": "baseball_mlb", "active": True, "has_outrights": False},
        {"key": "golf_pga", "active": True, "has_outrights": True},
    ])
    assert [row["key"] for row in sports] == ["baseball_mlb", "golf_pga"]
    assert [row["has_outrights"] for row in sports] == [False, True]
    assert meta["ok"] is True
    assert meta["partial"] is True
    assert meta["error"] == "MALFORMED_PROVIDER_SPORT_CATALOG"
    assert meta["n_sports_invalid"] == 3


def test_all_malformed_has_outrights_flags_cannot_certify_coverage(monkeypatch):
    sports, meta = _catalog(monkeypatch, [
        {"key": "basketball_nba", "has_outrights": "true"},
        {"key": "soccer_epl", "has_outrights": []},
    ])
    assert sports == []
    assert meta["ok"] is False
    assert meta["partial"] is True
    assert meta["n_sports_invalid"] == 2


def test_absent_has_outrights_flag_preserves_default_false(monkeypatch):
    sports, meta = _catalog(monkeypatch, [{"key": "basketball_nba"}])
    assert sports[0]["has_outrights"] is False
    assert meta["ok"] is True
    assert meta["partial"] is False
