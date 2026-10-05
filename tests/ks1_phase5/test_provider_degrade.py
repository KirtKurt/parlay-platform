"""429/401 must not kill the slate. Truncation stays fatal."""
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import degraded_odds_capture, odds_auth_unavailable


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    assert bbs_unavailable(receipt)
    out = degraded_bbs_capture(receipt)
    assert out["payload"] == {"data": []}
    assert out["receipt"]["degraded"] == "bbs_unavailable"


def test_odds_401_degrades_to_empty_market():
    receipt = {"provider": "odds", "status": 401}
    assert odds_auth_unavailable(receipt)
    out = degraded_odds_capture(receipt)
    assert out["payload"] == []
    assert out["receipt"]["market_status"] == "unavailable"
    assert out["receipt"]["degraded"] == "odds_auth_unavailable"


def test_truncation_and_schema_are_not_unavailable():
    assert not bbs_unavailable({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    assert not odds_auth_unavailable({"provider": "odds", "status": 500})
