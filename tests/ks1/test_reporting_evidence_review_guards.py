import json
import math

from ks1.reporting_evidence import report_evidence


def row():
    raw = 0.24
    p_raw = 0.5 * (1 + math.tanh(raw / 2))
    return {
        "date": "2026-09-22", "game_id": "1", "model_version": "KS1-test",
        "p_home": "0.56", "p_raw": str(p_raw),
        "home_team": "St. Louis Cardinals", "away_team": "New York Mets",
        "home_starter_id": "1", "home_starter_name": "Home Starter",
        "away_starter_id": "2", "away_starter_name": "Away Starter",
        "as_of": "2026-09-22T15:33:51Z", "commence_time": "2026-09-23T00:05:00Z",
        "odds_event_id": "g1",
        "starter_profile_json": json.dumps({"sides": {
            "home": {"starter_id": 1, "context_basis": "current_season_pitcher", "metrics": {}},
            "away": {"starter_id": 2, "context_basis": "prior_year_pitcher", "metrics": {}},
        }}),
        "signal_contributions_json": json.dumps({
            "additivity_verified": True,
            "scale": "raw_log_odds_SHAP",
            "bias": 0.10,
            "raw_score": raw,
            "groups": {"starter": {"signal_score": 0.14}},
            "top_features": [{"feature": "home_starter_era_7d", "score": 999.0}],
        }),
    }


def quote(record, home="St Louis Cardinals", away="new york mets", outcome="ST LOUIS CARDINALS"):
    return {"event_id": "g1", "as_of": "2026-09-22T15:31:00Z", "payload_json": json.dumps({
        "id": "g1", "commence_time": record["commence_time"],
        "home_team": home, "away_team": away,
        "bookmakers": [{"key": "draftkings", "last_update": "2026-09-22T15:30:00Z",
                        "markets": [{"key": "h2h", "last_update": "2026-09-22T15:30:00Z",
                                     "outcomes": [{"name": outcome, "price": -133}]}]}],
    })}


def test_top_feature_without_feature_level_binding_never_claims_model_consumption():
    result = report_evidence(row(), {"flags": []})
    item = result["contributions"][0]
    assert item["direction"] == "SUPPORT"
    assert item["consumption"] == "CONSUMPTION UNKNOWN"
    assert item["consumption_evidence"] == "GROUP_PROOF_ONLY_TOP_FEATURE_UNBOUND"


def test_odds_identity_uses_serving_punctuation_and_case_normalization():
    record = row()
    money = report_evidence(record, {"flags": []}, [quote(record)])["moneylines"]
    assert money["status"] == "EXACT_EVENT_MATCH"
    assert money["books"]["draftkings"]["price"] == -133


def test_odds_identity_still_fails_closed_for_a_different_team():
    record = row()
    money = report_evidence(record, {"flags": []}, [quote(record, home="Chicago Cubs")])["moneylines"]
    assert money["status"] == "EVENT_IDENTITY_MISMATCH"
    assert money["books"]["draftkings"]["price"] is None
