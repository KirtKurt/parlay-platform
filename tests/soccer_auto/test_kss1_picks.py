from copy import deepcopy
from datetime import datetime, timezone

import pytest

from soccer_auto.kss1_picks import recorded_picks


class Store:
    def __init__(self):
        self.fixture = {"PK": "game", "SK": "METADATA", "entity_type": "SOCCER_EVENT", "event_key": "game", "schedule_revision": 1, "home_team": "Home", "away_team": "Away", "commence_time": "2026-09-14T20:00:00Z"}
        self.events = [self.fixture]
        self.row = {**self.fixture, "SK": "PRED#KSS1#REV#1#TARGET#kss1_book#MODEL#model", "immutable": True, "prediction_status": "SHADOW", "automatic_prediction_allowed": False, "created_at": "2026-09-14T19:00:00Z", "model_digest": "model", "kss1": {"goals_model_digest": "model", "observation": {"action": "public_eligible"}, "markets": {"double_chance_published": "12", "p_12": .78}}}
        self.predictions = self
    def scan_all(self, table, **kwargs):
        assert kwargs["ConsistentRead"]
        return iter(table)
    def query(self, **kwargs):
        assert kwargs["ConsistentRead"]
        return {"Items": [deepcopy(self.row)]}


def test_recorded_12_pick_uses_saved_probability_and_remains_shadow():
    result = recorded_picks(Store(), "2026-09-14", selection="12")
    assert result["count"] == 1
    assert result["picks"][0]["markets"]["p_12"] == .78
    assert result["automatic_prediction_allowed"] is False


@pytest.mark.parametrize("field,value", [("created_at", "2026-09-14T19:00:01Z"), ("schedule_revision", 0), ("commence_time", "2026-09-14T21:00:00Z"), ("home_team", "Different"), ("immutable", False), ("model_digest", "different")])
def test_stale_late_or_mismatched_records_are_not_picks(field, value):
    store = Store()
    store.row[field] = value
    assert recorded_picks(store, "2026-09-14")["count"] == 0


def test_default_feed_excludes_untrained_baselines_and_research():
    store = Store()
    store.row["kss1"]["goals_model_digest"] = None
    assert recorded_picks(store, "2026-09-14")["count"] == 0
    baseline = recorded_picks(store, "2026-09-14", trained_only=False)
    assert baseline["picks"][0]["model_state"] == "UNTRAINED_BASELINE"
    store.row["goals_features"] = {"research_only": True}
    assert recorded_picks(store, "2026-09-14", trained_only=False)["count"] == 0


def test_12_filter_does_not_turn_other_selections_or_abstentions_into_picks():
    store = Store()
    for selected in ("1X", "ABSTAIN", None):
        store.row["kss1"]["markets"]["double_chance_published"] = selected
        assert recorded_picks(store, "2026-09-14", selection="12")["count"] == 0


def test_day_boundary_is_eastern_and_rejects_bad_date():
    store = Store()
    store.fixture["commence_time"] = store.row["commence_time"] = "2026-09-15T02:00:00Z"
    assert recorded_picks(store, "2026-09-14")["count"] == 1
    assert recorded_picks(store, "2026-09-15")["count"] == 0
    with pytest.raises(ValueError):
        recorded_picks(store, "not-a-date")
