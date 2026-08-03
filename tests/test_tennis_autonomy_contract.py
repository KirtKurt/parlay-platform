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
    assert "cron(11,26,41,56 * * * ? *)" in template
    assert '/v1/tennis/autonomy/status' in template
    assert 'lambda:InvokeFunction' in template
    assert 'TENNIS_LIVE_FUNCTION' in template
    assert 'TENNIS_BACKFILL_FUNCTION' in template


def test_autonomy_is_fail_closed():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    assert 'authority == "AUTHORITATIVE"' in source
    assert 'insufficient_total_training_samples' in source
    assert 'insufficient_live_audit_samples' in source
    assert 'live_accuracy_below_gate' in source
    assert 'live_calibration_below_gate' in source


def test_optional_history_does_not_open_live_circuit_breaker():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    template = Path('tennis-template.yaml').read_text()
    assert 'HISTORICAL_ENABLED = _env_bool("TENNIS_HISTORICAL_ENABLED", False)' in source
    assert 'HISTORICAL_REQUIRED = _env_bool("TENNIS_HISTORICAL_REQUIRED", False)' in source
    assert 'required=HISTORICAL_REQUIRED' in source
    assert '(failures if required else warnings).append(detail)' in source
    assert '"last_warning"' in source
    assert "TENNIS_HISTORICAL_ENABLED: 'false'" in template
    assert "TENNIS_HISTORICAL_REQUIRED: 'false'" in template


def test_unavailable_history_schedule_is_disabled():
    template = Path('tennis-template.yaml').read_text()
    backfill = template.split('BackfillEveryHour:', 1)[1].split('BackfillApi:', 1)[0]
    assert 'Enabled: false' in backfill


def test_live_collection_and_settlement_remain_required():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    collect_block = source.split('"collect",', 1)[1].split(')', 1)[0]
    settle_block = source.split('"settle",', 1)[1].split(')', 1)[0]
    assert 'required=False' not in collect_block
    assert 'required=False' not in settle_block


def test_required_live_schedules_are_staggered_to_avoid_invoke_collisions():
    template = Path('tennis-template.yaml').read_text()
    assert "cron(1,16,31,46 * * * ? *)" in template
    assert "cron(6,36 * * * ? *)" in template
    assert "cron(11,26,41,56 * * * ? *)" in template
    assert template.index("cron(1,16,31,46 * * * ? *)") < template.index(
        "cron(6,36 * * * ? *)"
    )
    assert template.index("cron(6,36 * * * ? *)") < template.index(
        "cron(11,26,41,56 * * * ? *)"
    )


def test_lambda_invoke_throttling_has_bounded_adaptive_retry():
    source = Path('tennis_learning/autonomous_controller.py').read_text()
    required = [
        'Config(retries={"mode": "adaptive", "max_attempts": 3})',
        'TENNIS_INVOKE_MAX_ATTEMPTS',
        'TooManyRequestsException',
        '_retryable_invoke_error',
        'time.sleep(jittered_delay)',
    ]
    for token in required:
        assert token in source
