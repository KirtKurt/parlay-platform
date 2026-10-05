"""BBS 429 must not kill the slate. Truncation stays fatal."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": "dict"}}
    assert bbs_unavailable(receipt)
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_unavailable"
    assert captured["receipt"]["match_catalogue"] == "empty"
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()


def test_truncation_and_schema_are_not_degradable():
    assert not bbs_unavailable({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    assert not bbs_unavailable({"provider": "bbs", "status": 200, "error": "MATCH_ID_MISSING"})
    assert not bbs_unavailable({"provider": "odds", "status": 429})
    try:
        degraded_bbs_capture({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    except ValueError as exc:
        assert "refusing" in str(exc)
    else:
        raise AssertionError("truncation must stay fatal")
