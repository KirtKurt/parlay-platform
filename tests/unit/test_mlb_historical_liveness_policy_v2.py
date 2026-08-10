from datetime import datetime, timezone

from scripts.mlb_historical_liveness_policy_v2 import classify

NOW = datetime(2026, 8, 5, 16, 0, tzinfo=timezone.utc)


def _waiting_state(**overrides):
    value = {
        "phase": "WAITING_FOR_SETTLED_HORIZON",
        "currentDate": "2026-08-05",
        "updatedAtUtc": "2026-08-05T12:00:00+00:00",
        "endDate": "2026-08-04",
        "rangeExtensionNextRetryDate": "2026-08-05",
        "lastError": None,
        "settledHorizonWait": {
            "authorizedThroughDate": "2026-08-04",
            "settledHorizonDate": "2026-08-04",
            "configuredCeilingDate": "2026-12-31",
            "nextEligibleSlateDate": "2026-08-05",
            "blockingError": False,
        },
        "completedSlates": [{"slateDateEt": "2026-08-04"}],
        "plan": {
            "endDate": "2026-08-04",
            "completeDateRangeLedger": True,
            "planningErrorCount": 0,
            "rejectedDates": [],
            "slates": [{"slateDateEt": "2026-08-04"}],
        },
    }
    value.update(overrides)
    return value


def test_stale_waiting_state_with_complete_settled_slate_is_healthy():
    result = classify(_waiting_state(), now=NOW, stale_after_minutes=75)
    assert result["sourceStateStale"] is True
    assert result["waitingHealthy"] is True
    assert result["waitingProofValid"] is True
    assert result["settledHorizonEvidence"] == "IMMUTABLE_COMPLETE_SLATE"
    assert result["recoveryRequired"] is False
    assert result["status"] == "WAITING_HEALTHY"


def test_stale_waiting_state_with_official_off_day_is_healthy():
    result = classify(
        _waiting_state(
            completedSlates=[{"slateDateEt": "2026-08-03"}],
            rejectedSlates=[
                {"slateDateEt": "2026-08-04", "reason": "official_off_day"}
            ],
        ),
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["waitingHealthy"] is True
    assert result["settledHorizonEvidence"] == "OFFICIAL_OFF_DAY_REJECTION"
    assert result["recoveryRequired"] is False


def test_stale_waiting_state_with_ledger_proven_off_day_is_healthy():
    result = classify(
        _waiting_state(
            completedSlates=[{"slateDateEt": "2026-08-03"}],
            plan={
                "endDate": "2026-08-04",
                "completeDateRangeLedger": True,
                "planningErrorCount": 0,
                "rejectedDates": [],
                "slates": [{"slateDateEt": "2026-08-03"}],
            },
        ),
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["waitingHealthy"] is True
    assert result["settledHorizonEvidence"] == "OFFICIAL_OFF_DAY_LEDGER"
    assert result["recoveryRequired"] is False


def test_current_day_cursor_without_wait_proof_is_not_healthy():
    result = classify(
        {
            "phase": "WAITING_FOR_SETTLED_HORIZON",
            "currentDate": "2026-08-05",
            "updatedAtUtc": "2026-08-05T12:00:00+00:00",
        },
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["waitingHealthy"] is False
    assert result["recoveryRequired"] is True
    assert "settled_horizon_wait_proof_missing" in result["waitingProofErrors"]
    assert "settled_horizon_completion_evidence_missing" in result[
        "waitingProofErrors"
    ]


def test_waiting_state_with_rejected_settled_slate_requires_recovery():
    result = classify(
        _waiting_state(
            completedSlates=[{"slateDateEt": "2026-08-03"}],
            plan={
                "endDate": "2026-08-04",
                "completeDateRangeLedger": True,
                "planningErrorCount": 0,
                "rejectedDates": [],
                "slates": [{"slateDateEt": "2026-08-04"}],
            },
            rejectedSlates=[
                {
                    "slateDateEt": "2026-08-04",
                    "reason": "incomplete_full_slate_dataset",
                }
            ],
        ),
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["waitingHealthy"] is False
    assert result["recoveryRequired"] is True
    assert result["settledHorizonRejectionReasons"] == [
        "incomplete_full_slate_dataset"
    ]


def test_fresh_invalid_wait_requires_recovery_immediately():
    result = classify(
        _waiting_state(
            updatedAtUtc="2026-08-05T15:30:00+00:00",
            completedSlates=[{"slateDateEt": "2026-08-03"}],
        ),
        now=NOW,
        stale_after_minutes=75,
    )
    assert result["sourceStateStale"] is False
    assert result["waitingHealthy"] is False
    assert result["recoveryRequired"] is True


def test_stale_waiting_state_behind_current_day_requires_recovery():
    result = classify(
        _waiting_state(currentDate="2026-08-04"),
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


def test_missing_timestamp_requires_recovery_unless_waiting_is_proven():
    stale = classify(
        {"phase": "BACKFILLING", "currentDate": "2026-08-05"}, now=NOW
    )
    waiting = classify(_waiting_state(updatedAtUtc=None), now=NOW)
    assert stale["recoveryRequired"] is True
    assert waiting["recoveryRequired"] is False
    assert waiting["waitingHealthy"] is True
