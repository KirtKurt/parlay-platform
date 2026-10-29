"""BBS rate-limit and network failures must not kill the official slate."""
import json
from pathlib import Path

from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture, ProviderFailure


def test_bbs_unavailable_statuses():
    assert bbs_unavailable({"provider": "bbs", "status": 429})
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    assert not bbs_unavailable({"provider": "bbs", "status": 200})
    assert not bbs_unavailable({"provider": "odds", "status": 429})


def test_degraded_bbs_capture_empty():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in captured["receipt"]
