import json
from pathlib import Path
from unittest.mock import patch

from ks1.live_inputs import capture, ProviderFailure


def test_bbs_429_degrades_to_empty_catalogue(tmp_path):
    def fake_bbs(*a, **k):
        raise ProviderFailure({"provider": "bbs", "status": 429, "body_shape": {"error": "rate_limited"}})

    with patch("ks1.live_inputs.bbs_catalogue", side_effect=fake_bbs):
        with patch("ks1.live_inputs.fetch") as mock_fetch:
            # Minimal official schedule
            mock_fetch.return_value = {"payload": {"dates": [{"games": [{"gamePk": 123, "gameDate": "2026-10-11T00:00:00Z", "status": {"abstractGameState": "Preview"}}]}]}, "receipt": {}}
            try:
                capture("2026-10-11", tmp_path)
            except ValueError as e:
                assert "provider capture failed" not in str(e) or "bbs" not in str(e)
            else:
                pass  # success is expected if only BBS 429

    bbs_path = tmp_path / "bbs.json"
    assert bbs_path.exists()
    data = json.loads(bbs_path.read_text())
    assert data["payload"]["data"] == []
    assert data["receipt"]["degraded"] == "bbs_unavailable"
