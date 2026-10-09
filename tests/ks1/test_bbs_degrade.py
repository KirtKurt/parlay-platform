"""BBS 429 must not kill the slate. Truncation stays fatal."""
from ks1.bbs_degrade import bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure


def test_429_is_empty_catalogue_not_identity_error():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert captured['receipt']['match_catalogue'] == 'empty'


def test_truncation_receipt_is_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'}
    assert not bbs_unavailable(receipt)
    try:
        degraded_bbs_capture(receipt)
    except ValueError as exc:
        assert 'refusing to degrade' in str(exc)
    else:
        raise AssertionError('truncation must stay fatal')


def test_provider_failure_429_is_unavailable():
    exc = ProviderFailure({'provider': 'bbs', 'status': 429, 'body_shape': {'error': 'dict'}})
    assert bbs_unavailable(exc.receipt)
