from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
import pytest


def test_rate_limit_degrades_to_empty_catalogue():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert captured['receipt']['status'] == 429


def test_schema_or_truncation_receipt_is_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'}
    assert not bbs_unavailable(receipt)
    with pytest.raises(ValueError):
        degraded_bbs_capture(receipt)
