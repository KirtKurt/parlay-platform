"""BBS 429 must not kill the slate. Truncation stays fatal.

Does not rewrite p_home, locks, or ledgers.
"""
import pytest

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {
        "provider": "bbs",
        "status": 429,
        "endpoint": "https://api.bigballsdata.com/v1/matches",
    }
    assert bbs_unavailable(receipt)
    out = degraded_bbs_capture(receipt)
    assert out["payload"] == {"data": []}
    assert out["receipt"]["degraded"] == "bbs_unavailable"
    assert out["receipt"]["match_catalogue"] == "empty"


@pytest.mark.parametrize("status", ["NETWORK_ERROR", "BBS_API_KEY_MISSING"])
def test_transport_and_missing_key_degrade(status):
    receipt = {"provider": "bbs", "status": status}
    assert bbs_unavailable(receipt)
    assert degraded_bbs_capture(receipt)["payload"] == {"data": []}


def test_truncation_and_schema_are_not_degraded():
    assert not bbs_unavailable({
        "provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"
    })
    with pytest.raises(ValueError):
        degraded_bbs_capture({"provider": "bbs", "status": 200})
