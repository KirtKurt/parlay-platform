"""Odds auth failure must not kill official scoring."""
import hashlib

from ks1.inventory import encode
from ks1.live_inputs import degraded_odds_capture, odds_auth_unavailable


def test_odds_401_is_degradable_and_writes_empty_catalogue():
    receipt = {'provider': 'odds', 'status': 401, 'body_shape': {'message': 'str'}}
    assert odds_auth_unavailable(receipt)
    assert odds_auth_unavailable({'provider': 'odds', 'status': 403})
    assert not odds_auth_unavailable({'provider': 'bbs', 'status': 401})
    assert not odds_auth_unavailable({'provider': 'odds', 'status': 429})
    assert not odds_auth_unavailable({'provider': 'odds', 'status': 500})
    captured = degraded_odds_capture(receipt)
    assert captured['payload'] == []
    assert captured['receipt']['degraded'] == 'odds_auth_unavailable'
    assert captured['receipt']['market_status'] == 'unavailable'
    assert captured['receipt']['sha256'] == hashlib.sha256(encode([])).hexdigest()
    assert captured['receipt']['as_of']


def test_missing_odds_key_is_degradable():
    assert odds_auth_unavailable({'provider': 'odds', 'status': 'ODDS_API_KEY_MISSING'})
