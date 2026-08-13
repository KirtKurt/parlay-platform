from mlb_auto.autonomous_handler import _model_access_status


def test_model_access_status_does_not_accept_stale_unconfigured_model():
    status = _model_access_status({
        'model_id': 'us.anthropic.claude-opus-4-8',
        'last_run_ok': True,
        'last_invocation_ok': True,
        'last_result': 'CANDIDATE_GENERATED',
    })

    assert status['selected_model_id'] is None
    assert status['verification_state'] == 'NOT_YET_INVOKED'
    assert status['model_access_verified_by_last_research_run'] is False
    assert status['exact_model_requirement'] is False
    assert status['required_exact_model_ids'] == []
    assert status['account_mutation_attempted'] is False


def test_model_access_status_verifies_only_current_configured_model():
    status = _model_access_status({
        'model_id': 'us.amazon.nova-micro-v1:0',
        'last_run_ok': True,
        'last_invocation_ok': True,
        'last_result': 'CANDIDATE_GENERATED',
        'provider_available': True,
    })

    assert status['selected_model_id'] == 'us.amazon.nova-micro-v1:0'
    assert status['verification_state'] == 'VERIFIED_BY_RESEARCH'
    assert status['model_access_verified_by_last_research_run'] is True
    assert status['selection_policy'] == 'FIRST_INVOKABLE_CONFIGURED_MODEL'
    assert status['provider_available'] is True
    assert status['provider_degraded'] is False


def test_model_access_status_reports_retryable_provider_degradation_without_false_verification():
    status = _model_access_status({
        'model_id': None,
        'last_run_ok': True,
        'last_invocation_ok': False,
        'last_result': 'MODEL_PROVIDER_UNAVAILABLE',
        'provider_available': False,
        'degraded': True,
        'retryable': True,
        'account_model_policy': 'ACCOUNT_SAFE_AMAZON_GEO_FALLBACK_V1',
        'account_excluded_model_ids': ['us.anthropic.claude-opus-4-6-v1'],
    })

    assert status['selected_model_id'] is None
    assert status['verification_state'] == 'PROVIDER_UNAVAILABLE_RETRYABLE'
    assert status['model_access_verified_by_last_research_run'] is False
    assert status['provider_available'] is False
    assert status['provider_degraded'] is True
    assert status['last_invocation_ok'] is False
    assert status['retryable'] is True
    assert status['runtime_account_enrollment_managed'] is False
    assert status['account_mutation_attempted'] is False
