"""BBS 429/network/missing-key degrade to empty catalogue; slate continues."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ks1.live_inputs import ProviderFailure, bbs_unavailable, degraded_bbs_capture, capture


def test_bbs_unavailable_statuses():
    assert bbs_unavailable({"provider": "bbs", "status": 429})
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    assert not bbs_unavailable({"provider": "bbs", "status": 500})
    assert not bbs_unavailable({"provider": "odds", "status": 429})


def test_degraded_bbs_capture_empty():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    result = degraded_bbs_capture(receipt)
    assert result["payload"] == {"data": []}
    assert result["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in result["receipt"]


def test_capture_continues_on_bbs_429(tmp_path):
    def fake_fetch(provider, *args, **kwargs):
        if provider == "official_schedule":
            return {"payload": {"dates": [{"games": [{"gamePk": 1, "gameDate": "2026-10-17T19:00:00Z", "status": {"detailedState": "Scheduled"}}]}]}, "receipt": {"provider": provider}}
        if provider == "bbs":
            raise ProviderFailure({"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"})
        if provider == "odds":
            raise ProviderFailure({"provider": "odds", "status": 401})
        return {"payload": {}, "receipt": {"provider": provider}}

    with patch("ks1.live_inputs.fetch", side_effect=fake_fetch), \
         patch("ks1.live_inputs.lineup_feeds", return_value={"games": {}}), \
         patch("ks1.live_inputs.aws_clients", return_value=(None, None, "bucket")), \
         patch("ks1.live_inputs.Reader") as MockReader, \
         patch("boto3.resource"), \
         patch("ks1.live_inputs.read_passive_context", return_value={}), \
         patch("ks1.recent_statcast.restore_recent_history", return_value={}):
        MockReader.return_value.pointer.return_value = {"artifact": {}, "games": [], "schedule": []}
        MockReader.return_value.read.return_value = {"artifact": {}}
        MockReader.return_value.keys.return_value = []
        MockReader.return_value.receipts = []
        # Minimal model_refs mock via Path
        with patch("pathlib.Path.read_bytes", return_value=b'{"lightgbm": {"key": "k", "version_id": "v", "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}}'), \
             patch("hashlib.sha256") as mock_hash:
            mock_hash.return_value.hexdigest.return_value = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
            capture("2026-10-17", tmp_path)

    bbs = json.loads((tmp_path / "bbs.json").read_bytes())
    assert bbs["payload"]["data"] == []
    assert bbs["receipt"]["degraded"] == "bbs_unavailable"
    assert (tmp_path / "capture.json").exists()
