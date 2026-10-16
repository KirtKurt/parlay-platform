import json
from pathlib import Path
from unittest.mock import patch

from ks1.live_inputs import ProviderFailure, bbs_unavailable, degraded_bbs_capture, capture


def test_bbs_unavailable_detects_429():
    receipt = {"provider": "bbs", "status": 429}
    assert bbs_unavailable(receipt)
    assert not bbs_unavailable({"provider": "bbs", "status": 500})
    assert not bbs_unavailable({"provider": "odds", "status": 429})


def test_degraded_bbs_capture_empty():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    result = degraded_bbs_capture(receipt)
    assert result["payload"] == {"data": []}
    assert result["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in result["receipt"]


def test_capture_degrades_bbs_429(tmp_path):
    def mock_fetch(provider, base, path, params, *, key=None, opener=None):
        if provider == "bbs":
            raise ProviderFailure({"provider": "bbs", "status": 429, "endpoint": base + path})
        if provider == "official_schedule":
            return {"payload": {"dates": [{"games": [{"gamePk": 1, "gameDate": "2026-10-16T23:00:00Z"}]}]}, "receipt": {"provider": provider}}
        if provider == "odds":
            raise ProviderFailure({"provider": "odds", "status": 401})
        raise AssertionError(provider)

    with patch("ks1.live_inputs.fetch", mock_fetch), \
         patch("ks1.live_inputs.aws_clients", return_value=(None, None, "bucket")), \
         patch("ks1.live_inputs.Reader") as MockReader, \
         patch("ks1.live_inputs.restore_recent_history", return_value={}), \
         patch("boto3.resource"):
        MockReader.return_value.pointer.return_value = {"games": [], "schedule": []}
        MockReader.return_value.read.return_value = {"artifact": {}}
        MockReader.return_value.keys.return_value = []
        MockReader.return_value.receipts = [{}]
        capture("2026-10-16", tmp_path)
    bbs = json.loads((tmp_path / "bbs.json").read_text())
    assert bbs["payload"] == {"data": []}
    assert bbs["receipt"]["degraded"] == "bbs_unavailable"
    manifest = json.loads((tmp_path / "capture.json").read_text())
    assert not any(e.get("provider") == "bbs" for e in manifest.get("errors", []))
