from mlb_auto.autonomous_handler import _model_access_status


def test_model_access_status_does_not_accept_stale_unconfigured_model():
    status = _model_access_status({
        'model_id': 'us.anthropic.claude-opus-4-8',
        'last_run_ok': True,
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
        'model_id': 'amazon.nova-micro-v1:0',
        'last_run_ok': True,
        'last_result': 'CANDIDATE_GENERATED',
    })

    assert status['selected_model_id'] == 'amazon.nova-micro-v1:0'
    assert status['verification_state'] == 'VERIFIED_BY_RESEARCH'
    assert status['model_access_verified_by_last_research_run'] is True
    assert status['selection_policy'] == 'FIRST_INVOKABLE_CONFIGURED_MODEL'
