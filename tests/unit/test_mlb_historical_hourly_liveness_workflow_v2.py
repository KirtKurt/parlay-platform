from pathlib import Path


def test_hourly_liveness_workflow_distinguishes_waiting_from_advancement_failure():
    source = Path(
        ".github/workflows/mlb-historical-hourly-liveness-v1.yml"
    ).read_text()

    assert "scripts.mlb_historical_liveness_policy_v2" in source
    assert "recoveryRequired" in source
    assert "waitingHealthy" in source
    assert "WAITING_HEALTHY" in source
    assert "heartbeatAtUtc" in source
    assert "sourceStateUpdatedAtUtc" in source
    assert "state_still_stale_after_recovery" in source
    assert "if classification.get('recoveryRequired') is True" in source
