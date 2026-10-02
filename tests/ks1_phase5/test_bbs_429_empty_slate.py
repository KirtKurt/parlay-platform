"""BBS 429 must not kill an empty official pregame slate. Games still fail closed."""
import hashlib
from datetime import datetime, timedelta, timezone

from ks1.inventory import encode
from ks1.live_inputs import bbs_rate_limited, degraded_bbs_capture, official_pregame_count


def test_bbs_429_degrades_only_for_empty_pregame_slate():
    receipt = {"provider": "bbs", "status": 429, "body_shape": {"error": "dict"}}
    assert bbs_rate_limited(receipt)
    assert not bbs_rate_limited({"provider": "odds", "status": 429})
    assert not bbs_rate_limited({"provider": "bbs", "status": 500})
    captured = degraded_bbs_capture(receipt)
    assert captured["payload"] == {"data": []}
    assert captured["receipt"]["degraded"] == "bbs_rate_limited_empty_slate"
    assert captured["receipt"]["bbs_status"] == "unavailable"
    assert captured["receipt"]["sha256"] == hashlib.sha256(encode({"data": []})).hexdigest()
    at = datetime(2026, 10, 2, 19, 34, tzinfo=timezone.utc)
    later = (at + timedelta(hours=3)).isoformat()
    games = [{"gameDate": later, "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"}}]
    assert official_pregame_count(games, "2026-10-02", at) == 1
    assert official_pregame_count([], "2026-10-02", at) == 0
    past = (at - timedelta(hours=2)).isoformat()
    finished = [{"gameDate": past, "status": {"abstractGameState": "Final", "detailedState": "Final"}}]
    assert official_pregame_count(finished, "2026-10-02", at) == 0
