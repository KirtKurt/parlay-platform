"""BBS 403/429 must not kill official scoring."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_bbs_403_and_429_are_degradable_and_write_empty_catalogue():
    for status in (403, 429):
        receipt = {"provider": "bbs", "status": status, "body_shape": {"error": "str"}}
        assert bbs_unavailable(receipt)
        captured = degraded_bbs_capture(receipt)
        assert captured["payload"] == {"data": []}
        assert captured["receipt"]["degraded"] == "bbs_unavailable"
        assert captured["receipt"]["status"] == status
        assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
        assert captured["receipt"]["as_of"]


def test_other_bbs_failures_are_not_degradable():
    assert not bbs_unavailable({"provider": "bbs", "status": 500})
    assert not bbs_unavailable({"provider": "odds", "status": 403})
    assert not bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
