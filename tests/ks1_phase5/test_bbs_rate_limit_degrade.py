"""BBS rate-limit must degrade to empty catalogue; slate continues."""
from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture, ProviderFailure
import pytest


def test_rate_limit_is_unavailable_and_degrades():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert captured['receipt']['match_catalogue'] == 'empty'


def test_schema_error_is_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'}
    assert not bbs_unavailable(receipt)
    with pytest.raises(ValueError):
        degraded_bbs_capture(receipt)


def test_network_error_degrades():
    receipt = {'provider': 'bbs', 'status': 'NETWORK_ERROR'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload']['data'] == []
