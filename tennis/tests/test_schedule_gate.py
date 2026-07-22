from __future__ import annotations

from datetime import datetime, timezone

from schedule_gate import evaluate_window, group_events_by_slate


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def event(event_id: str, start: str):
    return {"event_id": event_id, "commence_time": start}


def test_gate_opens_on_first_quarter_hour_at_or_after_t_minus_eight_hours():
    matches = [event("match-1", "2026-07-22T18:07:00+00:00")]

    waiting = evaluate_window("2026-07-22", matches, dt("2026-07-22T10:14:59+00:00"))
    active = evaluate_window("2026-07-22", matches, dt("2026-07-22T10:15:00+00:00"))

    assert waiting.state == "WAITING_FOR_T_MINUS_8H"
    assert active.state == "ACTIVE"
    assert active.gate_open_at_utc == "2026-07-22T10:15:00+00:00"


def test_exact_t_minus_eight_hours_is_not_rounded_forward():
    matches = [event("match-1", "2026-07-22T18:00:00+00:00")]

    decision = evaluate_window("2026-07-22", matches, dt("2026-07-22T10:00:00+00:00"))

    assert decision.state == "ACTIVE"
    assert decision.gate_open_at_utc == "2026-07-22T10:00:00+00:00"


def test_latched_window_stays_open_after_postponement():
    postponed = [event("match-1", "2026-07-22T20:00:00+00:00")]
    state = {
        "opened_at_utc": "2026-07-22T10:00:00+00:00",
        "first_match_at_utc": "2026-07-22T18:00:00+00:00",
    }

    decision = evaluate_window(
        "2026-07-22",
        postponed,
        dt("2026-07-22T10:15:00+00:00"),
        latched_state=state,
    )

    assert decision.state == "ACTIVE"
    assert decision.latched is True
    assert decision.first_match_at_utc == "2026-07-22T18:00:00+00:00"


def test_latch_does_not_retroactively_activate_a_pre_gate_slot():
    postponed = [event("match-1", "2026-07-22T20:00:00+00:00")]
    state = {
        "opened_at_utc": "2026-07-22T10:15:00+00:00",
        "first_match_at_utc": "2026-07-22T18:07:00+00:00",
    }

    decision = evaluate_window(
        "2026-07-22",
        postponed,
        dt("2026-07-22T10:00:00+00:00"),
        latched_state=state,
    )

    assert decision.state == "WAITING_FOR_T_MINUS_8H"
    assert decision.gate_open_at_utc == "2026-07-22T10:15:00+00:00"


def test_all_started_matches_complete_an_open_window():
    matches = [event("match-1", "2026-07-22T18:00:00+00:00")]

    decision = evaluate_window(
        "2026-07-22",
        matches,
        dt("2026-07-22T18:00:00+00:00"),
        latched_state={"opened_at_utc": "2026-07-22T10:00:00+00:00"},
    )

    assert decision.state == "COMPLETE"
    assert decision.upcoming_match_count == 0


def test_grouping_uses_match_date_in_eastern_time():
    grouped = group_events_by_slate(
        [event("match-1", "2026-07-22T03:30:00+00:00")],
        "America/New_York",
    )

    assert list(grouped) == ["2026-07-21"]
    assert grouped["2026-07-21"][0]["event_id"] == "match-1"
