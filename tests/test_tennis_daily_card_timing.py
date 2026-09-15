from tennis_learning.daily_card_timing import (
    prediction_is_compliant,
    timing_receipt,
)


def test_matching_live_and_displayed_starts_have_a_source_bound_cutoff():
    receipt = timing_receipt(
        displayed_commence_time="2026-09-14T20:00:00Z",
        live_commence_time="2026-09-14T20:00:00+00:00",
        cutoff_minutes=10,
    )
    assert receipt == {
        "t10_schedule_source": "LIVE",
        "t10_commence_time_utc": "2026-09-14T20:00:00+00:00",
        "display_commence_time_utc": "2026-09-14T20:00:00+00:00",
        "schedule_start_consistent": True,
        "t10_cutoff_utc": "2026-09-14T19:50:00+00:00",
    }
    assert prediction_is_compliant("2026-09-14T19:50:00Z", receipt)
    assert not prediction_is_compliant("2026-09-14T19:50:00.000001Z", receipt)


def test_reschedule_disagreement_is_visible_and_live_start_controls_t10():
    receipt = timing_receipt(
        displayed_commence_time="2026-09-14T16:00:00Z",
        live_commence_time="2026-09-14T17:00:00Z",
        cutoff_minutes=10,
    )
    assert receipt["t10_schedule_source"] == "LIVE"
    assert receipt["display_commence_time_utc"] == "2026-09-14T16:00:00+00:00"
    assert receipt["t10_commence_time_utc"] == "2026-09-14T17:00:00+00:00"
    assert receipt["schedule_start_consistent"] is False
    assert prediction_is_compliant("2026-09-14T16:45:00Z", receipt)


def test_missing_live_start_falls_back_explicitly_to_coverage_start():
    receipt = timing_receipt(
        displayed_commence_time="2026-09-14T16:00:00Z",
        live_commence_time=None,
        cutoff_minutes=10,
    )
    assert receipt["t10_schedule_source"] == "COVERAGE_FALLBACK"
    assert receipt["schedule_start_consistent"] is True
    assert receipt["t10_cutoff_utc"] == "2026-09-14T15:50:00+00:00"
