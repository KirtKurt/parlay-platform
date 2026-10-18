"""BBS 429 must degrade to empty catalogue; hard errors remain hard."""
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure


def test_bbs_429_is_unavailable():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": {}}}
    assert bbs_unavailable(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded["payload"] == {"data": []}
    assert degraded["receipt"]["degraded"] == "bbs_unavailable"
    assert degraded["receipt"]["match_catalogue"] == "empty"


def test_bbs_other_status_not_degraded():
    receipt = {"provider": "bbs", "status": 200, "body_shape": {}}
    assert not bbs_unavailable(receipt)
    try:
        degraded_bbs_capture(receipt)
        assert False, "should refuse"
    except ValueError:
        pass


def test_odds_429_not_bbs():
    assert not bbs_unavailable({"provider": "odds", "status": 429})
