"""BBS 429 must not fail the slate. Truncation and schema stay fatal."""
from pathlib import Path

from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_429_network_and_missing_key_degrade_to_empty_catalogue():
    for status in (429, "NETWORK_ERROR", "BBS_API_KEY_MISSING"):
        receipt = {"provider": "bbs", "status": status, "body_shape": {"error": "dict"}}
        assert bbs_unavailable(receipt)
        degraded = degraded_bbs_capture(receipt)
        assert degraded["payload"] == {"data": []}
        assert degraded["receipt"]["degraded"] == "bbs_unavailable"
        assert degraded["receipt"]["match_catalogue"] == "empty"
        assert degraded["receipt"]["status"] == status


def test_truncation_schema_and_other_providers_stay_fatal():
    fatal = [
        {"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"},
        {"provider": "bbs", "status": 200, "error": "MATCH_ID_MISSING"},
        {"provider": "bbs", "status": 500},
        {"provider": "odds", "status": 429},
    ]
    for receipt in fatal:
        assert not bbs_unavailable(receipt)
        try:
            degraded_bbs_capture(receipt)
        except ValueError as exc:
            assert "refusing to degrade" in str(exc)
        else:
            raise AssertionError("fatal receipt was degraded")


def test_capture_degrades_bbs_before_failing_the_slate():
    source = Path("ks1/live_inputs.py").read_text()
    assert "if bbs_unavailable(exc.receipt):" in source
    assert "degraded_bbs_capture" in source
    assert "provider capture failed; see redacted receipts" in source
    assert "exc.code == 429 and provider == 'bbs' and attempt == 0" in source
    assert "time.sleep(2)" in source
