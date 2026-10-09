"""BBS 429 must not kill the slate. Truncation stays fatal.

Evidence: scheduled run 37959659707 (2026-10-09T16:30Z) failed
ks1.live_inputs with BBS status 429 before daily isolate-skip.
Prior same-cause failure: 37951972444 (2026-10-09T15:28Z).
Odds 401 already degrades; it is not this kill path.
"""
from ks1.bbs_degrade import UNAVAILABLE, bbs_unavailable, degraded_bbs_capture
from ks1.live_inputs import ProviderFailure


def test_429_is_empty_catalogue_not_identity_error():
    receipt = {'provider': 'bbs', 'status': 429, 'endpoint': 'https://api.bigballsdata.com/v1/matches'}
    assert bbs_unavailable(receipt)
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert captured['receipt']['match_catalogue'] == 'empty'


def test_latest_scheduled_429_receipt_shape_degrades():
    """Shape from run 37959659707 ingest log. Must not raise provider capture failed."""
    receipt = {
        'provider': 'bbs',
        'endpoint': 'https://api.bigballsdata.com/v1/matches',
        'status': 429,
        'body_shape': {
            'error': {'code': 'str', 'message': 'str', 'retryable': 'bool'},
            'suggested_fix': 'str',
            'docs_url': 'str',
            'support': {'discord': 'str', 'email': 'str'},
            'meta': {'request_id': 'str', 'timestamp': 'str', 'limiting_bucket': 'str'},
        },
    }
    captured = degraded_bbs_capture(receipt)
    assert captured['payload'] == {'data': []}
    assert captured['receipt']['degraded'] == 'bbs_unavailable'
    assert 'missing_bbs_identity' not in captured['receipt']


def test_network_and_missing_key_degrade_the_same_way():
    for status in ('NETWORK_ERROR', 'BBS_API_KEY_MISSING'):
        assert status in UNAVAILABLE
        captured = degraded_bbs_capture({'provider': 'bbs', 'status': status})
        assert captured['payload'] == {'data': []}
        assert captured['receipt']['degraded'] == 'bbs_unavailable'


def test_truncation_receipt_is_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200, 'error': 'MATCH_CATALOGUE_INVALID_OR_TRUNCATED'}
    assert not bbs_unavailable(receipt)
    try:
        degraded_bbs_capture(receipt)
    except ValueError as exc:
        assert 'refusing to degrade' in str(exc)
    else:
        raise AssertionError('truncation must stay fatal')


def test_schema_status_is_not_degraded():
    receipt = {'provider': 'bbs', 'status': 200, 'error': 'MATCH_ID_MISSING'}
    assert not bbs_unavailable(receipt)


def test_odds_or_other_provider_429_is_not_bbs_degrade():
    assert not bbs_unavailable({'provider': 'odds', 'status': 429})


def test_provider_failure_429_is_unavailable():
    exc = ProviderFailure({'provider': 'bbs', 'status': 429, 'body_shape': {'error': 'dict'}})
    assert bbs_unavailable(exc.receipt)
