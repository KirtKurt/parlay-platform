"""BBS 429 must not kill the official slate."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import bbs_rate_limited, degraded_bbs_capture


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": {"code": "str"}}}
    assert bbs_rate_limited(receipt)
    assert not bbs_rate_limited({"provider": "bbs", "status": 401})
    assert not bbs_rate_limited({"provider": "odds", "status": 429})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_rate_limited"
    assert captured["receipt"]["identity_status"] == "missing_bbs_identity"
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
    assert captured["receipt"]["as_of"]
