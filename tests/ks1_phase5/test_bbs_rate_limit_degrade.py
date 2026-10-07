"""BBS rate-limit failure must not kill the official slate."""
import hashlib

import pytest

from ks1.inventory import encode
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture


def test_bbs_429_is_degradable_and_writes_empty_catalogue():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": "str"}}
    assert bbs_unavailable(receipt)
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    assert not bbs_unavailable({"provider": "odds", "status": 429})
    assert not bbs_unavailable({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_unavailable"
    assert captured["receipt"]["match_catalogue"] == "empty"
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
    assert captured["receipt"]["as_of"]


def test_truncation_and_schema_receipts_are_not_degradable():
    with pytest.raises(ValueError, match="refusing to degrade"):
        degraded_bbs_capture({"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"})
