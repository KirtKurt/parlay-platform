"""BBS 403/429 must not kill official scoring."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_bbs_403_is_degradable_and_writes_empty_catalogue():
    receipt = {"provider": "bbs", "status": 403, "body_shape": {"error": "str"}}
    assert bbs_unavailable(receipt)
    assert bbs_unavailable({"provider": "bbs", "status": 429})
    assert not bbs_unavailable({"provider": "odds", "status": 403})
    assert not bbs_unavailable({"provider": "bbs", "status": 401})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_unavailable"
    assert captured["receipt"]["status"] == 403
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
    assert captured["receipt"]["as_of"]


def test_bbs_429_is_degradable():
    receipt = {"provider": "bbs", "status": 429}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["status"] == 429
