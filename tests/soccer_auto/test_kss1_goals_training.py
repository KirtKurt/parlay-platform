from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from soccer_auto.canonical import digest
from soccer_auto.kss1_features import HistoryIndex, build_training_table
from soccer_auto.kss1_goals_model import evaluate, fit, rates, train_and_validate
from soccer_auto.kss1_engine import predict_match
from soccer_auto.kss1_runtime import build_kss1_shadow_item, write_kss1_shadow


def history(count=220):
    start = datetime(2025, 1, 1, 16, tzinfo=timezone.utc)
    rows = []
    for i in range(count):
        kickoff = start + timedelta(days=i)
        rows.append({"event_key": f"game-{i}", "sport_key": "soccer_epl",
                     "home_team": f"Team {i % 6}", "away_team": f"Team {(i + 1) % 6}",
                     "commence_time": kickoff.isoformat(), "available_at": (kickoff + timedelta(hours=3)).isoformat(),
                     "home_score": (i * 7) % 4, "away_score": (i * 3) % 3,
                     "home_xg": .2 + (i % 4) * .6, "away_xg": .3 + (i % 3) * .5,
                     "xg_available_at": (kickoff + timedelta(hours=4)).isoformat(),
                     "source_receipt": digest(["result", i]), "xg_source_receipt": digest(["xg", i]),
                     "provenance_mode": "VERIFIED_RECEIPT"})
    return rows


def fixture():
    return {"event_key": "future", "event_id": "future", "sport_key": "soccer_epl",
            "home_team": "Team 0", "away_team": "Team 1", "commence_time": "2025-10-01T16:00:00Z"}


def test_future_labels_and_late_xg_cannot_change_frozen_features():
    rows = history(30)
    target = rows[-1]
    cutoff = "2025-01-30T15:00:00Z"
    before = HistoryIndex(rows).features(target, cutoff)
    rows[-1]["home_score"] = 49
    rows[-1]["home_xg"] = 49
    rows[-1]["source_receipt"] = digest("future changed")
    after = HistoryIndex(rows).features(target, cutoff)
    assert before == after
    late = history(30)
    late[-2]["xg_available_at"] = "2025-02-01T00:00:00Z"
    before = HistoryIndex(late).features(target, cutoff)
    late[-2]["home_xg"] = 40
    late[-2]["xg_source_receipt"] = digest("late changed")
    assert before == HistoryIndex(late).features(target, cutoff)


def test_late_final_receipt_is_excluded_even_if_match_ended():
    rows = history(30)
    rows[-2]["available_at"] = "2025-02-01T00:00:00Z"
    result = HistoryIndex(rows).features(rows[-1], "2025-01-30T15:00:00Z")
    assert "game-28" not in {r["event_key"] for r in result["source_receipts"]}


def test_missing_xg_has_explicit_counts_and_nulls():
    rows = history(30)
    for row in rows:
        row["home_xg"] = row["away_xg"] = None
    result = HistoryIndex(rows).features(rows[-1], "2025-01-30T15:00:00Z")
    assert not result["xg_complete"]
    assert result["values"]["home_xg_attack"] is None
    assert result["counts"]["home_xg"] == 0


def test_conflicting_history_fails_closed():
    rows = history(2)
    changed = deepcopy(rows[0])
    changed["home_score"] = 4
    with pytest.raises(ValueError, match="conflicting"):
        HistoryIndex(rows + [changed])


@pytest.mark.parametrize("field,value", [("home_score", .5), ("home_score", True), ("home_xg", float("nan")), ("source_receipt", "missing"), ("provenance_mode", "UNVERIFIED")])
def test_invalid_history_is_rejected(field, value):
    rows = history(2)
    rows[0][field] = value
    with pytest.raises((ValueError, TypeError)):
        HistoryIndex(rows)


def test_t60_cutoff_enforced():
    with pytest.raises(ValueError, match="T60"):
        HistoryIndex(history(2)).features(fixture(), "2025-10-01T15:00:01Z")


def test_fit_rejects_future_training_labels():
    table = build_training_table(HistoryIndex(history(30)))
    with pytest.raises(ValueError, match="labels"):
        fit(table, use_xg=False, ridge=.1, fitted_as_of="2025-01-05T00:00:00Z")


def test_serialized_model_matches_engine_and_live_shadow_inference():
    rows = history(45)
    index = HistoryIndex(rows)
    table = [r for r in build_training_table(index) if r["features"]["team_strength_complete"]]
    model = fit(table, use_xg=True, ridge=.1, fitted_as_of="2025-03-01T00:00:00Z")
    model = json.loads(json.dumps(model))
    features = index.features(fixture(), "2025-10-01T15:00:00Z")
    expected = rates(model, features)
    context = {"context_as_of": "2025-09-30T00:00:00Z", "created_at": "2025-09-30T00:00:00Z", "history": rows, "model": model}
    item = build_kss1_shadow_item(fixture(), "2025-10-01T15:00:00Z", goals_context=context)
    assert (item["kss1"]["lambda_home"], item["kss1"]["lambda_away"]) == expected
    assert item["model_digest"] == model["model_digest"]
    assert item["goals_features"]["feature_digest"] == features["feature_digest"]
    assert item["model_authority"] == "SHADOW"
    assert item["automatic_prediction_allowed"] is False
    assert any(abs(v) > 1e-6 for v in model["home_coefficients"][3:5])


def test_research_history_cannot_serve_live_even_with_valid_schema():
    rows = history(2)
    rows[0]["provenance_mode"] = "DATE_ASSUMED_RESEARCH"
    context = {"context_as_of": "2025-03-01T00:00:00Z", "created_at": "2025-03-01T00:00:00Z", "history": rows, "model": None}
    with pytest.raises(ValueError, match="research"):
        build_kss1_shadow_item(fixture(), "2025-10-01T15:00:00Z", goals_context=context)


def test_late_context_and_late_shadow_write_are_rejected():
    context = {"created_at": "2025-10-01T15:01:00Z", "history": history(2)}
    with pytest.raises(ValueError, match="unavailable"):
        build_kss1_shadow_item(fixture(), "2025-10-01T15:00:00Z", goals_context=context)
    class NoWrites:
        def put_prediction(self, row): raise AssertionError("late write")
    assert write_kss1_shadow(NoWrites(), fixture(), "2025-10-01T15:01:00Z", goals_context=context)["reason"] == "MISSED_T60_GOALS_SHADOW"


def test_same_model_new_context_records_a_fresh_immutable_pick_before_t60():
    from soccer_auto.kss1_picks import recorded_picks
    rows = history(45)
    table = [r for r in build_training_table(HistoryIndex(rows)) if r["features"]["team_strength_complete"]]
    model = fit(table, use_xg=False, ridge=.1, fitted_as_of="2025-03-01T00:00:00Z")
    old = {"context_as_of": "2025-09-30T00:00:00Z", "created_at": "2025-09-30T00:00:00Z", "history": rows, "model": model}
    fresh = dict(old, context_as_of="2025-10-01T14:59:30Z", created_at="2025-10-01T14:59:30Z")
    target = fixture() | {"SK": "METADATA", "entity_type": "SOCCER_EVENT", "schedule_revision": 1}
    class Store:
        def __init__(self):
            self.saved = {}; self.events = [target]; self.predictions = self
        def put_prediction(self, row):
            if row["SK"] in self.saved:
                return False
            self.saved[row["SK"]] = deepcopy(row)
            return True
        def scan_all(self, table, **kwargs):
            return iter(table)
        def query(self, **kwargs):
            return {"Items": list(self.saved.values())}
    store = Store()
    first = write_kss1_shadow(store, target, "2025-10-01T14:59:00Z", goals_context=old)
    second = write_kss1_shadow(store, target, "2025-10-01T15:00:00Z", goals_context=fresh)
    assert first["written"] and second["written"] and first["sk"] != second["sk"]
    assert not write_kss1_shadow(store, target, "2025-10-01T15:00:00Z", goals_context=fresh)["written"]
    assert not write_kss1_shadow(store, target, "2025-10-01T15:00:01Z", goals_context=fresh)["written"]
    assert len(store.saved) == 2
    assert store.saved[first["sk"]]["goals_context_as_of"] == old["context_as_of"]
    picks = recorded_picks(store, "2025-10-01")
    assert picks["count"] == 1
    assert picks["picks"][0]["model_digest"] == model["model_digest"]
    assert picks["picks"][0]["goals_context_as_of"] == fresh["context_as_of"]


def test_holdout_labels_never_choose_or_fit_the_model():
    rows = history(220)
    before = train_and_validate(build_training_table(HistoryIndex(rows)), min_train=60, min_test=25)
    assert before["trained"]
    assert before["baseline"]["event_manifest"] == before["holdout"]["event_manifest"]
    for row in rows[-20:]:
        row["home_score"] = 7
    after = train_and_validate(build_training_table(HistoryIndex(rows)), min_train=60, min_test=25)
    assert before["selected"] == after["selected"]
    assert before["model"] == after["model"]
    assert before["holdout"]["log_loss"] != after["holdout"]["log_loss"]
    assert not before["prospective_qualified"]


def test_empty_data_never_reports_a_trained_model():
    assert train_and_validate([])["trained"] is False


def test_fitted_model_cannot_publish_selections_for_teams_without_history():
    rows = history(45)
    table = [r for r in build_training_table(HistoryIndex(rows)) if r["features"]["team_strength_complete"]]
    model = fit(table, use_xg=False, ridge=.1, fitted_as_of="2025-03-01T00:00:00Z")
    target = fixture() | {"home_team": "Unknown team", "away_team": "Another unknown team"}
    context = {"context_as_of": "2025-09-30T00:00:00Z", "created_at": "2025-09-30T00:00:00Z", "history": rows, "model": model}
    item = build_kss1_shadow_item(target, "2025-10-01T15:00:00Z", goals_context=context)
    assert item["kss1"]["input_coverage"]["team_strength_complete"] is False
    for key in ("1x2_published", "double_chance_published", "ou25_published", "btts_published"):
        assert item["kss1"]["markets"][key] == "ABSTAIN"
