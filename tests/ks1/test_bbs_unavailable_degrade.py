"""BBS unavailable (403/429/key/network) degrades to empty catalogue; does not kill slate."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ks1 import live_inputs


def test_bbs_403_degrades_to_empty():
    receipt = {"provider": "bbs", "status": 403, "endpoint": "https://api.bigballsdata.com/v1/matches"}
    assert live_inputs.bbs_unavailable(receipt)
    degraded = live_inputs.degraded_bbs_capture(receipt)
    assert degraded["payload"] == {"data": []}
    assert degraded["receipt"]["degraded"] == "bbs_unavailable"
    assert "status" not in degraded["receipt"] or degraded["receipt"].get("status") == 403


def test_bbs_429_still_degrades():
    receipt = {"provider": "bbs", "status": 429}
    assert live_inputs.bbs_unavailable(receipt)


def test_bbs_truncation_remains_hard():
    # Truncation is raised inside bbs_catalogue, not via unavailable.
    # This is a unit check that unavailable does not match hard errors.
    receipt = {"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"}
    assert not live_inputs.bbs_unavailable(receipt)
