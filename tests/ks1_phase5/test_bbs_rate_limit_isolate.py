"""BBS 429 is a missing catalogue, not an identity collision.

Official games without BBS must become missing_bbs_identity exclusions in daily
(isolate_unmatched=True). Truncation and schema errors stay fatal.
"""
import pytest

from ks1.live_inputs import bbs_unavailable, degraded_bbs_capture


def test_bbs_429_degrades_to_empty_catalogue():
    receipt = {'provider': 'bbs', 'status': 429,
               'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert captured['receipt']['match_catalogue'] == 'empty'


def test_transport_and_missing_key_degrade():
    for status in ('NETWORK_ERROR', 'BBS_API_KEY_MISSING'):
        receipt = {'provider': 'bbs', 'status': status}
        assert bbs_unavailable(receipt)
        assert degraded_bbs_capture(receipt)['payload'] == {'data': []}


def test_truncation_and_schema_are_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200,
               'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'}
    assert not bbs_unavailable(receipt)
    with pytest.raises(ValueError):
        degraded_bbs_capture(receipt)
