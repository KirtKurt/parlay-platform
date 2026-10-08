"""BBS 429/network/missing key degrade. Truncation and schema stay fatal."""
import pytest

from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture


@pytest.mark.parametrize('status', [429, 'NETWORK_ERROR', 'BBS_API_KEY_MISSING'])
def test_unavailable_receipt_degrades_to_empty_catalogue(status):
    receipt = {'provider': 'bbs', 'status': status, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    out = degraded_bbs_capture(receipt)
    assert out['payload'] == {'data': []}
    assert out['receipt']['degraded'] == 'bbs_unavailable'
    assert out['receipt']['match_catalogue'] == 'empty'
    assert out['receipt']['sha256']


def test_truncation_and_schema_receipts_are_not_degraded():
    for receipt in (
        {'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'},
        {'provider': 'bbs', 'status': 200, 'error': 'MATCH_ID_MISSING'},
        {'provider': 'odds', 'status': 429},
    ):
        assert not bbs_unavailable(receipt)
        with pytest.raises(ValueError, match='refusing to degrade'):
            degraded_bbs_capture(receipt)
