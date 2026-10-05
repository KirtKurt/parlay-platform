"""BBS 429 must not fail the slate; identities stay exclusions, not guesses."""
from ks1.live_inputs import bbs_rate_limited, degraded_bbs_capture


def test_bbs_429_is_degraded_not_a_hard_error():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    assert bbs_rate_limited(receipt)
    assert not bbs_rate_limited({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    assert not bbs_rate_limited({"provider": "odds", "status": 429})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_rate_limited"
    assert captured["receipt"]["identity_status"] == "unavailable_exclusions_only"
    assert captured["receipt"]["status"] == 429
