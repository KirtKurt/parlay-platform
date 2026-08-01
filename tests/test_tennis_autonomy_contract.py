from pathlib import Path


def test_autonomous_controller_contracts_present():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    required = [
        'AUTONOMY',
        'AUTHORITATIVE',
        'DEGRADED',
        'MIN_LIVE_AUDIT',
        'MIN_LIVE_ACCURACY',
        'MAX_LIVE_BRIER',
        'automatic_prediction_allowed',
        'pipeline_failure_circuit_breaker',
    ]
    for token in required:
        assert token in source


def test_template_wires_closed_loop_and_status_endpoint():
    template = Path('tennis-template.yaml').read_text()
    assert 'TennisAutonomousControllerFunction' in template
    assert 'rate(15 minutes)' in template
    assert '/v1/tennis/autonomy/status' in template
    assert 'lambda:InvokeFunction' in template
    assert 'TENNIS_LIVE_FUNCTION' in template
    assert 'TENNIS_BACKFILL_FUNCTION' in template


def test_autonomy_is_fail_closed():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    assert 'authority == "AUTHORITATIVE"' in source
    assert 'insufficient_live_audit_samples' in source
    assert 'live_accuracy_below_gate' in source
    assert 'live_calibration_below_gate' in source
