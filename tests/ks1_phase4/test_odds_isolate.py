"""Odds 401/403 must not fail the KS1 slate. BBS hard errors stay fatal."""
from ks1.live_inputs import fatal_provider_errors, odds_degraded


def test_odds_401_is_not_fatal():
    errors = [{"provider": "odds", "status": 401, "body_shape": {"error_code": "str"}}]
    assert odds_degraded(errors[0])
    assert fatal_provider_errors(errors) == []


def test_odds_403_and_missing_key_are_not_fatal():
    errors = [
        {"provider": "odds", "status": 403},
        {"provider": "odds", "status": "ODDS_API_KEY_MISSING"},
    ]
    assert fatal_provider_errors(errors) == []


def test_bbs_truncation_and_identity_remain_fatal():
    errors = [
        {"provider": "odds", "status": 401},
        {"provider": "bbs", "status": 200, "error": "MATCH_CATALOGUE_INVALID_OR_TRUNCATED"},
    ]
    fatal = fatal_provider_errors(errors)
    assert len(fatal) == 1
    assert fatal[0]["provider"] == "bbs"


def test_bbs_provider_failure_remains_fatal():
    errors = [{"provider": "bbs", "status": 401}]
    assert fatal_provider_errors(errors) == errors
