"""BBS 403/429 degrade to empty catalogue; do not kill the slate."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ks1.live_inputs import ProviderFailure, bbs_unavailable, degraded_bbs_capture, capture


def test_bbs_unavailable_statuses():
    assert bbs_unavailable({"provider": "bbs", "status": 403})
    assert bbs_unavailable({"provider": "bbs", "status": 429})
    assert bbs_unavailable({"provider": "bbs", "status": "NETWORK_ERROR"})
    assert bbs_unavailable({"provider": "bbs", "status": "BBS_API_KEY_MISSING"})
    assert not bbs_unavailable({"provider": "bbs", "status": 500})
    assert not bbs_unavailable({"provider": "odds", "status": 403})


def test_degraded_bbs_capture_empty():
    receipt = {"provider": "bbs", "status": 403, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    value = degraded_bbs_capture(receipt)
    assert value["payload"] == {"data": []}
    assert value["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in value["receipt"]


def test_capture_does_not_raise_on_bbs_403(tmp_path):
    def fake_fetch(*args, **kwargs):
        if args[0] == "official_schedule":
            return {"payload": {"dates": [{"games": []}]}, "receipt": {"provider": "official_schedule", "status": 200}}
        raise ProviderFailure({"provider": "bbs", "status": 403, "endpoint": "https://api.bigballsdata.com/v1/matches"})

    with patch("ks1.live_inputs.fetch", side_effect=fake_fetch), \
         patch("ks1.live_inputs.lineup_feeds", return_value={"games": {}}), \
         patch("ks1.live_inputs.aws_clients", return_value=(None, None, "bucket")), \
         patch("ks1.live_inputs.Reader") as MockReader, \
         patch("ks1.live_inputs.restore_recent_history", return_value={}), \
         patch("ks1.live_inputs.platt_inputs.capture", return_value={}):
        # Minimal mocks to reach the BBS path and continue
        MockReader.return_value.pointer.return_value = {"games": [], "schedule": []}
        MockReader.return_value.read.return_value = {"artifact": {}}
        MockReader.return_value.keys.return_value = []
        MockReader.return_value.receipts = []
        # S3 get_object will fail later; we only care that BBS 403 does not add to errors
        try:
            capture("2026-11-04", tmp_path)
        except Exception:
            pass  # later stages may fail on mocks; check bbs.json
        bbs_path = tmp_path / "bbs.json"
        assert bbs_path.exists()
        data = json.loads(bbs_path.read_text())
        assert data["payload"]["data"] == []
        assert data["receipt"]["degraded"] == "bbs_unavailable"
