import pytest

from tests.soccer_auto.test_health_contract import Store
from tests.soccer_auto.test_historical_materializer import (
    OBSERVED,
    historical_lock,
    live_lock,
    settlement,
)
from soccer_auto.health import prediction_and_training_health


@pytest.mark.parametrize("include_live", [False, True])
def test_conflicted_history_stays_excluded_without_a_lock_integrity_failure(include_live):
    final = settlement()
    locks = [historical_lock(final)]
    if include_live:
        locks.append(live_lock(final))
    conflict = {
        "PK": "SETTLEMENT_CONFLICT",
        "SK": final["event_key"],
        "event_key": final["event_key"],
        "training_blocked": True,
        "reason": "SETTLEMENT_EVIDENCE_CONFLICT",
    }
    result = prediction_and_training_health(
        Store(events=[], locks=locks, settlements=[final], conflicts=[conflict]),
        observed=OBSERVED,
    )
    assert result["training"]["training_rows_ready"] == 0
    assert result["training"]["conversion_backlog"] == 1
    assert result["training"]["exclusion_reasons"]["historical:settlement_conflict"] == 1
    assert result["training"]["invalid_existing_locks"] == 0
    assert result["integrity_failures"] == 0
    assert result["availability_warnings"] == 1
    assert result["state"] == "DEGRADED_AVAILABILITY"


@pytest.mark.parametrize("defect", ["tampered", "duplicate"])
def test_unquarantined_historical_integrity_failures_still_block_health(defect):
    final = settlement()
    lock = historical_lock(final)
    locks = [lock]
    if defect == "tampered":
        lock["feature_hash"] = "tampered"
    else:
        locks.append(live_lock(final))
    result = prediction_and_training_health(
        Store(events=[], locks=locks, settlements=[final]), observed=OBSERVED,
    )
    assert result["training"]["training_rows_ready"] == 0
    assert result["training"]["invalid_existing_locks"] == 1
    assert result["integrity_failures"] == 1
    assert result["healthy"] is False
    assert result["state"] == "DEGRADED_INTEGRITY"
