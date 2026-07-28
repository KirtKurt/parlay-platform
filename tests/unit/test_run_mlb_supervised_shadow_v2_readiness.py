from datetime import date

from scripts import run_mlb_supervised_shadow_v2 as subject


def test_future_configured_end_allows_completed_state_window():
    assert subject._runtime_date_ready(
        configured_end=date(2026, 12, 31),
        state_end=date(2026, 7, 24),
        current_date=date(2026, 7, 25),
    ) is True


def test_state_window_must_be_reached():
    assert subject._runtime_date_ready(
        configured_end=date(2026, 12, 31),
        state_end=date(2026, 7, 24),
        current_date=date(2026, 7, 23),
    ) is False


def test_configured_range_cannot_move_behind_state():
    assert subject._runtime_date_ready(
        configured_end=date(2026, 7, 20),
        state_end=date(2026, 7, 24),
        current_date=date(2026, 7, 25),
    ) is False


def test_materialization_counts_are_authoritative_when_boolean_is_stale():
    ready, proof = subject._feature_materialization_ready({
        "featureRematerializationComplete": False,
        "featureRematerializedSlateCount": 321,
        "featureRematerializationTotalSlateCount": 321,
        "featureRematerializationErrors": [],
    })
    assert ready is True
    assert proof["countsComplete"] is True


def test_materialization_errors_fail_closed():
    ready, proof = subject._feature_materialization_ready({
        "featureRematerializationComplete": True,
        "featureRematerializedSlateCount": 321,
        "featureRematerializationTotalSlateCount": 321,
        "featureRematerializationErrors": ["bad slate"],
    })
    assert ready is False
    assert proof["errorCount"] == 1
