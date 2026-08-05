from datetime import datetime, timezone

from scripts.mlb_historical_liveness_policy_v2 import classify

NOW = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)


def test_stale_waiting_state_at_current_et_day_is_healthy():
    result = classify(
        {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-05",
            "updatedAtUtc": "2026-08-05T12:00:00+00:00",
        },
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["sourceStateStale"] is True
    assert result["waitingHealthy"] is True
    assert result["recoveryRequired"] is False
    assert result["status"] == "WAITING_HEALTHY"


def test_stale_waiting_state_behind_current_day_requires_recovery():
    result = classify(
        {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-04",
            "updatedAtUtc": "2026-08-05T12:00:00+00:00",
        },
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["sourceStateStale"] is True
    assert result["waitingHealthy"] is False
    assert result["recoveryRequired"] is True
    assert result["status"] == "RECOVERY_REQUIRED"


def test_fresh_non_waiting_state_does_not_require_recovery():
    result = classify(
        {
            "phase": "BACKFILLING",
            "currentDate": "2026-08-04",
            "updatedAtUtc": "2026-08-05T15:30:00+00:00",
        },
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["sourceStateStale"] is False
    assert result["waitingHealthy"] is False
    assert result["recoveryRequired"] is False
    assert result["status"] == "SOURCE_STATE_FRESH"


def test_missing_timestamp_requires_recovery_unless_waiting_current_day():
    stale = classify(
        {"phase": "BACKFILLING", "currentDate": "2026-08-05"}, now=NOW
    )
    waiting = classify(
        {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-05",
        },
        now=NOW,
    )
    assert stale["recoveryRequired"] is True
    assert waiting["recoveryRequired"] is False
    assert waiting["waitingHealthy"] is True
