import copy
import csv
import json

import pytest

from ks1.forensic_consideration import build, evaluate
from ks1.reporting_evidence import report_evidence


def fixture():
    return {
        "date": "2026-09-22", "game_id": "822840", "model_version": "KS1-test",
        "p_home": "0.5562936261487743", "market_home_prob": ".5359",
        "home_team": "Texas Rangers", "away_team": "New York Mets",
        "home_starter_id": "669022", "home_starter_name": "MacKenzie Gore",
        "away_starter_id": "640455", "away_starter_name": "Sean Manaea",
        "as_of": "2026-09-22T15:33:51Z", "commence_time": "2026-09-23T00:05:00Z",
        "calibration_method": "temperature", "odds_event_id": "g1",
        "starter_profile_json": json.dumps({"sides": {
            "home": {"starter_id": 669022, "metrics": {
                "era_7d": 6.75, "era_30d": 3.0, "outs_7d": 12, "bf_7d": 16,
                "appearances_7d": 1, "whip_7d": 1.4, "k_bb_pct_7d": 13.5}},
            "away": {"starter_id": 640455, "metrics": {
                "era_7d": 3.375, "era_30d": 7.06, "outs_7d": 16, "bf_7d": 19,
                "appearances_7d": 1}},
        }}),
        "signal_contributions_json": json.dumps({
            "additivity_verified": True, "scale": "raw_log_odds_SHAP", "top_features": [
                {"feature": "home_team_starter_bb_pct_7d", "score": .0961},
                {"feature": "away_starter_whip_prior_year", "score": -.04},
                {"feature": "home_offense_iso_7d", "score": -.06},
            ]}),
    }


def odds(row, *, event_id="g1", price=-133, timestamp="2026-09-22T15:30:00Z"):
    return {"event_id": event_id, "as_of": "2026-09-22T15:31:00Z", "payload_json": json.dumps({
        "id": event_id, "commence_time": row["commence_time"],
        "home_team": row["home_team"], "away_team": row["away_team"],
        "bookmakers": [{"key": "draftkings", "last_update": timestamp,
                        "markets": [{"key": "h2h", "last_update": timestamp,
                                     "outcomes": [{"name": row["home_team"], "price": price}]}]}],
    })}


def test_gore_deterioration_belongs_to_texas_not_the_benefiting_mets():
    row = fixture()
    original = copy.deepcopy(row)
    diagnostic = evaluate(row)
    result = report_evidence(row, diagnostic)
    flag = next(f for f in result["flags"] if f["code"] == "SELECTED_STARTER_RECENT_DETERIORATION")
    assert flag["side"] == "away"  # Existing diagnostic direction stays unchanged.
    assert flag["observation_subject"]["team"] == "Texas Rangers"
    assert flag["observation_subject"]["starter_id"] == "669022"
    assert flag["observation_subject"]["starter_name"] == "MacKenzie Gore"
    assert flag["usable_as_named_pitcher_fact"] is True
    assert result["flags"][1]["observation_subject"]["starter_name"] == "Sean Manaea"
    assert result["calibration_method"] == "temperature"
    assert row == original


def test_team_rotation_features_are_not_assigned_to_the_named_starter():
    row = fixture()
    result = report_evidence(row, evaluate(row))
    team, individual, offense = result["contributions"]
    assert team["subject_population"] == "team_starter_history"
    assert team["starter_name"] is None
    assert individual["starter_name"] == "Sean Manaea"
    assert individual["direction"] == "OPPOSITION"
    assert offense["subject_population"] == "team_offense"
    row["p_home"] = ".4"
    away = report_evidence(row, evaluate(row))
    assert away["win_probability"] == .6
    assert away["contributions"][0]["score_toward_selected"] == -.0961
    assert away["contributions"][0]["direction"] == "OPPOSITION"


@pytest.mark.parametrize("proof", [{}, {"additivity_verified": False, "scale": "raw_log_odds_SHAP"}])
def test_unverified_attribution_never_claims_consumption(proof):
    row = fixture()
    proof["top_features"] = [{"feature": "home_starter_era_7d", "score": .1}]
    row["signal_contributions_json"] = json.dumps(proof)
    item = report_evidence(row, evaluate(row))["contributions"][0]
    assert item["consumption"] == "CONSUMPTION UNKNOWN"
    assert item["direction"] == "UNVERIFIED"


@pytest.mark.parametrize("observed,status", [("640455", "mismatch"), (None, "unavailable"), ("nan", "unavailable")])
def test_stale_or_absent_pitcher_join_is_not_a_named_fact(observed, status):
    row = fixture()
    profile = json.loads(row["starter_profile_json"])
    profile["sides"]["home"]["starter_id"] = observed
    row["starter_profile_json"] = json.dumps(profile)
    result = report_evidence(row, evaluate(row))
    assert result["starters"]["home"]["subject"]["identity_status"] == status
    assert result["starters"]["home"]["windows"]["7d"]["era"] is None
    assert result["flags"][0]["usable_as_named_pitcher_fact"] is False


@pytest.mark.parametrize("apps,outs,era,state", [
    (0, 0, None, "NO_APPEARANCES"), (1, 0, None, "APPEARANCE_WITH_ZERO_OUTS"),
    (1, 15, 0.0, "OBSERVED_RESULTS"),
])
def test_raw_zero_and_no_appearance_are_distinct_from_shrunk_priors(apps, outs, era, state):
    row = fixture()
    profile = json.loads(row["starter_profile_json"])
    profile["sides"]["home"]["metrics"].update(appearances_7d=apps, outs_7d=outs, era_7d=era)
    row["starter_profile_json"] = json.dumps(profile)
    result = report_evidence(row, evaluate(row))["starters"]["home"]["windows"]
    assert result["7d"]["state"] == state
    assert result["7d"]["era"] == era
    assert result["7d"]["whip_shrunk_model_estimate"] == 1.4
    assert result["7d"]["raw_whip"] is None
    assert result["7d"]["raw_k_minus_bb_pct"] is None
    assert result["7d"]["earned_runs"] is None
    assert result["15d"]["state"] == "UNAVAILABLE"


def test_doubleheader_books_match_event_id_not_identical_teams():
    row = fixture()
    quotes = [odds(row, event_id="g1", price=-133), odds(row, event_id="g2", price=-190)]
    books = report_evidence(row, evaluate(row), quotes)["moneylines"]["books"]
    assert books["draftkings"]["price"] == -133
    assert books["fanduel"]["status"] == "UNAVAILABLE"
    row["odds_event_id"] = "missing"
    assert report_evidence(row, evaluate(row), quotes)["moneylines"]["status"] == "EVENT_MISSING_OR_AMBIGUOUS"


def test_post_lock_cache_is_never_used_as_pregame_moneyline():
    row = fixture()
    row.update(as_of="2026-09-22T16:34:37Z", commence_time="2026-09-22T17:05:00Z")
    quote = odds(row)
    quote["as_of"] = "2026-09-22T17:31:00Z"
    result = report_evidence(row, evaluate(row), [quote])["moneylines"]
    assert result["status"] == "CACHE_AFTER_PREDICTION_OR_CUTOFF"
    assert all(b["price"] is None for b in result["books"].values())


@pytest.mark.parametrize("change,status", [("duplicate", "EVENT_MISSING_OR_AMBIGUOUS"),
                                            ("team", "EVENT_IDENTITY_MISMATCH"),
                                            ("start", "EVENT_TIME_UNVERIFIED")])
def test_ambiguous_or_mismatched_odds_fail_closed(change, status):
    row = fixture()
    quote = odds(row)
    quotes = [quote]
    payload = json.loads(quote["payload_json"])
    if change == "duplicate":
        quotes.append(quote)
    elif change == "team":
        payload["home_team"] = "Wrong team"
    else:
        payload["commence_time"] = "2026-09-23T05:00:00Z"
    quote["payload_json"] = json.dumps(payload)
    assert report_evidence(row, evaluate(row), quotes)["moneylines"]["status"] == status


def test_existing_artifact_path_adds_reporting_view_without_changing_detector(tmp_path):
    row = fixture()
    path = tmp_path / "predictions.csv"
    with path.open("w") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    original = path.read_bytes()
    expected = evaluate(row)
    result = build(path)["games"][0]
    report = result.pop("reporting_evidence")
    assert result == expected
    assert report["win_probability"] == float(row["p_home"])
    assert path.read_bytes() == original
