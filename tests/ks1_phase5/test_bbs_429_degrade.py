"""BBS 429 / unavailable degrades to empty catalogue; does not kill the slate."""
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture, ProviderFailure

def test_bbs_429_is_unavailable():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {}}
    assert bbs_unavailable(receipt)

def test_degraded_bbs_empty():
    receipt = {"provider": "bbs", "status": 429}
    out = degraded_bbs_capture(receipt)
    assert out["payload"]["data"] == []
    assert out["receipt"]["degraded"] == "bbs_unavailable"

def test_schema_error_not_degraded():
    receipt = {"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"}
    assert not bbs_unavailable(receipt)
