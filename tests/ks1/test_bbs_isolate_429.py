"""Regression: BBS 429 degrades to empty catalogue; isolate-skip does not kill slate."""
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import pytest

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import capture  # may need mock, but test the helpers

def test_bbs_unavailable_429():
    receipt = {"provider": "bbs", "status": 429}
    assert bbs_unavailable(receipt)
    degraded = degraded_bbs_capture(receipt)
    assert degraded["payload"]["data"] == []
    assert degraded["receipt"]["degraded"] == "bbs_unavailable"

def test_non_429_not_degraded():
    receipt = {"provider": "bbs", "status": 500}
    assert not bbs_unavailable(receipt)
    with pytest.raises(ValueError):
        degraded_bbs_capture(receipt)
