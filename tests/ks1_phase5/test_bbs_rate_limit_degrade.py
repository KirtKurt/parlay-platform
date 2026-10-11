"""BBS 429 must not kill the official slate."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import degraded_bbs_capture


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": {"code": "str"}}}
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_rate_limit"
    assert captured["receipt"]["bbs_status"] == "unavailable"
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
    assert captured["receipt"]["as_of"]
