"""BBS 429 is a missing catalogue, not a slate-killing capture error."""
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture


def test_429_is_unavailable_not_schema():
    assert bbs_unavailable({"provider": "bbs", "status": 429})
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    assert not bbs_unavailable({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    assert not bbs_unavailable({"provider": "odds", "status": 429})


def test_degraded_catalogue_is_empty_and_marked():
    out = degraded_bbs_capture({"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"})
    assert out["payload"] == {"data": []}
    assert out["receipt"]["degraded"] == "bbs_unavailable"
    assert out["receipt"]["match_catalogue"] == "empty"
    try:
        degraded_bbs_capture({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    except ValueError:
        return
    raise AssertionError("truncation must stay fatal")
