"""Regression cases for the eight post-merge findings on PR #853."""
from copy import deepcopy
import pytest

from soccer_auto.kss1_bbd import BbdClient, BbdError, extract_match_xg
from soccer_auto.kss1_engine import consider_public_bind, predict_match
from soccer_auto.kss1_identity import map_event
from soccer_auto.kss1_lock import void_for_postponement
from soccer_auto.kss1_markets import apply_abstain
from soccer_auto.kss1_runtime import build_kss1_shadow_item


KICKOFF = "2026-09-12T16:00:00Z"
MATCH_ID = "11111111-1111-1111-1111-111111111111"


def payload(**updates):
    row = {
        "odds_event_id": "fixture-1", "sport_key": "soccer_epl",
        "home_team": "Arsenal", "away_team": "Chelsea",
        "commence_time": KICKOFF, "observed_at": "2026-09-12T15:00:00Z",
        "home_attack": 1.2, "away_attack": 1.0,
        "home_defence": 1.0, "away_defence": 1.0,
        "bbd_matches": [{"id": MATCH_ID, "home": "Arsenal", "away": "Chelsea", "kickoff_utc": KICKOFF}],
    }
    return {**row, **updates}


def test_tier_b_does_not_publish_goals_markets_even_with_confident_xg():
    result = predict_match(payload(sport_key="soccer_netherlands_eredivisie", xg_home=4, xg_away=4))
    assert result["mapping"]["status"] == "mapped"
    assert result["markets"]["p_over_25"] > .7
    assert result["markets"]["ou25_published"] == "ABSTAIN"
    assert result["markets"]["btts_published"] == "ABSTAIN"


def test_native_first_bind_cannot_be_replaced_or_mutated_through_candidate():
    candidate = predict_match(payload())
    original = deepcopy(candidate)
    first = consider_public_bind(None, candidate)
    assert first["accepted"]
    assert candidate == original
    second = consider_public_bind(first["authority"], predict_match(payload(home_attack=2)))
    assert not second["accepted"]
    candidate["markets"]["p_home"] = .99
    assert first["authority"]["markets"] == original["markets"]
    assert first["authority"]["immutable"] is True
    assert first["authority"]["automatic_prediction_allowed"] is False


def test_old_unstamped_native_bind_is_also_preserved():
    old = predict_match(payload())
    assert not consider_public_bind(old, predict_match(payload(home_attack=2)))["accepted"]


@pytest.mark.parametrize("observed", ["2026-09-12T15:00:01Z", "2026-09-12T15:15:00Z", "2026-09-12T15:50:00Z", "2026-09-12T16:01:00Z"])
def test_late_candidate_cannot_acquire_t60_bind(observed):
    assert not consider_public_bind(None, predict_match(payload(observed_at=observed)))["accepted"]


@pytest.mark.parametrize("provider_time", [None, "", "bad", "2026-09-12T16:00:00", "2026-09-12T16:00:01Z", "2026-09-12T16:00:00+01:00"])
def test_missing_or_different_provider_kickoff_never_maps(provider_time):
    source = payload()
    source["bbd_matches"][0]["kickoff_utc"] = provider_time
    result = map_event(**{key: source[key] for key in ("odds_event_id", "sport_key", "home_team", "away_team", "commence_time", "bbd_matches")})
    assert result["status"] == "unmapped"


def test_provider_kickoff_equivalent_offset_maps():
    source = payload()
    source["bbd_matches"][0]["kickoff_utc"] = "2026-09-12T17:00:00+01:00"
    assert predict_match(source)["mapping"]["status"] == "mapped"


@pytest.mark.parametrize("field,target", [("home_attack", "lambda_home"), ("away_defence", "lambda_home"), ("league_home", "lambda_home"), ("away_attack", "lambda_away"), ("home_defence", "lambda_away"), ("league_away", "lambda_away")])
def test_explicit_zero_is_clipped_instead_of_replaced_by_average(field, target):
    assert predict_match(payload(**{field: 0}))[target] == .05


def test_equivalent_postponement_timestamp_does_not_void():
    assert void_for_postponement({"commence_time": KICKOFF}, "2026-09-12T17:00:00+01:00") == {"void": False, "reason": "KICKOFF_UNCHANGED"}


def test_real_postponement_still_voids():
    assert void_for_postponement({"commence_time": KICKOFF}, "2026-09-12T17:00:01+01:00")["void"] is True


@pytest.mark.parametrize("during_read", [False, True])
def test_provider_timeout_is_a_bbd_error(during_read):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): raise TimeoutError("network timeout")
    def opener(*args, **kwargs):
        if during_read: return Response()
        raise TimeoutError("network timeout")
    with pytest.raises(BbdError):
        BbdClient("test-token", opener=opener).match_stats(MATCH_ID)


def test_double_chance_abstains_on_balanced_outcomes_but_accepts_strong_pair():
    markets = {"1x2_pick": "home", "1x2_probability": 1/3,
               "double_chance_pick": "1X", "double_chance_probability": 2/3,
               "ou25_pick": "over", "p_over_25": .5, "p_under_25": .5,
               "btts_pick": "yes", "p_btts_yes": .5, "p_btts_no": .5}
    assert apply_abstain(markets)["double_chance_published"] == "ABSTAIN"
    markets["double_chance_probability"] = .8
    assert apply_abstain(markets)["double_chance_published"] == "1X"


@pytest.mark.parametrize("stats", [
    {"data": {"home": {"expected_goals": "1.8"}, "away": {"expected_goals": "0"}}},
    {"data": {"statistics": [{"name": "Expected Goals", "home": "1.8", "away": "0"}]}},
    {"data": {"stats": [{"type": "expected_goals_(xg)", "home_value": "1.8", "away_value": "0"}]}},
    {"data": {"home_xg": 1.8, "away_xg": 0}},
])
def test_supported_provider_xg_shapes(stats):
    assert extract_match_xg(stats) == {"has_xg": True, "xg_home": 1.8, "xg_away": 0.0}


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -1, True])
def test_invalid_xg_is_missing_not_a_usable_observation(invalid):
    assert extract_match_xg({"home_xg": invalid, "away_xg": 1})["has_xg"] is False


def test_shadow_output_exposes_defaulted_team_inputs():
    lock = {**payload(), "event_key": "EVENT#soccer_epl#fixture-1", "event_id": "fixture-1", "schedule_revision": 1,
            "frozen_features": {"market_prior": [.5, .25, .25]}}
    item = build_kss1_shadow_item(lock, "2026-09-12T15:00:00Z")
    coverage = item["kss1"]["input_coverage"]
    assert coverage["team_strength_complete"] is False
    assert set(coverage["defaulted_fields"]) == {"home_attack", "away_attack", "home_defence", "away_defence", "league_home", "league_away"}
    assert coverage["xg_complete"] is False
    assert item["prediction_status"] == "SHADOW"
    assert item["automatic_prediction_allowed"] is False
