"""BBS 429 / network / missing key degrade to empty catalogue; do not kill the slate."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ks1.live_inputs import ProviderFailure, bbs_unavailable, degraded_bbs_capture, capture


def test_bbs_unavailable_detects_429():
    receipt = {"provider": "bbs", "status": 429}
    assert bbs_unavailable(receipt) is True
    assert bbs_unavailable({"provider": "bbs", "status": 200}) is False
    assert bbs_unavailable({"provider": "odds", "status": 429}) is False


def test_degraded_bbs_capture_empty_data():
    receipt = {"provider": "bbs", "status": 429, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    result = degraded_bbs_capture(receipt)
    assert result["payload"] == {"data": []}
    assert result["receipt"]["degraded"] == "bbs_unavailable"
    assert "sha256" in result["receipt"]


def test_capture_degrades_bbs_429_does_not_raise(tmp_path):
    def fake_fetch(provider, base, path, params, *, key=None, opener=None):
        if provider == "bbs":
            raise ProviderFailure({"provider": "bbs", "status": 429, "body_shape": "error"})
        if provider == "official_schedule":
            return {"payload": {"dates": [{"games": [{"gamePk": 1, "gameDate": "2026-10-28T00:00:00Z", "status": "Preview"}]}]}, "receipt": {}}
        return {"payload": {}, "receipt": {}}

    with patch("ks1.live_inputs.fetch", side_effect=fake_fetch), \
         patch("ks1.live_inputs.lineup_feeds", return_value={"games": {}}), \
         patch("ks1.live_inputs.aws_clients", return_value=(None, None, "bucket")), \
         patch("ks1.live_inputs.Reader") as MockReader, \
         patch("boto3.resource"):
        MockReader.return_value.read.return_value = {"artifact": {}}
        MockReader.return_value.pointer.return_value = {"games": [], "schedule": []}
        MockReader.return_value.receipts = []
        MockReader.return_value.keys.return_value = []
        capture("2026-10-28", tmp_path)
        bbs_path = tmp_path / "bbs.json"
        assert bbs_path.exists()
        data = json.loads(bbs_path.read_bytes())
        assert data["payload"]["data"] == []
        assert data["receipt"]["degraded"] == "bbs_unavailable"
