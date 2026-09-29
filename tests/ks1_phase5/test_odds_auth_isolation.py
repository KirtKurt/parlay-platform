"""Odds auth failures must not kill an otherwise valid KS1 capture."""
from ks1.live_inputs import empty_odds_capture, odds_failure_is_isolatable


def test_odds_401_and_missing_key_are_isolatable():
    assert odds_failure_is_isolatable({'provider': 'odds', 'status': 401})
    assert odds_failure_is_isolatable({'provider': 'odds', 'status': 403})
    assert odds_failure_is_isolatable({'provider': 'odds', 'status': 'ODDS_API_KEY_MISSING'})


def test_odds_429_and_bbs_failures_remain_hard():
    assert not odds_failure_is_isolatable({'provider': 'odds', 'status': 429})
    assert not odds_failure_is_isolatable({'provider': 'odds', 'status': 500})
    assert not odds_failure_is_isolatable({'provider': 'bbs', 'status': 401})
    assert not odds_failure_is_isolatable({'provider': 'official_schedule', 'status': 401})


def test_isolated_odds_capture_writes_empty_events():
    value = empty_odds_capture({'provider': 'odds', 'status': 401, 'body_shape': {'message': 'str'}})
    assert value['payload'] == []
    assert value['receipt']['isolated'] == 'odds_unavailable'
    assert value['receipt']['status'] == 401
