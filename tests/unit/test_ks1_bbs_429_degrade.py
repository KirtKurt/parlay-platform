"""BBS 429 must degrade to empty catalogue, not kill the slate."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ks1.live_inputs import capture, ProviderFailure, bbs_rate_limited, degraded_bbs_capture


def test_bbs_rate_limited():
    assert bbs_rate_limited({"provider": "bbs", "status": 429})
    assert bbs_rate_limited({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert not bbs_rate_limited({"provider": "bbs", "status": 500})
    assert not bbs_rate_limited({"provider": "odds", "status": 429})


def test_degraded_bbs_capture():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "test"}
    result = degraded_bbs_capture(receipt)
    assert result["payload"] == {"data": []}
    assert result["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in result["receipt"]
